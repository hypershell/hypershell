---
slug: writeback-scoped-conditional
title: Scoped, conditional result writeback
kind: fix
appetite: big
status: blocked
branch: fix/writeback-scoped-conditional
base: develop
current_phase: done
last_updated: '2026-11-02'
phases:
- id: P1
  name: Red replays and unchanged-behaviour guards (tests only)
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R5
  - R6
  - R7
  - R3
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet d996579 -- src && uv run pytest -m unit -rxX tests/test_writeback.py
- id: P2
  name: 'Fenced writeback: result columns only, current assignment only'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: '! grep -q xfail tests/test_writeback.py && uv run pytest && sh -c ''r=$(pwd);
    for m in "-N4" "-N4 --no-confirm"; do d=$(mktemp -d "${TMPDIR:-/tmp}/wb-verify.XXXXXX")
    || exit 1; (cd "$d" && HYPERSHELL_SITE="$d" HYPERSHELL_DATABASE_FILE="$d/task.db"
    HYPERSHELL_CONFIG_FILE= UV_PROJECT="$r" && export HYPERSHELL_SITE HYPERSHELL_DATABASE_FILE
    HYPERSHELL_CONFIG_FILE UV_PROJECT && seq 20 | uv run hsx -t "echo {}" $m 2> log
    && test "$(uv run hs list -c)" -eq 20 && test "$(uv run hs list -c -S)" -eq 20
    && test "$(uv run hs list -c -w "client_id == null")" -eq 0 && ! grep -q -e "stale
    result" -e "duplicate result" log) || exit 1; done'''
- id: P3
  name: 'Lifecycle contract prose: result writeback is fenced'
  status: done
  satisfies:
  - R10
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet HEAD -- src tests && ! grep -q "late full-row writeback"
    AGENTS.md .agents/factory/invariants.md && grep -q "only its result columns" AGENTS.md
    && grep -q "only its result columns" .agents/factory/invariants.md && uv run pytest
    -m unit -q tests/test_writeback.py
review:
  last_reviewed_commit: '2569441'
  verdict: changes-requested
  blocked_reason: 'Review cycle 1: F1 duplicate probe mislabels exit -1 redelivery
    as stale (model.py, human gate); F2 stale-confirm seed misstates its defect'
  cycle: 1
---
# TECH.md — Scoped, conditional result writeback

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/writeback-scoped-conditional/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–02.

## Plan gate rulings

Passed by the foreman, as judge under the maintainer's delegation, 2026-11-02.

1. **Assignment identity `(id, schedule_time)`, with no schema or wire change: accepted**, on the
   condition that the PostgreSQL path is *run*, not only reasoned about. A test SHALL show that a
   dispatched `schedule_time` round-trips through the bundle's JSON and back into an
   `UPDATE … WHERE schedule_time = :echo` match on PostgreSQL, covering timezone-aware values and
   microsecond precision. It runs in the existing PostgreSQL CI job (pg13 and pg18) next to
   `tests/test_upgrade_postgres.py`, reusing that job's connection pattern, as its own module
   skipped when no PostgreSQL URL is configured. PostgreSQL is not available locally, so its green
   comes from CI on the PR, which the foreman opens. P2's verify gate gains: **the PostgreSQL
   round-trip test passes in CI**.
2. **Relay obligation (a): accepted.** Relay-facing contract: a relay SHALL return the upstream
   `schedule_time` exactly as it received it in the bundle (same instant, same UTC offset, same
   microseconds); any other value is refused as stale. The foreman carries this to relay's shaping;
   `issues/relay.md` is not edited in this cycle.
3. **A result for a missing row gets a refusal and a log line**, where `develop` raises
   `StaleDataError` and stops the receiver thread: accepted as within scope, as R2's "missing row is
   STALE".
4. **STALE and DUPLICATE both log at WARNING**, with distinct wording: accepted.
5. **Seed the pre-existing problems the planner found**, each `unshaped` with a `ROADMAP.md` entry,
   in P3's commit or a sibling commit: a stale confirm can hide a row from `revert_orphaned`; a late
   confirm can overwrite a relay's leaf attribution (relay-facing); `revert_orphaned` reads and then
   reverts as two steps, so a result landing in between is lost; `from_json_type` turns date-like
   `args` strings into datetimes. Generic names, framed as correctness issues.

## Final result-column set (R1)

`Task.result_columns`, exactly these 13, in this order:

```
client_id, client_host, command, start_time, completion_time, waited, duration,
exit_status, outpath, errpath, csvpath, cores_max, memory_max
```

Never written from a returned copy: `id`, `group`, `args`, `submit_id`, `submit_time`,
`submit_host`, `cores`, `memory`, `timeout`, `server_id`, `server_host`, `schedule_time`,
`attempt`, `retried`, `previous_id`, `next_id`, `source`, `fingerprint`, `part`, `tag`.
`schedule_time` is read from the copy only as the assignment token.

## Assignment identity

An assignment is `(id, schedule_time)`: `Task.next` stamps a fresh `schedule_time` on each dispatch,
revert clears it, cancel restamps it, and every client echoes it untouched. A result applies only
via `UPDATE … WHERE id = :id AND schedule_time = :echo AND completion_time IS NULL`. **No schema
change and no wire change**, so the #69 upgrade step is not involved. See PLAN §2.1.

## Touched functions (Z2 rebase surface)

| File | Symbol | Change |
|------|--------|--------|
| `src/hypershell/data/model.py` | imports | add `update`, `select` from `sqlalchemy`; `Enum` from `enum` |
| `src/hypershell/data/model.py` | `ResultOutcome` (new, module level, after `TaskGroupInfo`) | `Enum`: `APPLIED`, `DUPLICATE`, `STALE`; add to `__all__` |
| `src/hypershell/data/model.py` | `Task.result_columns` (new class attribute, after `Task.columns`) | the 13-name tuple above |
| `src/hypershell/data/model.py` | `Task.apply_results` (new classmethod, after `Task.cancel`) | the fence; PLAN §2.3 |
| `src/hypershell/server.py` | import line for `hypershell.data.model` | add `ResultOutcome` |
| `src/hypershell/server.py` | `Receiver.update_tasks` | body only; PLAN §2.4 |
| `AGENTS.md` | § Task lifecycle contract | R10 prose (P3) |
| `.agents/factory/invariants.md` | §3 Retry model | R10 prose (P3) |
| `tests/test_writeback.py` | new | P1, P2 |
| `tests/test_writeback_postgres.py` | new | P2 (plan gate ruling 1) |
| `.github/workflows/tests.yml` | `postgres` job's test step | P2: also runs `tests/test_writeback_postgres.py` |

**Unchanged by design:** `Entity.update_all`, `Entity.update`, `Task.next`, `revert_all`,
`revert_orphaned`, `revert_interrupted`, `cancel_all`, `Confirm.*`, `HeartMonitor.*`,
`serialize_tasks`/`deserialize_tasks`, `client.py`, `task.py`, `data/upgrade.py`.

## Conventions (apply to every phase)

- Commit conventions, style, and invariants come from [`AGENTS.md`](../../AGENTS.md); curated
  footguns in [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1–§4.
- One atomic commit per phase containing **both** the change and this file's state change.
  Subject: `[fix] Build writeback-scoped-conditional P<n>: …`. **No `Co-Authored-By` trailer.**
  Stage by explicit path.
- No CLI change, so **no** `docs/_include/*.rst` or `share/` updates (§12 does not trigger).
- Comments are declarative statements; **no R-IDs or phase ids in source or tests** (AGENTS.md).
- `verify:` commands run from the repo root. CLI drives isolate the site with `HYPERSHELL_SITE`,
  `HYPERSHELL_DATABASE_FILE` and an empty `HYPERSHELL_CONFIG_FILE`, and leave their scratch site
  under `$TMPDIR`. The CLI drives and integration tests bind a loopback port: a sandbox that forbids
  it fails `hsx` in `find_available_ports`, an environment fault, not a red verify.

---

## Phase P1 — Red replays and unchanged-behaviour guards (tests only)
**Satisfies:** R1, R2, R4, R5, R6, R7 (red half); R3, R8 (guards) · **Depends on:** —
**Goal:** commit replays that drive the real writeback path (`Receiver.update_tasks`) and fail **by
assertion** on `d996579`, plus guards that pin today's behaviour where it must not change.
**No `src/` edit in this phase.** Every test references only symbols that exist on `d996579`.

- [x] Create `tests/test_writeback.py`; every test `@mark.unit` (the CLI test in P2 is
      `@mark.integration`). Module docstring: deterministic replays of result writeback, driven
      in-process with no threads, queue or sleeps.
- [x] Copy the `task_db` fixture from `tests/test_retry_gate.py:35-47` verbatim (do not move it to
      `conftest.py`).
- [x] Helpers:
      `dispatch(limit=1, attempts=1)` → `deserialize_tasks(serialize_tasks(Task.next(limit=limit,
      attempts=attempts)))` (the client's copies, via the real wire codec);
      `confirm(client_id, host, ids)` → `Confirm(queue=None)` with `.client_info =
      ClientInfo(client_id=…, client_host=…, task_ids=ids)` then `.update_info()`;
      `finish(copy, exit_status=0, client=(id, host))` → stamp the 13 result columns as a client
      does (`command`, `start_time`, `completion_time`, `waited`, `duration`, `exit_status`,
      `outpath`, `errpath`, `csvpath`, `cores_max`, `memory_max`, `client_id`, `client_host`);
      `receive(copies, failures=None)` → `r = Receiver(queue=None, redirect_failures=failures);
      r.tasks = copies; r.update_tasks()`;
      `row(id)` → `Task.from_id(id, caching=False).to_dict()`.
      Eviction is `Task.revert_orphaned(client_id)` (exactly what `HeartMonitor.check_clients` calls).
- [x] Logs: `caplog.set_level(logging.DEBUG, logger='hypershell')`; match on `record.getMessage()`.
      Red-phase assertions use substrings that will exist after the fix: a WARNING whose message
      contains the task id, the returning client id and the word `stale` (R2), or `duplicate` (R5).
- [x] Preconditions use `pytest.fail(...)`, **never `assert`**, so a broken replay cannot pass as
      the expected failure.
- [x] Replays, each `@mark.xfail(strict=True, raises=AssertionError, reason=<declarative>)`:
  - `test_late_result_after_retry_keeps_chain` (R6): `Task.add(Task.new('false'))`; dispatch with
    `attempts=3` → copy A; confirm to A; `revert_orphaned(A)`; dispatch → copy B; confirm to B;
    `receive([finish(B, 1, client B)])`; dispatch with `attempts=3` → precondition: one retry row
    with `previous_id == X`, and X has `retried` and `next_id`; snapshot X; then
    `receive([finish(A, 1, client A)])`. Assert X's `retried`, `next_id`, `previous_id`, `attempt`
    and B's result columns are unchanged; a stale WARNING names X and A; the next
    `Task.next(limit=10, attempts=3)` returns no row whose `previous_id == X`, and exactly one row
    in the table has `previous_id == X`.
  - `test_stale_result_is_refused` (R2), `@mark.parametrize('case', ['reverted', 'same-client',
    'other-client', 'completed-elsewhere'])`: dispatch, confirm to A, copy A; then revert only /
    revert + dispatch + confirm to A / revert + dispatch + confirm to B / revert + dispatch + B
    returns success. Return A's failed copy with `failures=io.StringIO()`. Assert the row equals its
    snapshot, `failures.getvalue() == ''`, no `Completed task (X)` record, no `Non-zero exit
    status` record for X, exactly one stale WARNING naming X and A.
  - `test_cancelled_row_keeps_cancel_status` (R7): dispatch, confirm, `Task.cancel(X)`, return
    `exit_status=0`. Assert `exit_status == CANCEL_STATUS` and `completion_time` unchanged.
  - `test_duplicate_result_is_ignored` (R5), `@mark.parametrize('how', ['same-bundle',
    'redelivered'])`: `receive([c, c])` / `receive([c])` twice. Assert one `Completed task (X)`
    record, a WARNING containing `duplicate` and X, no record containing `stale`, and the row
    after the second copy equals the row after the first.
  - `test_non_result_columns_are_never_written` (R1): dispatch, confirm, take the copy; set every
    non-result column to a different valid value (`group`, `args`, `submit_id`, `submit_time`,
    `submit_host`, `cores`, `memory`, `timeout`, `server_id`, `server_host`, `attempt`, `retried`,
    `previous_id`, `next_id`, `source`, `fingerprint`, `part`, `tag`), keep `id` and the echoed
    `schedule_time`, finish it, receive. Assert those 18 columns equal the pre-return snapshot and
    the 13 result columns equal the copy's. Use a literal 13-name list in the test (not
    `Task.result_columns`, which does not exist yet).
  - `test_foreign_attribution_is_applied` (R4): dispatch, confirm to A; return the copy finished by
    client C with `server_id`/`server_host` changed. Assert `client_id`/`client_host` are C's, the
    result columns applied, `server_id`, `server_host`, `schedule_time` unchanged. (Without the
    stamp mutation this passes on `d996579`; keep it.)
- [x] Guards (no `xfail`; green now and after the fix):
  - `test_result_applies_as_today` (R3): dispatch, confirm to A, return a failed result with
    `failures=io.StringIO()`. Assert the row's 13 result columns equal the copy's, every other
    column equals the dispatched row, one `Completed task (X)` record, one `Non-zero exit status
    (1)` record, `failures.getvalue() == args + '\n'`. Parametrize the result over a success and a
    failure.
  - `test_no_confirm_result_applies` (R8): dispatch, **no confirm** (`client_id` NULL in the DB),
    return a success. Assert applied and attributed.
  - `test_in_memory_receiver_writes_nothing` (R8): `Receiver(queue=None, in_memory=True)` with a
    copy for a row that does not exist in the DB. Assert no exception, one `Completed task` record.
- [x] Red record: run `uv run pytest -m unit --runxfail tests/test_writeback.py`; it **must fail**,
      on the replay cases only. Paste its short failure summary into the commit body.
- **Verify:** `git diff --quiet d996579 -- src && uv run pytest -m unit -rxX tests/test_writeback.py`
  — exits 0 only if `src/` is `d996579`'s, every replay fails by `AssertionError` (strict: an XPASS
  or any other exception fails the run), and every guard passes.
- **Touches:** `tests/test_writeback.py`.

## Phase P2 — Fenced writeback: result columns only, current assignment only
**Satisfies:** R1–R9 · **Depends on:** P1
**Goal:** the replays go green with the full suite unchanged; CLI runs in default and
`--no-confirm` modes behave as today.

- [x] `data/model.py`: `ResultOutcome(Enum)` with `APPLIED`, `DUPLICATE`, `STALE`, docstring
      stating what each means; add to `__all__`.
- [x] `data/model.py`: `Task.result_columns: Final[Tuple[str, ...]]` = the 13 names above, with a
      declarative comment: the columns a client produces when it runs a task; a returned result
      writes these and nothing else.
- [x] `data/model.py`: `Task.apply_results(cls, results: List[Task]) -> List[ResultOutcome]`, per
      PLAN §2.3:
      a `None` `schedule_time` is `STALE` with no query (`column == None` renders `IS NULL`);
      `Session.execute(update(cls).where(cls.id == r.id, cls.schedule_time == r.schedule_time,
      cls.completion_time.is_(None)).values({n: getattr(r, n) for n in cls.result_columns})
      .execution_options(synchronize_session=False)).rowcount == 1` → `APPLIED` (NULLs included);
      else `Session.execute(select(cls.id).where(cls.id == r.id, cls.schedule_time ==
      r.schedule_time, cls.completion_time.isnot(None), cls.exit_status != CANCEL_STATUS))
      .first()` → `DUPLICATE` if a row, else `STALE`;
      one `Session.commit()` after the loop; `Session.rollback()` and re-raise on any exception.
      One `execute` per row (no executemany, no `RETURNING`). Log each outcome at `trace` only;
      the Receiver owns the user-facing lines.
- [x] `server.py` `Receiver.update_tasks`: if `in_memory`, unchanged. Otherwise zip
      `self.tasks` with `Task.apply_results(self.tasks)`: `APPLIED` → today's three effects
      (debug `Completed task (…)`, non-zero warning, `--failures` print), in today's order;
      `STALE` → `log.warning(f'Refused stale result for task ({task.id}) from client
      ({task.client_host}: {task.client_id})')`; `DUPLICATE` → `log.warning(f'Ignored duplicate
      result for task ({task.id}) from client ({task.client_host}: {task.client_id})')`. Still
      returns `ReceiverState.UNLOAD`. Declarative comment: a result applies only to the dispatch
      that produced it.
- [x] `tests/test_writeback.py`: remove every `xfail` marker. Add:
      `test_result_columns_are_fixed` (`Task.result_columns` equals the literal 13-tuple, and
      `set(Task.columns) - set(Task.result_columns)` equals the literal 20-name set);
      `test_missing_row_is_refused` (copy for an id not in the DB → no exception, stale WARNING);
      `test_unscheduled_token_is_refused` (copy with `schedule_time=None` for a reverted row → row
      unchanged, stale WARNING);
      `test_echoed_token_matches_after_round_trip` (`Task.apply_results` on a
      `deserialize_tasks(serialize_tasks(...))` copy returns `[APPLIED]`; the same copy with
      `schedule_time + timedelta(microseconds=1)` on a fresh dispatch returns `[STALE]`);
      `@mark.integration test_cluster_no_confirm_with_db(temp_site)`:
      `main(['hs', 'cluster', create_taskfile_echo(temp_site, count=4), '--no-confirm'])` → rc 0,
      four `[hypershell.server] Completed task` lines, no `stale result`/`duplicate result` line,
      and `main(['hs', 'list', '-c', '-w', 'client_id == null'])` reads `0`.
- [x] Do **not** touch `Entity.update_all`, Confirm, revert, cancel, `client.py`, `task.py`, or
      any existing test file.
- [x] (Plan gate ruling 1.) `tests/test_writeback_postgres.py`, `@mark.integration`, skipped
      without `HS_TEST_POSTGRES`, with the upgrade tests' admin-engine and throwaway-database
      pattern. Per session time zone (`UTC`, `Asia/Kolkata`): a real dispatch's aware
      `schedule_time` survives the bundle's JSON and `apply_results` returns `APPLIED`, ±1 µs
      returns `STALE`, a repeat returns `DUPLICATE`; stored stamps at offsets on both sides of UTC
      with microseconds 0, 1, 212078 and 999999 round-trip and match exactly, ±1 µs refused; a
      first dispatch's echo after revert and re-dispatch is refused through the Receiver.
- [x] (Plan gate ruling 1.) `.github/workflows/tests.yml` `postgres` job (pg13, pg18) runs it
      next to `tests/test_upgrade_postgres.py`.
- **Verify:** the frontmatter `verify:` — no `xfail` left; full `uv run pytest` (unit +
  integration, needs `uv sync`); then two CLI drives in throwaway sites, default and
  `--no-confirm`: 20 rows, 20 succeeded, none with a NULL `client_id`, and no stale or duplicate
  line in the server's log. **Plus (ruling 1): the PostgreSQL round-trip test passes in CI** on
  the PR (pg13 and pg18); it cannot run in the local gate, which skips it.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/server.py`,
  `tests/test_writeback.py`, `tests/test_writeback_postgres.py`, `.github/workflows/tests.yml`.

## Phase P3 — Lifecycle contract prose: result writeback is fenced
**Satisfies:** R10, R7 · **Depends on:** P2
**Goal:** the contract documents the stronger guarantee in one commit, with no predicate changed.

- [x] `AGENTS.md` § Task lifecycle contract, third "Unguarded" bullet: change to "a reset
      `retried` flag, from `hs task update`." Add after the bullet list, before the pre-2.9
      `UNIQUE` paragraph: "A returned result writes only its result columns
      (`Task.result_columns`), and only for its current assignment: the row's `schedule_time` as
      dispatched, while `completion_time` is NULL (`Task.apply_results`). A stale or duplicate
      result is logged and dropped, so a late result from an evicted client cannot reopen a
      retried or cancelled row."
- [x] `.agents/factory/invariants.md` §3: drop "late full-row writeback by an evicted client, " from
      the "a reset `retried` (…)" clause, and add a bullet with the same sentence, containing the
      phrase "only its result columns".
- [x] No change to the four state predicates, the `exit_status` table, or any other section.
      Both files in **one** commit; the foreman surfaces it to the maintainer at sign-off.
- **Verify:** the frontmatter `verify:` — no `src/`/`tests/` change; the old clause is gone from
  both files; the new sentence is in both; the writeback tests still pass.
- **Touches:** `AGENTS.md`, `.agents/factory/invariants.md`.

---

## Residuals (recorded, out of scope)

- A stale confirm can rewrite `client_id` on a rescheduled row, hiding it from `revert_orphaned`;
  a late confirm can overwrite a relay's leaf attribution. Needs the token on the Confirm wire.
- `revert_orphaned` selects then reverts unconditionally; a result committed in between is wiped.
- Relay must echo the upstream `schedule_time` verbatim, at the same UTC offset.
- `from_json_type` converts date-like `args` strings to datetimes (candidate seed).

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
