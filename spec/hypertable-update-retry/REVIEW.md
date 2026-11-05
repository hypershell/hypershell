# REVIEW — A task-row write that waited on a row lock still lands on a TimescaleDB hypertable

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `b33f99f561a0e5df18a8f2f4e189e5aafa220c3d`  ·  **Base:** `develop` at
  `0e8275e` (the remote-tracking ref; the local `develop` was stale)  ·  **Date:** 2026-11-04
- **Verdict:** approved, after the foreman's triage below. Under the rubric alone, F1 (LOW,
  CONFIRMED) gives `changes-requested`.
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

**How the pass ran.** The blind correctness pass of record came from a fresh reviewer subagent, as
the skill's Step 2 requires. An earlier orchestrator spawned it, then was forced to hand back
before the reviewer reported, so the report did not reach that orchestrator. The reviewer finished,
and its full report reached the foreman, who handed it to this orchestrator. The report says that
only `GOAL.md` was read under `spec/`. This orchestrator started a second pass inline, reading
GOAL.md, the spec-excluded diff, `AGENTS.md`, `invariants.md` and the rubric. On the foreman's
instruction it stopped that pass before running anything, and it adopted the delivered report
instead. No second blind pass ran, and nothing below comes from the stopped one. This orchestrator
read `TECH.md`'s frontmatter for its metadata. It read the R4 and R5 sections only to grade those
two criteria, after the blind verdict was fixed.

**Contract drift.** `GOAL.md` has one commit after shaping (`a39f762`). That commit is `560d44e`,
which records the maintainer's sign-off rulings before the plan commit (`806fb99`). It does three
things:
- fixes R5's exhaustion outcome;
- adds R4's rule for seeding cross-process paths;
- turns the "open for sign-off" items into dated Clarifications.

Nothing moved during the build. This review grades the signed-off text.

## Verification run

From the blind reviewer's report (verbatim substance):

- **Unit suite, 3.13:** 379 passed (`hs` put on PATH).
- **Related modules, 3.11 and 3.14:** 200 passed and 49 skipped on each. The 5
  `test_contended_write.py` tests skip without PostgreSQL.
- **Full suite, 3.12:** 680 passed, 79 skipped. The known upgrade timing flake
  (`test_upgrade_cli.py::test_waits_for_slow_peer_upgrade`) was deselected.
- **`test_contended_recovery.py` against `0e8275e`'s `src`:** 13 failed, 1 passed. The one pass is
  the missing-row parity control.
- **R6 statement listener (SQLite, branch against base):**
  - Rows, outcomes and INFO+ logs are identical.
  - Result path: APPLIED takes 1 UPDATE; STALE and DUPLICATE take 1 UPDATE and 1 SELECT.
  - Confirm and revert send N single-row UPDATEs where the base sent 1 executemany of N parameter
    sets. This is a change of round-trips only.
- **CLI drive, branch and base:** `seq 100 | hsx -t 'test {} -ne 7' -N4 -b1 --no-tls -f failed.txt`
  in a throwaway site.
  - Both runs: rc 0, `failed.txt` = `7`, 99 succeeded and 1 failed, and no row with a NULL
    `client_id`.
  - The INFO+ lines are the same apart from thread timing.
- **R3 fault injection:** the first UPDATE returns 0 while a holder commits a row change.
  - For a result, by the holder's change: confirm → APPLIED; revert → STALE; reschedule → STALE;
    complete → DUPLICATE; cancel → STALE; delete → STALE.
  - All of these match `develop`, and the refused cases write nothing.
  - Confirm and the orphan revert behave as `develop` does.
  - An orphan revert of a completed row wipes the result. That is pre-existing
    (`issues/orphan-revert-loses-result.md`).
- **Receiver thread, with a result that never lands:**
  - The result is held and retried every 2 s, with no busy loop.
  - The shutdown sentinel logs a WARNING and the thread joins in 0.00 s; `stop(wait=True)` joins in
    0.82 s.
  - Once the contention is released, the next cycle applies the result.
