# REVIEW — Relay hardening for 2.9.0

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-14)

- **Reviewed commit:** a7413400fe513991a0fb414f8b007474dfc3b824  ·  **Base:** `origin/develop` `6c46cbd` (merge base `2ffb5cc`)  ·  **Date:** 2026-11-14
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** one fresh blind reviewer (no `debate`, no `completeness` sub-pass). It got `GOAL.md`,
  the base `invariants.md` (`git show origin/develop:.agents/factory/invariants.md`) and the rubric
  inline, and two diff commands: the spec-excluded `git diff origin/develop...HEAD -- . ':(exclude)spec/'`,
  and `git diff origin/develop...HEAD -- 'spec/*/drive/*'` for the drives and the red check that R9
  grades. It was told not to read `PLAN.md`, `TECH.md`, `research/`, `META.md` or `evidence/`. The
  maintainer's gate rulings went to it as readings of the contract (below). The orchestrator graded
  the one part of the contract that names `TECH.md` (R8's recorded outcome), sanity-checked every
  finding against the source, and dropped one.

### Pre-flight

- **Base.** `TECH.md` records `base: develop`; local `develop` equals `origin/develop` (`6c46cbd`).
  The branch was cut at `2ffb5cc` (2.9.0a8), the merge base.
- **Build state.** `TECH.md` is `status: in_review`, P1–P8 done.
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/relay-hardening/GOAL.md` lists
  `26b6543` (shape) and `95f8740` (GOAL-gate rulings and lock), both before the plan (`e935394`) and
  the first build commit (`0894c14`). The contract did not move mid-build.
- **Contract readings applied (maintainer's gate rulings).** (1) R10's exemptions cover R1, R2,
  R5's one extra per-zone request (the three `tests/test_relay_connector.py` request-list assertions
  now `['cpu', 'gpu']`) and R7. (2) The two R8 fixes, the relay returning at once when its site has
  nothing left to run and the shutdown reply no longer waiting out the heart monitor's 2 s read,
  were pre-authorised provided shutdown order is kept and the existing suites pass; they are graded
  under R10 on those terms. (3) R8 may close under outcome (b) with a narrowed seed. (4) R2's
  contended-only TimescaleDB leg is shown by CI's TimescaleDB 2.31 jobs, not locally (2.32).
- **Invariants.** The code is graded against the base `invariants.md`; the branch's edits to it and
  to `AGENTS.md` are graded as part of the diff.

### Verification run

- `uv run pytest -q -p no:cacheprovider` (SQLite, CPython 3.14) → 2106 passed, 101 skipped.
- `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community uv run pytest tests/test_contended_write.py
  tests/test_timescale.py tests/test_zone_postgres.py tests/test_writeback_postgres.py` → 140 passed,
  1 skipped, 1 failed: `test_legacy_database_upgraded_not_converted` needs an upgrade template
  (Docker), expected.
- The same four files with `HS_TEST_TIMESCALE=` (plain PostgreSQL leg) → 124 passed, 16 skipped,
  2 failed: `test_server_matches_entry` and `test_absent_fails_before_schema` fail because the local
  server offers TimescaleDB 2.32 with `HS_TEST_TIMESCALE` empty; both fail the same way on `2ffb5cc`.
  Environmental.
- `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community sh spec/relay-hardening/drive/red_check.sh` →
  32 of 32 red, exit 0. Each node was then run on `2ffb5cc`'s exported `src` and its failure read
  (RV3).
- Docs: `UV_PROJECT_ENVIRONMENT=$TMPDIR/… uv run --python 3.12 --group docs sphinx-build -W
  --keep-going -q -E -b html docs $TMPDIR/…` → exit 0.
- Drives, `.agents/factory/bin/temp_site.sh sh spec/relay-hardening/drive/hardening_drive.sh MODE`:
  - `tls-on` and `tls-off`, against this build's server and against a `2ffb5cc` server: PASS. The
    relay exits 5 in about 1.1 s with one CRITICAL line naming its own setting, starts no
    relay-local server and launches nothing; the upstream server stays up and logs no error from the
    probe.
  - `restart`: PASS. Three server starts, the relay exits 0 (never 5), 60 of 60 tasks complete once.
    No `unknown proxy id` line was logged: this run did not take the stale-proxy path end to end,
    which the GOAL expects of a narrow window. R3 rests on the unit harness.
  - `zones` and `zones-nodb`: PASS. The gpu zone took one task 1 ms after cpu heard the shutdown;
    every task ran once in its own zone; both processes exit 0.
  - `pair-relay` (`2ffb5cc` relay, this server) and `pair-server` (`2ffb5cc` server, this relay):
    40 of 40 tasks complete once, both processes exit 0. The judge printed FAIL on its "ran the
    earlier build" check only, which is RV4.
- Probes outside the tree (`uv run python -m pytest -p tests.conftest <probe>`):
  - `revert_orphaned` over 251 rows (three passes of 100) reverts every row of the evicted client
    and leaves another client's row and an unconfirmed row alone. The not-eager retry path stamps
    its rows.
  - With TLS, a certificate that does not verify is still a fatal `SSLCertVerificationError`, a
    fingerprint mismatch is still `TLSError`, and a wrong key (TLS or cleartext) is still
    `AuthenticationError`; none becomes `TLSMismatchError`. Matching settings connect in 0.03 s (TLS)
    and 0.006 s (cleartext).
  - `revert_all` and `confirm_all` (strict) still raise `StaleDataError` on a gone row (RV2).
  - `plan_sites` with one gpu task remaining, sites B (rank 0) and A (rank 1) (RV1).
- Orchestrator, R8: `uv run python spec/relay-hardening/drive/tail_judge.py --budget 10 --outcome
  spec/relay-hardening/evidence/laptop` → 10 runs (budget 10 of 10), `outcome b: holds`, PASS.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `data/model.py`: `revert_orphaned` reverts each row by one conditional UPDATE (`__write_rows(held_by=)`, the `held` condition: scheduled, no `completion_time`, that `client_id`), each row tried once per eviction | `test_relay_return.py::test_eviction_revert_skips_a_row_collected_meanwhile`, `::test_eviction_revert_keeps_a_result_committed_meanwhile` (the primary-server case); `test_contended_recovery.py::test_contended_revert_*` (3); PostgreSQL and TimescaleDB `test_contended_write.py::test_contended_orphan_revert_*` (3); the 251-row probe; `restart` drive. Red on `2ffb5cc` for the defect (`StaleDataError`; the result wiped) | ✅ |
| R2 | `data/model.py`: `Task.next` stamps each selected row by one UPDATE conditional on `schedule_time IS NULL` (re-run under contention, a row that never lands left unscheduled) and returns only the rows stamped | `test_relay_return.py::test_dispatch_skips_a_row_deleted_meanwhile`, `::…_cancelled_meanwhile`; `test_contended_recovery.py` dispatch tests (5); `test_contended_write.py::test_contended_dispatch_lands`, `_skips_cancelled_row`, `_skips_deleted_row` on PostgreSQL and TimescaleDB 2.32. The 0-row-under-lock leg is CI's (reading 4). Red for the defect in `test_relay_return.py` and on PostgreSQL; harness-red only in `test_contended_recovery.py` (RV3) | ✅ |
| R3 | `core/reconnect.py`: `stale_proxy_id` / `is_stale_proxy` (a `RemoteError` whose traceback ends in `KeyError: '<hex>'`) counts as a transport error; `describe` names it a restart | `test_relay_link.py::test_first_call_after_an_upstream_restart_is_a_lost_link[puller, heartbeat, returner, handback]`, `::test_stale_proxy_is_a_transport_error`. Red on `2ffb5cc` (`RemoteError`). A lost hand-back stays due and is tried again (`relay/__init__.py` `hand_back`) | ✅ |
| R4 | `relay/upstream.py`: `first_connect`, `speaks_tls`, `speaks_plain`; `core/reconnect.py`: the `first_connect` hook, used only until the link first connects, and `TLSMismatchError` (a `TLSError`, so fatal); `RelayApp` maps it to exit status 5 | `test_relay_workload.py::test_relay_exits_at_once_when_its_tls_setting_is_not_its_upstream_servers[both]` (red on `2ffb5cc`: still running at 12 s); `test_relay_link.py` TLS tests (both directions, a later loss retried, an unreachable start retried); `tls-on`/`tls-off` drives; TLS probes | ✅ |
| R5 | `relay/upstream.py`: `finish_idle` removed; once a zone has heard the shutdown, a zone with no site client and nothing held asks for one task; docstrings restated | `test_relay_connector.py::test_a_zone_without_site_clients_takes_what_is_staged_for_it_at_a_shutdown[database, no-database]` (red on `2ffb5cc`: the puller ends), `::…ends_at_once_with_nothing_staged_for_it`; `zones`, `zones-nodb` drives | ✅ as written; RV1 (plausible) |
| R6 | `docs/_include/relay_desc.rst` | read; docs build `-W` | ✅ |
| R7 | `server.py`: `HeartMonitor.received`, the monotonic time each last beat was received, dropped with the beat on finish and eviction; `check_clients` ages by it; `relay.rst` limitation removed; `server.evict` reference restated with `versionchanged` | `test_server_fsm.py::TestHeartMonitor` clock tests (3); `test_relay_roster.py::test_relay_with_a_clock_behind_is_not_evicted`. Red on `2ffb5cc` (evicted at once, or never) | ✅ |
| R8 | Outcome (b). `TECH.md` P7/P8 record ten laptop runs at the R8 size (the whole budget), the tail and nine segments of each, the log windows kept in `evidence/laptop/`, the causes named (the designed chain of waits; the site client's own bundle wait; the scheduler's back-off) and the reason no cluster job ran; narrowed seed `issues/relay-shutdown-tail.md` (`unshaped`) with its `ROADMAP.md` entry. Fixes (i) and (ii) landed | orchestrator: `tail_judge.py --budget 10 --outcome` (above); seed read against `relay/site.py` and `server.py`. Fixes: `test_relay_fsm.py::test_returner_sends_at_once_when_the_site_holds_no_unfinished_task`, `test_server_fsm.py::TestHeartMonitor::test_signal_from_the_server_finishes_dispatch_at_once` | ✅ |
| R9 | the tests above; `spec/relay-hardening/drive/red_check.sh` + `red.txt` | red check 32/32; each node read on `2ffb5cc` | ✅ (RV3, RV4) |
| R10 | — | full SQLite suite and the PostgreSQL/TimescaleDB legs pass but for the environmental failures; changes to existing tests are the sanctioned ones (the `['cpu', 'gpu']` assertions, the R7 aging tests now reading `received`, an additive `keys=` on the contention harness); both pair drives against a `2ffb5cc` peer complete. Fix (ii) keeps §6's order (submitter → scheduler join → dispatch finish → heart monitor join → staged revert → sentinels); for a plain `hs server`, `hs cluster` or `--no-db` server the only change is that dispatch finishes without waiting for the heart monitor's next turn | ✅ |

Unmapped changes (possible scope creep): none beyond the two pre-authorised R8 fixes, which stay
within reading (2).

**Invariant edits.** The `AGENTS.md` edit is accurate: it adds the eviction revert and the dispatch
stamp to the §1 pattern and drops the stamp from what does not follow it yet; it weakens nothing. The
`invariants.md` edit adds the same two entries accurately and one sentence that is not (RV2).

### Findings

#### RV1 [MEDIUM/PLAUSIBLE] R5's one-task request can hold a task at a site the topology never gives a client in that zone
- **Where:** `src/hypershell/relay/upstream.py:505-513` (the gate's ask for a zone with no site
  client) and `:532-536` (the task held "until one runs them").
- **Failure scenario:** an upstream server autoscaling with relay sites; relay A serves cpu and gpu
  but the plan puts gpu at relay B (fill order, or the tie-break of spread), so A's gpu target is 0.
  At a shutdown or drain with gpu rows still staged, A's cpu zone hears the shutdown, and A's gpu
  zone (no client, nothing held) asks for one task and takes it whenever B is above its low-water
  mark. That task is then the zone's only remaining work. The planner places clients by site
  preference, not by where a task is held, so A's gpu target stays 0, and A's `SiteScaler` launches
  nothing at a target at or below what runs (`relay/site.py:263-283`). A never runs the task and
  never ends its gpu requests; the upstream server waits for both until A is stopped. Before this
  branch, `finish_idle` ended A's gpu zone at the shutdown and B drained the pool. A relay without a
  topology is not affected: its fleet's `min` is at least 1 (`relay/__init__.py:254`).
- **Evidence:** a probe calling `cluster/topology.py::plan_sites` with one gpu task remaining,
  sites B (rank 0) and A (rank 1), bounds min 0, init 0, max 2: under FIXED and DYNAMIC, SPREAD and
  FILL, A's gpu target is 0 both while B runs a client and after it exits. The ask itself is the
  branch's own `test_a_zone_without_site_clients_takes_what_is_staged_for_it_at_a_shutdown` and the
  `zones` drive ("holding 1 task for zone gpu"). Not reproduced end to end with two relays.
- **Touches:** R5 (its premise that the zone gets a site client), R10 (a relay and a non-forever
  upstream server still finish).
- **Triage:** whether this needs a fix this cycle (a held task counted where it is held, a hand-back
  of a task no site client in the zone can take, or no ask at a site whose plan gives the zone 0) or a
  seed is the maintainer's call. A two-relay drive (the `relay-topology` fill harness, with A's
  other zone finishing first) would settle it.

#### RV2 [LOW/CONFIRMED] The new §1 sentence "a row gone or no longer qualifying is skipped, never raised on" is false for two members of the same pattern
- **Where:** `.agents/factory/invariants.md:48-49`.
- **Failure scenario:** the sentence reads as a rule of the whole pattern list, which names
  `Task.confirm_all`/`revert_all`. `confirm_all` (strict by default, the primary server's Confirm)
  and `revert_all` raise `StaleDataError` on a gone row by design (`model.py:1044-1062`). A plan or
  review gating against the checklist would read raising there as a §1 violation, or pass a change
  that silently skips gone rows on a primary server's confirm. `AGENTS.md`, the ground truth, has no
  such sentence, so the checklist has drifted from it.
- **Evidence:** probes `test_revert_all_still_raises_on_gone_row` and
  `test_confirm_all_strict_still_raises_on_gone_row` pass on the head; `grep -n "never raised on"
  .agents/factory/invariants.md AGENTS.md` matches `invariants.md` only.
- **Touches:** invariant §1 text accuracy.
- **Fix:** delete the sentence, or scope it to the conditional writes (`revert_staged`,
  `revert_orphaned`, the dispatch stamp, `confirm_all` with `strict=False`).

#### RV3 [LOW/CONFIRMED] Eight `red.txt` entries are red on `2ffb5cc` for harness or API reasons, not the defect
- **Where:** `spec/relay-hardening/drive/red.txt`; `tests/test_contended_recovery.py` (the
  injection keyed on `d_id`, `:137`); `spec/relay-hardening/drive/red_check.sh` counts any pytest
  exit 1 as red.
- **Failure scenario:** five `test_contended_recovery.py` dispatch tests fail on `2ffb5cc` because
  the `d_id`-keyed injection never sees the base's ORM flush: `…_lands_on_rerun` (`assert [] ==
  ['UPDATE', 'SELECT', 'UPDATE']`), `…_costs_one_update_per_row` (`assert [] == ['UPDATE', 'UPDATE',
  'UPDATE']`), and `…_exhausted_leaves_row_unscheduled`, `…_skips_a_row_cancelled_meanwhile`,
  `…_skips_a_row_deleted_meanwhile` (the injected miss or change never fires). Two `test_relay_link.py`
  TLS tests fail with `AttributeError: … no attribute 'first_connect'`, and the R8 returner test with
  `TypeError: … unexpected keyword argument 'idle'`. The red check reports all eight as proof.
- **Evidence:** each node run with `PYTHONPATH=<export of 2ffb5cc>/src uv run pytest -x <node>`.
- **Touches:** R9 (test strength). R9 still holds: each defect has a behaviour-red test
  (`test_relay_return.py` dispatch and revert tests, the PostgreSQL `test_contended_write.py` tests,
  `test_relay_workload.py`'s TLS tests, the R7 clock tests). The contended stamp is the one case with
  no red for the defect on any backend here.
- **Fix:** mark the API- and harness-red entries as such in `red.txt` (or drop them), and, if the
  contended stamp should be red for the defect, key the SQLite injection on the statement rather than
  the bind name so the base's flush meets it.

#### RV4 [LOW/CONFIRMED] The pair judge fails every run that uses the documented `DRIVE_BASE_SRC` override
- **Where:** `spec/relay-hardening/drive/hardening_judge.py:242-243` requires `'/base/src/'` in
  the recorded source path; `hardening_drive.sh:54` documents `DRIVE_BASE_SRC` as a supported
  override.
- **Failure scenario:** with `DRIVE_BASE_SRC` set, `pair-relay` and `pair-server` print "FAIL the
  relay ran the earlier build" (and the server's), although the facts record the base source and
  every other check passes.
- **Evidence:** `DRIVE_BASE_SRC=<export of 2ffb5cc>/src` on both pair modes; the facts read
  `source_relay=<export>/src/hypershell`.
- **Touches:** R9/R10 drive tooling.
- **Fix:** judge against the source the drive recorded as the base, not a fixed path segment.

**Dropped on sanity check.** The reviewer's note that `ROADMAP.md` (the contended-writes entry) and
`issues/hypertable-contended-scheduler-and-cli-writes.md` still describe the dispatch stamp as an ORM
flush: the GOAL's Non-goals leave narrowing that seed to `/hs-roadmap` when this cycle lands, and
neither text is in this cycle's diff.

### Human-gate triggers

- Not triggered by a CONFIRMED finding: RV2–RV4 touch the checklist text, the red list and a drive
  judge, not the high-blast-radius core or a security/DB-lifecycle invariant.
- RV1 is PLAUSIBLE and needs the maintainer's triage before the next build cycle closes it or
  seeds it.

### Processes

The reviewer's drives stopped their servers, relays and site clients by recorded PID (cleanup trap);
its probe servers ran in-process and were shut down in `finally`; nothing was killed by name, and a
process listing afterwards showed no HyperShell process from this review. The orchestrator started no
long-running process. `git status --porcelain` was empty on hand-back.

## Review cycle 2 — changes-requested (2026-11-14)

- **Reviewed commit:** ff0973aeea2df99ac856d41afba838a338e9260c  ·  **Base:** `origin/develop` `6c46cbd` (merge base `2ffb5cc`)  ·  **Date:** 2026-11-14
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`. A third cycle that does not converge
  goes to the maintainer.
- **Mode:** a fresh blind pass over the whole spec-excluded diff, the default for a later cycle, not
  one scoped to the remediation. One fresh reviewer (no `debate`, no `completeness`) got frozen
  copies of `GOAL.md`, the base `invariants.md` and `AGENTS.md`, and the rubric, read from a scratch
  directory rather than pasted into its prompt. It also got the two diff commands of cycle 1, and
  the gate rulings below as readings of the contract. It was told not to read `PLAN.md`, `TECH.md`,
  `REVIEW.md`, `META.md`, `research/` or `evidence/`. It was not told what RV1–RV4 said. It was asked
  to give the remediation commit the same scrutiny as the rest of the diff, naming the areas it
  touched. The orchestrator graded RV1–RV4 itself by executed command, graded R8's record, re-ran
  the reviewer's probes, and dropped two of its four findings on sanity check.

### Pre-flight

- **Base.** `TECH.md` `base: develop`; `origin/develop` is `6c46cbd`, merge base `2ffb5cc`.
- **Build state.** `TECH.md` `status: in_review`; review cycle 1 left `changes-requested`. Since
  then: `fcea305` (the cycle-1 rulings, recorded in `PLAN.md`) and `ff0973a` (the remediation).
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/relay-hardening/GOAL.md`
  still lists only `26b6543` (shape) and `95f8740` (GOAL-gate lock). The contract did not move.
- **Contract readings applied.**
  - Cycle 1's four readings: R10's exemptions; R8 fixes (i) and (ii); R8 outcome (b); R2's TimescaleDB leg in CI.
  - The cycle-1 rulings:
    - R5's one-task request is made only by a zone whose fleet would launch a client for it (a
      topology target above 0 at this relay, or no topology). A zone at target 0 ends as before.
      A unit-level test at the puller suffices if a two-relay drive does not fit.
    - Every `red.txt` entry must be red on `2ffb5cc` for the defect itself.
- **Invariants.** The code is graded against the base `invariants.md` and `AGENTS.md`. The branch's
  edits to both are graded as part of the diff.

### Verification run

**Reviewer**
- SQLite: `uv run pytest -q -p no:cacheprovider` → 2116 passed, 101 skipped.
- PostgreSQL legs (`tests/test_contended_write.py`, `test_writeback_postgres.py`,
  `test_zone_postgres.py`, `test_contended_recovery.py`):
  - `HS_TEST_TIMESCALE=community` → 75 passed.
  - `HS_TEST_TIMESCALE=` (the first three files) → 47 passed.
- The `2ffb5cc` copies of the nine modified test files, run against the head → 216 passed, 12 skipped, 7 failed.
  - Every failure is a sanctioned exemption under R10's readings.
  - Four of them age a client by its own clock (R7).
  - Three of them expect no request from the idle zone (R5's one extra request).
- Docs: `sphinx-build -W --keep-going -E` in a separate venv → exit 0.
- Drives (`temp_site.sh … hardening_drive.sh MODE`), all PASS, exit 0: `tls-on`, `tls-off`, `zones`,
  `zones-nodb`, `restart`, and `pair-relay` and `pair-server` with `DRIVE_BASE_SRC` set to a `2ffb5cc` export.
  - As in cycle 1, `restart` hit no stale proxy id end to end; R3 rests on the unit harness.

**Orchestrator**
- `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community sh spec/relay-hardening/drive/red_check.sh` → 24 of 24 red, exit 0.
  - Every line is red by an assertion or by its named error: `StaleDataError`, `RemoteError`, or
    `Failed` (the relay still running).
- `RED_BASE_REV=a741340 RED_LIST=…/red-a741340.txt sh spec/relay-hardening/drive/red_check.sh` → 8 of 8 red.
  - One node's failure, read on `a741340`, is `assert drive(machine) is PullerState.FINAL` → `GATE`: the zone asked.
- `tail_judge.py --budget 10 --outcome spec/relay-hardening/evidence/laptop` → 10 runs, `outcome b: holds`, PASS.
  - The R8 record, the seed and the `ROADMAP.md` entry are unchanged since cycle 1 (`git diff a741340 HEAD`).
- The reviewer's two probes (RV5, RV6), re-run as described under each finding.

### Cycle-1 findings

| Finding | Status | Evidence |
|---|---|---|
| RV1 (MEDIUM, PLAUSIBLE) | **Resolved per the cycle-1 ruling** | See below |
| RV2 (LOW, CONFIRMED) | **Fixed** | See below |
| RV3 (LOW, CONFIRMED) | **Fixed** | See below |
| RV4 (LOW, CONFIRMED) | **Fixed** | See below |

**RV1.** A zone that has heard nothing, has no site client and holds nothing now asks after a
shutdown only while `launches()` says its fleet would start a client. That is true under a topology
that gives the zone a target above 0 at this relay, or when no topology is published
(`relay/upstream.py:492-501`, `:531-539`).
- The fleet reads the same `ZoneRef.from_name(zone).name` from the same view.
- The 8-case unit test, built from a real `plan_sites` plan, is red on `a741340` (8 of 8) and green on the head.
- Docs say it in `relay_desc.rst` and in the `versionchanged` clause of `relay.rst`.
- The residual case, where the target falls to 0 after the zone has asked, is RV5.

**RV2.** The sentence now covers only the conditional writes, and `AGENTS.md` carries the same sentence.
- It matches `__write_rows`: `revert_staged` (pending, not strict) and the `held` and `unscheduled`
  conditions skip a gone row. `confirm_all` (strict) and `revert_all` raise.

**RV3.** The eight entries are dropped.
- `red_check.sh` counts a test red only for a failure in the call, either an assertion or the error
  its line names. Its plugin marks a `ValueError` on `2ffb5cc` as NOT RED.
- The reviewer read every base failure, and all 24 are the defect.
- The checker still counts any assertion in the call as red, whatever made it fail. Its header says
  so, and the list it checks is correct, so this is a limit of the tool, not a finding.

**RV4.** The drive records `base_src`, and `on_base` in the judge compares real paths.
- Both pair modes pass with `DRIVE_BASE_SRC`.

### Requirement → evidence matrix

Only the changes since cycle 1 are listed; the cycle-1 matrix stands otherwise.

| R-ID | Change since cycle 1 | Verified how | Status |
|------|----------------------|--------------|--------|
| R1 | none | revert tests in `test_relay_return`, `test_contended_recovery` and `test_contended_write`, red on `2ffb5cc` for the defect | ✅ |
| R2 | none | dispatch tests in `test_relay_return` and `test_contended_write`, red on `2ffb5cc`; contended landing passes on PostgreSQL and TimescaleDB 2.32 (the 2.31 leg is CI's, per reading 4) | ✅ |
| R3 | none | the four-role restart harness, red on `2ffb5cc` with `RemoteError` ending `KeyError: '<proxy id>'` | ✅ |
| R4 | `PLAIN_CHECK_WAIT` 5 s → 1 s | both directions in `test_relay_workload` and the drives (exit 5 in about 1.1 s); unreachable-at-start still retried | ⚠️ RV6 |
| R5 | `launches()` gates the one-task request | unit tests with and without a database, the 8 topology cases, the `zones` and `zones-nodb` drives | ✅ per ruling; RV5 (plausible) |
| R6 | `relay_desc.rst` sentence on autoscaled sites | read; docs `-W` | ✅ |
| R7 | none | `test_server_fsm` clock tests and `test_relay_roster`, red on `2ffb5cc` | ✅ |
| R8 | none | `tail_judge.py` PASS; fixes (i) and (ii) keep §6 order; seed claims checked against `relay/__init__.py`, `relay/site.py` and `Dispatcher.wait_need` | ✅ |
| R9 | `red_check.sh` and `red_outcome.py`; `red.txt` down to 24 entries; `red-a741340.txt` added | 24 of 24 and 8 of 8 red, each base failure read | ✅ |
| R10 | — | full suites; base copies of the modified tests fail only on exempt changes; both pair drives pass; no wire field added | ✅ |

Unmapped changes (possible scope creep): `PLAIN_CHECK_WAIT` 5 s → 1 s answers no cycle-1 finding.
It is graded under R4 (RV6). Nothing else.

**Invariant edits.** Both edits are accurate, in step with each other, and weaken no rule (RV2 above).
The reviewer found no violation of the base invariants:
- §1: the conditional writes, one UPDATE on a first-time match.
- §4: `revert_orphaned` behind `not in_memory and not no_confirm`.
- §6: shutdown order kept.
- §9: the probes send no key; the connection keeps its configured context and fingerprint check.
- §12: docs in the same commit, markers present, `-W` clean.

### Findings

#### RV5 [MEDIUM/PLAUSIBLE] A task an idle zone took while its target was above 0 is held for good if the target then falls to 0 before a client starts
- **Where:** `src/hypershell/relay/upstream.py:529` (a zone holding a task is passed over) and
  `:531` (`launches()` is asked only of a zone that holds nothing).
- **Failure scenario.**
  - After a shutdown, a zone with no site client asks for one task while the view gives it a target above 0 here.
  - The task is inserted and confirmed.
  - A newer plan then sets the zone's target here to 0 before the fleet has a client running.
    That can happen when the client moves to another site, or while the fleet holds back on a stale
    `seen` count (`relay/site.py:273-275`).
  - The fleet launches nothing at target 0. The gate skips the zone on every pass because it now
    holds a task (`bound == 0`, `backlog == 1`), and never asks `launches()` again.
  - The puller never ends, and a non-forever upstream server waits for the relay until it is drained or stopped.
- **Compared with the base.** `2ffb5cc` ended the zone at the shutdown and took nothing.
- **Compared with RV1.** RV1's case is closed. This is the same hold, reached by a change of target after the ask.
- **Evidence.**
  - The probe used the test module's `finishing_upstream`, `zoned_puller` and `drive`. It set the
    view to target 1, drove the puller, then set target 0. Run with
    `uv run python -m pytest -p tests.conftest -q -s <probe>`.
  - It printed `states [GATE, GATE, GATE, GATE, GATE] … held 1` after 5×200 steps (the reviewer's
    run, re-run by the orchestrator).
  - Not reproduced end to end. A two-relay plan change would have to land inside the fleet's launch window.
- **Touches:** R5 and R10 (the relay and a non-forever upstream still finish).
- **Triage (maintainer).** Fix it this cycle or seed it. A fix could let a zone holding only what
  it asked for at the shutdown ask `launches()` again, and hand the task back at target 0.

#### RV6 [LOW/CONFIRMED] With a 1 s challenge wait, a TLS relay against a cleartext upstream on a slow path retries forever
- **Where:** `src/hypershell/relay/upstream.py:144` (`PLAIN_CHECK_WAIT = 1.0`, 5.0 at cycle 1) and
  `:256`. The wait is `min(PLAIN_CHECK_WAIT, deadline left)`.
- **Failure scenario.**
  - A relay with TLS on connects to a cleartext upstream whose challenge arrives more than 1 s after
    the connection opens, because of a slow network path or a loaded server.
  - The TLS connect fails with `SSLError` as it should. `speaks_plain` then gives up after 1 s on a
    second connection, and the `SSLError` is retried as a transport error.
  - Every later attempt does the same, so the relay never exits. R4's "within one
    `relay.upstream.deadline`" fails on that path, and the behaviour is `2ffb5cc`'s.
- **Not affected.**
  - Neither check can misclassify a matching upstream: a TLS server sends nothing first, and a
    cleartext server never completes a handshake.
  - An upstream unreachable at start is still retried.
- **Evidence.**
  - Setup: a real cleartext `QueueServer` behind a local proxy that delays each forwarded chunk by
    `PROBE_DELAY`, and a TLS-on `UpstreamLink` with `first_connect` (deadline 30 s).
  - Reviewer: 0.2 s → `TLSError` after 0.4 s; 0.6 s → after 1.2 s; 1.1 s → `wait_up -> False`,
    `DOWN`, 15 attempts in 40 s.
  - Orchestrator, at 1.1 s: `WAIT=1.0` → `wait_up -> False; state=DOWN; attempts=14` (40.8 s).
    With `PLAIN_CHECK_WAIT` patched to 5.0, cycle 1's value, the mismatch was found in 2.2 s
    (`TLSError … must agree on TLS`).
- **Touches:** R4.
- **Fix.** Let the challenge wait run as long as a slow path needs, within the deadline left, or
  take the cleartext evidence from the failed handshake itself.

**Dropped on sanity check.**
- *The red checker counts any call-phase assertion as red.* Its header documents this, the entries
  it checks are each red for the defect (read one by one), and no tool can tell what made an
  assertion fail. It is folded into RV3's status.
- *The `relay-shutdown-tail` title, "up to about 5 s late", against the body's "about 7 s".* The
  title measures the delay over the 1.7 s tail with nothing waiting; the body states the tail.
  Both match the evidence. This is wording, not a misstated defect.

### Human-gate triggers

- Not triggered by a CONFIRMED finding.
  - RV6 is in `relay/upstream.py`, outside the high-blast-radius core.
  - It does not touch TLS posture: no cleartext connection is made, and a missed mismatch is retried as before.
- RV5 is PLAUSIBLE and needs the maintainer's triage: fix it in the next build cycle, or seed it.

### Processes

- The reviewer's drives stopped their servers, relays and site clients by recorded PID. Its test
  fixtures and in-process probe servers shut down, and a final process listing showed none of its
  processes left.
- The orchestrator ran only pytest processes (the red checks and two in-process probes), and each
  exited.
- `git status --porcelain` was empty on hand-back.
