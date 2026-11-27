# REVIEW — Retire a superseded relay, and wake the main thread for signals

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-13)

- **Reviewed commit:** f98614f40768d0591fe529cf10cada7a946589af  ·  **Base:** `origin/develop` `fb89670`  ·  **Date:** 2026-11-13
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** one blind pass, no `debate`, no `completeness` sub-pass. `hs-review` was itself spawned
  as a fresh subagent with only the foreman's task statement, so it ran the correctness pass in
  that clean context instead of spawning another (the skill's no-subagent fallback). It read
  `GOAL.md`, the spec-excluded diff, `AGENTS.md`, `invariants.md` and the rubric, and `TECH.md`'s
  frontmatter for `base`/`kind`/`status` only. It did not read `PLAN.md`, the body of `TECH.md`,
  `research/` or `evidence/` prose; `META.md` was opened only after the findings were set, to
  append to it. Probes were scratch scripts outside the repository; every process they started
  was stopped by its recorded PID or process group.

### Pre-flight

- **Base.** `TECH.md` records `base: develop`; the graded diff is
  `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, and `fb89670` is the merge base.
- **Build state.** `TECH.md` reads `status: in_progress` with P1–P4 all `done`; the foreman asked
  for the review at this head (draft PR #101).
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/relay-retire/GOAL.md` lists
  `0586fbd` (shape) and `0d674e8` (GOAL-gate rulings), both before the plan and the first build
  commit (`dee8745`). The contract did not move mid-build.
- **Invariants.** The branch does not change `.agents/factory/invariants.md`; the branch copy was
  the checklist.

### Verification run

- **Targeted suite (SQLite):** `uv run pytest -q tests/test_signal_wake.py tests/test_relay_retire.py
  tests/test_retire_judge.py tests/test_topology_plan.py tests/test_topology_server.py
  tests/test_dispatch.py tests/test_dispatch_version.py tests/test_contended_write.py
  tests/test_relay_fsm.py tests/test_relay_topology.py` → **320 passed, 12 skipped** (the skips are
  the PostgreSQL-only contended writes).
- **Full suite (SQLite):** `uv run pytest -q tests` → **2033 passed, 95 skipped**, 2 warnings (the
  existing `fork()` deprecation warnings in `tests/test_logging.py`), 18 min, under CPython 3.14.
  A first run was abandoned when the docs build's `--python 3.12` re-created `.venv` beneath it
  (META F6); `.venv` was re-synced on 3.14 and the suite run again from the start.
- **PostgreSQL / TimescaleDB (sandbox off, local server):** `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community
  uv run pytest tests/test_upgrade_postgres.py tests/test_writeback_postgres.py tests/test_timescale.py
  tests/test_contended_write.py tests/test_zone_postgres.py` → **135 passed, 1 skipped, 29 failed**;
  all 29 are the expected missing templates (20 `hs283`, 9 `hs290a1`, "not listed in
  `HS_TEST_POSTGRES_TEMPLATES`"). With `HS_TEST_TIMESCALE=` (plain PostgreSQL provider),
  `tests/test_contended_write.py` → **12 passed**, the four hand-back revert tests among them.
- **R15 red on the base (macOS):** `git archive origin/develop src` into a scratch directory, then
  `PYTHONPATH=<scratch>/src uv run pytest tests/test_signal_wake.py::…` for the relay, client,
  server, local cluster and submit cases → **all five fail** on the base ("relay/client/server/cluster
  did not exit within 30.0 seconds of the signal"; submit times out waiting for "Received signal");
  the same tests pass on the branch. Linux was not run here.
- **R8 wire check:** a branch `reply_text` with a retired identity and one without, read by the
  base's `read_topology` (`PYTHONPATH` on the base source) → both parse; the unretired reply is the
  base's text byte for byte, and the retired one only adds `"retired": true`.
- **Docs:** `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs <tmp>`
  (in a separate `UV_PROJECT_ENVIRONMENT`, sandbox off for `sphinx_sitemap`'s socket) → **exit 0, no
  warnings**.
- **Cluster record (builder's, re-judged only):** `python3 spec/relay-retire/drive/retire_judge.py
  spec/relay-retire/evidence/cluster/retire-29c9bc7-45764803-retire.json` → **PASS** (every check,
  including `R7.bound`: last unstarted task available at +1.19 s of the mark against a 6 s bound).
  The record could not be regenerated here (no cluster tools).
- **Probes (scratch, not committed):**
  - `probe_retire_halt.py` (RV1): a real `SchedulerThread` on a relay database holding three
    received tasks, driven through `RelayThread.retire()` with `machine.halt()` landing while
    `load_bundle` is loading (a hook on `Task.next` waits for the halt flag).
  - `probe_restart_order.py` (RV2): an upstream server autoscaling with relay sites and two relays
    at one site as separate processes; the server is restarted with the older relay paused, so it
    reconnects after the newer one.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/topology.py` (`retired_identities`, `TopologyPublisher.check`/`note_retired`), `core/topology.py` (`Topology.retired`, `reply_text`) | `test_topology_plan.py`, `test_topology_server.py` pass; end-to-end `test_a_superseded_relay_hands_back_finishes_and_exits` passes; `warn_duplicates` unchanged | ⚠️ RV2 |
| R2 | `relay/upstream.py` (puller `gate`/`request` check `retired`; `fetch` logs once), `relay/site.py` (`TopologyView.retire`) | `test_relay_fsm.py`, `test_relay_topology.py`; the end-to-end test asserts one "Superseded" line | ✅ |
| R3 | `relay/site.py` (`SiteScaler` launches nothing once retired), `relay/__init__.py` (`retire` stops the fleets, ends no client) | End-to-end test: no launch after the mark, no "Terminating site client", every task runs once; judge `relay.no_terminate`, `retire.no_launch_after_mark` | ✅ |
| R4 | `relay/__init__.py` (`retire`, `hand_back`), `relay/store.py` (`unstarted`, `drop_handed_back`), `core/dispatch.py` (`handback`, `take_handbacks`), `server.py` (`Scheduler.revert_handed_back`), `data/model.py` (`Task.revert_handed_back`) | `test_relay_retire.py` hand-back tests, `test_dispatch.py`, `test_contended_write.py` on SQLite, PostgreSQL and TimescaleDB | ❌ RV1 |
| R5 | `store.unstarted` lists only rows with no local `schedule_time`; `take_staged` and a request's cut share the dispatcher lock | `test_unstarted_lists_only_what_no_site_client_received`; end-to-end test runs each task once | ✅ |
| R6 | `finish_upstream` (final heartbeat only when nothing is held) after `retire` | End-to-end test: final heartbeat, exit 0 | ❌ RV1 |
| R7 | All of the above | End-to-end laptop test passes; builder's cluster record re-judged PASS | ❌ RV1, RV2 |
| R8 | `reply_text` adds `retired` only when an asked identity is retired; `TOPOLOGY_PROTOCOL` and the work protocol stay 1; a relay hands back only once retired | Base reader parses both replies (above); `test_dispatch_version.py` pins the four-method surface | ✅ |
| R9 | Every new path is gated on `retired` / `retiring` / `handback_due` | Full suite; `test_relay_topology.py`, `test_topology_server.py` | ✅ |
| R10 | `docs/cli/relay.rst` (note removed, retire described, `versionchanged`), `docs/cli/server.rst`, `docs/_include/relay_desc.rst`, relay `APP_HELP` | Read; docs build `-W` clean | ✅ |
| R11 | `core/thread.py` `join_waking` in `RelayApp.run`; `wait_site` logs the drain each check | `test_relay_drains_on_a_signal_taken_by_another_thread` passes; fails on the base | ✅ |
| R12 | `join_waking` in `run_client`, `serve_from`, `serve_forever`, `serve_with_sites`, `run_local`, `run_ssh`, `run_cluster`, `submit_from` | Client, server, local cluster and submit tests pass on the branch, fail on the base; `--ssh`/`--remote`/`--autoscaling` share the helper (not driven) | ✅ |
| R13 | No handler change; the main thread waits in bounded `join` steps and the `except` paths are unchanged | `tests/test_signal.py`, `tests/test_relay_db.py` in the full suite | ✅ |
| R14 | `join_waking` ends with a plain `join`, so a `Thread` re-raises its failure | `test_join_waking_re_raises_the_worker_failure_unchanged`, `test_worker_failure_keeps_its_exit_status` | ✅ |
| R15 | `tests/test_signal_wake.py` (injector thread, `signal.pthread_kill`) | Red on the base, green on the branch (macOS, above) | ✅ |

Also checked and clean:
- **Hand-back surface.** `read_handback` bounds the text (256 KiB), the count (1000), each field (64
  characters), requires UUID identity and task ids, ISO 8601 stamps and distinct ids; a refusal is
  an error reply, never a raise; a server without a database refuses it. `MAX_HANDBACK_TASKS` is
  enforced on both sides.
- **Requeue SQL.** `Task.revert_handed_back` is one conditional `UPDATE` per row on `id`,
  `schedule_time` as dispatched, `completion_time IS NULL` and `client_id` the relay's or NULL, with
  the §1 re-check on a 0-row match (TimescaleDB), run in the scheduler thread, the sole `Task.next`
  caller. A completed, cancelled, re-dispatched, evicted-and-reverted or deleted row matches nothing.
  The contended tests pass on all three providers.
- **Signal wake.** `join_waking` raises the worker's own exception object; a `KeyboardInterrupt` or
  `RelayTerminated` is raised from the timed `join` as it was from the untimed one, into the same
  `except` blocks; handlers and their registration are unchanged.
- **Late confirmation after a hand-back.** A confirmation that lands after the requeue stamps the
  old relay on an unscheduled row (`Task.confirm_all` does not match the dispatch); the next
  dispatch's confirmation overwrites it, and it matters only if that relay is evicted before then.
  Not reported.

Unmapped changes (possible scope creep): none. `tests/test_retire_judge.py` tests a `spec/` drive
script, as `tests/test_relay_judge.py` and `tests/test_topology_judge.py` do on the base; the
`AGENTS.md` edit corrects the dispatcher's remote surface for R4's new method.

### Findings

#### RV1 [MEDIUM/CONFIRMED] A retired relay strands the chunk its relay-local scheduler was loading: neither staged nor handed back, so no final heartbeat and an upstream eviction
- **Where:** `src/hypershell/relay/__init__.py:744-749` (`retire`: `scheduler.stop(wait=True)`, then
  `revert_staged()` and `hand_back()`), acting on `src/hypershell/server.py:242-260`
  (`load_bundle` commits a chunk's local `schedule_time` in `Task.next` and returns `PACK`) and
  `src/hypershell/core/fsm.py` `next()` (a halt flag set meanwhile returns `HALT` before `PACK`
  stages the chunk); `src/hypershell/relay/store.py:263` (`unstarted` lists only rows with no local
  `schedule_time`).
- **Failure scenario:** a retired relay still holds unscheduled tasks and its site clients are
  taking work, so the relay-local scheduler is loading chunks. `retire()` calls
  `scheduler.stop()`; if `machine.halt()` lands while `load_bundle` is loading, the chunk has its
  local `schedule_time` set but is never put in a pool. `take_staged` does not see it, `unstarted`
  skips it, and no site client receives it. The relay then holds it with no result:
  `finish_upstream` logs "Holding N tasks with no result returned upstream - not finishing
  upstream" and sends no final heartbeat, and the upstream server evicts the relay's identities
  (600 s by default) before the tasks run elsewhere: RV5's delay, on this interleaving. The normal
  drain stops the scheduler only once the backlog is empty, so the window is new with `retire`.
- **Evidence:** `probe_retire_halt.py` (above), run with `uv run python -m pytest -p tests.conftest`
  → after `retire()`: `staged in relay-local pools: 0`, `held by relay (count_held): 3`,
  `unstarted (hand-back candidates): 0`, `handed back upstream: 0`, all three local rows with
  `schedule_time` set and no client; then `finish_upstream()` → `final heartbeat sent: False` and
  the warning "Holding 3 tasks with no result returned upstream - not finishing upstream (the
  upstream server will evict … and revert them)".
- **Touches:** R4 (held, unstarted, not handed back), R6, R7 ("evict none").

#### RV2 [MEDIUM/CONFIRMED] After an upstream restart, registration order can retire the newer relay; with the older one already retiring, both exit and the site is left with no relay
- **Where:** `src/hypershell/cluster/topology.py:193-204` (`retired_identities`, which takes
  "older" from `_arrange` at `:155`: the instance whose first identity registered earliest in *this*
  server process), with the mark sticky at the relay (`src/hypershell/relay/upstream.py:924`,
  `TopologyView.retire`).
- **Failure scenario:** relay A (the old process) has been retired and waits for its site client's
  long task; relay B (the new process) holds the site. The upstream server restarts (a supported
  operation; relays reconnect with backoff) and A happens to re-register after B. The new server
  process sees B as the older instance and marks it retired. B stops pulling, hands back and exits;
  A was already retiring and exits too. No relay is left at the site and its tasks wait upstream
  until an operator starts one (on the base, B stayed connected under `relay-topology` R5 and would
  have taken the site back). The same follows when a retiring relay is evicted by an outage and
  re-registers. GOAL's outcome is that the newest instance holds the site throughout, and R9 keeps a
  relay that is not superseded connected; the server cannot tell process age after a restart, and
  it chooses a relay already retiring to hold the site.
- **Evidence:** `probe_restart_order.py` (above), run with `uv run python -m pytest -p tests.conftest`:
  A logs "Superseded" before the restart; the server is stopped by SIGTERM with A's process group
  stopped, started again on the same port and database, and A resumed once B has registered. The
  new server logs `Relay 7ddb8084-… on … retired at site A (held by 2313c6e0-… on …)` (B retired,
  held by A); B logs `Superseded at site A by a newer relay - retiring`; both relays exit 0, and
  `hs list --remaining --count` reads `2` with no relay left at site A.
- **Touches:** R1 / the Outcome ("the newest instance holds the site throughout"), R9, R7.
- **Deferral considered, not applied.** The holder rule (registration order, `relay-topology` R4)
  predates the diff and the non-goals keep it, but the stranding comes from this diff: on the base
  a relay never exits for being the "older" one. The human may rule otherwise.

### Human-gate triggers

- Neither finding sits in a high-blast-radius file or touches a security/DB-lifecycle invariant: RV1's
  locus is `relay/__init__.py`, RV2's is `cluster/topology.py`. RV1 rests on `server.py`'s scheduler
  and `core/fsm.py` halting between states; a remediation that edits either brings it under the
  gate.
- No PLAUSIBLE findings.

## Review cycle 2 — approved (2026-11-13)

- **Reviewed commit:** 824e19f1f96f1b8343cd9b4bec0ab2232d5c5865  ·  **Base:** `origin/develop` `fb89670`  ·  **Date:** 2026-11-13
- **Verdict:** approved
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh blind pass over the full spec-excluded diff, plus a check of RV1 and RV2 against
  the cycle-1 head (`f98614f`). No `debate`, no `completeness` sub-pass. As in cycle 1, `hs-review`
  was spawned as a fresh subagent with only the foreman's task statement and ran the correctness pass
  in that context (the skill's no-subagent fallback). It read `GOAL.md` (with the cycle-1 rulings),
  this file's cycle-1 section (to grade RV1 and RV2), the spec-excluded diff, `AGENTS.md`, the base
  copy of `invariants.md` and the rubric, `TECH.md`'s frontmatter for `base`/`kind`/`status`, and the
  usage header of `spec/relay-retire/drive/retire_drive.sh` to run it. It did not read `PLAN.md`, the
  body of `TECH.md`, `research/` or `evidence/` prose; `META.md` was opened only after the verdict
  was set. Probes were scratch files outside the repository; every process they started was stopped
  by its test's process-group teardown or by its recorded PID.

### Pre-flight

- **Base.** `TECH.md` records `base: develop`; graded against `origin/develop` (`fb89670`, the merge
  base), not a local `develop` ref.
- **Build state.** `TECH.md` reads `status: in_review`, P1–P4 `done`, `review.cycle: 1`.
- **Contract drift.** Since cycle 1, `GOAL.md` gained `a1fcb8c`, the foreman's "Ruled at review
  cycle 1" clarification (fix RV1 and RV2; the RV2 residual case accepted). Graded as binding.
- **Invariants.** The branch edits `.agents/factory/invariants.md` (§11) and `AGENTS.md`. The code
  was graded against the base copy (`git show origin/develop:.agents/factory/invariants.md`); the
  edits were graded as part of the diff (below).

### Verification run

- **Targeted suite (SQLite):** `uv run pytest -q tests/test_signal_wake.py tests/test_relay_retire.py
  tests/test_retire_judge.py tests/test_topology_plan.py tests/test_topology_server.py
  tests/test_dispatch.py tests/test_dispatch_version.py tests/test_contended_write.py
  tests/test_relay_fsm.py tests/test_relay_topology.py` → **342 passed, 12 skipped** (the skips are
  the PostgreSQL-only contended writes).
- **Full suite (SQLite):** `uv run pytest -q tests` → **2055 passed, 95 skipped**, 2 warnings (the
  existing `fork()` deprecation warnings in `tests/test_logging.py`), 19 min, CPython 3.14, run
  while the probes below ran beside it.
- **PostgreSQL / TimescaleDB (local server):** `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community uv run
  pytest tests/test_upgrade_postgres.py tests/test_writeback_postgres.py tests/test_timescale.py
  tests/test_contended_write.py tests/test_zone_postgres.py` → **135 passed, 1 skipped, 29 failed**;
  all 29 are the expected missing templates (20 `hs283`, 9 `hs290a1`, "not listed in
  `HS_TEST_POSTGRES_TEMPLATES`"). With `HS_TEST_TIMESCALE=` (plain PostgreSQL),
  `tests/test_contended_write.py` → **12 passed**, the four hand-back revert tests among them.
- **Docs:** `UV_PROJECT_ENVIRONMENT=<tmp>/docs-venv uv run --python 3.12 --group docs sphinx-build -W
  --keep-going -q -E -b html docs <tmp>` (sandbox off for `sphinx_sitemap`'s socket) → **exit 0, no
  warnings**; the worktree's `.venv` stayed on CPython 3.14.
- **Laptop drive (throwaway sites):** `.agents/factory/bin/temp_site.sh sh
  "$PWD/spec/relay-retire/drive/retire_drive.sh" MODE` for `retire`, `retire-red`, `pair-relay`,
  `pair-server` and `idle` → **all five PASS, exit 0**. `retire`: A's three unstarted tasks
  available upstream at +1.230 s of its first "Superseded" line (bound 3 s, heartrate 1 s), the
  requeue logged at +1.020 s; `retire-red` (every role on `fb89670`) strands as the seed describes;
  `pair-relay` (relay A on `fb89670`) and `pair-server` (server on `fb89670`) behave as before, with
  no topology read error and no hand-back; `idle`: no relay retires, both stay connected at zero
  clients until drained.
- **Cluster record (builder's, re-judged only):** `uv run python spec/relay-retire/drive/retire_judge.py
  spec/relay-retire/evidence/cluster --require retire` → **PASS** (`R7.bound`: +1.193 s of the mark
  against 6 s). The record is of `29c9bc7`, before this remediation; it was not regenerated (no
  cluster tools). R7 at this head rests on the laptop end-to-end tests and the `retire` drive.
- **Wire (R8, the new heartbeat field):** a branch `Heartbeat` with `retiring=True`, packed, read by
  the base's `Heartbeat.unpack_or_none` (`PYTHONPATH` on an export of `origin/develop` `src`) →
  parses, `retiring` ignored; a beat that is not retiring packs no `retiring` key, so it is the
  base's text.
- **Red-before-green of the remediation.** The builder's new tests cannot run against the cycle-1
  source (their modules import `retiring_instances` and build `SiteRecord(retiring=…)`), so two
  scratch probes, independent of them, were run against an export of `f98614f` `src` on `PYTHONPATH`
  and against this head:
  - `test_probe_rv1.py` (RV1): a real `SchedulerThread` on a real queue server's dispatcher, over a
    relay database holding eight received tasks. A site-client request (`Dispatcher.request`) takes
    2 of the 3 the scheduler staged, leaving 1 in the pool; the scheduler's next load of 3 is held
    (a hook on `Task.next`) until `machine.halt()` lands, so it halts between that load and its
    staging; then `RelayThread.retire()`. **Cycle-1 source:** 3 handed back, 5 held (the loaded
    chunk stranded). **This head:** 6 handed back (the pooled one, the loaded three, the two never
    loaded), neither of the 2 the site client took, 2 held, nothing staged.
  - `test_probe_rv2.py` (RV2): an upstream server autoscaling with relay sites and relays A and B at
    one site as processes; A reads that it is superseded, A is paused, the server is restarted on
    the same port and database, and A resumes only once B has registered with the new server.
    **Cycle-1 source:** the new server logs B retired (held by A), B logs "Superseded", both relays
    exit and 4 tasks remain with no relay at the site. **This head, four runs:** each time the new
    server logs A retired (held by B) and B never reads a mark; A exits 0. In three runs B stays
    connected and every task completes. In one run B's upstream puller raised a `RemoteError`
    (`KeyError` on a proxy id) as the server restarted, and B ended with exit status 5: a
    pre-existing defect outside this diff (observation O1 below), not the holder rule.

### RV1 and RV2

- **RV1 — fixed.** `retire()` no longer reverts the staged rows itself; `hand_back()` first calls
  `withdraw()`, which does nothing while the relay-local scheduler runs and, once it has stopped,
  takes back the staged rows (`ServerThread.revert_staged`) and the chunk it loaded and never staged
  (`scheduler.machine.loaded`, reverted with the conditional `Task.revert_staged`), once. Halting is
  checked only between states (`core/fsm.py` `next`), and `pack_bundle` empties `loaded` in the
  same state that stages it, so after a stop `loaded` holds exactly the rows in no pool and with no
  site client; a row a site client's request cut keeps its local `schedule_time` and is never
  listed by `store.unstarted`. A scheduler slower than `STOP_TIMEOUT` leaves the hand-back due, tried
  at each check in `wait_site` and `stop_site` and once more before `finish_upstream`, so no
  hand-back runs while the scheduler can still stage. Evidence: `test_probe_rv1.py` above; the
  builder's `test_retire_hands_back_the_chunk_the_scheduler_loaded_but_never_staged` and
  `test_retire_hands_back_nothing_until_the_scheduler_has_stopped` pass. The fix stays in relay code
  (`relay/__init__.py`), as the cycle-1 ruling requires; the scheduler and `core/fsm.py` are unchanged.
- **RV2 — fixed, per the ruling.** The relay's beats carry `retiring` under every identity from the
  first beat after it reads the mark (`UpstreamHeartbeat.submit`); the server records it, sticky
  for the life of the record (`SiteRegistry.record`, through the one-line `HeartMonitor.record_site`
  change in `server.py`); `_arrange` never picks a retiring instance as holder, so it never makes
  another instance a duplicate's `older`; and `retired_identities` names every retiring instance, so
  the server marks it again at once. A relay of an earlier release never says `retiring` and is
  ordered as before; a base server ignores the field (wire check above). Eviction and
  re-registration: a re-registered retiring relay is a new record that says `retiring` on its first
  beat, so it never holds. The residual case (a restart before either relay read the mark) keeps
  registration order and one holder, as accepted. Evidence: `test_probe_rv2.py` above; the
  builder's `test_a_retiring_relay_registering_again_after_an_upstream_restart_keeps_the_newer_relay`
  passes.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/topology.py` (`_arrange` excludes retiring instances from holding, `retiring_instances`, `retired_identities`, `note_retired`/`note_retiring`), `core/topology.py` (`SiteRecord.retiring`, `Topology.retired`, `reply_text`), `server.py` `record_site` | `test_topology_plan.py`, `test_topology_server.py`; restart probe and end-to-end restart test; `warn_duplicates` unchanged | ✅ (RV2 fixed) |
| R2 | `relay/upstream.py` (puller `gate`/`request` check `retired`; `fetch` logs once) | `test_relay_fsm.py`, `test_relay_topology.py`; end-to-end tests assert one "Superseded" line | ✅ |
| R3 | `relay/site.py` (`SiteScaler` launches nothing once retired), `relay/__init__.py` (`retire` stops fleets, ends no client) | End-to-end tests; `retire` drive | ✅ |
| R4 | `relay/__init__.py` (`retire`, `withdraw`, `hand_back`), `relay/store.py`, `core/dispatch.py`, `server.py` `revert_handed_back`, `data/model.py` `revert_handed_back` | RV1 probe; hand-back and contended tests on SQLite, PostgreSQL, TimescaleDB; `retire` drive | ✅ (RV1 fixed) |
| R5 | `store.unstarted` lists only rows with no local `schedule_time`; `withdraw` touches only pooled and loaded rows | RV1 probe (the two taken rows are never handed back); end-to-end tests run each task once | ✅ |
| R6 | `finish_upstream` after `retire` | RV1 builder test (final heartbeat once the taken task's result is returned); end-to-end tests: final heartbeat, exit 0 | ✅ |
| R7 | All of the above | End-to-end tests and the `retire` drive at this head; cluster record (pre-remediation) re-judged PASS | ✅ |
| R8 | `reply_text` adds `retired` only for a retired identity; a beat packs `retiring` only when set; `TOPOLOGY_PROTOCOL` stays 1 | Wire checks (cycle 1's topology reply, this cycle's heartbeat); `pair-relay` and `pair-server` drives | ✅ |
| R9 | Every new path is gated on `retired`/`retiring`/`handback_due` | Full suite; `idle` drive | ✅ |
| R10 | `docs/cli/relay.rst`, `docs/cli/server.rst`, `docs/_include/relay_desc.rst`, relay `APP_HELP`, `versionchanged` | Read; docs build `-W` clean | ✅ |
| R11–R15 | `core/thread.py` `join_waking` and its call sites (unchanged since cycle 1) | `tests/test_signal_wake.py` in the targeted and full suites; cycle 1's red-on-base run stands | ✅ |

**The branch's invariant edits.** The `invariants.md` §11 addition ("a relay whose beats say
`retiring` never holds a site or causes another to be retired, whenever it registered") and the
matching `AGENTS.md` sentence describe `_arrange` and `retired_identities` as built, and the two files
stay in lockstep. `AGENTS.md`'s "pinned four-method surface" matches `DISPATCH_EXPOSED` and
`tests/test_dispatch_version.py`.

Also checked and clean:
- **Requeue SQL, hand-back bounds, signal wake.** Untouched by the remediation (`data/model.py`,
  `core/dispatch.py`, `core/topology.py`'s hand-back reader, `core/thread.py`); their tests pass on
  all three providers.
- **Tags.** All 63 new tests are tagged `unit` or `integration` (the four new contended tests by the
  module's `pytestmark`); no existing assertion was removed.

Unmapped changes (possible scope creep): none.

### Findings

None.

### Observations (outside this diff, not graded)

- **O1 — An upstream link role's first call after the upstream server restarts at the same address
  can raise `RemoteError` instead of `LinkDown`.** `multiprocessing` proxies share one connection per
  thread and address, so a role's new worker thread, making its first call on a client from before
  the restart, dials the new server with a proxy id the old one minted (`KeyError` in
  `serve_client`); `UpstreamLink.call` (`core/reconnect.py`) raises it as a non-transport error.
  `probe_link_restart.py` (an `UpstreamLink` on a `QueueServer`; the `heartbeat` role uses
  `client.dispatcher`; the server is stopped and started again on its port; then the first call of
  another role) reproduces it every time on `origin/develop` (role `puller`, `dispatcher.request`)
  and on this head (role `handback`). In the puller it ends the relay (exit status 5, the one RV2
  probe run above). In the new `handback` role it is caught: the hand-back is refused and the tasks
  are left to eviction, though a restarted server has already requeued them at start
  (`revert_interrupted`). A single relay survived four restarts in each of three runs (base once,
  this head twice), and two relays the base's restart scenario three times, so the window is
  narrow. Predates this cycle and is outside its GOAL; worth a seed.

### Human-gate triggers

- No CONFIRMED finding, so the rubric's mandatory gate is not triggered. For the record: the
  remediation's only `server.py` change is `HeartMonitor.record_site` passing the beat's `retiring`
  to the registry (no scheduler, lifecycle or SQL change), and the diff as a whole touches other
  high-blast-radius files (`data/model.py`, `client.py`, `core/queue.py`, `core/thread.py`,
  `cluster/remote.py`, `cluster/ssh.py`), each graded clean in cycle 1 and unchanged since.
- No PLAUSIBLE findings.
