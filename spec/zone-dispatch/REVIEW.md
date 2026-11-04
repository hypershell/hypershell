# REVIEW — Zone-aware, demand-driven dispatch (zone, cycle 2)

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** b946b14820b4109d483a5311b8f24692e2d9c307  ·  **Base:** `ec75169` (Z1 final + `develop` `b31c66e`, merged)  ·  **Date:** 2026-11-04
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** `debate` — two independent blind reviewers, one arguing "ship" and one "block",
  reconciled by the orchestrator. The diff touches the coupled core (`server.py`, `client.py`,
  `core/queue.py`, `data/model.py`) and changes the dispatch wire. The orchestrator ran the full
  suite. No `completeness` sub-pass.

## Pre-flight

- **Base resolution.** `TECH.md` records `base: develop`, but this cycle is stacked on Z1, which is
  reviewed separately and lands first. The graded diff is `git diff ec75169...HEAD -- .
  ':(exclude)spec/'`. `ec75169` is an ancestor of HEAD, merged in at `a040e1d`, and the merge's
  resolutions in `src/hypershell/core/queue.py` were graded as part of this change. The local
  `develop` ref is stale and was not used.
- **Build state.** `TECH.md` is `status: in_review`.
- **Contract drift.** `git log --oneline ec75169..HEAD -- spec/zone-dispatch/GOAL.md` shows
  `1554030` (shape) and `8a0a7b0` (judge clarifications, 2026-11-02). Both predate the plan
  (`e9aef9e`) and every build commit, so the contract did not move mid-build.
- **Multi-node evidence.** The evidence is the raw per-run JSON in `evidence/cluster/`, produced on
  test clusters by the maintainer's foreman with `tests/cluster/drive.sh`. It was graded as
  committed and not re-run. The reviewers were allowed to read `evidence/cluster/` but not
  `TECH.md`.

## Verification run

Commands executed, and what they showed. Builds are frozen offline venvs over `git archive` extracts:
HEAD on 3.11.18, 3.12.16, 3.13.18 and 3.14.10; 2.8.3, 2.9.0a2 and `b819de6` on 3.12.16; each
reviewer also built `ec75169` on 3.12. Every run had its own process group and a kill timer, in a
throwaway site.

- **Full suite at HEAD** (orchestrator, `pytest -q`, one Python at a time):
  - 3.12: **821 passed, 67 skipped**.
  - 3.11: **817 passed, 71 skipped**.
  - 3.13: **818 passed, 70 skipped**.
  - 3.14: **818 passed, 70 skipped**.
  - The skips are the PostgreSQL and TimescaleDB tests (no server locally), the 2.8.3
    fixture-regeneration test, and two version-gated `test_queue_gc.py` cases on 3.11.
  - `tests/test_zone_postgres.py` therefore did not run locally. CI runs it.
- **Targeted modules on 3.12** (ship reviewer): the 13 zone, dispatch, queue, writeback and FSM
  modules gave **175 passed, 3 skipped** (PostgreSQL).
- **Wire compatibility (R5).** Both reviewers drove real processes, TLS on and off.
  - **Old clients against the new server.** Clients from 2.8.3, `b819de6`, 2.9.0a2 and `ec75169`
    each exit **rc 5 within 0.35–1.43 s**.
    - Each old client reports "client too old".
    - The server logs `Client too old (no work-request protocol) from 127.0.0.1:<port>` once per
      old client.
    - A concurrent new client finished every task (30/30, and 60/60), so the server kept serving.
  - **New client against old servers.** Against 2.8.3, 2.9.0a2 and `ec75169` servers, the new
    client exits **rc 5 in about 0.35 s** with one CRITICAL `QueueVersionError … does not speak
    work-request protocol 1`.
    - Each old server then served its own client to completion (20/20, and 30/30).
  - **No hang and no misroute** was seen in any direction.
  - The handshake keys on an integer protocol number, not the package version string, which still
    reads `2.9.0a2` at HEAD.
- **Multi-node evidence.** `tests/cluster/compare.py` was run offline over `evidence/cluster/`: the
  default judge, `--zoned`, and `--min-ratio 0.95` with `--baseline 2.8.3 --candidate feature
  --control 2.9.0a2`. All **PASS**.
  - **Run 1, homogeneous and unzoned** (3 repetitions per build, 20000 tasks):
    - Exit histograms are identical (`{0: 19800, 3: 200}`), with 20000 rows and 0 incomplete in
      every run.
    - Median p10–p90 throughput is feature 201.3 vs 2.8.3 140.6 tasks/s, **1.43×**. The overall
      medians are 194.3 vs 133.4.
    - Server tree: 0 child processes vs 1, and 18–19 open fds vs 43.
  - **Run 2, zoned** (zones `default:40,cpu:30,gpu:30`, 3 groups, a retried failure every 50
    tasks, 6 clients):
    - 20000 tasks and 20400 rows (400 retried), 0 incomplete, server rc 0, `group_order` true.
    - `zone_mismatched_retries` 0, and every client's `exit7` 0.
    - The sum of `received` equals the row count, so no task was delivered twice.
    - The cap-32 repetition shows a client advertising 64 receiving at most 32. A plain client is
      served the server's `-b 4`.
  - Both reviewers judged that `compare.py` tests what R1, R4 and R14 state. One note: its default
    throughput rule tolerates max(spread, 5%), which is looser than R1. `--min-ratio 0.95` is the
    strict form, and it passes.
- **Single-host R1 benchmark** (shared machine, so indicative only):
  - Block reviewer: 7 interleaved repetitions of `hs cluster`, 1500 × `true`, `-N8`, TLS on. Median
    **0.972×** of 2.8.3.
  - Ship reviewer: 5 interleaved repetitions of `hs cluster -N16`, 4000 × `true`, TLS and database
    on. Median **1.076×**. Feature fds 21–22 vs 58–59, Python children 0 vs 2.
- **Schema upgrade (R11).** Both reviewers built a 2.8.3 database and a 2.9.0a2 database, then opened
  each with HEAD.
  - The `zone` table, `task.zone` and `index_tasks_zone` were created in place on the first command.
  - Old rows stayed `zone IS NULL` and ran on a default-zone client. A gpu task ran only on the gpu
    client.