- **The seed's claims, by probe:**
  - A 0-row match on the `Task.next` flush, or on the `retried` mark, raises `StaleDataError`.
  - A failed mark leaves the attempt-2 row committed and the old row at `retried = 0`, which can
    lead to a double retry.
- **Evidence consistency:**
  - `6a67b0a..HEAD` changes nothing in `src`, `tests` or `.github`.
  - `test_contended_write.py` is identical from `7d68a33` to HEAD.
  - `e085aca..HEAD` touches `spec/` only.
  - The CI step runs `test_contended_write.py` in all 5 `postgres` entries, with `HS_TEST_POSTGRES`
    set.
- **Real-database side:** neither reviewer could rerun it, as no local libpq was available. It is
  graded from `EVIDENCE.md`:
  - The foreman's contention runs: on 2.31.0, `develop` is red in exactly 3 tests; the fix is green
    on 2.31.0 and 2.24.0.
  - CI on `e085aca`: 14/14, with the contended tests at 5 passed / 0 skipped in every PostgreSQL
    entry.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `Task.confirm_all` (per-row UPDATE by id, re-run while the row exists), called from `Confirm.update_info` (`model.py`, `server.py`; `3737b70`) | `test_contended_confirm_lands_on_rerun`; `test_contended_confirm_records_client` on 2.31.0, from the recorded evidence | ✅ |
| R2 | `Task.__apply_result` re-runs behind the classifying SELECT (`6a67b0a`) | `test_contended_result_applies_on_rerun`; `test_contended_result_applies` (failures line, no STALE) from the evidence | ✅ |
| R3 | The re-run keeps the full conditional UPDATE. It is taken only while the SELECT shows the row in flight under the same dispatch | Fault injection over all six holder outcomes; `test_contended_result_refused_when_reverted` | ✅ |
| R4 | Code: `confirm_all`, the result re-run, and `revert_all` on the helper, with loop exits in `revert_orphaned` and `revert_interrupted`. Seed `issues/hypertable-contended-scheduler-and-cli-writes.md` and its `ROADMAP.md` entry | Blind: met on the code side. **Orchestrator:** the `TECH.md` § R4 table is graded below | ✅ |
| R5 | `CONTENDED_RERUNS = 3`; DEBUG per extra attempt; WARNING at the bound per path; `ResultOutcome.DEFERRED` with `Receiver.held` | Blind: exhaustion tests and the Receiver thread drive. **Orchestrator:** the `TECH.md` § R5 statement is graded below | ✅ |
| R6 | A first-attempt match is neither re-run nor re-checked; `in_memory` guards kept; no schema or wire change | Statement listener; CLI drive; `test_uncontended_*`; `test_in_memory_receiver_never_holds` | ✅ (round-trips: N statements for confirm and revert, not 1 executemany) |
| R7 | `tests/test_contended_write.py`, with the lock wait observed through `pg_stat_activity`/`pg_blocking_pids` and 30 s deadlines; the CI line in `tests.yml` | Recorded evidence (contention runs and CI) | ✅ for the in-server paths; F1 for `revert_interrupted`, ruled in triage |
| R8 | `AGENTS.md` § Data layer specifics and the hazard list; `invariants.md` §1 | Read | ✅ |

**R4, the orchestrator's grade (`TECH.md` § R4).** The table gives a verdict for every path GOAL's
Problem names, and for every other `task` UPDATE path in `src`:
- `rotatedb`'s part stamp, which raises on any provider but SQLite (`data/__init__.py`);
- the `client`-table writes, which are unaffected.

A sweep of `src` for UPDATE forms found nothing outside the table. The table seeds the Scheduler's
`Task.next` stamp and `retried` mark as in-server contended, per the plan-gate ruling. The seed's
`Problem` matches the code at the paths it cites.

One precision on `revert_interrupted`. It runs from `Scheduler.start()`, and
`ServerThread.start_threads` starts the Confirm and Receiver threads before the Scheduler
(`server.py` L961–971). So those threads exist when it runs. What keeps them from contending it is
that nothing has been dispatched yet in that run, which is the reason the table gives. **Met.**

