# 06 — What guards a doubled retry (R8)

Paths are under `src/hypershell/` in this worktree (base `c1d4470`). The post-sibling guard was
read from `fix/group-advance-retry-gate` **source** at `9bb1442` (P2 is built), not only its PLAN.
Behaviour was confirmed with a scratch SQLite probe (outside the repo) against this worktree's
`.venv`.

## Finding

Nothing in the schema is the real guard in normal operation. A failed row is retried once
because `Task.next` has exactly one caller, the server's single Scheduler thread
(`server.py:211`), and the retry selection skips rows already marked `retried`
(`data/model.py:595`; after the sibling, `Task.query_retryable`). The UNIQUE constraints only
turned the *abnormal* cases into a loud `IntegrityError` that kills the Scheduler thread. After
removal, the same cases quietly produce a second retry row with the same `previous_id`. Running
more than one server against one database was never guarded: new tasks have no row locking
either.

## Retry-writer trace

| Writer | Writes | Notes |
|---|---|---|
| `Task.__schedule_next_failed_tasks` (`data/model.py:640-658`) | inserts rows with `previous_id` (`add_all`, :655), then sets `retried=True, next_id` (`update_all`, :656) | the only creator of retry rows; **two commits** (`add_all` :179, `update_all` :198); `Task.next` then commits `schedule_time` (:616), a third |
| `Task.next` (:601-617) | via the above | sole caller: `Scheduler.load_bundle` (`server.py:211`) |
| Scheduler | one `SchedulerThread` per `ServerThread` (`server.py:829`), none when `in_memory` (:821) | every entry point (`hs server`, `hsx`, `hs cluster` local/ssh/remote) builds one `ServerThread`; `forever` mode uses the same thread |
| Receiver writeback (`server.py:459`) | `update_all(task.to_dict())`: **every column**, including `retried=False`, `next_id=None` from the client's copy | clobbers the chain mark if the row was already retried (late duplicate result) |
| Confirm (`server.py:361`) | `client_id`, `client_host` only (`client.py:137-140`) | — |
| `revert_all` / `revert_interrupted` / `revert_orphaned` (:691-766), HeartMonitor (`server.py:634`) | schedule and result columns only | leave `retried`/`previous_id`/`next_id` alone |
| `hs task update` (`task.py:1026-1043`, `1175-1195`, `1136`) | any column except `id` | `retried=false` re-arms a retried row |
| `hs submit`, `Loader` (`task.py:104`, `submit.py:520`) | `Task.new` with defaults | never sets chain columns |

No query anywhere uses `with_for_update` or `skip_locked`.

## Guard as of group-advance-retry-gate

The sibling moves `select_failed`'s filter chain verbatim into `Task.query_retryable` and adds
`Task.count_retryable` for the group and stop holds. It does not touch
`__schedule_next_failed_tasks` or `Task.next`, and adds no lock or commit change. The guard is
therefore: **one Scheduler thread per server calls `Task.next`, and `Task.query_retryable`
excludes `retried` rows; the mark is committed before that thread's next `Task.next`.** Within
one call, `select_failed` returns each row once, so a batch cannot double a retry.

## Unguarded configurations

1. **Kill between the two commits** (in `__schedule_next_failed_tasks`). The retry row exists
   unscheduled; the parent still has `retried=False`. On restart `revert_interrupted` ignores both
   rows (neither is scheduled), and the next `select_failed` picks the parent again.
   *Before:* `IntegrityError` on `previous_id` inside `Task.next`, so the Scheduler thread dies
   (`core/thread.py:32-37`, re-raised at join), and so does the server. That repeats on every
   restart, and the orphan retry row never runs until someone repairs the parent by hand.
   *After:* two retry rows at the same `attempt`, both dispatched in one bundle (probe-confirmed).
   The parent's `next_id` names the newer one; the orphan is reachable only by `previous_id`.
   If both fail, the chain forks again.
2. **Reset `retried` flag**: a late full-row writeback from an evicted-but-alive client (the
   sibling PLAN §5(b)), or `hs task update ... retried=false`. *Before:* `IntegrityError`, Scheduler
   dies. *After:* a duplicate retry; `next_id` is repointed to the newer child.
