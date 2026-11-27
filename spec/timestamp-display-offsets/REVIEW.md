# REVIEW — timestamp-display-offsets

## Review cycle 1 — approved (2026-11-27)

- **Mode:** blind correctness pass (fresh reviewer, xhigh), over `git diff develop...HEAD -- . ':(exclude)spec/'`.
- **Reviewed commit:** f93e96eb. Base: develop 5aa70c2c.
- **Contract drift:** one post-shape GOAL commit, aa913047 — R2 named a `toml` format `hs info` does not
  have; dropped, recorded as a dated clarification. No requirement moved.

### Verification run (by the reviewer)

- CLI in throwaway sites on SQLite and PostgreSQL, a task run through `hs cluster -N 1` so all four
  times are set, read under `TZ` America/New_York, UTC, Asia/Kolkata and Asia/Kathmandu with
  `PGTZ` set to another zone: every format shows `YYYY-MM-DD HH:MM:SS.ffffff±HH:MM` in the reader's
  zone; PostgreSQL follows `TZ` whatever the session zone.
- DST and fractional offsets on PostgreSQL (repeated hour in New York, Lord_Howe, St_Johns): correct.
- SQLite partitions (auto-union of `task.1`): offsets shown.
- develop vs branch over 22 commands per backend, stamps masked: only column widths differ; empty
  times print `null` as before.
- Wire: `Task.to_json()` and the sha256 of `serialize(...)`/`serialize_tasks(...)` identical on
  develop and the branch; no diff in types.py, data/, server.py, client.py, relay/, codec, queue, submit.
- Docs: `sphinx-build -W -E` exit 0.
- Tests: `tests/test_timestamp_display.py` 17 passed with PostgreSQL; related subsets 567 + 1129
  passed. One failure, `test_timescale.py::test_legacy_database_upgraded_not_converted`, is the local
  environment's missing `hs283` template (fixture setup), unrelated.

### Requirement → evidence

| R | Status | Evidence |
|---|---|---|
| R1 | met | multi-zone CLI drives, both backends |
| R2 | met | every `hs info`/`hs list` format, `-x`, `hs wait` log and `hs wait -i` |
| R3 | met | `TZ` variations, DST and fractional offsets |
| R4 | met | SQLite wall time with reader's offset; partitions |
| R5 | met | develop/branch comparison, `null` unchanged |
| R6 | met | wire hashes identical; untouched modules |
| R7 | met | both pages under `versionchanged:: 2.9.0`; `-W` build |

### Findings

- **F1 — PLAUSIBLE, declined by the foreman.** `docs/cli/task_wait.rst` and `task_run.rst` carry no
  marker for the `hs wait` completion log now showing the local offset. R7 names the `hs info` and
  `hs list` pages; the change on `hs wait` is a log line, and `hs wait -i` prints `hs info` output,
  which that page documents. Not graded as a gap; the 2.9.0 release notes mention the rule for every
  command that shows a task time.

### Notes

- Pre-existing, out of scope (storage is a non-goal): SQLite drops an aware value's offset without
  converting it, so a time written by a client in another zone is stored as that client's wall time
  and now reads with the reader's offset. Present before this branch.

### Human gate

Not triggered: no CONFIRMED finding; no high-blast-radius file touched.
