"""Event-file ingestion into the append-only ledger."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .normalize import normalize_phone, parse_timestamp_us
from .pii import PiiVault

BATCH = 5000


class EventIngestor:
    def __init__(self, conn: sqlite3.Connection, vault: PiiVault):
        self.conn = conn
        self.vault = vault
        # every event_id seen so far (any drop). Loaded once; ~600k ids is a few tens of MB.
        self.seen = {r[0] for r in conn.execute("SELECT event_id FROM events")}

    def ingest_file(self, path: Path, file_key: str) -> dict:
        stats = {"total": 0, "malformed": 0, "duplicates": 0, "stored": 0}
        batch: list[tuple] = []
        new_ids: set[str] = set()
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                stats["total"] += 1
                rec = self._parse(line)
                if rec is None:                       # 1. malformed
                    stats["malformed"] += 1
                    continue
                eid = rec["event_id"]
                if eid in self.seen or eid in new_ids:   # 2. duplicate
                    stats["duplicates"] += 1
                    continue
                new_ids.add(eid)
                batch.append((eid, rec["event_type"], rec["workflow_id"], rec["call_id"],
                              rec["occurred_us"], rec["phone_hash"],
                              json.dumps(rec["payload"], sort_keys=True), file_key))
                stats["stored"] += 1
                if len(batch) >= BATCH:
                    self._flush(batch)
        self._flush(batch)
        # only now (file committed by caller) are the ids "seen" for later files
        self.seen.update(new_ids)
        return stats

    def _flush(self, batch):
        if batch:
            self.conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", batch)
            batch.clear()

    def _parse(self, line: str):
        try:
            d = json.loads(line)
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None
        eid = d.get("event_id")
        if eid is None or str(eid).strip() == "":
            return None
        us = parse_timestamp_us(d.get("occurred_at"))
        if us is None:                                # can't place it on the timeline
            return None
        phone = normalize_phone(d.get("phone"))
        payload = d.get("payload")
        return {
            "event_id": str(eid),
            "event_type": str(d.get("event_type")),
            "workflow_id": d.get("workflow_id"),
            "call_id": None if d.get("call_id") is None else str(d["call_id"]),
            "occurred_us": us,
            "phone_hash": self.vault.phone_hash(phone) if phone else None,
            "payload": payload if isinstance(payload, dict) else {},
        }