3. **Two servers, one database.** Not supported: `docs/alternatives.rst:339` says "one server
   against one SQL database", and the `.schema` option (`docs/_include/config_param_ref.rst:110`)
   separates instances by schema, not by sharing a table. Both schedulers can select the same
   failed row before either marks it. *Before:* the loser's `add_all` raises, unhandled, and that
   server's Scheduler dies. *After:* duplicate retries, last `update_all` wins `next_id`. **New
   tasks were never guarded:** `select_new` (:579-584) takes no lock, so two servers already
   double-schedule new rows. The UNIQUE chain never made multi-server safe.

**Pre-2.9 SQLite (R7).** In normal single-server operation the constraints are never violated:
each parent is selected once and each child gets a fresh UUID. Retries therefore behave exactly
as on a fresh database. They differ only in cases 1–3, where an old database fails loud and a
new one double-runs. GOAL R7's "no effect on behaviour" holds for normal operation only. The doc
text should say "in normal operation".

**Side finding (pre-existing, not a doubled retry row).** In eager mode `__next_eager` commits the
retry rows unscheduled and then calls `select_new`, which returns them again when the bundle has
room (`bundlesize` > number of retries and too few new tasks). One `Task.next` then returns the
same retry row twice (probe-confirmed). The row is dispatched twice. With the default
`bundlesize: 1` (`core/config.py:124`) this cannot happen. Duplicate writebacks feed case 2. The
client side was not traced.

## Draft invariant text

`AGENTS.md` → Task lifecycle contract → **Retry model** (replaces the first sentence; the rest
of the post-sibling paragraph stays):

```markdown
**Retry model:** retries are **new rows** (`attempt+1`; the new row's `previous_id` names the
old row, which gets `retried=True` and `next_id`) — never mutate a row in place to retry. A failed
row is retried once only because the server's single Scheduler thread is the sole caller of
`Task.next` and `Task.query_retryable` skips `retried` rows; no constraint enforces it. Unguarded:
two servers on one database (unsupported; no row locks, so new tasks double-schedule too), a kill
between the retry insert and the `retried` mark (separate commits), and a reset `retried` flag
(late full-row writeback from an evicted client, `hs task update`) each add a second retry with
the same `previous_id`. Pre-2.9 SQLite databases keep `UNIQUE` on both columns (no table
rebuild): harmless in normal operation, an `IntegrityError` in those cases.
```

`AGENTS.md` → High-risk files & footguns:

```markdown
- **`data/model.py`** — highest blast radius. A dropped `CANCEL_STATUS` filter or a missing
  `completion_time` silently double-runs or resurrects tasks; one retry per failed row rests on
  the single Scheduler plus the `retried` filter, not on the schema (`previous_id`/`next_id` are
  not unique, except in pre-2.9 SQLite databases).
```

`.agents/factory/invariants.md` §3, first bullet:

```markdown
- Retries are **new rows** (`attempt+1`, new row's `previous_id` → old row, old row
  `retried=True` and `next_id` → new row) — never mutate a row in place to retry.
- One retry per failed row is held by the single Scheduler thread (sole `Task.next` caller) and
  `Task.query_retryable`'s `retried IS FALSE`, **not** by the schema. Unguarded, each yielding a
  second retry with the same `previous_id`: two servers on one database (unsupported, no row
  locks); a kill between the retry insert and the `retried` mark (two commits); a reset `retried`
  (late full-row writeback, `hs task update`). Pre-2.9 SQLite DBs keep the `UNIQUE` constraints
  (forward-only): never tripped in normal operation, an `IntegrityError` in those cases.
```

Model comment (`data/model.py`, above `previous_id`/`next_id`):

```python
    # Retry chain links; not unique: one retry per row is kept by the scheduler's retried filter.
```

## Follow-up candidate

Closing case 1 means writing the parent mark and the child insert in one commit. Set
`old.retried`/`old.next_id` on the already-loaded ORM objects and `Session.add_all(new_tasks)`
without committing, then let `Task.next`'s own commit (:616) cover the insert, the mark and
`schedule_time` together. A kill then leaves either nothing, or scheduled rows that
`revert_interrupted` reverts correctly. The change sits entirely in `__schedule_next_failed_tasks`,
which the sibling does not touch: about 5–8 source lines plus one unit replay. The rollback-on-
error now in `add_all` would move to `Task.next`. It does not fix the eager in-batch duplicate,
since autoflush would still surface the children to `select_new`. That needs `select_new` to skip
the ids already chosen, a few more lines. Cases 2 and 3 need different fixes (a writeback that
omits chain columns; row locking or a documented single-server rule). Reported only, not
recommended for this cycle.
