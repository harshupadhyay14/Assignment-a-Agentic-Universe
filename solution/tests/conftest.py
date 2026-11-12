import json
from pathlib import Path

import pytest

from solution.ledger.db import connect
from solution.ledger.pii import PiiVault
from solution.ledger.schema import DEFAULT_SCHEMA_DIR, load_dispositions, load_schemas

SCHEMA_DIR = DEFAULT_SCHEMA_DIR


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "t.db")
    return {"conn": connect(db), "vault": PiiVault(b"test-key"), "schemas": load_schemas(SCHEMA_DIR),
            "disp": load_dispositions(SCHEMA_DIR), "tmp": tmp_path}


def write_drop(root: Path, name: str, leads: dict[str, dict[str, str]] | None = None,
               events: list | None = None) -> Path:
    """leads: {workflow: {'batch_001': csv_text}}; events: list of dicts or raw strings."""
    d = root / name
    for wf, files in (leads or {}).items():
        (d / "leads" / wf).mkdir(parents=True, exist_ok=True)
        for stem, text in files.items():
            (d / "leads" / wf / f"{stem}.csv").write_text(text, encoding="utf-8")
    if events is not None:
        (d / "events").mkdir(parents=True, exist_ok=True)
        (d / "events" / f"events_{name}.jsonl").write_text(
            "\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return d


def ev(eid, etype, call, ts, phone="9876543210", wf="motor_renewal", **payload):
    e = {"event_id": eid, "event_type": etype, "workflow_id": wf, "call_id": call,
         "occurred_at": ts, "payload": payload}
    if phone is not None:
        e["phone"] = phone
    return e
