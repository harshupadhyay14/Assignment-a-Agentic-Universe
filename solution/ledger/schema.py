"""Workflow lead schemas and disposition catalogue loaders."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import norm_header

DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "assignment-a-data" / "schemas"


@dataclass
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    values: list[str] = field(default_factory=list)
    default: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class WorkflowSchema:
    workflow_id: str
    fields: list[Field]

    @property
    def reference_field(self) -> str:
        return next(f.name for f in self.fields if f.reference)

    def header_map(self) -> dict[str, Field]:
        m: dict[str, Field] = {}
        for f in self.fields:
            for key in [f.name, *f.aliases]:
                m.setdefault(norm_header(key), f)
        return m


def load_schemas(schema_dir: Path) -> dict[str, WorkflowSchema]:
    out = {}
    for p in sorted(Path(schema_dir).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        fields = [Field(name=f["name"], type=f["type"], required=f.get("required", False),
                        pii=f.get("pii", False), reference=f.get("reference", False),
                        values=f.get("values", []), default=f.get("default"),
                        aliases=f.get("aliases", [])) for f in d["fields"]]
        out[d["workflow_id"]] = WorkflowSchema(d["workflow_id"], fields)
    return out


@dataclass(frozen=True)
class Disposition:
    is_terminal: bool
    is_success: bool


def load_dispositions(schema_dir: Path) -> dict[tuple[str, str], Disposition]:
    out = {}
    with open(Path(schema_dir) / "dispositions.csv", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[(r["workflow_id"].strip(), r["code"].strip())] = Disposition(
                r["is_terminal"].strip().lower() == "true",
                r["is_success"].strip().lower() == "true")
    return out