- **Sizing and cap (R2, R3, R4).** The ship reviewer drove a server with `-b 4`:
  - a plain client got bundles of at most 4;
  - `-b 2` got 2;
  - `HYPERSHELL_SUBMIT_BUNDLESIZE=3` got 3;
  - with `bundlemax 6`, `-b 50` got at most 6.
  - 600 tasks were received for 600 rows, and partial bundles were observed.
- **CLI (R6, R12).** `hs cluster tasks.txt --zone gpu` stamped 3 rows `gpu`, rc 0. `hs info` shows
  `zone: gpu (<id>)`. `hs list` shows the name in the plain, json, csv, table and normal formats.
- **Defect drives** (see Findings):
  - cancel a staged task, then the server ends (block and ship);
  - cancel a staged task, then the zone's last client leaves (block and ship);
  - delete a staged task in both of those situations (block and ship);
  - `hs update --revert` on a staged task, at HEAD vs `ec75169` (block);
  - `hs submit -q` against a database-backed server, at HEAD vs `ec75169` (block).
- **Tree hygiene.** `git status --porcelain` in the worktree is empty after both reviewers. Every
  process group either reviewer started was checked and none is alive.

## Requirement → evidence matrix

| R-ID | Implemented by (file) | Verified how | Status |
|------|------------------------|--------------|--------|
| R1 | `server.py` staging and dispatch; `core/dispatch.py` (the default zone is always demanded); `client.py` `advertised_size` | Run 1 evidence through `compare.py --min-ratio 0.95` (1.43×, identical final states, fewer processes and fds); single-host interleaved runs 0.972× and 1.076×; full suite | ✅ |
| R2 | `client.py` `request_text` / `advertised_size`; `core/dispatch.py` sizing | `test_client_request.py`, `test_configured_bundlesize_is_advertised`, `test_server_bundlesize_serves_plain_clients`; sizing drive; Run 2 (advertised 64 → 64, plain → server `-b`) | ✅ |
| R3 | `Dispatcher.cap` (`max(server.bundlemax, bundlesize)`, default 1000); JSON text both ways | Sizing drive (`bundlemax 6` bounds `-b 50`); Run 2 cap-32; `test_dispatch.py` | ✅ |
| R4 | Per-zone pools with first-come-first-served tickets, cut under one lock, bounded wait | `test_dispatch.py`; sizing drive (600/600, partial bundles); Run 2 (sum received = rows) | ⚠️ met on the request path; F3 strands a task when a staged row is reverted |
| R5 | `core/queue.py` `_versioned_provider`, `QueueVersionError` | Live drives in every direction, TLS on and off (above); `test_dispatch_version.py` | ✅ |
| R6 | `--zone` on `submit`, `task submit`, `client`, `cluster`; argv in `local`/`remote`/`ssh`/autoscale | `hs cluster --zone gpu` drive; `test_zone_dispatch.py`, `test_submit.py`, `test_cluster.py` | ✅ |
| R7 | `Zone` table; `task.zone` | Schema read after upgrade. The id is `uuid5(name)`, accepted as minted; default-zone tasks store NULL (see R11) | ✅ |
| R8 | `Zone.ensure` (`uuid5` + `INSERT … ON CONFLICT DO NOTHING`) | `test_ensure_under_concurrent_processes` (SQLite). PostgreSQL unverified locally (no server); CI runs `test_zone_postgres.py` | ✅ (SQLite) |
| R9 | `Task` zone filter on `select_new`/`select_failed`; retries copy `zone` | `test_retry_keeps_zone`; `test_zone_workload.py`; Run 2 `zone_mismatched_retries` 0, `exit7` 0 | ✅ |
| R10 | `increment_group` unchanged (global) | `test_group_gating_stays_global`; Run 2 `group_order` true | ✅ |
| R11 | Default zone with a fixed id, stored as NULL; `data/upgrade.py` | Upgrade drives on 2.8.3 and 2.9.0a2 databases (above) | ✅ |
| R12 | `task.py` `resolve_zone` | `hs info`, and `hs list` in five formats | ✅ |
| R13 | `docs/_include/*` and `share/` completions, in the same commits as the CLI changes | Diff read: `--zone` on all four commands, the client's `-b` as its advertised size, `server.bundlemax` in the config reference | ✅ |
| R14 | Nine new test modules | Targeted 175 passed; full suite green on 3.11–3.14; the loopback mixed-zone grouped workload with a retry is present and passes | ⚠️ met; no test covers cancel, delete or revert of a staged row (F1–F3) |

**Unmapped changes (possible scope creep):** none harmful.
- Evidence tooling for R1, R4, R5 and R14: `tests/bench/bench_queue.py` and `tests/cluster/{drive.sh,compare.py,smoke.sh}`.
- The CI PostgreSQL job's added `test_zone_postgres.py` (R8).
- The `report_held` log line, which comes from GOAL's note.
- The client back-off tweak, the `AGENTS.md` prose and the `issues/zone.md` carve note.

**Seed `issues/client-bundlesize-config-key.md` (graded as prose).** It is accurate. The client takes
its `-b`/`-w` defaults from `config.submit.bundlesize`/`bundlewait` (`client.py:329-330`,
`client.py:1559`). `[client].bundlesize` is documented in `config_param_ref.rst` but read nowhere.
The behaviour predates this cycle. `status: unshaped` fits, and the `ROADMAP.md` entry points at a
file that exists.

