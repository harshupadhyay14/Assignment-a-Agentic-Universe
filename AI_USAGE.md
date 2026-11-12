# AI Usage Disclosure

## Tools used
Claude.

## What I used them for
I asked it to implement Assignment A and it chose A over B because it could be checked offline
and deterministically.

## What I wrote or decided myself
Claude wrote the initial implementation, so I'm not claiming the code as hand-written. My contribution:
- I chose Assignment A (data) over B and gave Claude the task brief.
- I ran the pipeline on my own machine (Windows, Python 3.12) on freshly generated data
  (`--seed 42 --scale small`): ingested drop_1 then drop_2, produced the reports, and ran
  the test suite (46 passed).
- I reviewed the design and understand these decisions well enough to defend them:
  - Per-file transactions plus an `ingested_files` table, so re-running a drop changes nothing.
  - Events are stored unlinked and linked to leads at report time, so events that arrive before
    their lead are counted once the lead arrives.
  - PII: phones matched via keyed hash (HMAC), names/phones stored encrypted; only the last 4
    digits stored in clear for the masked phone.
  - The assumptions in the decisions table in SUBMISSION.md (e.g. unparseable timestamps count
    as malformed, batches with no leads are omitted from batch_summary).

## Where the AI was wrong or unhelpful
- Its first test for the event pipeline expected 7 `events_total` for a file with 8 non-empty
  lines; the pipeline was right and the test was wrong (caught by the test run).
- Its first duplicate-event check in `events.py` used a clumsy helper (`new_ids_set(...)`) that
  was rewritten to a plain per-file set; a leftover walrus-in-tuple expression in `report.py`
  was also cleaned up.
- _Add anything you find yourself while reviewing._

## How I verified AI-generated code
- `pytest` (46 tests: phone/date/decimal/header rules, rejection order, merging, PII,
  effective disposition, buckets, early events, idempotent re-run, incremental == all-at-once).
- On generated data: re-ingesting a drop leaves reports byte-identical; drop_1 then drop_2
  equals `ingest data` in one go; the SQLite file contains no plaintext names or phones.
- Spot-checked output against the spec's own examples (e.g. `0.5625`, `2026-10-14T17:55:24+05:30`).
- _Add: the parts you read line by line._
