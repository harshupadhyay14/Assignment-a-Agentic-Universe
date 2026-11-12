"""Drop discovery and idempotent, per-file-atomic ingestion."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .events import EventIngestor
from .leads import ingest_lead_file
from .pii import PiiVault
from .schema import WorkflowSchema


def _natural(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def find_drops(path: Path) -> list[Path]:
    """`path` is either one drop (has leads/ or events/) or a folder of drop_* folders."""
    path = Path(path)
    if (path / "leads").is_dir() or (path / "events").is_dir():
        return [path]
    drops = sorted((p for p in path.iterdir() if p.is_dir() and p.name.startswith("drop")),
                   key=_natural)
    if not drops:
        raise SystemExit(f"{path}: no leads/ or events/ folder and no drop_* subfolders")
    return drops


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _already_done(conn, file_key: str, sha: str) -> bool:
    row = conn.execute("SELECT sha256 FROM ingested_files WHERE file_key=?", (file_key,)).fetchone()
    if row is None:
        return False
    if row[0] != sha:
        raise SystemExit(f"{file_key} was ingested before with different content; "
                         "refusing to ingest a changed file under the same name")
    return True


def _record(conn, file_key, kind, sha, stats):
    seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM ingested_files").fetchone()[0]
    conn.execute("INSERT INTO ingested_files VALUES (?,?,?,?,?)",
                 (file_key, kind, sha, seq, json.dumps(stats, sort_keys=True)))


def ingest_path(conn: sqlite3.Connection, vault: PiiVault, schemas: dict[str, WorkflowSchema],
                path: Path, log=print) -> None:
    events = EventIngestor(conn, vault)
    for drop in find_drops(path):
        # Lead files: workflow folders then filename order. Identity includes workflow_id,
        # so cross-workflow order cannot change the result.
        lead_files = sorted((drop / "leads").glob("*/*.csv"), key=lambda p: (p.parent.name, p.name))
        for f in lead_files:
            wf = f.parent.name
            file_key = f"{drop.name}/leads/{wf}/{f.name}"
            if wf not in schemas:
                log(f"  ! {file_key}: no schema for workflow {wf!r}, skipped")
                continue
            sha = _sha256(f)
            if _already_done(conn, file_key, sha):
                log(f"  = {file_key}: already ingested")
                continue
            with conn:                     # file + its bookkeeping row commit atomically
                _record(conn, file_key, "leads", sha, {})
                stats = ingest_lead_file(conn, vault, schemas[wf], f, file_key)
                conn.execute("UPDATE ingested_files SET stats_json=? WHERE file_key=?",
                             (json.dumps(stats, sort_keys=True), file_key))
            log(f"  + {file_key}: {stats['rows']} rows, {stats['rejected']} rejected, "
                f"{stats['merged']} merged")
        for f in sorted((drop / "events").glob("*.jsonl")):
            file_key = f"{drop.name}/events/{f.name}"
            sha = _sha256(f)
            if _already_done(conn, file_key, sha):
                log(f"  = {file_key}: already ingested")
                continue
            with conn:
                _record(conn, file_key, "events", sha, {})
                stats = events.ingest_file(f, file_key)
                conn.execute("UPDATE ingested_files SET stats_json=? WHERE file_key=?",
                             (json.dumps(stats, sort_keys=True), file_key))
            log(f"  + {file_key}: {stats['total']} lines, {stats['stored']} stored, "
                f"{stats['malformed']} malformed, {stats['duplicates']} duplicates")