**Invariant §6 amendment** (`AGENTS.md`, `.agents/factory/invariants.md`). Both reviewers judged it
legitimate: under R2/R4 clients no longer read `scheduled`, and the amendment puts
`dispatcher.finish()` where the per-client sentinels on `scheduled` used to go. The code follows the
order stated: submitter → scheduler → heartbeat (`finish`, join) → receiver sentinel → confirm
sentinel. The amended text omits one new step between the heartbeat join and the receiver sentinel,
`ServerThread.revert_staged`, which is where F1 and F2 strike at shutdown.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### [CRITICAL/CONFIRMED] F1 — A task cancelled while staged is un-cancelled, and then run
- **Where:**
  - `src/hypershell/server.py:322-326`: `Scheduler.revert_unstaged`, called at `:237`.
  - `src/hypershell/server.py:981-986`: `ServerThread.revert_staged`, called from `wait_heartbeat`
    at `:979`.
  - Both call `Task.revert_all(ids)` (`src/hypershell/data/model.py:814`). That is an unconditional
    bulk update by primary key, which sets `schedule_time`, `completion_time`, `exit_status` and the
    rest back to NULL.
  - Every base caller of `revert_all` goes through `select_interrupted`/`select_orphaned`, which
    filter `completion_time IS NULL`. These two new callers pass raw staged ids with no such filter.
- **Failure scenario:** `hs update --cancel` on a task that is staged but not yet delivered, then
  either:
  - the zone's last client leaves; or
  - the server ends while the row is still staged, for example default-zone rows while only zoned
    clients are connected.

  The cancelled row goes back to unscheduled (exit_status NULL, completion_time NULL), and the same
  run or the next one executes it. The natural trigger is this cycle's own "Task group N waiting on
  zone X: … no client serving it" line: cancelling the stuck tasks is the obvious response.
- **Evidence** (both reviewers, independently):
  - **Shutdown path** (block reviewer). One gpu task `sleep 6` and three default tasks run with a
    gpu client only, so d0 and d1 are staged in the default pool.
    1. `hs update --remaining --cancel --no-confirm -w 'zone == null'` sets d0, d1 and d2 to
       `exit_status -1` with `completion_time` set.
    2. The server logs "Reverted 2 staged tasks never delivered".
    3. Final state: d0 and d1 have `schedule_time`, `completion_time` and `exit_status` all NULL.
       d2, which was never staged, stays cancelled.
  - **Shutdown path** (ship reviewer). The same result: d1 is staged, cancelled, then reads exit
    NULL and completion NULL after "Reverted 1 staged tasks never delivered".
  - **Mid-run path** (ship reviewer). A gpu client runs with `-b 1 -N 1` on a `sleep 6` task while
    g4 and g5 are staged.
    1. `hs update --cancel -t mark:g5` sets g5 to exit -1.
    2. SIGUSR1 to the client produces "Reverted 2 staged tasks (zone has no client)", and g5 returns
       to unscheduled.
    3. **A second gpu client then ran g5 and recorded exit 0.**
  - **Mid-run path** (block reviewer). Six cancelled gpu tasks: two go back to unscheduled during
    the run.
  - **Not pre-existing.** At `ec75169` the only revert calls in `server.py` are `revert_interrupted`
    and `revert_orphaned`.
- **Touches invariant / requirement:**
  - Invariants §1 and §2: cancelled is terminal (`completion_time` terminality, the CANCEL_STATUS
    filter).
  - The `hs update --cancel` contract: cancelled tasks "will not be scheduled, retried, or
    reverted".
  - R4 and R9, through the staging and unstaging paths they introduce.
- **Suggested fix:** revert staged ids with one conditional update that skips rows that are now
  terminal or deleted: `UPDATE task … WHERE id IN (:ids) AND completion_time IS NULL`, as a `Task`
  classmethod (e.g. `Task.revert_staged(ids)`), with no rowcount assertion. Add tests that cancel
  and delete a staged row on both paths.

### [MEDIUM/CONFIRMED] F2 — A staged task that is deleted crashes the Scheduler (mid-run) or the server (at exit)
- **Where:** the same two call sites as F1. `Task.revert_all` uses `Session.bulk_update_mappings`,
  which raises `StaleDataError` when an id no longer matches a row.
- **Failure scenario:** `hs update --delete` on a staged task, followed by one of these:
  - **The zone's last client leaves.** The **Scheduler thread** dies with "Uncaught exception from
    Scheduler … StaleDataError: … expected to update 2 row(s); 1 were matched". Then:
    - the server stops with rc 5;
    - another staged row is left stamped and never delivered;
    - a running `sleep 30` task in another zone is left incomplete;
    - the next client gets ConnectionRefused.
  - **The server ends.** It exits rc 5 with a StaleDataError CRITICAL. The receiver and confirm
    threads are stopped through `stop()` instead of their orderly sentinels, because
    `revert_staged` runs before `wait_receiver`/`wait_confirm`.
- **Evidence:**
  - Both reviewers reproduced both paths (mid-run and at exit) with `hs update --remaining --delete
    --no-confirm -w …` on staged rows.
  - **At base:** a deleted row that had already been delivered gives a StaleDataError in the Confirm
    thread, but the server keeps serving. Under this diff the error is fatal, and it also hits rows
    that were never delivered.
- **Touches invariant / requirement:**
  - §6 shutdown ordering: the sentinels are skipped.
  - §5: a scheduler thread dies on an uncaught exception.
  - R4.
- **Suggested fix:** the F1 fix (a conditional update without a rowcount assertion) removes this
  too.

### [MEDIUM/CONFIRMED] F3 — Reverting a staged task strands it: a stale result is refused and the server never exits
- **Where:** `src/hypershell/core/dispatch.py:385`. `Dispatcher.stage` skips any id already in
  `pool.ids`, so it keeps the pool's earlier record and drops the freshly stamped one.
- **Failure scenario:**
  1. `hs update -w 'id == <id>' --revert --no-confirm` runs on a task that is staged but not
     delivered.
  2. The scheduler re-selects the row and stamps a new `schedule_time` in the database.
  3. `stage` discards the new record, because the id is already pooled with the old stamp.
  4. The client returns the old stamp, and the receiver logs "Refused stale result for task …".
  5. The row stays stamped and incomplete, is never selected again, and the server never finishes.
- **Evidence** (block reviewer, A/B): 4 default tasks; the server stages d0 and d1; d1 is reverted;
  one client is started.
  - **HEAD:** "server HUNG >60s". d1 is left with a new `schedule_time` and `completion_time`
    NULL, and the server log shows the stale refusal.
  - **`ec75169`:** server rc 0 with all 4 tasks completed, exit 0.
