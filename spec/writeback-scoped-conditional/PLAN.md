# PLAN — Scoped, conditional result writeback

> **Status:** Draft for review · **Last updated:** 2026-11-02
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

A returned result is applied by one atomic conditional `UPDATE` per task that sets only the 13
result columns, and only while the row is still in flight under the dispatch the result came from.
The dispatch is identified by the row's `schedule_time`, which the server stamps fresh on every
dispatch and every client already echoes unchanged. That needs no schema change and no wire
change, so the #69 upgrade step is not involved. When the `UPDATE` matches nothing, a second
predicate classifies the refusal as a duplicate (R5) or stale (R2), and the Receiver logs it
instead of treating it as a completion. The diff is two functions' worth of code in
`data/model.py` and one method in `server.py`, plus a new test file and the R10 prose. It fits
about a day of build and review.

## 2. Design

### 2.1 Assignment identity (no schema change)

An **assignment** is identified by `(id, schedule_time)`. `Task.next` (`model.py:631-647`) sets
`schedule_time = now()` on every dispatch; `revert_all` (`:721`) sets it NULL; `cancel_all`
(`:757`) and `hs task update --cancel` restamp it together with `completion_time`. So the value a
client was dispatched with equals the row's current `schedule_time` exactly when the row has not
been reverted, rescheduled (to any client, the same one included) or cancelled since. Adding
`completion_time IS NULL` excludes a row already completed by any assignment. None of this reads
the returned `client_id` (R4).

Why not something else ([research 01](research/01-assignment-token.md) §5): `server_id` is
constant for a server run; `client_id` is ruled out by R4 and is never stamped under `no_confirm`;
a new token field breaks an older peer, because `Task.from_json` is `cls(**data)` and rejects
unknown keys (R9). A token column added through #69 would still have to cross the wire, so it buys
nothing over `schedule_time`.

Fidelity: the bundle is serialized after a reload (`expire_on_commit=True`), so on SQLite it
carries naive wall-clock time, and on PostgreSQL an aware `timestamptz`. The echo is bound through
the same `DATETIME` type, so equality is exact. A SQLite probe confirmed a match for the echo and
no match for a 1 µs shift, a revert, a re-dispatch, a replay and a cancel
([research 01](research/01-assignment-token.md) §3). The same instant at another UTC offset does
**not** match on SQLite; see §5 (relay).

### 2.2 Result columns (R1)

`Task.result_columns` is a new class attribute in `data/model.py` next to `Task.columns`: a tuple of
exactly these 13 names, which every client (2.8.3 and current) sets on a result and nothing else:

`client_id`, `client_host`, `command`, `start_time`, `completion_time`, `waited`, `duration`,
`exit_status`, `outpath`, `errpath`, `csvpath`, `cores_max`, `memory_max`.

Every other column (`id`, `group`, `args`, `submit_id`, `submit_time`, `submit_host`, `cores`,
`memory`, `timeout`, `server_id`, `server_host`, `schedule_time`, `attempt`, `retried`,
`previous_id`, `next_id`, `source`, `fingerprint`, `part`, `tag`) is never in the `SET` list.
`schedule_time` is read from the copy only as the token in the `WHERE`.

All 13 are set from the copy, NULLs included, as today. `revert_all` does not clear `csvpath`,
`cores_max`, `memory_max`, and today's writeback overwrites them; skipping NULLs would change R3's
outcome ([research 02](research/02-callers-concurrency-tests.md) §3).

### 2.3 The fence (`data/model.py`)

New, module level: `class ResultOutcome(Enum)` with `APPLIED`, `DUPLICATE`, `STALE`.

New classmethod `Task.apply_results(cls, results: List[Task]) -> List[ResultOutcome]`, one outcome
per input, in order. Inside one transaction:

1. If `result.schedule_time is None` → `STALE`, no query. (In SQLAlchemy `column == None` renders
   `IS NULL`, which would match a reverted row.)
2. `Session.execute(update(Task).where(Task.id == result.id, Task.schedule_time ==
   result.schedule_time, Task.completion_time.is_(None)).values({name: getattr(result, name) for
   name in cls.result_columns}).execution_options(synchronize_session=False))`. `rowcount == 1` →
   `APPLIED`.
3. Otherwise run `select(Task.id).where(Task.id == result.id, Task.schedule_time ==
   result.schedule_time, Task.completion_time.isnot(None), Task.exit_status != CANCEL_STATUS)`.
   A row → `DUPLICATE` (this assignment already completed it). No row → `STALE`: reverted,
   rescheduled, completed by another assignment, cancelled, or missing (rotated or deleted).
4. `Session.commit()` once at the end; on any exception `Session.rollback()` and re-raise
   (mirrors `Entity.add_all`).