**R5, the orchestrator's grade (`TECH.md` § R5).** `TECH.md` states the bound and a WARNING naming
the task and path for each of `confirm`, `result` and `revert`. It also states what happens to a
deferred result:
- it is held and presented again on every Receiver cycle until it is applied, or classified STALE
  or DUPLICATE;
- its row stays in flight under its own dispatch, so nothing reschedules it (no double run);
- the run cannot stop while the row is in flight (no hang);
- only a signalled or forced stop delivers the sentinel with a result held, which logs a WARNING per
  result and leaves the row for `revert_interrupted`.

The code matches each point:
- `Receiver.unload_bundle` moves to UPDATE on an empty queue while results are held;
- `update_tasks` prepends the held results and warns once per result;
- `unpack_bundle` warns on the sentinel;
- `Scheduler.load_bundle` reaches its normal FINAL only when `count_remaining() == 0`. Its other
  exits are a signal and a failed group, which are stops.

**Met.**

Unmapped changes (possible scope creep): the `hs task update` → `hs update` renames in `AGENTS.md`,
`invariants.md`, `ROADMAP.md` and two seeds. They apply the maintainer's top-level-commands docs
rule and are harmless. No added line outside `spec/` names an `hs task …` command.

## Findings

### [LOW/CONFIRMED] F1 — No PostgreSQL race test for `Task.revert_interrupted`
- **Where:** `tests/test_contended_write.py`. The path is `src/hypershell/data/model.py` L864–869.
- **Failure scenario:** `Task.revert_interrupted` shares `revert_all` and gains a loop exit
  (L864–869). `AGENTS.md` and the seed name "the start-up reverts" as fixed. R7 asks for a race
  test for "each further path R4 brings into scope", but `test_contended_write.py` has none for this
  path.
- **What covers it today:**
  - `test_contended_interrupted_revert_exhausted_returns`, by SQLite fault injection;
  - `test_contended_orphan_revert_lands`, a real race through the same `revert_all` path.
- **Evidence:** the five `def test_` names in `test_contended_write.py`; the blind reviewer's read
  of R4's scope against R7.
- **Touches invariant / requirement:** R7 (test coverage). It is not an invariant violation.
- **Suggested fix:** add `test_contended_interrupted_revert_lands`.

**Checked and dropped by the blind reviewer:**
- unbounded growth of `held`;
- a double run or a hang with a result held: `count_remaining() > 0` blocks FINAL, and a revert
  turns the held result STALE;
- held results lost on stop: a WARNING is logged, and `develop` also drops results on a stop;
- a Confirm/Receiver deadlock: pre-existing, and named in the seed;
- revert exhaustion: a WARNING, and it needs four consecutive contenders.

## Human-gate triggers

- **Not triggered by the rubric.** F1, the only CONFIRMED finding, is in `tests/`, not the coupled
  core. It touches no security or DB-lifecycle invariant.
- The foreman nevertheless triaged it as judge (below).

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Triage (human gate) — cycle 1

The foreman rules as judge (2026-11-04).

| Finding | Ruling | Disposition |
|---|---|---|
| F1 · LOW · CONFIRMED | accept, no change | `Task.revert_interrupted` runs once, at server start-up, before the Confirm and Receiver threads exist, so no in-server writer can contend it. Its only possible contenders are other processes (a second server, `hs update`), which R4 scoped to the seed `issues/hypertable-contended-scheduler-and-cli-writes.md`. The shared write helper it uses (`revert_all` → per-row re-run) is proven against a real hypertable race by `test_contended_orphan_revert_lands` and in-process by `test_contended_interrupted_revert_exhausted_returns`. A dedicated PostgreSQL race test would re-prove the same helper and require re-running every contention evidence run. R7 is met for the in-server paths R4 brought into scope; recorded here. |
| R7 notes (pg13/pg18 develop runs, which 2.31 build) | record | CI's develop runs on `0e8275e` (run 41906240784) show pg13/pg18 green; the foreman's develop-fails run used 2.31.0-pg18 Community by digest (EVIDENCE harness note). |

Verdict after triage: approved.
