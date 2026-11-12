# Assignment A: Interaction Ledger & Reporting Pipeline

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

python -m solution.ledger ingest data/drop_1      # idempotent; may also pass `data` (all drops)
python -m solution.ledger ingest data/drop_2
python -m solution.ledger report --out reports/
pytest                                            # unit + pipeline tests
```

Options (before the sub-command): `--db PATH` (default `./ledger.db`, or `$LEDGER_DB`),
`--schemas DIR` (default `assignment-a-data/schemas`). PII key: `$LEDGER_PII_KEY`, otherwise a
random key is created once at `<db>.key` (chmod 600, git-ignored).

Layout: `normalize.py` (pure value rules) · `schema.py` · `pii.py` · `db.py` (DDL) ·
`leads.py` / `events.py` (ingestion) · `ingest.py` (drops, idempotency) · `report.py` · `cli.py`.
