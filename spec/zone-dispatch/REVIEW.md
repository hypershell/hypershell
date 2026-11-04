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
