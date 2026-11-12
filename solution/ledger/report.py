"""Derive the five report files from the database.

Everything is computed from the full current state (leads + ledger), so reports never depend
on how the data was split across drops.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import rate4, us_to_ist
from .schema import Disposition

IST_OFFSET_US = 19_800 * 1_000_000
DAY_US = 86_400 * 1_000_000


@dataclass
class Call:
    wf: str
    call_id: str
    lead: int | None = None
    initiated_us: int | None = None
    failed: bool = False
    connected: bool = False
    set_disp: tuple | None = None        # (occurred_us, event_id, code)
    override: tuple | None = None
    raw: list = field(default_factory=list)   # (type, occ, eid, lead_id) of linked events

    @property
    def effective(self) -> str | None:
        if self.override:
            return self.override[2]
        return self.set_disp[2] if self.set_disp else None


def _later(cur, new):
    return new if cur is None or (new[0], new[1]) > (cur[0], cur[1]) else cur


def compute(conn: sqlite3.Connection, dispositions: dict[tuple[str, str], Disposition]):
    leads = {}                      # lead id -> dict
    by_identity = {}                # (workflow, phone_hash) -> lead id
    for lid, wf, ph, last4, ref, batch in conn.execute(
            "SELECT id, workflow_id, phone_hash, phone_last4, external_ref, batch_id FROM leads ORDER BY id"):
        leads[lid] = {"wf": wf, "last4": last4, "ref": ref, "batch": batch}
        by_identity[(wf, ph)] = lid

    calls: dict[tuple[str, str], Call] = {}
    pending_overrides = []
    orphans = unknown = 0

    # pass 1: everything that links by (workflow, phone)
    for eid, et, wf, cid, occ, ph, payload in conn.execute(
            "SELECT event_id, event_type, workflow_id, call_id, occurred_us, phone_hash, payload_json "
            "FROM events ORDER BY occurred_us, event_id"):
        if et == "disposition.override":
            pending_overrides.append((eid, wf, cid, occ, payload))
            continue
        lid = by_identity.get((wf, ph)) if ph else None
        if lid is None:
            orphans += 1
            continue
        if cid is None:
            continue                # linked but not part of any call: nothing to report
        call = calls.get((wf, cid))
        if call is None:
            call = calls[(wf, cid)] = Call(wf, cid)
        call.raw.append((et, occ, eid, lid, payload))

    # settle each call's lead (call.initiated wins, else earliest linked event), then
    # accumulate its facts from the events that agree with that lead
    for call in calls.values():
        lead = next((r[3] for r in call.raw if r[0] == "call.initiated"), call.raw[0][3])
        call.lead = lead
        for et, occ, eid, lid, payload in call.raw:
            if lid != lead:
                continue
            if et == "call.initiated":
                call.initiated_us = occ if call.initiated_us is None else min(call.initiated_us, occ)
            elif et == "call.telephony_failed":
                call.failed = True
            elif et == "call.connected":
                call.connected = True
            elif et == "disposition.set":
                code = json.loads(payload).get("disposition_code")
                if (leads[lead]["wf"], code) in dispositions:
                    call.set_disp = _later(call.set_disp, (occ, eid, code))
                else:
                    unknown += 1
        call.raw = []

    # pass 2: overrides link through their call_id
    for eid, wf, cid, occ, payload in pending_overrides:
        call = calls.get((wf, cid))
        if call is None:
            orphans += 1
            continue
        code = json.loads(payload).get("disposition_code")
        if (leads[call.lead]["wf"], code) in dispositions:
            call.override = _later(call.override, (occ, eid, code))
        else:
            unknown += 1

    counted = [c for c in calls.values() if c.initiated_us is not None]
    return leads, counted, orphans, unknown


def build_reports(conn, dispositions, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    leads, calls, orphans, unknown = compute(conn, dispositions)

    def disp(c: Call) -> Disposition | None:
        code = c.effective
        return dispositions.get((leads[c.lead]["wf"], code)) if code else None

    by_lead: dict[int, list[Call]] = defaultdict(list)
    for c in calls:
        by_lead[c.lead].append(c)

    # ---- per-lead derived facts
    lead_rows, bucket_of = [], {}
    for lid, L in leads.items():
        cs = sorted(by_lead.get(lid, []), key=lambda c: (c.initiated_us, c.call_id))
        if not cs:
            bucket = "yet_to_reach_out"
        elif any((d := disp(c)) and d.is_terminal for c in cs):
            bucket = "completed"
        elif all(c.failed for c in cs):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"
        bucket_of[lid] = bucket
        with_disp = [c for c in cs if c.effective]
        lead_rows.append({
            "workflow_id": L["wf"], "external_ref": L["ref"], "batch_id": L["batch"],
            "phone_masked": "+91******" + L["last4"], "bucket": bucket,
            "total_calls": len(cs), "connected_calls": sum(c.connected for c in cs),
            "last_call_at": us_to_ist(cs[-1].initiated_us).replace(microsecond=0).isoformat() if cs else "",
            "current_disposition": with_disp[-1].effective if with_disp else "",
            "ever_success": str(any((d := disp(c)) and d.is_success for c in cs)).lower(),
            "_id": lid,
        })
    lead_rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"], r["_id"]))

    # ---- 1. batch_summary
    bs: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for lid, L in leads.items():
        k = bs[(L["wf"], L["batch"])]
        k["total"] += 1
        k[bucket_of[lid]] += 1
    cols = ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
            "in_progress_telephony_failed", "in_progress_dialed", "completed"]
    _write_csv(out / "batch_summary.csv", cols, [
        [wf, b, k["total"], k["yet_to_reach_out"], k["in_progress_telephony_failed"],
         k["in_progress_dialed"], k["completed"]] for (wf, b), k in sorted(bs.items())])

    # ---- 2. daily_overview
    daily: dict[tuple[str, int], list] = defaultdict(lambda: [0, 0, set()])
    for c in calls:
        day = (c.initiated_us + IST_OFFSET_US) // DAY_US          # days since epoch, IST
        d = daily[(leads[c.lead]["wf"], day)]
        d[0] += 1
        d[1] += c.connected
        if (dd := disp(c)) and dd.is_success:
            d[2].add(c.lead)
    from datetime import date, timedelta
    _write_csv(out / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               [[wf, (date(1970, 1, 1) + timedelta(days=day)).isoformat(), t, cn, rate4(cn, t), len(s)]
                for (wf, day), (t, cn, s) in sorted(daily.items())])

    # ---- 3. lead_report
    lc = ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
          "connected_calls", "last_call_at", "current_disposition", "ever_success"]
    _write_csv(out / "lead_report.csv", lc, [[r[c] for c in lc] for r in lead_rows])

    # ---- 4. rejections (no PII: only workflow, batch, row number, reason)
    rej = conn.execute("SELECT workflow_id, batch_id, row_number, reason FROM lead_rejections "
                       "ORDER BY workflow_id, batch_id, row_number, source_file").fetchall()
    _write_csv(out / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"], rej)

    # ---- 5. data_quality
    fs = [(k, json.loads(s)) for k, s in conn.execute("SELECT kind, stats_json FROM ingested_files")]
    lstats = [s for k, s in fs if k == "leads"]
    estats = [s for k, s in fs if k == "events"]
    reasons = Counter(r[3] for r in rej)
    dq = {
        "lead_rows_total": sum(s["rows"] for s in lstats),
        "lead_rows_rejected": len(rej),
        "rejections_by_reason": dict(sorted(reasons.items())),
        "duplicate_lead_rows_merged": sum(s["merged"] for s in lstats),
        "field_warnings": sum(s["warnings"] for s in lstats),
        "events_total": sum(s["total"] for s in estats),
        "malformed_events": sum(s["malformed"] for s in estats),
        "duplicate_events_dropped": sum(s["duplicates"] for s in estats),
        "orphan_events": orphans,
        "unknown_disposition_events": unknown,
    }
    (out / "data_quality.json").write_text(json.dumps(dq, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, header, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
