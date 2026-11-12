# Submission

## Candidate
- **Name:** Harsh
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** 24 Hours

## How to run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution.ledger ingest data/drop_1
python -m solution.ledger ingest data/drop_2
python -m solution.ledger report --out reports/
pytest
```
`ingest` also accepts a folder of drops (`ingest data`). `--db`, `--schemas`, `$LEDGER_PII_KEY` are optional.

## Design
SQLite, four tables (full DDL in `solution/ledger/db.py`):

- `ingested_files(file_key, sha256, seq, stats_json)`: one row per file, written **in the same
  transaction** as the file's data. Re-running a drop skips files already present (same
  `drop/path` and hash); a changed file under the same name is refused. This gives idempotency
  and crash safety (a half-ingested file rolls back entirely).
- `leads`: unique on `(workflow_id, phone_hash)`. `batch_id` is set once at creation. Normalised
  fields in `fields_json`; PII fields are encrypted.
- `lead_rejections`: `(source_file, row_number, reason)`, no PII.
- `events`: the **append-only ledger** (UPDATE/DELETE blocked by triggers). Stores every
  non-malformed, non-duplicate event, linked or not.

**Linking and reports happen at report time**, from the full state: events are matched to leads
by `(workflow_id, phone_hash)`, overrides through their call's lead. So an event that arrived
before its lead counts automatically once the lead is ingested, and drop splitting cannot change
results. Orphan and unknown-disposition counts are therefore always the final state.
Reports are computed in Python from ordered queries (not SQL aggregation).

**PII:** phones are matched via HMAC-SHA256 (keyed, so not brute-forceable from the DB alone);
names and phones are stored as Fernet tokens. Only the last 4 digits are stored in clear, to
render `phone_masked`. Events store only the phone hash. Key = `$LEDGER_PII_KEY` or a generated
`<db>.key` file (in production: a KMS).

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Event with unparseable `occurred_at` counts as malformed | Can't be placed on the timeline; spec silent | Store with null time |
| A linked event is classified orphan-first: an unlinked event with an unknown disposition counts only as orphan | Spec's step order (orphan before unknown) | Count both |
| Overrides link only via `call_id` (their own phone, if any, is ignored) | Spec | Link by phone when present |
| Call's lead = lead of its `call.initiated` (else earliest linked event); events for that call with a different lead are ignored | Deterministic on conflicting data | Treat as separate calls |
| Unknown-code override is ignored, so the call falls back to its `disposition.set` | "Unknown disposition: ignore it" | Let it shadow the set |
| `batch_summary` lists only batches that own at least one lead | Batches are a property of leads | Emit zero rows for all-rejected batches |
| `rejections_by_reason` lists only reasons that occurred | Counter semantics | Pre-fill zeros |
| Required non-phone field present but invalid rejects as `invalid_<field>` | Spec only defines two reasons (not triggered by the given schemas) | Warning only |
| Header normalisation also strips after collapsing | More lenient for `_name_` style headers | Strict order |
| Dates accept 1-2 digit day/month; month names are 3-letter | Practical | Strict zero padding |
| Decimal rounding is half-up | Conventional for money | Banker's rounding |
| Same-named file in a different drop is ingested again (key includes drop name); same name + same hash is skipped | Matches "drops are independent deliveries" | Hash-only identity |

## Failure modes considered
- Re-runs and crashes: per-file atomic transactions plus the `ingested_files` row.
- Dirty leads: blank rows, ragged rows, extra/renamed/reordered columns, BOM, bad dates/amounts/enums.
- Dirty events: malformed JSON, missing id, duplicates within a file and across drops, events
  before leads, overrides without phone, naive/offset/Z timestamps.
- Not handled: two processes ingesting concurrently (SQLite single writer; the second would block
  or fail rather than corrupt); a file edited in place after ingest is refused, not reconciled;
  no key rotation for the encrypted fields; very large drops hold all event ids in memory.

## Testing
`pytest`: value rules (parametrised), rejection precedence, merge semantics, PII absence,
dedupe/orphan/unknown counting, effective disposition and overrides, bucket logic, early events,
idempotent re-run, incremental == all-at-once. Not tested: concurrent writers, corrupted
encodings beyond `errors="replace"`, Postgres.

## Results
`--scale small` (seed 42): ingest ~0.3s, report <0.1s. `--scale large` (seed 7, ~144k lead
rows): ingest 25.7s, report 5.9s on this sandbox, well under the 2 minute target.
Re-ingesting a drop and ingesting both drops at once both produce byte-identical reports.
Observations: about 5% of lead rows are rejected, ~3% of event lines are duplicates, and orphans
are mostly events for rejected leads and never-uploaded numbers.

## If I had another day
1. Move aggregation into SQL (views) and compare with the Python version as a cross-check.
2. Postgres backend + advisory lock so two workers can't ingest at once.
3. True incremental reporting: only recompute leads touched by new events/leads.
4. Property-based tests that shuffle event order/drop splits.
5. Production note (100k leads/day): scheduled per-drop ingest, per-file idempotency (already in
   place), dead-letter table for malformed lines, backfills by re-ingesting into a fresh DB.
