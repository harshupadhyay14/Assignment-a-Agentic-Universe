import csv
import json

from solution.ledger.ingest import ingest_path
from solution.ledger.report import build_reports

from .conftest import ev, write_drop

HDR = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
LEAD = "Asha Rao,9876543210,POL1,01-Dec-2026,\"₹1,000\",HI\n"
P = "9876543210"


def run(env, *drops):
    for d in drops:
        ingest_path(env["conn"], env["vault"], env["schemas"], d, log=lambda *_: None)
    out = env["tmp"] / "out"
    build_reports(env["conn"], env["disp"], out)
    return out


def rows(out, name):
    with open(out / name, newline="") as f:
        return list(csv.DictReader(f))


def dq(out):
    return json.loads((out / "data_quality.json").read_text())


def scenario(env, events, lead=LEAD):
    d = write_drop(env["tmp"], "drop_1", {"motor_renewal": {"batch_001": HDR + lead}}, events)
    return run(env, d)


def test_lead_rejection_order_and_blank_rows(env):
    csv_text = (HDR + ",,,,,\n"                         # blank: not counted
                "Bad Phone,12345,P1,,,\n"
                ",12345,P2,,,\n"                         # name missing wins over bad phone
                "No Policy,9876543210,,,,\n"
                "Ok,9876543211,P4,31/02/2026,N/A,Hindi\n")
    d = write_drop(env["tmp"], "drop_1", {"motor_renewal": {"batch_001": csv_text}})
    out = run(env, d)
    assert [(r["row_number"], r["reason"]) for r in rows(out, "rejections.csv")] == [
        ("1", "invalid_phone"), ("2", "missing_required:customer_name"),
        ("3", "missing_required:policy_number")]
    q = dq(out)
    assert q["lead_rows_total"] == 4 and q["lead_rows_rejected"] == 3
    assert q["field_warnings"] == 3                      # bad date, bad amount, bad language


def test_duplicates_merge_last_non_null_wins_and_batch_sticks(env):
    d1 = write_drop(env["tmp"], "drop_1", {"motor_renewal": {
        "batch_001": HDR + "Asha,9876543210,POL1,,,\n",
        "batch_002": HDR + "Asha R,+91 98765 43210,POL1-NEW,01-12-2026,,\n"}})
    out = run(env, d1)
    leads = rows(out, "lead_report.csv")
    assert len(leads) == 1
    assert leads[0]["batch_id"] == "batch_001" and leads[0]["external_ref"] == "POL1-NEW"
    assert dq(out)["duplicate_lead_rows_merged"] == 1
    fields = json.loads(env["conn"].execute("SELECT fields_json FROM leads").fetchone()[0])
    assert fields["preferred_language"] == "en"          # default applied after merge
    assert fields["policy_expiry"] == "2026-12-01"       # overwritten only where non-null


def test_aliased_reordered_headers_and_extra_columns(env):
    d = write_drop(env["tmp"], "drop_1", {"motor_renewal": {"batch_001":
        "Language,Insured Name,Policy No,Mobile No,agent_notes\nhi,Asha,POL9,9876543210,hi there\n"}})
    out = run(env, d)
    assert rows(out, "lead_report.csv")[0]["external_ref"] == "POL9"


def test_pii_not_plaintext(env):
    d = write_drop(env["tmp"], "drop_1", {"motor_renewal": {"batch_001": HDR + LEAD}},
                   [ev("e1", "call.initiated", "c1", "2026-10-03T10:00:00Z", phone="+91 98765 43210")])
    run(env, d)
    env["conn"].commit()
    blob = "".join(str(r) for t in ("leads", "events") for r in env["conn"].execute(f"SELECT * FROM {t}"))
    assert "Asha" not in blob and "9876543210" not in blob


def test_event_pipeline_order_malformed_duplicate_orphan_unknown(env):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00"),
        ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00"),                  # duplicate
        "{not json", json.dumps({"event_type": "call.initiated"}),                  # malformed x2
        ev("e2", "call.connected", "c1", "2026-10-03T04:31:00Z"),
        ev("e3", "disposition.set", "c1", "2026-10-03T10:10:00+05:30", disposition_code="WAT"),   # unknown
        ev("e4", "call.initiated", "c9", "2026-10-03 11:00:00", phone="9111111111"),             # orphan
        ev("e5", "disposition.override", "c404", "2026-10-04 09:00:00", phone=None,
           disposition_code="renewed"),                                                          # orphan
    ]
    q = dq(scenario(env, events))
    assert (q["events_total"], q["malformed_events"], q["duplicate_events_dropped"]) == (8, 2, 1)
    assert q["orphan_events"] == 2 and q["unknown_disposition_events"] == 1


