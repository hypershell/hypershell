# 00 — Digest: contended-writes

`appetite: small`, `kind: fix`, but the change touches `data/model.py` (high blast radius) and the
GOAL hands `hs-plan` two open questions (the mark's re-check and atomicity; whether an in-server
writer still contends the mark). Per `hs-plan` Step 3's exceptions, those questions were researched,
sequentially and by probe rather than by a subagent fan-out: each is one code path and one probe.

| Brief | Question | Decision |
|-------|----------|----------|
| [01](01-retry-mark-and-cancel.md) | How do the mark and its retry row become atomic, and what re-check does the mark ask? | One transaction: per-row conditional marks through `__write_rows` (new `retryable` condition: still awaiting retry), then INSERT only the retry rows whose mark landed, then one commit. Kill → nothing; exhausted → WARNING, no retry row, row stays retryable; no longer awaiting → skipped. |
| [01](01-retry-mark-and-cancel.md) | Can an in-server writer still contend the mark after #82 R19 / #103 R1? | **Yes, narrowly.** A conditional write that began before the failure's result committed waits on the Receiver's lock, re-checks to 0 rows, and *keeps the row lock until its transaction ends* (probed on PostgreSQL 18 and TimescaleDB 2.32). A late confirmation racing its own failed result is the main case. Plus `hs update` and API callers cross-process. The design does not depend on the answer. |
| [01](01-retry-mark-and-cancel.md) | `Task.cancel_all`/`cancel` | `__write_rows(condition=None)`: re-runs while the row exists, raises `StaleDataError` (nothing committed) for a gone row, WARNING and left as is at the bound; returns the ids cancelled (`cancel` returns whether it cancelled). |
| [02](02-source-row.md) | Conflict-safe get-or-create of `<direct>`/`<stdin>` | Lookup; on a miss `INSERT … ON CONFLICT (id) DO NOTHING` built for the bind's dialect (SQLite, PostgreSQL), commit, re-read. `source.id` is the primary key on fresh, 2.8.3-upgraded and TimescaleDB-provider databases alike. |

Before-fix evidence gathered here (reused by the build's evidence): the mark's `StaleDataError` and
doubled retry on SQLite by driver-level injection; the stop between the commits leaving an orphaned
retry row; the source race 3/20 (fresh SQLite), 2/10 (2.8.3-upgraded SQLite), 7/10 (fresh
PostgreSQL) failing rounds of six concurrent first `hs submit`s.

Out of scope, recorded for a seed: `Task.next(eager=True)` returns a just-committed retry row twice
(brief 01, aside). The plan leaves it as it is.
