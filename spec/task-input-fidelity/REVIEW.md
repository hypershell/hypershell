# REVIEW — Every task the input gives runs as given: date-like lines run as text, and a streamed run waits for its input to end

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 0b84a745b0fa63ac1450ed7d67f28eb10c1b175d  ·  **Base:** `develop` (`origin/develop` 48cc5056, also the merge-base)  ·  **Date:** 2026-11-15
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

**Mode.** One fresh blind correctness reviewer (not `debate`; no completeness sub-pass). It was given
`GOAL.md`, the spec-excluded diff (`git diff origin/develop...HEAD -- . ':(exclude)spec/'`), the
**base** copies of `invariants.md` and `AGENTS.md` (the branch edits both; code was graded against
the base text, the edits as prose), `review-rubric.md`, the drive `drive/fidelity_drive.sh` and
`tests/fixtures/make-task-wire-2.9.0a8.sh`, and the gate rulings below. It was told not to read
`PLAN.md`, `TECH.md`, `research/` or `META.md`.

**Contract.** `GOAL.md` has two commits on the branch: the shaping (`afde825f`) and the GOAL-gate
ruling and lock (`ae83e66e`, which appends the ruled clarifications and changes no requirement
text). No drift after the lock. Gate rulings that bind the grading: two seeds at appetite small; a
stream run waits for the end of its input and every task terminal (a producer that never closes
keeps the run open; closing it, Ctrl-C or a failure stop ends it); the "30 of 60" case is included;
client `-T` idle-outs during a pause are out of scope; stop latency is bounded by `server.poll`;
the mixed-version leftovers are accepted; R2 covers result columns only. Plan-gate: a prompt stop
at the end of input through a public wake not exposed over RPC is accepted; `scheduled_work`
counts committed work only; the `-T` and `-b N>1` seeds are accepted deferrals.

## Verification run

