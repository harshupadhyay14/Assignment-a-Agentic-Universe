# AI Usage Disclosure

## Tools used
Claude (claude.ai chat).

## What I used them for
Claude read the assignment brief and generated the first full draft of the pipeline
(`solution/ledger/*`), the tests (`solution/tests/*`) and the drafts of `SUBMISSION.md` and this file.
I asked it to implement Assignment A and it chose A over B because it could be checked offline
and deterministically.

## What I wrote or decided myself
_Fill in before submitting. Be specific: which design decisions you reviewed, changed or can
defend (schema, PII approach, orphan handling at report time, per-file transactions). Do not
claim anything you did not do._

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
