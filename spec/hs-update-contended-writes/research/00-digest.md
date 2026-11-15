# Research digest — `hs update` contended writes (2026-11-15)

Three briefs: [`01`](01-residual-and-limit.md) (the planner's: missed-row detection, `--limit`'s
transaction, in-process testing), [`02`](02-filter-and-change.md) (the user filter and the combined
change), [`03`](03-contended-delete.md) (contended DELETE, upstream). One decision each:

1. **Missed rows of a set-based UPDATE are found by `RETURNING id` and a residual read, on
   PostgreSQL only.** `xmin` works on plain tables but the columnstore chunks the `timescale` provider
   creates refuse it (01 §2), so it is out (03 §4 tested only uncompressed chunks). SQLite needs no
   residual (a write transaction excludes every other writer). A set-based DELETE's residual is the
   filter alone (a deleted row is gone for its own transaction), PostgreSQL only, for the same reason.
2. **Every change of one `hs update` is one SET.** Fields plus one tag expression: SQLite
   `json_remove(json_set(tag, …), …)`, PostgreSQL `(tag || patch) - names`; same results as today's
   sequence in every case 02 tried, on all three providers. It fixes R5 on both forms by
   construction and keeps the set-based form at one UPDATE. The `--limit` path uses the same SET by
   id, so the tag merge happens at write time, not from the selected snapshot.
3. **The user's filter becomes a list of self-contained criteria** built once on `SearchableMixin`
   and used by the count, the selection, the set-based write, the residual read and every per-row
   write and lookup (02 §4). On SQLite the name-only tag criterion needs its own bind per tag, which
   also fixes `hs list -t a b` checking only `b` (02 §4a): **foreman decision** (it touches `hs list`).
4. **The multi-key PostgreSQL variant reproduces** on PG 18 and TimescaleDB 2.32 (02 §2), and the
   set-then-remove variant on SQLite too. The GOAL's example line is mis-parsed (`-t` takes every
   following word): tests and docs put the changes before `-t`.
5. **A contended DELETE misses on TimescaleDB 2.28.1–2.31.0** (upstream #10902, fixed by #10906 in
   2.32.0, 03 §1). R6's `--delete` real-race tests are expected red on CI's 2.31.0 jobs, not a guard.
   Upstream dates the first bad release 2.28.1, not 2.29 as AGENTS.md says; out of scope here.
6. **Fault injection reaches the set-based form on a server database** through a `BEFORE … FOR EACH
   ROW` trigger returning NULL (01 §1), red on every PostgreSQL job before the fix; the driver-level
   SQLite injector (`DriverContention`) covers the per-row paths (legacy, `--limit`, the re-run).
7. **`--limit` as one transaction** costs about what the batches cost, but holds every written row's
   lock (SQLite: the database write lock) to the end: about 13 µs per row on SQLite, 0.1 ms per row on
   local PostgreSQL (01 §3). Accepted at the gate; documented.
8. **The real race runs `hs update` as a subprocess** inside `contend`; `blocked_by` must also
   recognise `DELETE FROM task` (03 §3).
