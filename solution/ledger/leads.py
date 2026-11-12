"""Lead-file ingestion: header mapping, validation, normalisation, rejection, merging."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .normalize import norm_header, normalize_phone, parse_date, parse_decimal, parse_enum
from .pii import PiiVault
from .schema import Field, WorkflowSchema


def _parse(f: Field, raw: str):
    """Parse one non-empty trimmed value. Returns the normalised value or None if invalid."""
    if f.type == "string":
        return raw
    if f.type == "phone":
        return normalize_phone(raw)
    if f.type == "date":
        return parse_date(raw)
    if f.type == "decimal":
        return parse_decimal(raw)
    if f.type == "enum":
        return parse_enum(raw, f.values)
    raise ValueError(f"unknown field type {f.type!r} for {f.name}")


def ingest_lead_file(conn: sqlite3.Connection, vault: PiiVault, schema: WorkflowSchema,
                     path: Path, file_key: str) -> dict:
    """Ingest one CSV inside the caller's transaction. Returns the per-file stats."""
    batch_id = path.stem
    hmap = schema.header_map()
    phone_field = next(f for f in schema.fields if f.type == "phone")
    stats = {"rows": 0, "rejected": 0, "merged": 0, "warnings": 0, "reasons": Counter()}

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next((r for r in reader if any(c.strip() for c in r)), None)
        if header is None:
            stats["reasons"] = {}
            return stats
        col: dict[str, int] = {}          # field name -> column index (first match wins)
        for i, h in enumerate(header):
            f = hmap.get(norm_header(h))
            if f is not None:
                col.setdefault(f.name, i)

        row_number = 0
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue                  # blank rows are skipped and not counted
            row_number += 1
            stats["rows"] += 1

            values: dict[str, str | None] = {}
            warnings = 0
            reject: str | None = None
            for f in schema.fields:       # schema order: first failure wins
                i = col.get(f.name)
                raw = cells[i].strip() if i is not None and i < len(cells) else ""
                if raw == "":
                    if f.required:
                        reject = f"missing_required:{f.name}"
                        break
                    values[f.name] = None
                    continue
                v = _parse(f, raw)
                if v is None:
                    if f.required:
                        reject = "invalid_phone" if f.type == "phone" else f"invalid_{f.name}"
                        break
                    warnings += 1
                values[f.name] = v

            if reject:
                stats["rejected"] += 1
                stats["reasons"][reject] += 1
                conn.execute("INSERT INTO lead_rejections VALUES (?,?,?,?,?)",
                             (file_key, row_number, schema.workflow_id, batch_id, reject))
                continue
            stats["warnings"] += warnings
            _upsert_lead(conn, vault, schema, phone_field, batch_id, values, stats)

    stats["reasons"] = dict(stats["reasons"])
    return stats


def _upsert_lead(conn, vault, schema, phone_field, batch_id, values, stats):
    phone = values[phone_field.name]
    phash = vault.phone_hash(phone)
    row = conn.execute("SELECT id, fields_json FROM leads WHERE workflow_id=? AND phone_hash=?",
                       (schema.workflow_id, phash)).fetchone()
    by_name = {f.name: f for f in schema.fields}
    stored = {}
    for name, v in values.items():
        if v is not None:
            stored[name] = vault.encrypt(v) if by_name[name].pii else v

    if row is None:
        merged = stored
    else:                                  # duplicate: non-null values overwrite (last wins)
        stats["merged"] += 1
        merged = json.loads(row[1])
        merged.update({k: v for k, v in stored.items() if k != phone_field.name})
    for f in schema.fields:                # defaults apply after merging, to still-null fields
        if merged.get(f.name) is None and f.default is not None:
            merged[f.name] = f.default
    ref = values[schema.reference_field]
    if row is None:
        conn.execute(
            "INSERT INTO leads (workflow_id, phone_hash, phone_last4, external_ref, batch_id, fields_json)"
            " VALUES (?,?,?,?,?,?)",
            (schema.workflow_id, phash, phone[-4:], ref, batch_id, json.dumps(merged, sort_keys=True)))
    else:
        conn.execute("UPDATE leads SET external_ref=?, fields_json=? WHERE id=?",
                     (ref, json.dumps(merged, sort_keys=True), row[0]))
