# Research 01 — terminal statuses: retry selection, group gate, autoscaler demand (R5, R6)

Worktree `fix/update-client-retry` at `eb2a4487`. Line numbers are that commit's. Probes: SQLite via
the cycle venv; SQL rendering compiled for both dialects (SQLAlchemy 2.0.50). PostgreSQL not run.

## Current predicates

All retry-side reads go through one private helper; the gate's "failed" count is a separate literal.

| Site | Today | Change |
|---|---|---|
| `data/model.py:776-787` `__awaiting_retry` (cached) | `exit_status IS NOT NULL, != 0, != CANCEL_STATUS` + `retried IS FALSE` | add `exit_status NOT IN NO_RETRY_STATUSES` to the failure tuple |
| `:790-799` `query_retryable` | `*failed, attempt < attempts, unretried` (+group, zone) | none (inherits) |
| `:802-805` `select_failed` | `query_retryable … ORDER BY completion_time LIMIT` | none |
| `:808-818` `count_retryable` | 0 if `attempts <= 1 and not eager`, else `query_retryable().count()` | none |
| `:939-952` `count_retryable_by_zone` | same, grouped by zone | none |
| `:874-914` `__schedule_next_failed_tasks` | `select_failed`, then conditional mark under `'retryable'` | none (R7 edits the row copy here) |
| `:1012-1015` `__row_statements('retryable')` (retry mark) | `by_id, *failed, unretried` | none (inherits: a row set to -1001 between select and mark is skipped) |
| `:684-731` `increment_group` failed count (`:711-724`) | `exit_status IS NOT NULL, != 0, != CANCEL, attempt >= attempts, retried IS FALSE` | `attempt >= attempts` → `or_(attempt >= attempts, exit_status IN NO_RETRY_STATUSES)` |
| `:735-748` `starting_group` | `increment_group` + `count_retryable` | none (inherits) |
| `server.py:308-309`, `:393` stop predicate; `:317-339` gate; `:440` start | call the above | none; message only (below) |
| `cluster/demand.py:107,128,131`; `cluster/remote.py:625`; `cluster/watch.py:151` | `starting_group`, `increment_group`, `count_retryable[_by_zone]` | none (inherit). Autoscaler stops counting a terminal row as demand; `scheduled_work` stops calling it work (no exit 5 wait on it). |

Not retry queries, unchanged: `report_held` (`server.py:469`, unscheduled rows per zone), `hs list -F/--retries`
(`task.py:541,561`: `exit_status != 0`, `attempt > 1`), `status_label` (`model.py:490`, `<= -1000` → ERROR),
relay-local scheduler (never retries). `Scheduler.start` on `--restart` stops when nothing is incomplete
without consulting `count_retryable` (existing, `server.py:252-261`); untouched.

Develop probe (SQLite, one `-1001` row on attempt 1, `attempts=3`): `count_retryable=1`,
`count_retryable_by_zone={default:1}`, `increment_group` viable `'waiting on 1 failed tasks to be retried'`,
`Task.next` creates attempt 2. That is the red baseline.

## The shared expression

- **Constants move to `data/model.py`**, beside `CANCEL_STATUS`: `TASK_TEMPLATE_ERROR = -1001`,
  `TASK_RESOURCE_ERROR = -1002`, `NO_RETRY_STATUSES: Final[Tuple[int, ...]] = (TASK_TEMPLATE_ERROR,
  TASK_RESOURCE_ERROR)`, all in `__all__`. Import direction forces it: `client.py:61` imports
  `data.model`; model cannot import client. `client.py:717-718` then imports them from model (names stay
  importable from `hypershell.client`; not in its `__all__` today, no test imports them).
- **Name:** avoid `TERMINAL_STATUSES`. In model.py "terminal" already means `completion_time` set
  (`:61`, `:1337-1341`, AGENTS.md lifecycle). `NO_RETRY_STATUSES` (or `NEVER_RAN_STATUSES`) says what the
  set does; the docstring can say "the GOAL's terminal statuses".
