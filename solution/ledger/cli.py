from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from .db import connect
from .ingest import ingest_path
from .pii import PiiVault
from .report import build_reports
from .schema import DEFAULT_SCHEMA_DIR, load_dispositions, load_schemas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ledger", description="Interaction ledger & reporting pipeline")
    ap.add_argument("--db", default=os.environ.get("LEDGER_DB", "ledger.db"),
                    help="SQLite database path (default: ./ledger.db or $LEDGER_DB)")
    ap.add_argument("--schemas", default=str(DEFAULT_SCHEMA_DIR), help="folder with schemas + dispositions.csv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="ingest one drop (or a folder of drop_* folders); idempotent")
    ing.add_argument("path")
    rep = sub.add_parser("report", help="write the five report files")
    rep.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    t0 = time.time()
    conn = connect(args.db)
    if args.cmd == "ingest":
        vault = PiiVault.for_database(args.db)
        ingest_path(conn, vault, load_schemas(Path(args.schemas)), Path(args.path))
        print(f"ingest done in {time.time() - t0:.1f}s")
    else:
        build_reports(conn, load_dispositions(Path(args.schemas)), Path(args.out))
        print(f"reports written to {args.out} in {time.time() - t0:.1f}s")
    return 0