Properties this buys:
- Atomic against concurrent writers. The check and the write are one statement. PostgreSQL READ
  COMMITTED re-evaluates the `WHERE` after a concurrent revert commits; SQLite serializes writers.
  A read-then-write in Python would race the HeartMonitor's revert and the Scheduler's reschedule.
- Two identical copies in one bundle: the second sees the first's uncommitted write, matches 0 rows
  and classifies as `DUPLICATE` (R5). Same for a redelivered bundle after commit.
- A result for a row that no longer exists is refused and logged. Today `bulk_update_mappings`
  raises `StaleDataError` there and kills the receiver thread.
- The classification is a SQL predicate, not a Python comparison, so it never compares naive
  against aware datetimes and never reads a stale identity-mapped instance.

`Entity.update_all`, `Entity.update`, `revert_all`, `cancel_all`, `revert_orphaned`, `Task.next`
and the Confirm path are **unchanged** (R9).

### 2.4 The Receiver (`server.py`)

Only `Receiver.update_tasks` changes. With `in_memory` it behaves exactly as today: no DB, every
task logged as completed (R8). Otherwise it calls `Task.apply_results(self.tasks)` and:
- `APPLIED` → today's handling, unchanged: `log.debug('Completed task (…)')`, the non-zero-exit
  warning, and the `--failures` redirect of `task.args` (R3).
- `STALE` → `log.warning(f'Refused stale result for task ({task.id}) from client ({task.client_host}:
  {task.client_id})')`. No completion line, no non-zero-exit line, no `--failures` write (R2). The
  client named is the one the result says ran it; it is used for the log only.
- `DUPLICATE` → `log.warning(f'Ignored duplicate result for task ({task.id}) from client (…)')`,
  same suppression (R5). The wording differs from the stale line.

No other server state, thread, queue or ordering changes, so §5/§6 of the invariants are not
touched.

### 2.5 Contract prose (R10)

