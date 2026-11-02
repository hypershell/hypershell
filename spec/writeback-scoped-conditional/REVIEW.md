# REVIEW — Scoped, conditional result writeback

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-02)

- **Reviewed commit:** `2569441`  ·  **Base:** `d996579` (`origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested (two CONFIRMED findings, both LOW)
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh blind reviewer over `git diff d996579...2569441 -- . ':(exclude)spec/'`, then
  orchestrator verification against GOAL, TECH (including the plan gate rulings) and PLAN.
- **Contract drift:** `GOAL.md` has two commits, the shaping commit (`456abde`) and the judge's
  rulings (`a044f7e`). Both come before the plan (`ca7250a`). Nothing changed during the build.

### Verification run

- Full suite, with the PostgreSQL modules skipping as designed → 554 passed, 0 failed, 44 skipped.
- `pytest tests/test_writeback.py tests/test_writeback_postgres.py` → 19 passed, 14 skipped.
  - The skipped tests are the PostgreSQL round trip (plan gate ruling 1). They run in CI on
    pg13 and pg18, and that gate is **pending CI (foreman)**.
- **R6 replay on base:** `test_late_result_after_retry_keeps_chain` fails on `d996579` with
  `AssertionError` on `retried` and passes on the branch.
- **R2 end-to-end, real processes:**
  - Client A was suspended mid-task, evicted, and its task was rerun and failed on client B, which
    created the retry. A was then resumed.
  - Branch: logs `Refused stale result for task (X) from client (…: A)`. The row keeps `retried`
    and `next_id`, there is one retry row, and the failures file has 2 lines.
  - Base: applies the late result, creates a second retry row with the same `previous_id`, and
    writes 4 failure lines.
- **R3/R8 parity, base vs branch, same workloads:** default with `-r 1 --capture --monitor`,
  `--no-db` and `--no-confirm` with a database.
  - Equal on both: return codes, stdout, normalised DB rows, schema and the failures file.
  - Logs equal at DEBUG and above, apart from the per-site TLS fingerprint and timing lines.
  - With `--no-confirm`, `client_id` is set on 12/12 rows on both.
- **Concurrency stress:** threaded SQLite, 300 tasks per seed, six seeds. Dispatch,
  select-then-revert, and apply with duplicates all ran concurrently.
  - 0 errors, and 0 completed rows holding a result from a dispatch other than the one whose
    `schedule_time` they carry.
  - Every outcome occurred (APPLIED, DUPLICATE, STALE).
- **R9, in tree:**
  - The client, the bundle codec, `update_all`, revert, cancel and `task.py` are untouched.
  - A database that base left with rows in flight runs correctly under the branch's
    `hs cluster --restart -r 1`.
  - `hs task update` with `--cancel`, `--revert`, `k:v` and `retried=false` leaves identical rows
    on base and branch.
- **R9 cross-version drives:** release 2.8.3, base `d996579` and the branch, SQLite, `-r 2 -f`,
  10 tasks with 2 always failing, TLS on and off. Results are in the table below.
- **R10 / P3:**
  - `AGENTS.md` and `invariants.md` change in one commit (`2569441`).
  - The only change is that "late full-row writeback" leaves the unguarded list and one paragraph
    states the new guarantee.
  - The four predicates, the `exit_status` table, and the "three cases above" count are unchanged.
    Nothing is relaxed.

| Server | Client | TLS | Result |
|---|---|---|---|
| branch | base | on, off | ✅ 14/14 rows completed, 0 NULL `client_id`, 4 retries with distinct `previous_id`, 0 stale/duplicate lines |
| base | branch | on, off | ✅ same as above |
| branch, on a 2.8.3-created DB | branch | on, off | ✅ upgraded in place without manual steps, same as above |
| 2.8.3 | 2.8.3 | off | ✅ (control) |
| branch | 2.8.3 | on, off | ❌ the 2.8.3 client exits: `TypeError: 'source' is an invalid keyword argument for Task`. **Identical against the base server**, so this predates the diff. |
| 2.8.3 | branch | on, off | ❌ the 2.8.3 server's Receiver dies with the same `TypeError` and the run hangs. **Identical with the base client**, so this predates the diff. |

So R9 holds: every pairing that works on `develop` still works. The 2.8.3 ↔ 2.9 pairings, however,
do not work on `develop` either; see the Observations.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Task.result_columns`, `Task.apply_results` (`model.py`); the set is listed in TECH | `test_non_result_columns_are_never_written`, `test_result_columns_are_fixed`; parity run: DB rows equal | ✅ |
| R2 | `Task.apply_results` conditional UPDATE; `Receiver.update_tasks` | `test_stale_result_is_refused` (4 cases), `test_missing_row_is_refused`, `test_unscheduled_token_is_refused`; real-process end-to-end run | ✅ |
| R3 | same | `test_result_applies_as_today`; base vs branch parity at DEBUG and above (TRACE differs, see Observations) | ✅ |
| R4 | same | `test_foreign_attribution_is_applied` | ✅ |
| R5 | DUPLICATE probe in `Task.apply_results` | `test_duplicate_result_is_ignored` (same bundle, redelivered); **gap for `exit_status == -1`, F1** | ⚠️ |
| R6 | same | `test_late_result_after_retry_keeps_chain`, red on base and green on branch; real-process end-to-end run | ✅ |
| R7 | predicates untouched | `test_cancelled_row_keeps_cancel_status`; AGENTS.md hunk read | ✅ |
| R8 | `in_memory` branch in `update_tasks`; the fence does not depend on `client_id` | `test_no_confirm_result_applies`, `test_cluster_no_confirm_with_db`, `test_in_memory_receiver_writes_nothing`; parity runs with `--no-db` and `--no-confirm` | ✅ |
| R9 | no wire or schema change | the cross-version table above; base DB under the branch; `hs task update` parity | ✅ (PostgreSQL pending CI) |
| R10 | `AGENTS.md`, `invariants.md` (`2569441`) | prose read and the P3 grep gate | ✅ |

**Plan gate rulings:**
1. The PostgreSQL module is present and wired into the CI job; its result is pending CI (foreman).
2. `issues/relay.md` is untouched.
3. A missing row gets a refusal and a log line.
4. STALE and DUPLICATE both log at WARNING, with distinct wording.
5. Four seeds are filed, all `unshaped`, each with a `ROADMAP.md` entry. One misstates its defect
   (F2 below).

**Unmapped changes:**
- `.github/workflows/tests.yml` adds the PostgreSQL writeback module. This is ruling 1.
- Four seeds and their ROADMAP entries. This is ruling 5.
- Two seed status flips to `adopted:writeback-scoped-conditional`, which GOAL requires.

### Findings

#### F1 · LOW · CONFIRMED — Redelivering a result with exit status -1 is logged as "stale", not "duplicate"
- **Where:** `src/hypershell/data/model.py:853-859`, the `cls.exit_status != CANCEL_STATUS` term
  of the DUPLICATE probe.
- **Failure scenario:**
  - A task killed by SIGHUP completes legitimately with `exit_status == -1`, which is the same
    value as `CANCEL_STATUS`.
  - Its result is then redelivered: a crash replay, or the same task twice in one bundle.
  - The second copy is logged as `Refused stale result …`. R5 requires the log to distinguish this
    case from a stale refusal.
  - The row is unchanged and nothing counts twice, so only the label is wrong.
- **Evidence:** an in-process replay (Receiver, SQLite) delivered each result twice.
  - exit -1: first delivery logs `Completed task` and `Non-zero exit status (-1)`; second logs
    `Refused stale result`.
  - exit -15: first delivery logs `Completed task`; second logs `Ignored duplicate result`.
- **Why the term is not needed:** every cancel path restamps `schedule_time` (`Task.cancel_all`,
  `task.py` cancel). A cancelled row therefore never matches the returned `schedule_time`, and
  falls to STALE without this term.
- **Touches:** R5. This is in `data/model.py`, a high-blast-radius file.

#### F2 · LOW · CONFIRMED — Seed `stale-confirm-hides-orphan` misstates its defect
- **Where:** `issues/stale-confirm-hides-orphan.md:21-26` (Problem) and its R1 sketch;
  `ROADMAP.md:229-232`.
- **Failure scenario:**
  - The seed says that when client A is evicted before its confirmation is applied, the task "is
    reverted, rescheduled, and confirmed to client B".
  - That cannot happen. `Task.revert_orphaned(A)` selects rows by `client_id == A`, and an
    unconfirmed row's `client_id` is NULL, so eviction does not revert it.
  - The row stays in flight, is never redispatched, and waits until restart.
  - A's late confirmation then names the already-evicted A on it.
  - The real defect is that an unconfirmed row is invisible to orphan recovery. The seed's R1 test
    sketch cannot be driven as written.
- **Evidence:** an in-process replay: dispatch, then `revert_orphaned(A)` before the confirmation.
  - The row still has `schedule_time` set and `client_id` NULL.
  - `Task.next` returns `[]`.
  - After A's late confirmation and `revert_orphaned(B)`, the row is still in flight with
    `client_id == A`.
- **Touches:** the prose of the seed and its ROADMAP entry only. No code.

### Observations (not findings)

- **Pre-existing:** 2.8.3 and the 2.9 development line cannot interoperate in either direction.
  - Bundles now carry columns that 2.8.3's `Task` rejects (`source`, among others).
  - A 2.8.3 client crashes on its first bundle from a 2.9 server.
  - A 2.9 client kills a 2.8.3 server's Receiver, and the run hangs.
  - Behaviour is identical on `d996579`. This diff neither causes nor worsens it.
  - It is unrecorded in `issues/`, and it matters for the 2.9.0 release notes.
- **TRACE-level log lines differ:**
  - Base's Receiver path logs `Updated N tasks` once per bundle.
  - The branch logs `Result applied for task (<id>)` once per task.
  - DEBUG and above are identical. TRACE is diagnostic, so this is not counted against R3's
    "same logging".
- **Seeds that reproduce as written:** the mechanisms in `orphan-revert-loses-result`,
  `late-confirm-overwrites-attribution` and `date-like-args-coerced` reproduce as described.
  These are pre-existing and out of scope.

### Human-gate triggers

- **Triggered by F1** (`data/model.py`). A human must sign off before `hs-publish`.
- F2 is prose only and touches no core file and no security or DB-lifecycle invariant.

### Remediation for `hs-build`

- **F1:** drop the `exit_status != CANCEL_STATUS` term from the DUPLICATE probe, and add a replay
  with an exit status of -1, redelivered.
  - Alternatively, the judge may rule the label acceptable under the `-1` overload. Under §1 that
    row reads as cancelled, and R2 refuses results for cancelled rows.
- **F2:** rewrite the seed's Problem, its R1 sketch and its ROADMAP entry to describe the real
  defect: a row whose client is evicted before its confirmation is applied is invisible to orphan
  recovery, and stays in flight until restart.

## Triage (human gate) — cycle 1

The human gate for F1 (high-blast-radius `data/model.py`) was **passed by the foreman's ruling**, as
judge under the maintainer's delegation, on 2026-11-02. Remediation returns to `hs-build`.

| Finding | Ruling | Disposition |
|---|---|---|
| F1 · CONFIRMED | fix | Drop the `CANCEL_STATUS` exclusion from the DUPLICATE classifier. Every cancel path restamps `schedule_time`, so a result for a cancelled row already fails the echo match and is STALE (R2), while a row that its own assignment completed with exit status −1 (e.g. SIGHUP) and is then redelivered is a DUPLICATE (R5). Red-first test: a redelivered result for a row completed with exit −1 logs "Ignored duplicate result", and a result for a cancelled row still logs "Refused stale result". R7 is unchanged. |
| F2 · CONFIRMED | fix | Correct `issues/stale-confirm-hides-orphan.md` and its `ROADMAP.md` entry to the mechanism actually reproduced: an unconfirmed row has a NULL `client_id`, so `revert_orphaned(A)` skips it and it stays in flight until restart, and A's late confirm then names the evicted A. Make the test sketch one that can be driven. |
| Observation · TRACE-level log line | accepted | Below DEBUG, one `Updated N tasks` line becomes a `Result applied` line per task; DEBUG and above are byte-identical (R3 holds). No change. |
| Observation · 2.8.3 ↔ 2.9 wire break | seed | Pre-existing on `d996579` (2.9.0a1 onwards), not from this diff, so R9 holds: a 2.8.3 client crashes on the first bundle (`'source' is an invalid keyword argument for Task`), and a 2.9 client kills a 2.8.3 server's Receiver and the run hangs. Seed it `unshaped` with a `ROADMAP.md` entry and both reproductions, and record that zone-dispatch's version handshake (its R5 and the new-client-vs-old-server ruling) is the intended clean failure, to be verified against 2.8.3 there. |

Out of scope for cycle 2: everything except F1, F2 and the seed.