- **One cached helper** replacing the literal tuple, e.g. `Task.__failure_filters()` →
  `(failed, never_ran)` with `failed = (exit_status IS NOT NULL, != 0, != CANCEL_STATUS)` and
  `never_ran = table.c.exit_status.in_(NO_RETRY_STATUSES)`. Then
  `__awaiting_retry` = `(*failed, table.c.exit_status.not_in(NO_RETRY_STATUSES)), unretried`, and
  `increment_group` filters `*failed, or_(attempt >= attempts, never_ran), retried IS FALSE`.
  Rendered identically on both dialects: `task.exit_status NOT IN (-1001, -1002)` /
  `task.attempt >= 3 OR task.exit_status IN (-1001, -1002)`. Smallint holds both values.
- **NULL:** `NULL NOT IN (…)` is NULL (row dropped) on both dialects (probed on SQLite). Harmless:
  every query that gains the term already requires `exit_status IS NOT NULL`, and no query that must keep
  NULL rows (`remaining`, `select_new`, `reached_group` in #118) gains it. Do not put the term anywhere
  without the `IS NOT NULL` guard.
- **Cached conditional UPDATE:** probed the retry-mark shape with `not_in` and per-call binds on SQLite:
  `-1001` row matches 0, failure matches 1, re-run 0. The expanding IN is baked as literals, so the
  `@cache`d statements stay valid.

## R6: `increment_group` and the halt message

New failed-count statement (still one statement, so count and example cannot disagree):

```python
never_ran = cls.exit_status.in_(NO_RETRY_STATUSES)
failed, unrun, example = (
    Session.query(func.count(cls.id),
                  func.count(case((never_ran, cls.id))),
                  func.min(cast(cls.id, Text)))
    .filter(cls.group == previous_group, *failed_filters,
            or_(cls.attempt >= attempts, never_ran), cls.retried.is_(False))
    .one())
```

Ordering stays: incomplete → retryable → failed. So a group with a terminal row *and* another failure
with retries left is held until those retries finish, then halts (exactly "as a failure with no retries
left"). The terminal row itself is halted on attempt 1 whatever `-r`. With `attempts=1` nothing
changes but the message.

**Message** (`model.py:722`; logged as `Failed task group N (…)` at `server.py:320,333`):
- exhausted only: unchanged, `at least {n} tasks exceeding allowed retries, example: {id}`. Keeps
  `tests/test_zone_postgres.py:151` (the only test asserting the reason text).
- terminal only: `at least {m} tasks not retried because they did not run, example: {id}`.
- both: `at least {n} tasks exceeding allowed retries and {m} not retried because they did not run,
  example: {id}`.
A terminal row on `attempt >= attempts` counts as "did not run" (R6). Optional: a second `min` under
`case` for a terminal example. Tests asserting only the prefix `Failed task group`:
`test_retry_gate.py:127,231`, `test_group_order.py:308`, `test_stream_input.py:42`, `test_groups.py:181`.
Unaffected.

## `hs update --revert` / `--cancel` / `--delete` clear the hold

`--revert` writes `revert_columns` (`model.py:449-463`) to NULL: `schedule_time`, `completion_time`,
`exit_status` and more; `attempt`/`retried` untouched (`task.py:1136-1137`). The row is unscheduled and
incomplete, so `increment_group` holds on `remaining > 0`, `select_new` dispatches it (same row,
same attempt), and the group advances on success or halts again on another terminal result. `--cancel`
sets `-1` (excluded from `failed`); `--delete` removes the row. In `--forever` the gate re-evaluates
every pass (`server.py:318-321`), so no new code is needed. Worth one test (below).

## Client line (R5)

`client.py:827,834` end with `(cancelled)`. Proposed: `Template expansion failed for task ({id}):
{error} (task did not run)` and `Insufficient resources for task ({id}): {reason} (task did not run)`.
Adding "not retried" is accurate under R5 but is the server's decision; keep it to "did not run". No
test pins either line.

## Tests touched and red candidates

Existing, still green after the fix: `test_retry_gate.py::test_hold_matches_retry_selection`
(`:279-300`, already has a `-1001` row; parity holds both sides), `test_zone_model.py::test_count_retryable_by_zone`
(`:319-337`), `test_zone_postgres.py::test_group_checks_on_native_uuid_ids` (`:137-155`, keep exhausted
wording). `test_autoscale.py:61`, `test_zone_autoscale.py:440` monkeypatch the counts: unaffected.

Red on develop, green after:
1. `test_retry_gate.py` — `test_never_ran_status_halts_group`, parametrize status ∈ {-1001, -1002},
   attempts ∈ {2, 3}, eager, forever: `submit(0, 1)`, `fail(first.id, status)`, one `load_bundle()`:
   non-forever → `FINAL`, a `Failed task group 0` message containing `did not run`; forever → `LOAD`,
   `current_group == 0`; both: group 0 has one row, `scheduled_in_group(1) == 0`. Develop packs attempt 2.
2. Same file — forever + `Task.revert(id)` → `next_bundle` yields the same id at attempt 1; `succeed`
   → advances to group 1. (Red through its first half.)
3. `test_hold_matches_retry_selection` — add a `-1002` row and assert no retry's `previous_id` is a
   never-ran row (red on develop).
4. `test_zone_model.py` — a `-1001` gpu row and a `-1002` default row in group 0 at attempts=2:
   `count_retryable_by_zone == {}` for them; `increment_group(0, attempts=3)` not viable with `did not
   run` in the reason.
5. Guard (green both): a `-9` and a `-15` row are still retried at attempts=2.
6. PostgreSQL — in `test_zone_postgres.py`, a `-1001` row at `attempts=3`: `count_retryable == 0`,
   `increment_group` not viable, reason names it. Run (sandbox off, local socket):
   `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run --extra
   postgres pytest tests/test_zone_postgres.py` (skipped without the variable; CI runs it).
7. Integration (`test_groups.py` style, `temp_site`): `hs cluster FILE -r2` with
   `echo 2 #HYPERSHELL: n:2 group:1 cores:100000` and `echo 3 #HYPERSHELL: n:3 group:2`: exit 0,
   `hs list -c -t n:2` is `1`, stderr has `Failed task group 1` and `did not run`, n:3 never runs.
   Develop: three rows, and (R7) the retry runs undeclared and succeeds, so group 2 runs.

## Risks

- Inverting the wrong tuple: `increment_group` must not reuse `__awaiting_retry`'s failure tuple once it
  excludes never-ran rows, or the gate never sees them (group advances past a failure). Keep two names.
- `count_retryable` docstring promise ("exactly the failures `Task.next()` retries") holds only because
  both read `query_retryable`; keep it so.
- Rows from a 2.8 run (`-1001` with `retried=True` and a retry chain) are handled by `retried IS FALSE`;
  a reset `retried` via `hs update` on such a row now halts instead of retrying (correct).
- Lockstep prose: AGENTS.md status table (`:218-220`) and § Retry model (`:225-230`, `:257-259`),
  invariants §2 (table + MUST-filter rule gains "retry selection also excludes `NO_RETRY_STATUSES`") and
  §3 (the "change them together" list gains the set). The comment at `client.py:712-716` moves with the
  constants.

## #118 overlap (`fix/submit-input-checks`, committed state)

- **Textual conflict, R7:** #118 rewrites the retry-row construction in `__schedule_next_failed_tasks`
  (loop instead of dict comprehension; `parse_inline=False`, `strict_tag=False`, group-tag split).
  R7's `cores`/`memory`/`timeout` must be added to #118's form. Rebase onto whichever lands first.
- **Imports:** #118 adds `and_` to the model.py sqlalchemy import; this cycle adds `case` (and maybe
  `Tuple` is already imported). Trivial conflict.
- **New query `Task.reached_group`** (#118) reads `exit_status` (`IS NULL OR != CANCEL_STATUS`) for
  group reach, not retry; a never-ran row was scheduled and should count. No change needed.
- `server.py`: #118 touches imports and `ServerApp.prepare_source` only; no overlap with the Scheduler
  gate. `__awaiting_retry`, `query_retryable`, `increment_group`, `demand.py`, `client.py`: untouched by #118.
