# 00 — Digest: one recommendation per question

Tree: `fix/update-client-retry` after the signed merge of `origin/develop` `1f688c7c` (#117) at
`eb2a4487`. `src`, `tests` and `docs` are byte-identical to `1f688c7c`; line numbers in the briefs are
that tree's. Five briefs, one per rabbit hole; where they disagree or leave a choice, this file settles
it, and `PLAN.md` builds on this file.

| Brief | Topic | R-IDs |
|---|---|---|
| [01](01-terminal-retry-gate.md) | Retry selection, group gate, autoscaler demand | R5, R6 |
| [02](02-retry-declarations-and-118.md) | The retry row's columns; composing with #118 | R7 |
| [03](03-update-refusals.md) | `hs update` refusals | R1, R2 |
| [04](04-client-bundle-keys.md) | `hs client` bundle keys | R3, R4 |
| [05](05-retry-defaults.md) | Python API retry default | R8 |

## Decisions

1. **R1/R2 check site.** At the top of `TaskUpdateApp.run`, before `ensuredb()`: compute the
   single-task routing predicate once; refuse with `ArgumentError` (exit 2). The check reads raw
   state (`delete_mode`, `update_args`, `remove_tag`), so a malformed positional with `--delete` gets
   R1's message rather than `process_arguments`' (both exit 2). (03)
2. **Messages.** R1: `Cannot use --delete with changes (given: group=9 foo:bar --remove-tag foo)`.
   R2: `Cannot use --cancel with the single-task form ID FIELD VALUE` (every offending option named,
   fixed order `--cancel`, `--revert`, `--delete`, `--remove-tag`, joined by `, `). No hint. (03,
   wording "single-task form" from the `hs update` page's own term.)
3. **R3 detector.** `advertised_size` checks `client.bundlesize` in each form (`bundlesize`,
   `_env`, `_eval`), catching cmdkit's `KeyError` from `which`, and converts a text value with
   `read_value(Kind.INT, …)`, raising `ConfigurationError` (exit 3) naming `client.bundlesize` and
   where it came from, never the value (an `_eval` value is command output). The read stays in
   `run()`. This also fixes the `submit.bundlesize_env` `KeyError` crash found by 04, because the
   client no longer reads `[submit]`. No range checks (rubric: absurd input). (04)
4. **R4.** `ClientApp.bundlewait = config.client.bundlewait` (class body), the mechanism
   `submit.bundlewait` uses today; durations parse through `-w`'s `parse_time`. (04)
5. **R5 shared expression.** `TASK_TEMPLATE_ERROR`, `TASK_RESOURCE_ERROR` and
   `NO_RETRY_STATUSES = (-1001, -1002)` move to `data/model.py` beside `CANCEL_STATUS` (client imports
   model, never the reverse); `client.py` imports them back. Name the set `NO_RETRY_STATUSES`, not
   "terminal", which already means `completion_time` set in `model.py`. `__awaiting_retry` gains
   `exit_status NOT IN NO_RETRY_STATUSES` (every query that reads it already requires
   `exit_status IS NOT NULL`, so `NOT IN`'s NULL is harmless). That one tuple feeds
   `query_retryable`, `select_failed`, `count_retryable`, `count_retryable_by_zone`, the retry mark
   and `starting_group`, and through them the scheduler, the autoscaler demand
   (`cluster/demand.py`), `cluster/remote.py` and `cluster/watch.py`. (01)
6. **R6 gate.** `increment_group`'s own failed count becomes
   `or_(attempt >= attempts, exit_status IN NO_RETRY_STATUSES)` with a second count under `case`
   for the never-ran rows, one statement as today. It must **not** reuse `__awaiting_retry`'s tuple.
   The order incomplete → retryable → failed stays, so a group holding a never-ran task and another
   failure with retries left is held until those retries finish, then halts. (01)
7. **Halt message.** Exhausted only: unchanged (`tests/test_zone_postgres.py` pins it). Never-ran
   only: `at least M tasks not retried because they did not run, example: ID`. Both:
   `at least N tasks exceeding allowed retries and M not retried because they did not run, example:
   ID`. A never-ran row on its last attempt counts as "did not run". (01)
8. **Client line.** `(cancelled)` → `(task did not run)` in both `TaskExecutor.create_task` lines. (01)
9. **R7 design: `Task.retry_columns` + `Task.new_retry(task)`.** The retry is built from its task's
   declaration columns as stored (`args`, `tag` as a new dict, `group`, `fingerprint`, `source`,
   `zone`, `cores`, `memory`, `timeout`) plus fresh `id`, submit stamps, `attempt + 1`,
   `retried=False`, `previous_id`; no `Task.new` parse. A fence test puts every `Task.columns` name in
   exactly one of retry, result, or fresh. Consequence (sign-off item): a `cores`/`memory`/`timeout`
   *tag* written by `hs update KEY:VALUE` stays a tag and no longer sets the retry's column, as #118
   ruled for a `group` tag. (02)
10. **#118 composition.** One source conflict: the retry-construction block in
    `Task.__schedule_next_failed_tasks` (#118 rewrites it as a loop with `parse_inline=False`,
    `strict_tag=False` and a `group`-tag pop/restore). `new_retry` subsumes all three: whichever lands
    second takes `retries = {task.id: cls.new_retry(task) for task in failed_tasks}`. Import-line
    conflict on `model.py`'s `sqlalchemy` import (`and_` vs `case`). Prose conflicts in `AGENTS.md`
    § Retry model / group-gating paragraph and `invariants.md` §3, kept both. #118's three retry tests
    read explicit columns and pass against `new_retry`. (01, 02, 05)
11. **R8: `configured_retries()` in `server.py`** (`config.server.attempts - 1`, read when called);
    every signature `max_retries: Optional[int] = None`. `ServerThread` resolves first thing (before
    the in-memory warning), so `serve_*`, `LocalCluster`, `RemoteCluster`, `SSHCluster` pass `None`
    through; `AutoScalingCluster` and `AutoScalingServer` (`cluster/topology.py`, not in R8's list,
    included: it is an entry point taking `max_retries`) resolve before their `max_retries + 1`. Both
    apps' class bodies use the helper. Not a module constant (frozen at import, untestable in
    process). No `>= 1` handler (absurd input; `bounded-inputs`). (05)
12. **Invariant prose.** §11's "Constructors … never read configuration" sits in the advertise
    paragraph and is already bent by `SSHCluster` (`ssh.args`) and `ServerThread` (`queuesize`);
    narrow it to `advertise=`/`evict_after=` and record `max_retries=None` → `configured_retries()`;
    add `server.attempts` to §10's call-time reads. Declared in PLAN's deviation table. (05)
13. **R9 wording correction.** R9 says `[submit]`'s keys are read "by `hs submit` and `hs server`".
    Exactly: `submit.bundlesize` by `hs submit` only; `submit.bundlewait` by `hs submit`, `hs server`
    and `hs cluster` (which hands it to its clients as `-w`). The reference says that; it meets R9's
    intent (no `hs client`) and is accurate. (04)
14. **Probes.** Not the GOAL's `-c 100000` retry probe for R7 (after R5 that task is never retried):
    `hs submit -c 1 -m 1MB -W 2 sleep 6` run by `hs cluster --restart -r 1` with no `-W` (every
    cluster class passes its own `-W` to clients, so only the column can kill). (02)

## Residuals (not this cycle)

- A library caller gets `server.attempts` but not `server.eager` (only the apps read it through
  `configured_eager`). Candidate seed. (05)
- `server.attempts` as file text crashes at import (seed `config-text-attempts-crashes`); `0` or
  negative counts are `bounded-inputs`'. (05)
- `hs update cores:x` stores text in an integer column on SQLite; with `new_retry` the retry no
  longer reads it, so the PostgreSQL scheduler-crash path is gone; the stored tag is otherwise as
  today. (02)