def test_effective_disposition_override_and_buckets(env):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00"),
        ev("e2", "call.connected", "c1", "2026-10-03 10:00:10"),
        ev("e3", "disposition.set", "c1", "2026-10-03 10:05:00", disposition_code="not_interested"),
        ev("e4", "disposition.override", "c1", "2026-10-04 10:00:00", phone=None, disposition_code="renewed"),
        ev("e5", "disposition.override", "c1", "2026-10-05 10:00:00", phone=None, disposition_code="BOGUS"),
    ]
    out = scenario(env, events)
    lead = rows(out, "lead_report.csv")[0]
    assert lead["current_disposition"] == "renewed" and lead["ever_success"] == "true"
    assert lead["bucket"] == "completed" and lead["last_call_at"] == "2026-10-03T10:00:00+05:30"
    day = rows(out, "daily_overview.csv")[0]
    assert (day["date"], day["total_calls"], day["connected_calls"], day["connectivity_rate"],
            day["success_leads"]) == ("2026-10-03", "1", "1", "1.0000", "1")
    assert dq(out)["unknown_disposition_events"] == 1
    # the ledger is append-only
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.DatabaseError):
        env["conn"].execute("DELETE FROM events")


def test_bucket_telephony_failed_vs_dialed_vs_untouched(env):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00"),
        ev("e2", "call.telephony_failed", "c1", "2026-10-03 10:00:03", reason="x"),
        ev("e3", "call.initiated", "c2", "2026-10-03 12:00:00", phone="9111111111"),   # no lead -> not counted
        ev("e4", "call.connected", "c5", "2026-10-03 12:00:00"),                        # no initiated
    ]
    out = scenario(env, events, lead=LEAD + "Ravi,9123456789,POL2,,,\n")
    by_ref = {r["external_ref"]: r for r in rows(out, "lead_report.csv")}
    assert by_ref["POL1"]["bucket"] == "in_progress_telephony_failed"
    assert by_ref["POL2"]["bucket"] == "yet_to_reach_out" and by_ref["POL2"]["last_call_at"] == ""


def test_early_events_count_once_lead_arrives_and_rerun_is_noop(env):
    ev1 = [ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00"),
           ev("e2", "call.connected", "c1", "2026-10-03 10:00:10")]
    d1 = write_drop(env["tmp"], "drop_1", None, ev1)
    out = run(env, d1)
    assert dq(out)["orphan_events"] == 2 and rows(out, "lead_report.csv") == []
    d2 = write_drop(env["tmp"], "drop_2", {"motor_renewal": {"batch_003": HDR + LEAD}})
    out = run(env, d1, d2)                              # d1 again: must change nothing
    q = dq(out)
    assert q["orphan_events"] == 0 and q["events_total"] == 2
    lead = rows(out, "lead_report.csv")[0]
    assert lead["batch_id"] == "batch_003" and lead["connected_calls"] == "1"
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    out = run(env, d1, d2)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


def test_incremental_equals_all_at_once(env, tmp_path):
    from solution.ledger.db import connect
    from solution.ledger.pii import PiiVault
    d1 = write_drop(env["tmp"], "drop_1", {"motor_renewal": {"batch_001": HDR + LEAD}},
                    [ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00")])
    d2 = write_drop(env["tmp"], "drop_2", {"motor_renewal": {"batch_002": HDR + "Asha,9876543210,POL1,,,\n"}},
                    [ev("e2", "call.connected", "c1", "2026-10-03 10:00:10"),
                     ev("e1", "call.initiated", "c1", "2026-10-03 10:00:00")])
    out_a = run(env, d1, d2)
    env2 = dict(env, conn=connect(str(tmp_path / "other.db")), vault=PiiVault(b"another-key"),
                tmp=tmp_path / "second")
    env2["tmp"].mkdir()
    ingest_path(env2["conn"], env2["vault"], env2["schemas"], env["tmp"], log=lambda *_: None)
    out_b = env2["tmp"] / "out"
    build_reports(env2["conn"], env2["disp"], out_b)
    for p in out_a.iterdir():
        assert p.read_bytes() == (out_b / p.name).read_bytes(), p.name