Orchestrator (`hs` and `hsx` on `PATH` from the interpreter's own environment):

- `.venv/bin/python -m pytest -q tests` (Python 3.14.6) → 3007 passed, 111 skipped.
- `/tmp/.../venv311/bin/python -m pytest -q tests` (Python 3.11.18) → 3004 passed, 114 skipped.
- PostgreSQL 18 + TimescaleDB 2.32, `HS_TEST_POSTGRES` set, files `test_contended_write`,
  `test_server_initdb`, `test_timescale`, `test_zone_postgres`, `test_upgrade_postgres`,
  `test_writeback_postgres`, `test_stream_input`, `test_task_wire`:
  - `HS_TEST_TIMESCALE=community`, 3.14 → 375 passed, 29 failed, 1 skipped; 3.11 → the same.
  - `HS_TEST_TIMESCALE=` (plain `postgres`), 3.14 → 355 passed, 30 failed, 20 skipped.
  - Every failure is environmental and outside the diff: 28 `test_upgrade_postgres` cases and
    `test_timescale::test_legacy_database_upgraded_not_converted` need the `hs283`/`hs290a1`
    template databases, which are not built on this host ("Template … not listed in
    HS_TEST_POSTGRES_TEMPLATES"); in plain mode two `test_timescale` cases fail because the server
    offers TimescaleDB 2.32 while `HS_TEST_TIMESCALE` is empty.
- `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) → exit 0, no warnings.
- Merge status: `origin/develop` has not moved past the merge-base, so the branch merges cleanly.
  Trial merge with `origin/fix/contended-writes` (PR #109, `cc7d61f8`; touches `data/model.py`
  `Task`/`Source`, `AGENTS.md`, `invariants.md`, `ROADMAP.md`): `git merge-tree --write-tree` is
  conflict-free (tree `35ce0432`). On that tree, exported to a scratch directory and run with
  `PYTHONPATH` pointing at it (import path confirmed): 22 affected test files (wire, stream input,
  writeback, contended write/recovery, source, server, server FSM, restart, relay return/db,
  dispatch, cluster exit/launch, group order, groups, retry gate, submit, client, signal wake) →
  692 passed, 27 skipped; the PostgreSQL files plus wire and stream input under
  `HS_TEST_TIMESCALE=community` → 285 passed, 1 skipped.
- F1 reproduction (below): `.agents/factory/bin/temp_site.sh uv run sh <scratch>/probes/edge.sh fail-higher 30`
  → `CRITICAL … Failed task group 0`, rc=0 at 9 s, `echo b` (group 1) left remaining.

Reviewer (all pass unless noted):

- New test files `tests/test_task_wire.py` and `tests/test_stream_input.py` → 175 passed on 3.14
  and on 3.11. The same files against `develop`'s source → 120 failed, 55 passed (the stream test
  fails on its behaviour assertion, not by hanging). Stream tests stable over 9 repeats.
- `test_writeback_postgres.py -k date_like` → passes with `HS_TEST_TIMESCALE` empty and
  `community`; on `develop`'s source it stores `'2026-11-02 00:00:00'`.
- Related suites (client_request, cluster_exit, group_order, relay_return, retry_gate, server_fsm,
  writeback, zone_dispatch, restart, relay_retire, topology_server, submit_json, contended_write)
  → 263 passed, 18 skipped.
- `fidelity_drive.sh` on SQLite, 3.14, `FD_POLL=2`: dates, dates-default, dates-stdin, dates-nodb,
  pause, fail-pause, burst (60 of 60), server, fifo, nodb-stream, file, launch-false (exit 5 after
  5 s with the input open), cross-old-client, cross-old-server. At poll 30 with a 20 s pause: pause,
  server and fifo end 1–2 s after the input ends (SQLite), pause likewise on `postgres`. On 3.11:
  dates, dates-default, dates-stdin, dates-nodb, pause, fail-pause. On `postgres` and `timescale`:
  dates, dates-default, pause, fail-pause, burst, server, fifo, cross-old-client, cross-old-server.
- The same drives on `develop`'s source: dates, dates-default, dates-nodb, pause, fail-pause, burst,
  server and fifo FAIL as expected; launch-false passes, as a guard should.
- Fixture: `tests/fixtures/make-task-wire-2.9.0a8.sh` regenerated against `git archive 2.9.0a8`
  (`2ffb5ccf`) → byte-identical; a mutation of the encoder (`isoformat(sep='T')`) makes
  `test_encoding_matches_2_9_0a8` fail.
- Probes: date-like cross-version runs (old client records -1001; old server stores
  `2026-11-02 00:00:00` — the accepted residue); decode→encode round-trip bytes, branch vs
  2.9.0a8; stream edge cases retry, retry-eager, retry-pending-at-end, fail-higher, late-lower,
  empty, empty-file, prior-then-empty, prior-then-stream, procsub, blank-pause, client-timeout,
  prior-failed-stream, prior-failed-file, bundlewait, forever (branch vs `develop` where relevant);
  random-timing stress, 25 runs on SQLite and 12 on PostgreSQL — every line ran, no run hung.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `data/model.py:194-201` `Entity.from_json` decodes by declared column type (9e7973d3); client path `client.py:249` | Wire tests: 6 forms × 8 text fields × 2 decoders, template expansion under a user and the default template, 3.14 and 3.11; fail on `develop`. Drives dates, dates-default, dates-stdin, dates-nodb (SQLite, 3.14 and 3.11); dates, dates-default on `postgres`/`timescale` | ✅ |
| R2 | `server.py:644` → `deserialize_tasks` → `Task.apply_results` (9e7973d3) | `test_apply_results_stores_date_like_command_verbatim` (SQLite); `test_writeback_postgres::test_date_like_result_stored_verbatim` (both PostgreSQL providers; fails on `develop`); drive dates-default checks the stored command on all three backends | ✅ |
| R3 | The four time columns stay declared `datetime`; `revert_handed_back` keeps its direct `from_json_type` (`model.py:1128`) | `test_timestamps_decode_as_times` (4 fields × aware/naive × with/without microseconds), STALE on a shifted `schedule_time`, DUPLICATE on a repeat; `test_datetime_columns_are_declared`; hand-back suites (`test_topology_server`, `test_contended_write`); cross-version runs show `waited`, `duration`, schedule and completion times intact | ✅ |
| R4 | Encoder untouched | Fixture regenerated byte-identical from the 2.9.0a8 tag; encoder mutation caught; round-trip bytes identical to 2.9.0a8 for non-date args; cross-old-client and cross-old-server on SQLite, `postgres` and `timescale` with no stale or duplicate result | ✅ |
| R5 | `server.py:262-300` stop rule and `back_off` (f2f20ca7) | Drives pause, fail-pause, burst (60/60), server, fifo on SQLite/`postgres`/`timescale`, all failing on `develop`; probes procsub, retry, retry-eager, retry-pending-at-end, late-lower, prior-then-stream; stress runs | ✅ |
| R6 | `ServerThread.run_with_exceptions` wake `server.py:1213-1214`, `Scheduler.wake` `:323-325`, `Dispatcher.wake` `core/dispatch.py:254-261` (07f92f5e) | At poll 30 runs end 1–2 s after the input ends; `test_wake_*`, `test_local_cluster_stops_promptly_at_end_of_input`; empty input hangs on both `develop` and the branch (unchanged non-goal); prior-then-empty stops in 2 s on both. `wake` is not in `DISPATCH_EXPOSED` (`core/queue.py:1172`); it sits between `wait_submitter` and `wait_scheduler`, so the §6 shutdown order and submit flush are unchanged | ✅ |
| R7 | Forever and autoscaling paths untouched (`cluster/remote.py:984` passes `forever_mode=True`); failed-group branch kept | Drives file, nodb-stream, launch-false (exit 5, input open); forever probe still running 8 s after the input ended, as on `develop`; fail-higher probe and `test_failed_group_still_halts_late_higher_group` | ✅ |
| R8 | `tests/test_task_wire.py`, `tests/test_stream_input.py`, `tests/test_writeback_postgres.py`, `tests/fixtures/make-task-wire-2.9.0a8.sh` + README | 175 new tests pass on 3.14 and 3.11, 120 of them fail on `develop`'s source; stream tests deterministic over 9 repeats; every new test carries a marker | ✅ |
| R9 | `docs/cli/cluster.rst:56-59`, `docs/cli/server.rst:34-37` (`versionchanged:: 2.9.0`), `docs/_include/task_groups_alt.rst:27-29` | Prose matches the drives; Sphinx `-W` build clean | ✅ |

Unmapped changes (possible scope creep): none. The `cluster/watch.py` change is a docstring. The
two new seeds and their `ROADMAP.md` entries are accepted deferrals, and each one's Problem was
reproduced as stated: with `-T 3` the run was still going at 26 s with `echo b` staged; with
`-b 2 -w 1` the line `a` printed only at 6.1 s, together with `b`. The `invariants.md` and
`AGENTS.md` additions are the cycle's invariant record (F1 aside).

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

No finding against the code: `data/model.py`, `server.py`, `core/dispatch.py` and `cluster/watch.py`
were found correct against the base invariants.

### [LOW/CONFIRMED] F1 — The new §3 invariant text says the scheduler stops only on end of input with every task terminal; it also stops on a failed group and on a signal
- **Where:** `.agents/factory/invariants.md:92-96` (§3) and `AGENTS.md:260-265` (Group-gating
  paragraph), both added by this branch.
- **Failure scenario:** both say "**A non-`forever` scheduler stops only once its submitter has
  ended and every task is terminal**". The code (correctly, per R7) also stops while the input is
  open when a group fails permanently (`Scheduler.load_bundle`, the `increment_group` not-viable
  branch, `log.critical` + FINAL) and on SIGUSR1/SIGUSR2 (`server.py:246`). The same §3 bullet
  just above already says a permanently failing task halts the server, so the record contradicts
  itself, and later plan gates and reviews read this file as binding.
- **Evidence:** `.agents/factory/bin/temp_site.sh uv run sh <scratch>/probes/edge.sh fail-higher 30`,
  which pipes `{ echo false; sleep 4; echo 'echo b  # HYPERSHELL: group:1'; sleep 4; } | hsx -N1`:
  `CRITICAL [hypershell.server] Failed task group 0 (at least 1 tasks exceeding allowed retries …)`
  logged while the input was still open; rc=0 at 9 s; `echo b` remaining (reproduced by the
  reviewer and again by the orchestrator). The behaviour is right (R7,
  `test_failed_group_still_halts_late_higher_group`); the sentence is wrong. Refutation tried: the
  second clause ("with every task done and the input still open it backs off …") narrows the
  failed-task case, but the lead sentence stays absolute.
- **Touches invariant / requirement:** the §3 record of the scheduler's stop conditions and group
  gating (prose only). Repair: scope the sentence to the terminal-count stop ("the stop on every
  task terminal fires only once the submitter has ended …") and name the failed-group halt and the
  signals as the other stops, in both files.

### Notes (not findings)
- `fidelity_drive.sh` cross-old-client / cross-old-server do not check which code each side
  imported, and both trees report version `2.9.0a8`; a wrong `FD_OLD_SRC` or import order would
  let them pass running new code against new code. The reviewer checked independently that
  `PYTHONPATH` wins (printed `hypershell.__file__` on each side) and that date-like cross probes
  show 2.9.0a8 behaviour on the old side.
- The fixture's `RESULT` is the 2.9.0a8 encoder's output for a hand-built result object, not a
  recorded client reply for that dispatch; it still pins encoder and decoder against 2.9.0a8
  bytes, which is what R8 asks.
- Not run against `develop`'s source: dates-stdin, nodb-stream, file (the last two are guards).

## Human-gate triggers

- **Triggered — F1.** It is CONFIRMED and its subject is the recorded text of a lifecycle invariant
  (§3: the scheduler's stop conditions and group gating); its repair rewrites that invariant record,
  which binds later cycles. No CONFIRMED finding touches high-blast-radius code
  (`data/model.py`, `server.py`, `core/dispatch.py` were found correct), and none touches a
  security or DB-lifecycle filter. Human sign-off is required before any further step.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run.

## Review cycle 2 — changes-requested (2026-11-15)

- **Reviewed commit:** 1a28fb9e4b360de91acb2f88275ee5898007ba88  ·  **Base:** `origin/develop` fbc656e5 (merge-base 48cc5056)  ·  **Date:** 2026-11-15
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`. Second cycle without convergence: escalate to the human.

**Mode.** A fresh blind pass over the full spec-excluded diff (the default for a later cycle), plus the
orchestrator's own grading of F1. Since cycle 1 the only non-spec change is 1a28fb9e, wording in
`AGENTS.md` and `invariants.md`; no `src/`, `tests/` or `docs/` change since 0b84a745. One fresh
`general-purpose` reviewer (not `debate`, no completeness sub-pass) got `GOAL.md`, the diff command
`git diff origin/develop...HEAD -- . ':(exclude)spec/'`, the **base** (`origin/develop`) copies of
`invariants.md` and `AGENTS.md`, `review-rubric.md`, a copy of the drive, the binding gate rulings,
and the instruction not to read `spec/`. It was not told about F1. `TECH.md` read `status: blocked`
at the start (cycle 1's verdict); the review proceeded on the invocation's instruction.

**Reviewer hand-back.** The reviewer had not handed back when the orchestrator closed the review: its
last item, the Python 3.11 full suite on the branch, was at 60% (the cycle-1 3.11 full run on the same
code passed, and the merged-tree 3.11 run below passed). Its executed results were read from its
session record and are reported below as "Reviewer"; the two findings were re-run by the
orchestrator before grading.

**Contract.** No drift: `GOAL.md` still has only the shaping (`afde825f`) and lock (`ae83e66e`)
commits. Gate rulings binding the grading are as in cycle 1, plus the review-cycle-1 ruling: F1 is
fixed as wording, and the gate was ruled not triggered by it.

### Verification run

Orchestrator:

- Merge: `git merge-tree --write-tree HEAD origin/develop` → tree d9d13b16, no conflicts. #109's
  `invariants.md` §1/§3 and `AGENTS.md` edits sit apart from this branch's; the merged §3 text is
  intact. Exported the merged tree and ran it with `PYTHONPATH` set to its `src` and root, with `hs`
  on `PATH` (import path printed, merged `src` confirmed):
  - full suite, Python 3.14 → 3021 passed, 119 skipped;
  - PostgreSQL files (`test_contended_write`, `test_contended_recovery`, `test_source`,
    `test_server_initdb`, `test_timescale`, `test_zone_postgres`, `test_writeback_postgres`,
    `test_stream_input`, `test_task_wire`), 3.14: `HS_TEST_TIMESCALE=community` → 436 passed, 1
    failed (`test_legacy_database_upgraded_not_converted`: "Template hs283 not listed in
    HS_TEST_POSTGRES_TEMPLATES"); empty → 416 passed, 2 failed ("server offers TimescaleDB 2.32.0, but
    HS_TEST_TIMESCALE is empty"). Both environmental, as in cycle 1;
  - 15 affected files on Python 3.11 (wire, stream input, contended write/recovery, source,
    writeback, writeback statement, relay return, topology server, server FSM, group order, restart,
    cluster exit, signal wake, retry gate) → 441 passed, 23 skipped.
- F1 probes (`temp_site.sh sh <scratch>/probes/f1.sh MODE`, `HYPERSHELL_LOGGING_LEVEL=TRACE`,
  default poll 30): `sig1` and `failhigh` (F3 below).
- F2 re-run: the reviewer's interleaving probe (pytest, `-s`) → both cases fail as described below;
  `fidelity_drive.sh fail-pause` with `FD_PROVIDER=timescale`, ten runs → 9 ok, 1 FAIL (critical).

Reviewer (Python 3.14 unless noted):

- Full suite on the branch → 3007 passed, 111 skipped. New test files pass on 3.14 and 3.11.
  Mutations: the old decoder → 112 of `test_task_wire` fail; the old stop rule →
  `test_stream_input` fails.
- PostgreSQL files, both Pythons, both providers: `community` → 375 passed, 29 failed; empty → 355
  passed, 30 failed; every failure template-missing (`test_upgrade_postgres`, 28) or the
  provider/variable mismatch in `test_timescale`.
- Drives, SQLite, 3.14 and 3.11: dates, dates-default, dates-stdin, dates-nodb, pause, fail-pause,
  burst, server, fifo, nodb-stream, file, launch-false, cross-old-client, cross-old-server, all ok
  (old side confirmed importing the 2.9.0a8 tree). `postgres` (3.14; 3.11 subset): all ok.
  `timescale`: all ok except **fail-pause, FAIL** (CRITICAL `Failed task group 0`) — F2.
- Fixture regenerated from the 2.9.0a8 tree on both Pythons → `DISPATCH`, `BUNDLE`, `RESULT` equal
  the test literals. Date-like lines on 3.11 keep `waited` and `duration`. Process substitution as
  FILE runs both lines. Sphinx `-W` → exit 0; "Changed in version 2.9.0" renders on the cluster and
  server pages.
- Seeds: `-T 3` with a pause → still running at 25 s, `echo b` staged; `-b 2` → first output at
  9 s. Both Problems hold as written; every `ROADMAP.md` seed link resolves.
- Probes: SIGUSR1 during a pause (F3); the count/`increment_group` interleaving and natural
  repeats of the fail-pause shape (F2): TimescaleDB `{ echo false; sleep 4; echo "echo b"; } |
  hsx -N2`, 16 trials → 1 early stop; SQLite 20 trials and plain PostgreSQL 12 → none.

### Requirement → evidence matrix

| R-ID | Status | Change since cycle 1 |
|------|--------|----------------------|
| R1–R4 | ✅ | None in code; drives, wire tests and cross-version runs pass again on both Pythons and all three backends |
| R5 | ⚠️ F2 | Holds for the common timing; a failure landing inside the scheduler's count window still stops a stream run with the input open |
| R6 | ✅ | Pause, server and fifo end 1–2 s after the input ends |
| R7 | ✅ | file, nodb-stream, launch-false; the fail-higher probe still halts with the input open |
| R8 | ✅ (gap) | The deterministic stream tests do not cover F2's interleaving |
| R9 | ✅ | Prose and markers render; `-W` clean |

Unmapped changes: none.

### F1 status

**Fixed as worded, with one residual inaccuracy (F3).** Both files now scope the absolute sentence
to the out-of-work stop and name the failed-group halt and SIGUSR1/SIGUSR2 as stops that apply with
the input open or not. The failed-group claim holds: `failhigh` (`false`, then at 4 s a group-1 task,
input open to 30 s) logs `CRITICAL Failed task group 0` at 7.7 s and the scheduler is done while the
input stays open. The pointers "(the halt above)" and "(above)" resolve to the group-gating text in
each file. Two claims are not accurate: "at once" (F3) and "a failed task with no retry left never
ends a stream run early" (`invariants.md:96`, `AGENTS.md:266`), which F2 contradicts until it is
fixed.

### Findings

#### [MEDIUM/CONFIRMED] F2 — A stream run still stops early when the last in-flight task fails during the scheduler's count
- **Where:** `src/hypershell/server.py:264-282` (`Scheduler.load_bundle`: `Task.count_remaining()` at
  :265, `Task.increment_group` at :274, `log.critical` and FINAL at :281-282), with
  `Task.increment_group` (`src/hypershell/data/model.py:676-713`).
- **Failure scenario:** input open; the only task in flight fails with no retry left. `load_bundle`
  reads `count_remaining() == 1`, so it skips the out-of-work branch and its input-open back-off. The
  Receiver writes the failure. `increment_group(0)` then finds no incomplete task, nothing retryable
  and one exhausted failure, and returns not viable. The scheduler logs `CRITICAL Failed task group 0`
  and goes FINAL with the input still open; the next line is committed and never scheduled; exit 0.
  The same input otherwise runs every line, so the outcome depends on timing. This violates R5
  ("whatever the timing of the input") and the branch's own §3 text. It is not the R7 failed-group
  halt the GOAL keeps unchanged: no task waits in another group; the halt is reached only because
  the result landed between the two reads. When the input has already ended, the same interleaving
  only logs a spurious critical at the end of a complete run.
- **Evidence:** `pytest -q -s <reviewer probe>` (run by the reviewer and again by the orchestrator):
  (1) the result written right after `count_remaining` returns → `STATE=FINAL input_open=True`,
  critical logged, late line `schedule_time` None; (2) a `LocalCluster` fed
  `false`, a 4 s pause and `echo b` (`max_retries=0`, `poll=2`), with the scheduler thread's
  `count_remaining` 0.5 s slower → done in 4.1 s, `echo b` never scheduled, nothing printed. Natural,
  with no injection: TimescaleDB `{ echo false; sleep 4; echo "echo b"; } | hsx -N2`, 16 trials → 1
  with `echo b` remaining and the critical logged (reviewer); `fidelity_drive.sh fail-pause` on
  `timescale` failed once in the reviewer's run and once in ten of the orchestrator's (critical
  logged; in that run `b` ran, the end-of-input variant). SQLite 20 and plain PostgreSQL 12 trials →
  no early stop.
- **Touches:** R5, R8 (no test of the interleaving); `server.py` (high-blast-radius core). The
  deferral exception does not apply: R5 requires the change, not preserving the old behaviour.
- **Repair direction:** before halting on a non-viable group in non-`forever` mode with the input
  open, re-check the out-of-work condition (or count incomplete tasks after the group check), so a
  failure that lands mid-pass backs off rather than halts; add a deterministic test that writes the
  failure between the two reads.

#### [LOW/CONFIRMED] F3 — The new §3 wording says the scheduler stops "at once" on SIGUSR1/SIGUSR2; it stops on its next pass, up to `server.poll` later
- **Where:** `.agents/factory/invariants.md:93-94`, `AGENTS.md:262-263`.
- **Failure scenario:** the signal handler only records the signal (`core/signal.py:96-100`).
  `load_bundle` checks it at the top of a pass (`server.py:246`), and a back-off wait ends only on
  `halt()`, a dispatcher generation change or its timeout (`server.py:302-313`). During a pause the
  back-off grows to `server.poll`, so the scheduler sees a signal up to one poll later. The
  failed-group halt is likewise taken on the next pass. A later cycle reading "at once" would assume
  a signal ends a back-off.
- **Evidence:** `sig1` (input `echo a`, a 45 s pause, `echo b`; SIGUSR1 to `hsx` at 20.1 s):
  `Received signal 30: SIGUSR1` at 05:56:29.938, `Signal interrupt (SIGUSR1)` at 05:56:41.425,
  11.5 s later at the end of a 16 s back-off; `hsx` exited when the input ended (46 s), `echo b`
  committed and never scheduled, rc 0. The reviewer saw the same independently: the signal was taken
  at the end of an 8 s back-off. `failhigh`: the group-1 task was committed at 05:57:32.949 and the
  halt logged at 05:57:36.609, at the end of a 4 s back-off.
- **Touches:** the §3 record (prose only); the code is pre-existing and signals are a GOAL non-goal.
  **Repair:** replace "at once" with "on its next pass (within one back-off, bounded by
  `server.poll`)", or drop it, in both files.

### Notes (not findings)
- `invariants.md:94` runs past the file's line width; a reflow can ride with the F3 fix.

### Human-gate triggers
- **Triggered — F2.** It is CONFIRMED and the code is in `server.py`, on the rubric's
  high-blast-radius list. Human sign-off is required before any further step.
- F3 is prose about the scheduler's stop conditions; under the review-cycle-1 ruling (those
  conditions are not on the rubric's mandatory list) it does not trigger the gate on its own.

### Optional completeness sub-pass
- Not run.

## Review cycle 3 — approved (2026-11-15)

- **Reviewed commit:** 664027aa35c718b205f1add40fe42f845afe9870  ·  **Base:** `origin/develop` fbc656e5 (merge-base 48cc5056)  ·  **Date:** 2026-11-15
- **Verdict:** approved
- **Cycle:** 3 of ≤3, the last allowed. It mirrors `review.cycle` in `TECH.md`.

**Mode.** This cycle ran a fresh blind pass over the full spec-excluded diff. The orchestrator also
graded F2 and F3 and the builder's extension of the F2 fix to the input-ended case.
- **Change since cycle 2:** commit 664027aa. In `server.py` it rewrites the not-viable branch of
  `Scheduler.load_bundle` (:280-288). In `data/model.py` it adds `Task.count_remaining_outside`
  (:882-885). It adds five tests to `tests/test_stream_input.py` and rewrites the §3 sentence in
  `AGENTS.md` and `invariants.md`.
- **The reviewer:** one fresh `general-purpose` reviewer, not `debate`, with no completeness
  sub-pass. It got copies outside `spec/` of `GOAL.md`, `review-rubric.md`, the base
  (`origin/develop`) `invariants.md` and `AGENTS.md`, the drive and the fixture generator. These were
  passed as file paths, with an instruction to read each in full. It also got the diff command
  `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, the binding gate rulings including the
  review-cycle-2 ruling, and an instruction not to read `spec/`. It was asked to judge the
  input-ended interleaving on its merits. It handed back with the tree clean. It stopped one orphaned
  `hsx` of its own by PID and left none of its processes running.

**Contract.** There is no drift: `GOAL.md` has only the shaping commit (`afde825f`) and the lock
(`ae83e66e`). The rulings that bind this grading are those of cycles 1 and 2, plus the
review-cycle-2 ruling (87867ac8), which the maintainer signed off:
- With the input open, the scheduler re-checks out-of-work before halting on a non-viable group.
- A permanently failed group with a task waiting in another group still halts (R7).
- Signals and the failed-group halt take effect on the next pass, within `server.poll`.

### Verification run

Orchestrator:

- **Merge:** `git merge-tree --write-tree HEAD origin/develop` gives tree b456f9b0 with no
  conflicts. The merged tree was exported, with `PYTHONPATH` set to its `src` and root and `hs` on
  `PATH`, and the import path was printed to confirm the merged `src` was in use.
  - **Full suite, Python 3.14:** 3026 passed, 119 skipped. The two warnings are a pre-existing
    `fork()` deprecation in `test_logging`.
  - **Full suite, Python 3.11:** 3023 passed, 122 skipped.
  - **PostgreSQL files** (`test_contended_write`, `test_contended_recovery`, `test_source`,
    `test_server_initdb`, `test_timescale`, `test_zone_postgres`, `test_writeback_postgres`,
    `test_stream_input`, `test_task_wire`), Python 3.14:
    - `HS_TEST_TIMESCALE=community`: 441 passed, 1 failed (`test_legacy_database_upgraded_not_converted`).
    - Empty: 421 passed, 2 failed (`test_server_matches_entry`, `test_absent_fails_before_schema`).
    - These are the environmental failures of cycles 1 and 2: a template is missing, and the
      provider does not match the variable.
  - **Affected files, Python 3.11:** 16 files (wire, stream input, contended write and recovery,
    source, writeback, writeback statement, relay return, topology server, server FSM, group order,
    restart, cluster exit, signal wake, retry gate, groups): 457 passed, 23 skipped.
- **Sphinx:** `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) exits 0.
- **The five new tests on 1a28fb9's source** (`PYTHONPATH` set to an export of that commit):
  - 4 fail: `…during_the_count_waits_for_late_line`, `…during_the_check_still_runs`,
    `…stops_quietly` and `test_local_cluster_failure_during_the_count_waits_for_late_line`.
  - `…still_halts_higher_group`, a guard, passes.
  - On HEAD, all 16 tests of `test_stream_input.py` pass.
- **`fidelity_drive.sh fail-pause`, `FD_PROVIDER=timescale`, 25 consecutive runs:** 25 ok. Cycle 2
  had 1 failure in 10.
- **Natural `-N2` probe on TimescaleDB** (`{ echo false; sleep 4; echo 'echo b'; } | hsx -N2`,
  TRACE logs kept), 16 runs: 16 ok. The new back-off never fired, so this timing never reached the
  window, and these runs prove the outcome only.
- **Window-widened CLI probe on TimescaleDB:** a `sitecustomize` makes the scheduler thread's
  whole-run `count_remaining` sleep after counting. The probe ran against 1a28fb9's source and
  against HEAD, three runs each:
  - **Input open** (`{ echo false; sleep 6; echo 'echo b'; } | hsx -N1`, 1 s window):
    - 1a28fb9: `CRITICAL Failed task group 0` with the input open; `echo b` committed and never
      scheduled (exit_status null); nothing printed; rc 0; 3 of 3 runs.
    - HEAD: `b` printed; no critical; the TRACE back-off "Failed task group 0 holds no task in
      another group" logged once per run; nothing remaining; rc 0; 3 of 3 runs. This is the F2 path,
      reached and handled.
  - **Input ended** (`printf 'false\nsleep 2\n' | hsx -N2`, 3 s window):
    - 1a28fb9: `CRITICAL Failed task group 0` at the end of a complete run, rc 0, 3 of 3 runs.
    - HEAD: no critical, rc 0, both tasks terminal, 3 of 3 runs. Its trace shows both completions
      followed directly by `Done (scheduler)`, with no back-off between them.
- **Reviewer's P1 and P2 probes, re-run:**
  - On HEAD: P1 → `FINAL critical []`; P2 → `[LOAD, FINAL]` with the critical.
  - Under the reviewer's mutant M6 (the re-count at :285 removed): P2 → `[FINAL] critical []`.
  - Under M6, `test_stream_input`, `test_group_order`, `test_retry_gate`, `test_server_fsm` and
    `test_restart` all pass (93 passed). See N1.

Reviewer:

- **Targeted suites,** 13 files (stream input, task wire, group order, retry gate, groups, server
  FSM, writeback, writeback statement, cluster launch, dispatch, dispatch staged, server, zone
  dispatch): 506 passed and 1 skipped on 3.14, and the same on 3.11. Also `test_dispatch_version`,
  `test_relay_fsm` and `test_autoscale_failed_launches` on 3.14: 89 passed.
- **Drives:**
  - SQLite, on 3.14 and 3.11, all 14 modes ok.
  - `postgres` and `timescale`, on 3.14 and 3.11: dates, dates-default, dates-stdin, file, pause,
    fail-pause, burst, server, fifo, launch-false, cross-old-client and cross-old-server. That is 48
    runs, all ok.
  - launch-false exits 5 in 5–6 s. Stream runs end 1–2 s after the input ends.
- **Mutants:**
  - M1 (the old decoder): 112 failures.
  - M2 (the base `server.py`): 12 failures.
  - M4 (no wake call) and M10 (wake does nothing): 1 and 3 failures.
  - M5 (input read after the counts): 1 failure.
  - M9 (FINAL with the input open): 10 failures.
  - M3 (no outside check), M7 (no `input_ended` in the new branch) and M8 (the outside count
    taken over every group): killed.
  - M6 (no re-count) survives: N1.
- **Interleaving probes P1–P6,** with the input ended or open, a late commit in this group or
  another, and the input ending mid-pass. All gave the outcomes graded below.
- **CLI group probes, branch against base 48cc5056:** for file input and for an ended stream, both
  give the same rc, critical count and remaining count.
- **Stress:** 12 failing and 12 passing lines, a pause, then a late line, into `hsx -N4` at poll
  1 s: SQLite 8 of 8, `postgres` 5 of 5, `timescale` 5 of 5, all clean.
- **Wire fixture:** regenerated byte-identical from the 2.9.0a8 tree.
- **Seeds:** both seeds reproduced as written.

### Requirement → evidence matrix

| R-ID | Status | Change since cycle 2 |
|------|--------|----------------------|
| R1–R4 | ✅ | None in code. Wire tests, mutant M1, the date drives and the cross-version drives pass on both Pythons and all three backends |
| R5 | ✅ | F2 fixed: a failure landing in the count window backs off with the input open (`server.py:280-288`). Deterministic test, LocalCluster test, window-widened CLI probe, fail-pause 25 of 25 on TimescaleDB |
| R6 | ✅ | The input-ended interleaving ends as a complete run (FINAL, rc 0, no spurious critical). Wake mutants killed |
| R7 | ✅ | The halt with a task waiting in another group is kept: `test_failure_landing_during_the_count_still_halts_higher_group`, `test_failed_group_still_halts_late_higher_group`, reviewer P2/P5/P6, CLI group probes equal to base, launch-false exit 5 |
| R8 | ✅ (N1) | The five new tests pin the F2 interleaving, input open and ended; 4 are red on 1a28fb9. The re-count guard at :285 is not pinned (N1) |
| R9 | ✅ | No docs change since cycle 2; `-W` build clean |

Unmapped changes: none. `count_remaining_outside` and the not-viable branch implement the
review-cycle-2 ruling. The input-ended branch is graded below.

### F1–F3 status

- **F1 (cycle 1, §3 wording): fixed**, confirmed again. The text scopes the out-of-work stop and
  names the other stops.
- **F2 (cycle 2, MEDIUM, `server.py`): fixed.**
  - **Logic.** The not-viable branch now halts only when `Task.count_remaining_outside(group)`
    finds an incomplete task in another group (:280). `increment_group` reports not viable only when
    the current group has no incomplete task and nothing retryable. So, with no task outside the
    group, every task was terminal at that read, and the pass is the out-of-work case.
    - Input open: the pass backs off (:287).
    - Input ended, and the re-count finds nothing: FINAL (:285-286).
    - Input ended, and a row was committed since: the pass backs off, and the next pass loads that
      row or halts on it.
  - **Evidence.** The deterministic test the ruling asked for exists and is red on 1a28fb9. The
    window-widened CLI probe shows the defect on 1a28fb9 and its absence on HEAD, with the new path
    taken. fail-pause on TimescaleDB passed 25 of 25.
- **F3 (cycle 2, LOW, "at once"): fixed.**
  - Both files now say each stop "takes effect on its next pass, within `server.poll`, not during a
    back-off". The failed-group halt is stated with its condition ("while a task waits in another
    group").
  - The claim "neither a pause nor a failed task with no retry left ends a stream run early" now
    holds as built (F2 evidence above).
  - The reviewer checked every other claim of the rewritten sentence against the code (`:264`,
    `:273`, `:280-288`, `:1223`).
  - One residual imprecision is not a finding. When a full pool means no refill, the scheduler waits
    `DEFAULT_REMOTE_TIMEOUT` (2 s, `core/queue.py:1116`). With `server.poll` under 2 s, a signal can
    therefore take up to 2 s. The wording follows the ruling, and the behaviour predates the branch.

### The builder's input-ended extension (graded on its merits)

The ruling asked only for the input-open case. The commit also stops quietly when the same
interleaving happens after the input has ended.

**Graded correct, not scope creep.** Develop's own outcome for that input, when the race does not
occur, is the quiet FINAL of its first branch. Develop's critical arose only when a result landed
inside the window, so the branch removes a timing-dependent CRITICAL at the end of a complete run.
- **Exit status unchanged:** the critical has no effect on it (`finalize` only, and `test_groups`
  asserts rc 0 after a halt). Every probe and drive ended rc 0 on both sides, as R6 requires.
- **Nothing develop halts on is left un-halted:**
  - A task waiting in another group at the outside read still halts with the critical (P5, P6, and
    `…still_halts_higher_group`).
  - One committed after that read is caught by the re-count, and the next pass halts on it (P2).
- **One new back-off with the input ended:** it needs `count_remaining() > 0` with nothing outside
  the group. That can only be a row an external writer committed to the failed group, and it is
  loaded on the next pass, within `server.poll`.
- **Scope:** the extension serves R5 and R6 ("the stop with the exit status it has on `develop`")
  and the ruling's drive requirement: the TimescaleDB critical the ruling names was this
  input-ended variant (cycle 2: "in that run `b` ran").

### Findings

No CONFIRMED finding. The code in `server.py`, `data/model.py`, `core/dispatch.py` and
`cluster/watch.py` was found correct against the base invariants and the binding rulings.

#### [LOW · coverage note, non-blocking] N1 — The re-count guard on the input-ended quiet stop is not pinned by a test
- **Where:** `src/hypershell/server.py:285`, `if input_ended and Task.count_remaining() == 0:`.
- **Scenario:** the input has ended, and the current group has just failed permanently. Another
  writer (`hs submit` into the same database) commits a row after `count_remaining_outside` reads
  zero.
  - **With the guard:** the pass backs off. A row in another group then meets the R7 halt on the
    next pass; a row in the failed group is scheduled.
  - **Without the guard (mutant M6):** the run stops quietly, and that row is left unscheduled with
    no critical.
- **Evidence:**
  - Reviewer: probe P2 on HEAD gives `[LOAD, FINAL]` with the critical; under M6 it gives `[FINAL]`
    with none. Under M6, `test_stream_input.py` and `test_task_wire.py` give 180 passed.
  - Orchestrator: re-ran P2 on HEAD and on M6, with the same results. Under M6, `test_stream_input`,
    `test_group_order`, `test_retry_gate`, `test_server_fsm` and `test_restart` give 93 passed.
- **Why not CONFIRMED:**
  - **No observed wrong behaviour.** The branch behaves correctly; only a mutant shows wrong
    behaviour, and the refutation protocol's CONFIRMED requires observed wrong behaviour.
  - **R8 is met.** Every test it names exists and passes, and so does the test the review-cycle-2
    ruling asked for.
  - **Rubric scope.** The severity table grades missing coverage as LOW, "non-blocking".
  - **Rare path.** The path needs an external writer racing the end of a run. Develop also leaves
    such a row unscheduled, behind a CRITICAL halt.
- **Triage for the maintainer:** accept as is, or add a test of the P2 interleaving. Adding one
  means a build and review past the three-cycle bound.

### Notes (not findings)
- The docs note "Input that is never closed keeps the run going until it is" does not repeat the
  exit-5 launch-failure exception. The same page states that exception just above.
- The 16 natural `-N2` runs never hit the window. The deterministic tests and the window-widened
  probe carry the F2 evidence, and the natural repeats show only that nothing regressed.

### Human-gate triggers
- **Not triggered by cycle 3:** there is no CONFIRMED finding, and N1 is a non-blocking coverage
  note.
- **Still open from cycle 2:** F2 was CONFIRMED in `server.py`, and the rubric requires a human
  approval before `hs-publish` whenever a CONFIRMED finding touches the high-blast-radius core. The
  maintainer's review-cycle-2 sign-off authorised the fix, and its remediation is itself a
  `server.py`/`data/model.py` change. The maintainer's go on the remediation, and on N1's triage,
  is required before `/hs-publish`.

### Optional completeness sub-pass
- Not run.