- **Touches requirement:** R4 (each staged task delivered for its current attempt). It is a
  regression from base on a documented `hs update --revert` path.
- **Suggested fix:** when an id is already staged, replace its record in place, keeping its queue
  position, instead of skipping the update. That remains harmless for the duplicate case the
  dedupe guards against. Add a revert-while-staged test.

### [LOW/CONFIRMED] F4 — `hs submit -q` against a database-backed server now hangs, and nothing runs
- **Where:**
  - `src/hypershell/core/dispatch.py:228-232`: `Dispatcher.attach` attaches the inbound `scheduled`
    queue only when the server is in memory.
  - `src/hypershell/core/queue.py:956` is the call site.
  - With a database, nothing drains `scheduled`, so bundles a live `hs submit -q` puts there are
    never served.
- **Failure scenario:** `hs server` running with a database, plus a client, then `seq 5 | hs submit
  -q …`.
  - **HEAD:** the submit hangs (">40 s"), and 0 tasks run.
  - **`ec75169`:** submit rc 0 in 0.4 s and the 5 tasks ran. Base was already broken here
    differently: its Confirm thread hit StaleDataError and the results were refused as stale.
  - Against an in-memory server, both refs behave the same.
- **Evidence:** block reviewer, an A/B drive at both refs with and without a database.
- **Touches requirement:** none directly. It is an edge-path regression from "runs, but loses its
  results" to "hangs forever".
- **Suggested fix:** refuse `--queue` against a database-backed server with one clear error, or
  drain the inbound queue into the default pool in database mode too.

**Dropped under refutation** (recorded, not findings):
- A refill-versus-stage race that leaves rows in the pool of a zone whose last client has just
  left. It heals when a client returns or at exit, and was not reproduced.
