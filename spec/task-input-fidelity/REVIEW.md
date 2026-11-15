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