`AGENTS.md` § Task lifecycle contract: drop "from a late full-row writeback by an evicted client"
from the third "Unguarded" bullet (it keeps "a reset `retried` flag … from `hs task update`"), and
add one sentence: a returned result writes only its result columns, and only for its current
assignment (the row's `schedule_time` as dispatched, while `completion_time` is NULL); a stale or
duplicate result is logged and dropped. Mirror both in `.agents/factory/invariants.md` §3. The four
state predicates, the `exit_status` table and the retry model are untouched (R7). One commit.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `Task.result_columns` (13 names, listed in TECH) is the only `SET` list in `Task.apply_results`; test mutates every non-result column and asserts none changed |
| R2   | Conditional `WHERE id, schedule_time = echo, completion_time IS NULL`; `STALE` → warning naming task id and client, no completion line, no `--failures` write |
| R3   | `APPLIED` sets all 13 columns from the copy, NULLs included; Receiver's existing logging/redirect runs only for `APPLIED`; parity test against today's `update_all` outcome |
| R4   | Token is `schedule_time`, never the returned `client_id`; `client_id`/`client_host` are result columns; test returns a copy attributed to another client with different `server_id`/`server_host` |
| R5   | Second predicate classifies `DUPLICATE`; distinct warning; row unchanged; one completion |
| R6   | Late failed result is `STALE` (its token no longer matches); chain columns never in `SET`; replay test drives evict → revert → reschedule → fail → retry → late result → `Task.next` |
| R7   | No predicate or query changes; cancel restamps `schedule_time` and sets `completion_time`, so any result for a cancelled row is `STALE` |
| R8   | `in_memory` branch unchanged; `no_confirm` never stamps `client_id` but the token does not depend on it; tests and a `--no-confirm` CLI drive |
| R9   | No wire change, no schema change; `Entity.update_all` and its callers untouched |
| R10  | §2.5, one commit in P3 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design was drafted.

- **§1 Task lifecycle.** No predicate changes. The fence's `completion_time IS NULL` and the
  classifier's `completion_time IS NOT NULL` are the in-flight and terminal predicates verbatim.
  The logic is a `Task` classmethod, not FSM code. Setting `completion_time` only via a matched
  assignment cannot strand a scheduled row.
- **§2 `exit_status`.** No new sentinel. The classifier's `exit_status != CANCEL_STATUS` keeps a
  cancelled row from being read as this assignment's completion. No failure/retry query is touched.
- **§3 Retry model.** Retries stay new rows. The fence removes one of the listed unguarded causes
  (the late full-row writeback); `retried`, `next_id`, `previous_id`, `attempt` leave the `SET`
  list. `query_retryable`, `select_failed`, `count_retryable`, `increment_group` are untouched.
- **§4 Server modes.** The DB write stays under `if not self.in_memory`. `no_confirm` keeps working
  because the token is `schedule_time`, which `Task.next` always stamps.
- **§5/§6 Concurrency, shutdown.** No new state, thread, queue op or ordering. `update_tasks`
  still returns `UNLOAD`. Each thread uses its own scoped session; the method commits or rolls back
  before returning.
- **§9 Queue transport.** Payloads unchanged; `serialize_tasks`/`deserialize_tasks` untouched.
- **§12 Conventions.** No CLI change, so no `docs/_include` or `share/` edit. New tests carry
  `@mark.unit` / `@mark.integration`. No R-IDs in source or tests.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

- **What identifies an assignment without a schema or wire change?** → the echoed `schedule_time`
  ([`research/01-assignment-token.md`](research/01-assignment-token.md) §1–§5).
- **Does the timestamp survive JSON and both dialects exactly?** → yes on SQLite by probe; on
  PostgreSQL by type semantics; not across a UTC-offset change (01 §3).
- **Is a per-row conditional UPDATE atomic and is `rowcount` reliable?** → yes, matched-row count
  on SQLite and psycopg 3; PostgreSQL re-checks the `WHERE` under READ COMMITTED
  ([`research/02-callers-concurrency-tests.md`](research/02-callers-concurrency-tests.md) §3).
- **Do other `update_all` callers or Confirm need to change?** → no (02 §1–§2).
- **Can rotation hide an in-flight row from the fence?** → no; only rows with `exit_status` set
  leave the main file (02 §4).
- **Can the red tests be driven without threads or a queue?** → yes: `Receiver(queue=None)`,
  `Confirm(queue=None)`, `Task.revert_orphaned`, and the `task_db` fixture pattern from
  `tests/test_retry_gate.py` (02 §5).

## 5. Risks & open questions

- **Relay must echo the upstream `schedule_time` verbatim.** The other stamps no longer matter
  (they are never written), but the token must come back as received, at the same UTC offset, or the
  result is refused (fail-safe, logged, not lost silently, since the row reruns). `issues/relay.md`
  already retains the received bundle; its return-path note shrinks from "restore the stamps" to
  "echo the received `schedule_time`". Left for the foreman to carry to relay shaping (GOAL
  Clarifications). Flagged to the judge as a clarification.
- **A result from before a server restart is now refused.** `Scheduler.start` reverts interrupted
  rows, so a result produced under the previous run no longer matches. That is R2 as written; no
  current client reconnects across a server restart, so the case is theoretical.
- **An operator `hs task update … schedule_time=…` on an in-flight row** makes its result stale.
  Acceptable: it is an explicit edit of the token.
- **Pre-existing residuals, recorded, not fixed:** a stale confirm can rewrite `client_id` on a
  rescheduled row (hiding it from `revert_orphaned`); a late confirm can overwrite a relay's leaf
  attribution; `revert_orphaned` selects then reverts unconditionally, so a result committed in
  between is wiped. Each needs the token on the Confirm wire or a revert change (R9 keeps revert as
  it is). Routes: relay shaping, `issues/evicted-client-fencing-and-cancel-reach.md`.
- **Side finding:** `from_json_type` turns date-like strings (`args` of `20261102`) into
  datetimes; the client builds `command` from the converted value. Worth its own seed; out of scope.
- **Z2 rebase surface** is small: `Task.result_columns`, `ResultOutcome`, `Task.apply_results`, two
  new imports in `data/model.py`, and the body of `Receiver.update_tasks` plus one import in
  `server.py`. TECH lists them.

## 6. Verification strategy

- **Red first (P1).** `tests/test_writeback.py` drives `Receiver.update_tasks` on a per-test SQLite
  file (the `task_db` pattern), so each replay runs the real writeback path and fails by assertion
  on `d996579`: R1 every-column, R2 log and `--failures`, R4 foreign attribution, R5 duplicate in one
  bundle and redelivery, R6 the evict/retry interleaving, R7 cancelled row. Guards that pass before
  and after: R3 happy-path parity and R8 `no_confirm` apply.
- **Green (P2).** Remove the `xfail` marks; add pins for the result-column tuple, the `None` token,
  a missing row, and echo equality after a JSON round-trip; add an integration test for `hs cluster
  FILE --no-confirm` against a DB. Full `uv run pytest`, then CLI drives in a throwaway site
  (isolated by `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`, `HYPERSHELL_CONFIG_FILE=''`), default
  and `--no-confirm`: every task succeeded, every row attributed, no stale or duplicate line.
  `--no-db` is already covered by `tests/test_cluster.py`.
- **Prose (P3).** Greps for the removed clause and the new sentence; no `src/` or `tests/` change.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