- In-flight loss when a client dies mid-reply. The same class exists at base.
- `hs cluster --zone` with an in-memory database configuration crashes HeartMonitor ("no such table:
  client") identically at base and HEAD.
- Stale prose in the server's `--help`, which is not an R13 file.

**Known pre-existing items: does this diff make them worse?**

| Item | Verdict | Evidence |
|---|---|---|
| TLS localhost flake (fixed on develop after this base) | Not worse | `core/tls.py` untouched. Not seen in four full suites or in the TLS-on drives |
| Tasks stranded at group advance (separate cycle) | Not worse, not reproduced | `increment_group` is unchanged, and staged rows count as incomplete and hold their group. Run 2 `group_order` true; local grouped workloads complete in order |
| A stream run stopping before its input ends | Not worse | A 12-line stream with 1 s gaps gave 12/12 at both refs, 2/2 repetitions. HEAD finished about 2 s later (14.8–14.9 s vs 12.7–12.8 s). The block reviewer's case behaved identically at both refs |
| An empty task file hanging | Not worse | It hangs at both refs (30 s and 45 s bounds) |
| "database is locked" during ingest | Not worse | 20000 tasks, `hs cluster -b 100 -N 16`, 2 repetitions per ref: 0 lock lines at both, and HEAD is slightly faster |
| `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` | Not worse | 3/3 at each ref, interleaved; passes in all four full suites |
| `test_restart.py::test_restart_update_adds_novel_and_runs` | Not worse | 3/3 at each ref, interleaved; passes in all four full suites |

## Human-gate triggers

**Triggered.**
- F1 is CONFIRMED in `server.py` and breaks a DB-lifecycle invariant: cancelled rows lose
  `completion_time` terminality and the CANCEL_STATUS fence.
- F2 and F3 are CONFIRMED in `server.py` and `core/dispatch.py` on the same staging seam.

Explicit human sign-off is required before any further step.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run this cycle.

## Triage (human gate) — cycle 1

Cycle 1 (`9a81343`, reviewing `b946b14`) requested changes on F1–F4, with a CRITICAL in `server.py`, which triggers the human gate. The foreman ruled on 2026-11-04 as judge.

| Finding | Ruling | Disposition |
|---|---|---|
| F1 · CRITICAL · CONFIRMED | fix | The staged-revert paths (`Scheduler.revert_unstaged`, `ServerThread.revert_staged`) must never touch a completed or cancelled row. Revert with one conditional statement, `UPDATE … WHERE id IN (:ids) AND completion_time IS NULL`, as a `Task` classmethod with no rowcount assertion. **Red first:** integration tests that cancel a staged, undelivered task and then (a) let the zone's last client leave and (b) stop the server. Each must fail at `b946b14` (the task is un-cancelled or runs) and pass after (the row stays cancelled with exit −1 and never runs). |
| F2 · MEDIUM · CONFIRMED | fix (with F1) | **Red first:** a test that deletes a staged task (`hs update --delete`) and then hits each revert path. The scheduler must not die, the server must exit 0, and other tasks must complete. |
| F3 · MEDIUM · CONFIRMED | fix | `Dispatcher.stage` must replace an already-staged record with the current one (current `schedule_time`), not skip it, so a reverted-then-restaged task carries its live stamp. **Red first:** `hs update --revert` on a staged task. The run must finish at HEAD (it hangs at `b946b14`) with every task done. |
| F4 · LOW · CONFIRMED | fix | `hs submit -q` against a database-backed server refuses with one clear error and a nonzero exit, naming the reason. It must not hang. Draining the inbound queue in database mode is out of scope; if real deferred work remains, seed it. **Test:** the refusal, and the in-memory `-q` path still works. |
| Note: §6 invariant text omits `revert_staged` | fix | Amend the invariant text (and `AGENTS.md` if it carries the same list) to name the staged revert, and state that a staged revert never touches a completed or cancelled row. |

Gates for cycle 2:
- every new red test fails at `b946b14` and passes after the fix;
- the zone and dispatch modules pass;
- the full suite passes once on each of 3.11, 3.12, 3.13 and 3.14;
- parity and skew against `b819de6` pass, with TLS on and off, as TECH records them;
- the local zoned rehearsal (`smoke.sh`) passes.

The multi-node evidence stands. These fixes touch only cancel, delete and revert edge paths, and the request path is unchanged; the reviewer confirms in cycle 2.

## Review cycle 2 — changes-requested (2026-11-04)

- **Reviewed commit:** 32379a44c4a1d45511d887edca1be76bbc2b1ef6  ·  **Base:** `ec75169`, unchanged  ·  **Remediation:** `git diff 9a81343..32379a4 -- . ':(exclude)spec/'`
- **Verdict:** changes-requested. Two LOW findings, both CONFIRMED and both prose: F5 in `AGENTS.md`, F6 in the new seed and its `ROADMAP.md` entry. F1–F4 and the §6 note are fixed.
- **Cycle:** 2 of ≤3. The loop has not converged, so this goes to the human.
- **Mode:** a fresh blind pass over the whole spec-excluded diff `ec75169...HEAD`, plus verification of cycle 1's F1–F4.
  - One blind reviewer, without `debate`.
  - Given inline: GOAL.md, the invariants, the rubric, the cycle-1 findings F1–F4 and the judge's rulings. It could also read `evidence/cluster/`.
  - Not given: PLAN, TECH, research, META or the earlier REVIEW.md text.
  - The orchestrator ran the full suite. No `completeness` sub-pass.

### Pre-flight

- **Base.** As in cycle 1: `ec75169`. The merge resolutions in `core/queue.py` at `a040e1d` are in scope. The stale local `develop` was not used.
- **Build state.** `TECH.md` is `status: in_review`.
- **Contract drift.** `git log --oneline 9a81343..HEAD -- spec/zone-dispatch/GOAL.md` is empty, so the contract has not moved since cycle 1.
- **Builds.** Frozen offline venvs, each from a `git archive` extract with a private copy of the uv cache:
  - HEAD on 3.11.18, 3.12.16, 3.13.18 and 3.14.10;
  - `b946b14` on 3.12.16;
  - `ec75169`, 2.8.3, 2.9.0a2 and `b819de6` on 3.12, reused from cycle 1.

  Every run had its own process group and a kill timer, in a throwaway site.

### Verification run

- **Full suite at HEAD** (orchestrator, `pytest -q`, one Python at a time). The reviewer's drives shared the machine.
  - 3.12: **829 passed, 67 skipped**.
  - 3.11: **825 passed, 71 skipped**.
  - 3.13: **826 passed, 70 skipped**.
  - 3.14: **826 passed, 70 skipped**.
  - Each count is cycle 1's plus the 8 new tests in `tests/test_dispatch_staged.py`. The skips are the same as in cycle 1: there is no PostgreSQL or TimescaleDB server locally.
- **Zone and dispatch modules on 3.12** (orchestrator; 11 modules, including `test_dispatch_staged.py`): **106 passed, 3 skipped** (PostgreSQL).
- **Targeted modules** (reviewer):
  - 3.12: 290 passed, plus `test_cluster.py` with 98 passed;
  - 3.14: 149 passed.
- **Red/green** (reviewer). HEAD's `tests/test_dispatch_staged.py` was run in a copy of the `b946b14` extract. All 8 tests fail there, and all 8 pass at HEAD.

  | Test | At `b946b14` | At HEAD |
  |---|---|---|
  | `test_stage_replaces_a_staged_record` | the old record is served | pass |
  | `test_revert_staged_leaves_terminal_and_missing_rows` | `AttributeError` (no `Task.revert_staged`) | pass |
  | `test_staged_rows_changed_before_shutdown[cancel]` | cancelled row returns with exit NULL | pass |
  | `test_staged_rows_changed_before_shutdown[delete]` | server rc 5, `StaleDataError` | pass |
  | `test_staged_rows_changed_before_zone_empties[cancel]` | cancelled staged rows run | pass |
  | `test_staged_rows_changed_before_zone_empties[delete]` | server dies once the zone empties | pass |
  | `test_reverted_staged_row_runs_with_its_new_stamp` | server never finishes | pass |
  | `test_queue_submit_refused_by_database_server` | `hs submit -q` hangs | pass |

- **`Task.revert_staged`** (reviewer probe, SQLite):
  - **1201 ids.** They ran as 3 statements with 513, 513 and 214 bound parameters, and all 1201 rows were reverted.
  - **One transaction.** A failure injected on the second chunk raised and rolled back the first chunk: all 1201 rows stayed stamped, and the session was still usable afterwards.
  - **Mixed rows.** With deleted, cancelled and completed rows spread across chunks, 1197 of 1201 were reverted, and the cancelled row stayed terminal.
  - **Concurrent delete.** On SQLite, a delete from another connection between chunks waits on the held write lock.
  - **Sessions.** The session is per thread: `revert_unstaged` runs on the Scheduler thread, the shutdown revert on the ServerThread.
  - **PostgreSQL.** The bound limit is 65535, far above 513 per statement. This was reasoned, not run.
- **`Dispatcher.stage` replacing a record in place** (reviewer probe):
  - **Locking and position.** `stage`, `_replace`, `cut`, `take_*` and `finish` all run under the dispatcher lock. A replaced record keeps its position: an instrumented launcher replaced at positions 1 and 3 of 6, and all six tasks ran once each, in submit order, with 0 stale results.
  - **Old stamp already cut.** The id has left the pool, so the new record is appended. The holder of the old stamp has its result refused as stale. This is the same as at base for a hand revert.
  - **Return value.** The added count is unused for accounting: refill uses `len(pool.rows)`.
  - **Normal runs.** In an instrumented 214-task run (three zones, three groups, retries, mixed `-b`), the replace branch was hit **0** times.
- **`hs submit -q`** (reviewer, every combination bounded; none hung):
  - **HEAD against a HEAD server with a database:** rc 5 in 0.3–0.5 s, with one CRITICAL `QueueSubmitRefused` that names the reason (the server schedules from its database) and the remedy (`--no-db`). The server keeps running.
  - **HEAD against `--no-db`:** rc 0, and the tasks run.
  - **Old submitters** (2.8.3, 2.9.0a2, `b819de6`, `ec75169`) against HEAD, with or without a database: rc 5 in 0.4–0.9 s with "client too old". The server logs "Client too old" and keeps serving. This is identical against `b946b14`.
  - **HEAD against old servers** (2.8.3, 2.9.0a2, `b819de6`, `ec75169`): rc 5 in about 0.5 s, with one `QueueVersionError`.
  - **The handshake.** `PROTOCOL` is unchanged, and the handshake does not involve the new exposed method.
- **Multi-node evidence.** The remediation diff changes only:
  - the duplicate-id branch of `stage`;
  - the new `takes_submissions`;
  - the two staged-revert paths;
  - the submit check.

  `request`, `cut`, registration, sizing, refill and the cap are unchanged. The reviewer re-ran `tests/cluster/compare.py` over `evidence/cluster/` in three modes, all **PASS**:
  - default, with `--control 2.9.0a2`;
  - `--zoned feature`;
  - `--min-ratio 0.95 --baseline 2.8.3 --candidate feature --control 2.9.0a2`.

  The cycle-1 grade stands.
- **Single-host R1 benchmark** (reviewer; indicative only, because the machine was shared with the full suite): 5 interleaved repetitions of 1500 tasks give a median of **0.971×** 2.8.3.
- **Tree hygiene.** `git status --porcelain` is empty after the reviewer. None of the 253 process groups it recorded is alive, and none of the orchestrator's is either.

### Cycle-1 findings: remediation

| Finding | Status | Evidence at HEAD |
|---|---|---|
| F1 · CRITICAL | **Fixed** | Cycle 1's own repro script was re-run in four modes: shutdown, delete, unstage, and unstage-delete. Each gives server rc 0, and the cancelled rows stay at exit −1 with `completion_time` set and never run. The log reads "Reverted 0 staged tasks … (2 left as completed, cancelled or deleted)". In the mixed variants, the unchanged staged row is reverted, then either runs or is left unscheduled |
| F2 · MEDIUM | **Fixed** | Delete before shutdown and delete mid-run: server rc 0, no `StaleDataError`, the Scheduler keeps running, and the other rows complete |
| F3 · MEDIUM | **Fixed** | Replacing in place keeps the position and carries the new stamp. The run finishes with every task done and no stale refusal |
| F4 · LOW | **Fixed** | A clean refusal with rc 5 in under 0.5 s. The in-memory `-q` path still works. Seed `issues/queue-submit-database-server.md` filed (see F6) |
| §6 note | **Fixed** | `AGENTS.md` and `invariants.md` now name the staged revert and its conditional rule. The order matches `ServerThread` (`server.py:977-994`), and both call sites use `Task.revert_staged` (`server.py:328`, `:992`). "One conditional update" is several statements in one transaction when there are more than 500 ids; the property it states holds |

### Requirement → evidence matrix

| R-ID | Verified how (cycle 2) | Status |
|------|------------------------|--------|
| R1 | `test_cluster_default_path` and `test_server_bundlesize_serves_plain_clients`; `compare.py --min-ratio 0.95` PASS; single host 0.971× | ✅ |
| R2 | `test_request_carries_identity_zone_and_size`, `test_advertise_only_an_explicit_size`, `test_cut_follows_advertised_size`, `test_configured_bundlesize_is_advertised` | ✅ |
| R3 | `test_reply_shape`, `test_cap_bounds_the_largest_bundle`; evidence cap-32 PASS | ✅ |
| R4 | `test_no_task_is_delivered_twice`, the partial, empty and re-entry tests; mixed drive with 214 tasks and 0 double runs; F3 fixed | ✅ |
| R5 | `test_dispatch_version.py`; the old/new submit matrix above | ✅ |
| R6 | `test_submit_stamps_zone`, `test_zone_usage_errors`; `test_cluster.py` 98 passed | ✅ |
| R7 | `test_zone_model.py` | ✅ |
| R8 | `test_ensure_is_idempotent`, `test_ensure_under_concurrent_processes` (SQLite). PostgreSQL is not run locally; CI runs `test_zone_postgres.py` | ✅ (SQLite) |
| R9 | `test_zoned_request_gets_only_its_zone`, `test_retry_keeps_zone`; mixed drive with 0 wrong-zone runs | ✅ |
| R10 | `test_group_gating_stays_global`, `test_mixed_zone_workload`, `test_groups.py` | ✅ |
| R11 | `test_upgrade_adds_zone_in_place`, `test_new_task_stores_default_zone_as_null` | ✅ |
| R12 | `test_zone_display.py` | ✅ |
| R13 | `--zone` is documented everywhere it is required. The F4 help text landed in the same commit as the code (`7dee92c`) | ✅ |
| R14 | Full suite green on 3.11–3.14; the staged cancel, delete and revert paths are now covered | ✅ |

**Unmapped changes:** none harmful.
- The changes cycle 1 listed.
- The `Task.revert_columns` refactor of `revert_all`, which keeps the same 13 columns.
- `takes_submissions` and `QueueSubmitRefused`, from the F4 ruling.
- The new seed and its `ROADMAP.md` entry.

### Findings

#### [LOW/CONFIRMED] F5 — `AGENTS.md` says the dispatcher exposes one method; it exposes two
- **Where:**
  - `AGENTS.md:167`: "whose single method `request` hands work to clients".
  - `AGENTS.md:175`: "the one method `request` on the dispatcher".
  - The code: `src/hypershell/core/queue.py:904`, `DISPATCH_EXPOSED = ('request', 'takes_submissions')`.
- **Failure scenario:** `AGENTS.md` is the stated ground truth for the remote surface, and that surface is a pickle-framed RPC. It now under-reports the methods a remote party can call on the dispatcher. No test pins `DISPATCH_EXPOSED`.
- **Evidence:**
  - `grep -n "one method\|single method" AGENTS.md` matches both lines.
  - `grep -rn "DISPATCH_EXPOSED\|takes_submissions" tests/` finds nothing.
  - `takes_submissions` was added in `7dee92c`.
- **The method itself is sound.** It is authenticated, takes no arguments and returns a bool. It was ruled for under F4. The defect is the prose only.
- **Suggested fix:** name both methods in those two sentences: `takes_submissions` is the one `hs submit -q` calls.

#### [LOW/CONFIRMED] F6 — The new seed understates what `hs submit -q` did to a database-backed 2.8.x or 2.9.0a2 server
- **Where:**
  - `issues/queue-submit-database-server.md:19-21`: "Their confirmations failed and their results were refused, so the tasks ran and nothing was recorded."
  - `ROADMAP.md:375`: "the tasks used to run with their results refused".
- **What actually happens:**
  - **2.8.3 and 2.9.0a2:** both the server's Confirm thread and its Receiver thread die on an uncaught `StaleDataError`. The server stops recording results for the rest of the run.
  - **`ec75169`:** only the Confirm thread dies, and results are refused as stale. This matches the seed's wording, which fits `ec75169` but not the releases it names.
- **Evidence:** the reviewer drove each old submitter against its own version's server with a database. Its logs show `Uncaught exception from <class 'hypershell.server.Receiver'>` and `StaleDataError` at 2.8.3 and 2.9.0a2, and `Refused stale result` at `ec75169`.
- **What is accurate:**
  - every other claim in the seed;
  - `status: unshaped`;
  - the ROADMAP link target.
- **Suggested fix:** correct both sentences to say what the released versions did.

**Dropped under refutation** (recorded, not findings):
- **A deleted staged row cut by a live client.** The Confirm thread hits `StaleDataError` and the server exits rc 5. This is identical at `ec75169`, where the row sat on `scheduled`, so it is pre-existing and not made worse.
- **A cancelled staged row cut by a live client still runs.** Its result is dropped. This is the same as at base, and `hs update` warns that the task is already scheduled.
- **Hand-reverting rows that were delivered but not yet confirmed.** They run twice, and the stale result is refused. This is the same at `ec75169` (HEAD: 59 double runs; base: 85).
- **Editing a staged row's zone to a raw UUID and then reverting it** puts the id in two pools. It needs a hand edit, the result fence holds, and the remediation does not change it.
- **A new `hs submit -q` against a `b946b14` server** raises `AttributeError`. `b946b14` was never released.
- **`cluster/remote.py` passes `--zone` unquoted.** The argv is a list given to `Popen` with no shell, so this is not a bug.

**Known pre-existing items: does this diff make them worse?**

| Item | Verdict | Evidence |
|---|---|---|
| TLS localhost flake (fixed on develop after this base) | Not worse | `hs submit -q` opens one more proxy. The TLS-on refusal test passes on 3.12 and 3.14, and four full suites are green |
| A stream run stopping before its input ends | Not exercised | Untouched by the remediation |
| An empty task file hanging | Not exercised | Untouched by the remediation |
| "database is locked" during ingest | Not worse | `revert_staged` holds the SQLite write lock across its chunks, as `revert_all` held it at `b946b14`. The window is milliseconds for realistic pools. Not exercised during ingest |
| `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` | Not worse | Passes in all four full suites |
| `test_restart.py::test_restart_update_adds_novel_and_runs` | Not worse | Passes in all four full suites |
| TimescaleDB ≥ 2.29 concurrent-update regression | Exposed, PLAUSIBLE, LOW; owned by its own cycle | `Task.revert_staged` is this cycle's only new UPDATE on `task`. The regression needs another transaction to hold that staged row's lock at that moment, which only a hand `hs update` or `hs task update` does. If that writer was a cancel, matching 0 rows is the right outcome. Otherwise the row stays stamped with `completion_time` NULL and sits in no pool. A simulated spurious 0 (SQLite) showed the consequence: mid-run, a non-`forever` server does not finish and logs nothing about why; at the next start, `revert_interrupted` reverts the row and every task completes |

### Human-gate triggers

**Not triggered by a finding.** F5 and F6 are prose in `AGENTS.md`, `issues/` and `ROADMAP.md`. No CONFIRMED finding is in a high-blast-radius file or breaks a security or DB-lifecycle invariant. F5 describes the remote surface of `core/queue.py`, but the code it describes was graded sound.

**The bounded loop does escalate.** This is cycle 2 of ≤3 without convergence, so the human decides the next step.

## Triage (human gate) — cycle 2

Cycle 2 (`04a83b4`, reviewing `32379a4`) confirmed F1–F4 fixed and requested changes on two LOW prose findings. Not a human-gate trigger by finding; the foreman rules as judge (2026-11-04). Cycle 3 is a scoped review of this remediation diff.

| Finding | Ruling | Disposition |
|---|---|---|
| F5 · LOW · CONFIRMED | fix + pin | `AGENTS.md` names both remotely callable dispatcher methods, `request` and `takes_submissions`, wherever it describes the dispatcher's surface. Add a unit test that pins `DISPATCH_EXPOSED` to exactly that tuple, so the pickle-framed RPC surface cannot grow unnoticed. |
| F6 · LOW · CONFIRMED | fix | `issues/queue-submit-database-server.md` and its `ROADMAP.md` entry state accurately what 2.8.x and 2.9.0a2 did (Confirm and Receiver threads die on an uncaught `StaleDataError`, after which the server records no results), and what `ec75169` did. |
| Note: `Task.revert_staged` under TimescaleDB ≥ 2.29 | defer to merge | The `hypertable-update-retry` cycle on develop adds a contended-write re-run helper. When Z2 next merges develop after that lands, `revert_staged` goes through the same helper. Record this in TECH as a merge obligation. |

## Review cycle 3 (scoped) — approved (2026-11-04)

- **Reviewed commit:** f8a078f33daa13364bbd43e9fb89e81c4bad9508  ·  **Base:** `ec75169`, unchanged  ·  **Remediation:** `git diff 04a83b4..f8a078f -- . ':(exclude)spec/'`
- **Verdict:** approved. F5 and F6 are fixed. No new finding.
- **Cycle:** 3 of ≤3. The loop has converged.
- **Mode:** scoped, as ruled in the cycle-2 triage. The review covers only the cycle-2 remediation diff, and checks it against F5, F6 and their rulings.
  - One blind reviewer, without `debate`.
  - Read: the cycle-2 section of this file and its triage.
  - Not read: PLAN, TECH (beyond its `kind` and `review` fields), research, META, or the builder's commit body.
  - No `completeness` sub-pass.

### Pre-flight

- **Scope.** The diff touches exactly the four expected files: `AGENTS.md`, `tests/test_dispatch_version.py`, `issues/queue-submit-database-server.md` and `ROADMAP.md`.
- **No source change.** `git diff --quiet 04a83b4..f8a078f -- src/` exits 0.
- **Builds.** A `git archive` extract of `f8a078f`, run on the frozen offline 3.12 venv with the import path pinned to the extract; the cycle-2 venvs for 3.11, 3.13 and 3.14. Every run had its own process group and a kill timer, in a throwaway site.

### Verification run

- **Zone and dispatch modules on 3.12.** 11 modules: the 9 `test_dispatch*.py` and `test_zone*.py` modules, plus `test_client_request.py` and `test_server_fsm.py`. Result: **113 passed, 3 skipped** (PostgreSQL). Unraisable and unhandled-thread exceptions were errors.
- **`tests/test_dispatch_version.py` on 3.11, 3.13 and 3.14:** 5 passed on each.
- **The pin test, 20 times on 3.12.** Each run was a separate pytest process. **20/20 passed**, in 0.58–0.60 s each. `ResourceWarning`, unraisable and unhandled-thread exceptions were errors.
- **Mutation of the pin.** Each mutant was a scratch copy of the extract with one line changed, and each failed the pin test in about 0.6 s:

  | Mutant (`core/queue.py`) | Fails at |
  |---|---|
  | `DISPATCH_EXPOSED` grows: `+ 'stage'` | `DISPATCH_EXPOSED == surface` |
  | `DISPATCH_EXPOSED` shrinks to `('request', )` | `DISPATCH_EXPOSED == surface` |
  | `DISPATCH_EXPOSED` reordered | `DISPATCH_EXPOSED == surface` |
  | The registry serves `None` (every public method) in place of `DISPATCH_EXPOSED` | `type(client.dispatcher)._exposed_ == surface` (15 methods served) |
  | The registry serves `DISPATCH_EXPOSED + ('stage', )` | `_exposed_ == surface` |
  | The registry serves `('request', )` | `_exposed_ == surface` |

  So the test catches a change to the declared tuple and a change to what the server actually hands a client. The last three mutants fail inside the `with QueueServer(...)` block, and the server still tears down: each run exits promptly, with no hang.
- **Teardown.** The pin test ran 200 times in one process, about 4–5 ms each.
  - **Threads.** None added. Only the main thread and the per-process `hypershell-queue-decref` sender remain.
  - **Errors.** None: no warning, no unraisable or thread exception, and nothing logged at WARNING or above.
  - **Sockets.** One loopback client socket is still open after 50 or 200 iterations, and none after one iteration. The same single socket remains when the loop runs the pattern that predates this diff: a server, a client and one `request`, with no `takes_submissions`. It is not this test's.
- **Server-side enforcement** (probe, not part of the test). A raw `_callmethod` on the dispatcher proxy for `stage`, `finish`, `staged` or `configure` is refused with `RemoteError`, and the staged pool is unchanged. `request` is still served. `__repr__` is answered by the stdlib proxy fallback, as on every managed object.
- **F6 re-drive.** Each version was driven against its own server, with a database:
  1. `hs submit -q` with 3 tasks;
  2. one client;
  3. then `hs submit` with 3 database tasks.

  | Server | Threads that died (`StaleDataError`) | `-q` tasks | Later database tasks |
  |---|---|---|---|
  | 2.8.3 | Confirm and Receiver | ran, not recorded | ran, **not recorded** (completion and exit NULL) |
  | 2.9.0a2 | Confirm and Receiver | ran, not recorded | ran, **not recorded** |
  | `ec75169` | Confirm only | ran; 3 × "Refused stale result" | ran, **recorded** (exit 0) |

  - "After which the server recorded no results for the rest of the run" holds for the releases.
  - "Only the Confirm thread died" and "nothing was recorded for them" hold for `ec75169`.
  - **"2.8.x"** is reasoned, not run: `server.py` is identical from 2.8.0 to 2.8.3, and the `model.py` changes between them do not touch `Task.update_all`.
  - **"Unreleased"**: `git tag --contains ec75169` is empty.

### Cycle-2 findings: remediation

| Finding | Status | Evidence at `f8a078f` |
|---|---|---|
| F5 · LOW | **Fixed** | `AGENTS.md:166-168` and `:176-179` name both methods. A grep of the whole file finds no other description of the dispatcher's remote surface, and no "one method" or "single method" anywhere. The prose matches the code: `queue.py:904` (`DISPATCH_EXPOSED`), `queue.py:959` (the registry) and `submit.py:888` (`check_server` calls `takes_submissions`). The pin test `test_dispatch_version.py:85-100` is deterministic: it uses no timing, binds port 0, and asks with `wait` 0. The mutants above show it fails on growth, shrinkage or a bypassed registry |
| F6 · LOW | **Fixed** | `issues/queue-submit-database-server.md:19-25` and `ROADMAP.md:375-380` now state the released and `ec75169` behaviour; the re-drive above confirms each clause. Both Refs lines cite cycle 2, F6. `status: unshaped` is unchanged |

### Findings

None.

**Dropped under refutation** (recorded, not findings):
- **The pin is order-sensitive.** A reordered `DISPATCH_EXPOSED` fails it, which is stricter than "grows or shrinks". This is harmless for a pin.
- **The test never disconnects its client.** The neighbouring tests in the module do the same. The teardown probe shows no thread, warning or per-run leak.
- **"Two remotely callable methods".** The stdlib proxy also answers `__repr__` and `__str__` on every managed object, the queues included. `AGENTS.md` describes the exposed surface, as it already did for `QUEUE_EXPOSED`.

### Human-gate triggers

**None.** There is no CONFIRMED finding. The diff is prose plus one unit test, and it changes no source. The TimescaleDB merge obligation from the cycle-2 triage is outside this diff and was not checked.
