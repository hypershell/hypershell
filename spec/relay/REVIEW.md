# REVIEW — HyperShell Relay: two-level scheduling across administrative sites

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 (interim, after P5) — changes requested

- **Reviewed commit:** `d6e9d7d` (P5, the delegation-core checkpoint)  ·  **Base:** `develop`  ·
  **Date:** 2026-11-05
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** interim review of the delegation core (P1–P5; R1–R6, R8, R10, R17, R19, R20), started
  after P5 as TECH's *review checkpoint* allows (plan-gate ruling N5) while P6 built. The `debate`
  variant: two independent blind reviewers, each given `GOAL.md`, the spec-excluded diff
  (`git diff develop...d6e9d7d -- . ':(exclude)spec/'`), `invariants.md` and the rubric, and told
  not to read `PLAN.md`, `TECH.md`, `research/` or `META.md`.
  - **Reviewer A — correctness:** the delegation path (insert before confirm, the late-confirm
    fence, the puller and returner state machines, time stamps), by in-process scripts on scratch
    databases.
  - **Reviewer B — security, lifecycle, compatibility, quality:** the credential fence, process
    lifecycle under failure and signals, configuration, mixed-version peers, by real `hs server`
    / `hs relay` processes over loopback in throwaway sites, plus source mutation of the fence.
- Both reviewers worked on archived copies of `d6e9d7d` outside the worktree; the worktree was
  left clean.

### Verification run

Commands actually executed (the scratch harnesses are not committed; each finding names its
reproduction, and the remediation round turns each into a test):

- Reviewer A: in-process scripts against a scratch relay database and a fake upstream —
  halting the puller while a work request is outstanding; a leaf time in the repeated DST hour
  through insert, collect and return under `TZ=America/New_York` and `TZ=UTC`; late confirmations
  against completed, cancelled, reverted and deleted rows on a real upstream database (no
  finding: the R19 fence held in every case); a relay against `hs server --no-db`.
- Reviewer B: loopback process harnesses (upstream `hs server`, `hs relay -N 2` through a loopback
  launcher) for: the upstream killed mid-run; SIGUSR1 with a site backlog; SIGTERM / SIGINT;
  `[relay] port` and `HYPERSHELL_RELAY_PORT`; `[relay.upstream] auth_env` / `auth_eval`;
  `HYPERSHELL_CONFIG_FILE` holding `[relay.upstream]` and an explicit `[server.tls]` cert; an
  `[export]` variable carrying the key; mixed-version peers (2.8.4, 2.9.0a3, protocol 2) and
  `--no-tls` against TLS. Source mutation of the fence (each scrub rule removed in turn, the
  `[export]` check removed, the fence moved after launch) with the relay test files re-run.
- P5's own gate at `d6e9d7d` (full suite, 3.12) was green, so every finding below is a gap in the
  tests as well as in the code.

### Requirement → evidence matrix (reviewed scope)

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1   | `relay/__init__.py`, `relay/site.py` | process harnesses; lifecycle under failure fails (B1, B2) | ❌ |
| R2   | `relay/upstream.py` (puller, request text) | fake upstream; mixed-version peers exit with the client's version error | ✅ |
| R3   | `relay/store.py::insert_received`, puller INSERT → CONFIRM | halt mid-request drops the bundle (A2); `--no-db` upstream insert fails (A1) | ❌ |
| R4   | `relay/site.py::site_cluster` (`max_retries=0`) | loopback workload with a retry | ✅ |
| R5   | returner, outbox, `mark_returned` after put | outbox scripts | ✅ |
| R6   | `store.result_record` | leaf times lose their offset (A4) | ❌ (LOW) |
| R8   | puller GATE | `-N 0` / size 0 deadlocks (A3) | ❌ (LOW) |
| R10  | `core/config.py` relay database section | process tests; no primary created | ✅ |
| R17  | `relay/site.py::fence_site` | scrub holds; config-file scrub breaks site TLS (B5); `[export]` check untested (B7); `auth_env`/`auth_eval` crash (B4) | ❌ |
| R18  | help snippets, config reference, completions | `[relay] port` ignored (B3); `evict_hint` documented, unread (B8); signal text wrong (B6) | ❌ |
| R19  | `data/model.py` confirm fence | late-confirm scripts: fence held | ✅ |
| R20  | relay test files | gaps behind A1–A4, B1–B7 | ❌ |

Unmapped changes (possible scope creep): none found.

### Findings

Severity per the rubric. All are **CONFIRMED** (reproduced) except A5, which predates the branch.

#### [HIGH/CONFIRMED] B1 — After a thread failure the relay never exits
- **Where:** `relay/__init__.py` (`RelayThread.run_with_exceptions`'s failure path, `RelayThread.stop`,
  `RelayApp.run`).
- **Failure scenario:** the upstream dies mid-run → the upstream heartbeat raises `EOFError` → the
  failure path stops the site cluster once; `RelayApp.run` then calls `RelayThread.stop`, which stops
  it again. The second stop blocks forever putting the shutdown sentinel into the relay-local
  server's full one-slot completed queue.
- **Evidence:** lifecycle harness, scenario "upstream SIGKILLed with two site tasks running": at
  t+3 s, t+10 s and t+40 s the relay is alive with no exit code; a stack dump shows the main thread
  in `queue.put` under the cluster's `stop`, called from `RelayThread.stop`.
- **Touches:** R1, R9's precondition (the relay must end), process lifecycle.

#### [MEDIUM/CONFIRMED] B2 — Site clients outlive a failed relay
- **Where:** `relay/__init__.py` (no site-client clean-up on a failure exit).
- **Failure scenario:** same harness: after the relay is killed or fails, both site clients run on
  with nothing to connect to.
- **Evidence:** the same run lists both `hs client` processes alive at t+40 s.
- **Touches:** R1, R16 (the relay owns its fleet).

#### [MEDIUM/CONFIRMED] A1 — A relay against `hs server --no-db` loses every task
- **Where:** `relay/store.py` (`relay_task.schedule_time` is `NOT NULL`).
- **Failure scenario:** an in-memory upstream stamps no `schedule_time`; the insert raises an
  integrity error three times and the relay exits with `RelayInsertError`; the bundle was cut for
  the relay and is lost (an in-memory upstream has no confirmation to revert).
- **Evidence:** a relay against `hs server --no-db`: the insert fails on the NULL stamp and the
  task is lost.
- **Touches:** R2 (an ordinary client works against `--no-db`), R3.

#### [MEDIUM/CONFIRMED] A2 — Halting the puller mid-request drops the returned bundle
- **Where:** `relay/upstream.py` (`UpstreamPuller` halts at the next state whatever it holds);
  `RelayThread.finish_upstream`.
- **Failure scenario:** `finish_upstream` halts the puller while its work request is outstanding;
  the reply arrives, and the machine goes to HALT without INSERT or CONFIRM. The tasks were cut for
  the relay upstream, are nowhere in the relay database, and `count_held` reads 0, so FINISHED
  goes out and the rows are stranded. A halt between INSERT and CONFIRM likewise leaves inserted
  rows unconfirmed.
- **Evidence:** fake upstream holding the request on an event, `stop(wait=True)` from another thread,
  then release: `tasks cut for the relay: 3 | inserted: 0 | held: 0 | confirmations sent: 0`.
- **Touches:** R3, R5 (FINISHED only when nothing is held).

#### [MEDIUM/CONFIRMED] B3 — `[relay] port` and `HYPERSHELL_RELAY_PORT` are ignored
- **Where:** `core/config.py` (`DEFAULT_RELAY_PORT` reads the built-in default, not the merged
  configuration).
- **Evidence:** `HYPERSHELL_RELAY_PORT=<free>`, no `--port`: the relay logs `Relay-local server ready
  on 127.0.0.1:50002`.
- **Touches:** R18 (the documented setting does nothing), R1.

#### [MEDIUM/CONFIRMED] B4 — `[relay.upstream] auth_env` / `auth_eval` crash the relay at start
- **Where:** `core/config.py` defaults (`relay.upstream.auth`) with the configuration library's
  variant lookup.
- **Failure scenario:** the built-in default `auth` and a file's `auth_env` (or `auth_eval`) are two
  variants of one key; reading `config.relay.upstream.auth` raises `'auth' has more than one
  variant`.
- **Evidence:** a config file with only `auth_env = "MYKEY"` (and one with only `auth_eval`):
  `hs relay … -N 0` exits with that `AttributeError`.
- **Touches:** R17 (the documented ways to keep the key out of files do not work). The same
  collision exists for `[server] auth_env` on `develop`, before this branch.

#### [MEDIUM/CONFIRMED] B5 — The config-file scrub strips site clients of their TLS and client settings
- **Where:** `relay/site.py::scrub_environment` (`HYPERSHELL_CONFIG_FILE` dropped whenever the file
  holds a relay table — which a relay's file always does).
- **Failure scenario:** a relay configured by `HYPERSHELL_CONFIG_FILE` with an explicit
  `[server.tls]` cert: site clients start without the file, fall back to `<auto>` material, fail
  certificate verification, exit, and are relaunched in a loop; no task completes.
- **Evidence:** 25 s run: 12 site-client exits with status 5, `SSLCertVerificationError: … self-signed
  certificate`, 0 of 4 tasks completed upstream.
- **Touches:** R17 (fence), TLS posture of the site hop.

#### [LOW/CONFIRMED] B6 — The documented SIGUSR1/SIGUSR2 drain abandons the site backlog
- **Where:** `docs/_include/relay_desc.rst`; the signal is process-wide, so the relay-local
  scheduler sees it too.
- **Failure scenario:** the doc says the signals stop the relay "in the same way" as an upstream
  shutdown. In fact the local scheduler stops at once, the unscheduled backlog is abandoned, and the
  relay leaves without a final heartbeat, so upstream reverts only on eviction.
- **Evidence:** 20 tasks, `-N 2`, SIGUSR1 with 6+ held: the relay logs `Holding 8 tasks with no
  result returned upstream - not finishing upstream`; upstream keeps the rows in flight until
  eviction.
- **Touches:** R18 (docs), R5.

#### [LOW/CONFIRMED] A3 — Size 0 deadlocks
- **Where:** `relay/__init__.py::check_args` accepts `-N 0`; the gate's bound is then 0.
- **Failure scenario:** no site client is launched, the gate never opens, and the relay waits forever.
- **Touches:** R8, R16.

#### [LOW/CONFIRMED] A4 — Leaf times come back with the relay host's offset
- **Where:** `relay/store.py` (`result_record` gives naive relay-database times the relay's local
  offset); SQLite keeps no offset.
- **Failure scenario:** a leaf reports `01:30-05:00` (the second 01:30 of a DST change). It is stored
  as wall clock `01:30` and returned as `01:30-04:00` (one hour early); under `TZ=UTC` the same
  record returns five hours early.
- **Evidence:** the round-trip script: `TZ=America/New_York` error −1 h; `TZ=UTC` error −5 h.
- **Touches:** R6 (timing reflects the executing client).

#### [LOW/CONFIRMED] B7 — The `[export]` fence check is untested
- **Where:** `tests/test_relay_launch.py`.
- **Evidence:** mutation removing the `[export]` check: every relay test still passes. Driving the
  real path (an `[export]` variable carrying the key) shows the check itself works: the relay
  refuses with `CredentialFenceError`.
- **Touches:** R17, R20.

#### [LOW/CONFIRMED] B8 — `relay.upstream.evict_hint` is documented but read by nothing
- **Where:** `core/config.py`, `docs/_include/config_param_ref.rst`.
- **Touches:** R18.

#### [LOW/CONFIRMED, predates this branch] A5 — The site server's eviction period is fixed at 600 s
- **Where:** `cluster/remote.py` builds the autoscaling cluster's `ServerThread` with no
  `evict_after`, so `[server] evict` never reaches a relay's site (or an `hs cluster` server).
- **Touches:** R9 (a killed site client delays the relay's drain until its local eviction).

**Non-findings (recorded, not blocking).** Behaviour a stock client shares, noted by B:

- A `--no-tls` relay against a TLS upstream hangs silently at connect, as a `--no-tls` client does.
- SIGTERM / SIGINT end the relay without draining, orphaning the running tasks, as they do a client.

### Foreman triage and rulings (2026-11-05)

| Finding | Ruling |
|---|---|
| A1 | **Fix.** Allow a NULL upstream stamp; a result for an unstamped task returns as the stock client would return it; an in-memory upstream has no fence. Test against a `--no-db` upstream. |
| A2 | **Fix.** Once a request is sent, the puller completes that request's INSERT and CONFIRM before it stops; `finish_upstream` decides FINISHED only after the puller has fully stopped, then recomputes `count_held`. Test by a forced interleaving (hook or barrier, no sleeps). |
| A3 | **Fix.** Refuse a size below 1 at start-up with a clear error. |
| A4 | **Fix.** Store leaf times as UTC and return them as aware UTC. Test with a TZ change and a repeated DST hour. |
| A5 | **Defer to P8** (outage and restart); note in TECH under P8. No `cluster/` edit this round: a concurrent security cycle is changing `cluster/remote.py`. |
| B1 | **Fix.** The relay always exits, non-zero on failure, within a bounded time: an idempotent stop sequence (each component once, guarded on liveness) with bounded waits. Test: upstream killed mid-run → relay exits non-zero within 30 s. |
| B2 | **Fix.** On any exit other than a clean drain, terminate every site client the relay launched (the autoscaler's client handles: TERM, then KILL after a bounded grace); `client.py` unedited. Test: no site client survives N seconds after the relay exits. |
| B3 | **Fix.** Read the configured port, as the server does. Test. |
| B4 | **Fix for `[relay.upstream]`** so both documented forms work, with tests. `[server]` is not fixed here; a follow-up public seed covers it (noted in TECH). |
| B5 | **Fix.** Do not drop the variable: give site clients a sanitised copy of the file (`[relay]`/`[relay.upstream]` removed, any value holding the key removed), mode 0600 in the relay's private directory, cleaned up on exit; an unreadable original keeps the current behaviour with a WARNING. Test with an explicit `[server.tls]` cert. |
| B6 | **Fix.** Make the behaviour match the doc (stop pulling, finish everything held, return, FINISHED); failing that without zone edits, make the doc exact and send a final heartbeat. |
| B7 | **Fix.** A test through the real `[export]` path, mutation-checked. |
| B8 | **Fix.** Mark "used from P8" if P8 uses it; otherwise remove it. |

Two follow-up public seeds are recorded in TECH for the foreman to file: the `[server]
auth_env`/`auth_eval` variant collision, and the advertised host ignoring the bind address
(relevant to a future `--advertise`).

### Human-gate triggers

- **Triggered.** B4, B5 and B7 touch the credential fence and the site hop's TLS posture (a
  security invariant); A5 names `cluster/remote.py` (core). The foreman's triage above is recorded;
  maintainer sign-off is still required before `hs-publish`.

### Optional completeness sub-pass

- Not run (interim review; P6–P10 not yet built).

## Review cycle 2 (scoped, P6 and the cycle-1 remediation) — changes requested (2026-11-05)

- **Reviewed commit:** `e84ff17`  ·  **Range:** `d6e9d7d..e84ff17` (P6 `9810598`, remediation
  `e84ff17`)  ·  **Base:** `develop`  ·  **Date:** 2026-11-05
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** scoped, as the foreman directed: P6 reviewed fresh, and each of the twelve remediation
  fixes verified. One reviewer, inline (no subagent): the correctness pass was formed from `GOAL.md`,
  the spec-excluded diff (`git diff d6e9d7d..e84ff17 -- . ':(exclude)spec/'`), `invariants.md`, the
  rubric and the runnable tree; `TECH.md` and the cycle-1 record were read only afterwards, to map
  the fixes and check closure. All runs used an archived copy of `e84ff17` outside the worktree,
  with the tree on `PYTHONPATH`; the worktree was left clean until this record.

### Verification run

- Full suite: 3.12, 1042 passed, 85 skipped; 3.14, 1039 passed, 88 skipped (PostgreSQL-backed
  tests skip). The six relay test files three more times on 3.14: 129 passed each time, no flake (the suites
  ran beside the process harness, so under load).
- Process harness (not committed): a real upstream `hs server --forever` (SQLite, evict 10 s),
  `hs relay -N 2` through a loopback launcher that records each site client's pid, environment and
  configuration file, and tasks that record their own pid. Scenarios: upstream SIGKILL; upstream
  SIGSTOP then SIGCONT; SIGUSR1, SIGUSR1 twice, SIGUSR1 then SIGINT, SIGINT twice, SIGTERM, all
  mid-run; SIGUSR1 and SIGINT at 0.15–2.0 s after start; eight drains at 0–4.1 s into a
  400-task run (relay bundle 3, upstream bundle 7) followed by an upstream drain; the relay frozen
  (SIGSTOP) past upstream eviction, then resumed; two relays on one relay database; the key as
  `auth`, `auth_env` and `auth_eval` with `HYPERSHELL_CONFIG_FILE` holding `[relay.upstream]`, an
  explicit `[server.tls]` cert, `[client]`, `[task]`, `[export]`, and the key in a list item, a
  nested inline table and an entry name; relay `TZ=Asia/Kolkata`, leaves `TZ=America/Los_Angeles`,
  upstream `TZ=UTC`; a 2.9.0a4 upstream server with this relay, and a 2.9.0a4 client with this server.
- In-process relay (real `hs server` upstream, real site clients) with one fault injected per run:
  the puller's insert, the relay-local receiver, the relay-local scheduler, the returner, the
  relay-local heart monitor.
- Upstream heart monitor stepped in-process for roster spoofing (below).
- Baseline: a stock `hs client` under server SIGKILL, SIGTERM and SIGINT, to tell inherited from new.
- Mutations (one per copy of the tree, the relevant test files re-run): 19 mutants, 14 killed,
  5 survived: four named under C2-2, C2-3 and the notes, and one equivalent (dropping the SIGTERM
  to a site client leaves the SIGKILL after the grace period to end it).
- Docs: the three changed includes parse clean under docutils. A Sphinx `-W` build was **not**
  run: `sphinx_sitemap` needs an AF_UNIX listener, which the sandbox refuses, and running it outside
  the sandbox was denied by the safety check.

### Requirement → evidence matrix (reviewed scope)

| R-ID | Implemented by (this range) | Verified how | Status |
|------|-----------------------------|--------------|--------|
| R1   | `RelayThread.abort`, `end_site_clients`, `wait_site` | lifecycle scenarios; fault injection | ⚠ C2-1 |
| R3   | puller halts at its gate (A2); nullable stamp (A1) | eight drains; `--no-db` test | ✅ |
| R6   | `UTCDateTime`, `store_utc`, `result_record` (A4) | cross-zone run; mutation | ✅ (wiring unpinned, C2-2) |
| R7   | heartbeat `site`/`roster`, `client.site`/`relay`, roster cascade | stepped monitor; false eviction; mixed versions | ✅ |
| R8   | size ≥ 1 (A3) | `-N 0`/`-1` refused (test) | ✅ |
| R17  | `upstream_key` (B4), `site_config_copy` (B5), `[export]` test (B7) | three key forms end to end; mutation | ✅ |
| R18  | `relay_desc.rst`, `relay_help.rst`, `config_param_ref.rst`, drain handler (B6) | read against behaviour | ⚠ C2-4, C2-5 |
| R20  | the new and changed relay tests | suites; mutations | ⚠ C2-2, C2-3 |

Unmapped changes (possible scope creep): none. `Client.reconnect` now also refreshes `server_id` /
`server_host` for an ordinary client re-registering (issue #29 path); that serves R7 (a relay
re-registering with a restarted upstream must count under it) and changes nothing for a client
re-registering with the same server.

### Findings

#### [MEDIUM/CONFIRMED] C2-1 — A failed relay-local receiver leaves the relay running, and its tasks held, indefinitely
- **Where:** `src/hypershell/relay/__init__.py:368` (`RelayThread.wait_site` watches the cluster
  thread, the puller, the returner and the heartbeat, not the relay-local server's own threads);
  the cause it misses is in `src/hypershell/server.py:1072` (`ServerThread` joins its scheduler
  first, which in forever mode runs until told to stop, so a receiver that has died is never surfaced).
- **Failure scenario:** any exception in the relay-local receiver (for instance an `OperationalError`
  from the relay's SQLite file: disk full, a lock held past the busy timeout) ends that thread. Site
  results are no longer written, the backlog never falls, the puller's gate stays shut, and the relay
  keeps heartbeating upstream, so it is never evicted and nothing it holds is reverted. A held task
  holds its group upstream, so the campaign stalls with no error on either side.
- **Evidence:** in-process relay against a real `hs server` (8 tasks, `-N 2`), `Task.apply_results`
  raising on its second call: 150 s later the relay thread is alive, `hypershell-receiver` is gone
  from the thread list, 0 of 8 tasks are complete upstream, both site clients are alive. The same
  harness with the fault elsewhere exits non-zero with no site client left: scheduler 2.2 s, returner
  4.5 s, puller insert 10.5 s (`RelayInsertError`). A dead relay-local heart monitor degrades (no new
  site registrations) but work goes on (8 of 8 complete).
- **Touches:** R1, R3 (failover rests on the relay ending), and B1's bounded-exit contract. The fix
  can stay in the relay (treat a dead receiver, confirm or heart monitor of the relay-local server
  as a failure in `wait_site`, `stop_site` and `wait_returner`), with no `server.py` edit; a stock
  `hs server` with a dead receiver behaves the same today, outside this scope.

#### [LOW/CONFIRMED] C2-2 — A4's wiring is unpinned: removing `store_utc` from the relay passes every relay test
- **Where:** `src/hypershell/relay/__init__.py:631`; `tests/test_relay_return.py` applies `store_utc`
  in its own fixture, and no process test crosses time zones.
- **Evidence:** mutant without the call: `test_relay_return.py`, `test_relay_db.py` and
  `test_relay_workload.py`, 54 passed. The cross-zone run on that mutant returns each leaf time
  48,604 s (13.5 h) off the instant reported; unmutated, the returned instant is 3.7 s old, as it
  should be.
- **Touches:** R6, R20. A process test with the relay and its leaves in different zones would pin it.

#### [LOW/CONFIRMED] C2-3 — B6's test does not pin the relay's own drain handler
- **Where:** `tests/test_relay_workload.py::test_drain_signal_finishes_everything_the_relay_holds`.
- **Evidence:** mutant without `install_drain_handlers()` (`relay/__init__.py:634`): the drain test,
  `test_relay_db.py` and `test_relay_fsm.py`, 56 passed. In two harness drains on that mutant the
  stock path (the relay-local scheduler stopping on the signal) also completed everything the relay
  held, because the relay-local dispatcher had already staged it; the test's shape cannot tell the
  two apart. The fixed behaviour itself holds (eight drains, every held task returned, FINISHED).
- **Touches:** R18 (the documented drain), R20.

#### [LOW/CONFIRMED] C2-4 — The site's configuration copy outlives a relay ended by SIGTERM
- **Where:** `docs/_include/relay_desc.rst:17` says the copy is "removed when the relay exits";
  `RelayThread.release` runs on a clean drain, a failure and SIGINT, not on SIGTERM or SIGKILL
  (SIGTERM is not captured, by design).
- **Evidence:** SIGTERM mid-run, four runs (three key forms and a workload): the copy is still there
  after exit. Its contents were checked: no key material, mode 0600, overwritten by the next run.
- **Touches:** R18 (docs). Say when it is removed, or remove a stale copy at start.

#### [LOW/CONFIRMED] C2-5 — Public docs name an internal build phase
- **Where:** `docs/_include/config_param_ref.rst:624`, "Not read yet: used from P8 (outage
  ride-through)". The B8 ruling's wording was taken literally; a user reading the reference has no
  P8. TECH has P8 rewrite it; it must not ship as is.
- **Touches:** R18, public-repo hygiene.

### Notes (not findings)

- **Roster from any authenticated peer** (asked). Reproduced: an ordinary client (no `site`)
  heartbeating a roster can reconnect and re-attribute another client's row while that client is not
  beating, and later mark it disconnected while it is beating; a direct re-registration does not
  clear `site`/`relay` (`Client.reconnect` gets no relay fields). No path without a deliberately
  forged heartbeat was found: leaf ids are per process and a relay's identity derives from its own
  instance. Every key holder already has the pickle RPC, so this is not a weakening of the trust
  model. Unpinned: a mutant without the `not in self.beats` filter (`server.py:806`) passes. Optional
  hardening: process a roster only from a beat that names a site.
- **Tasks outlive their site client** when the relay ends it or its server goes away. Inherited: a
  stock client leaves its tasks on server SIGKILL, SIGTERM and SIGINT alike (baseline run); cycle 1
  recorded the same.
- **A stalled upstream** (SIGSTOP) neither fails nor ends the relay: the site keeps working and the
  relay carries on after SIGCONT. No socket timeout, as for a stock client; outage handling is P8.
- **Unpinned hardening in B5:** a mutant that keeps key-named entries in the copy passes (the case was
  checked by hand: dropped). The 0600 mode and the clean-up are pinned.
- **AGENTS.md** says USR1/USR2 "steer every polling FSM toward shutdown"; in a relay process they do
  not reach the stock flag. TECH puts the relay notes in AGENTS.md in P9; they should cover this.

### What held (executed)

- **Lifecycle:** upstream SIGKILL → relay exit 5 in 11 s, no site client left; SIGUSR1 → exit 0, held
  tasks completed, FINISHED; SIGUSR1 twice → exit 0; SIGUSR1 then SIGINT → exit 4 in 6.5 s, "Ended 2
  site clients"; SIGINT twice → exit 4 in 1.9 s; SIGTERM → −15, site clients end on EOF; signals in
  the first 2 s → bounded and clean (at 0.15 s the process predates any handler and dies of the
  default action, as every `hs` command does). The handler leaves SIGINT and SIGTERM alone.
- **A2:** eight drains: every received task completed upstream and returned, none in flight under
  the relay's identity, no row left scheduled after the upstream drained.
- **A4:** the deviation from "return as aware UTC" is sound. The record carries the exact instant
  (+05:30, 3.7 s old), which a PostgreSQL/Timescale upstream stores as such. A SQLite upstream keeps
  the relay host's wall clock (22:11 beside its own UTC schedule stamp of 16:41), the same skew a
  stock client in another zone gives it today; UTC would only move the skew to UTC.
- **B5:** in all three key forms the key is in no copy and no site client's environment; the copy
  is 0600 and named by the site clients' `HYPERSHELL_CONFIG_FILE`; `[server.tls]`, `[client]`,
  `[task]` and `[export]` survive; nested, listed and key-named values are dropped; site TLS verifies
  and the workload completes. A second relay on the same database is refused before it touches the
  first one's copy.
- **P6:** this relay against a 2.9.0a4 server works (leaves are simply not listed); a 2.9.0a4 client
  registers with this server (`site`/`relay` NULL); the upgrade adds both columns nullable (test),
  which on PostgreSQL is a plain `ALTER TABLE … ADD COLUMN` of TEXT and UUID (read). False eviction:
  relay evicted, leaves disconnected and not evicted; on resume relay and leaves reconnected, 12 of
  12 completed, 2 late results refused as stale, drain exit 0. `count_connected` feeds only the
  autoscaler's log; stop and drain go by heartbeats, which leaves never enter.
- **Mutations killed:** the eviction and FINISHED cascades, dropped-leaf disconnect, the relay row
  excluded from `count_connected`, the `server_id` refresh, optional heartbeat fields, the puller
  dropping a reply on halt, the record's zone, the key layer order, the copy's mode, its clean-up,
  `abort`'s idempotence guard, `end_site_clients` as a no-op, and the `[export]` check.

### Cycle-1 closure

| Finding | Status |
|---|---|
| A1 | Closed (`--no-db` test; stamps NULL, every task run once and returned). |
| A2 | Closed (forced-interleaving test, mutation-killed; eight harness drains). |
| A3 | Closed (`-N 0`/`-1` refused, test). |
| A4 | Closed; deviation accepted (above). Wiring unpinned: C2-2. |
| A5 | Deferred to P8 as ruled; unchanged. |
| B1 | Closed for the cycle-1 scenario (exit 5 in 11 s, test bound 30 s) and the puller, returner and scheduler faults. Residual: C2-1. |
| B2 | Closed (site clients ended; mutation-killed). Their tasks: inherited, as noted. |
| B3 | Closed (environment and file, test). |
| B4 | Closed (three forms end to end; layer order mutation-killed). |
| B5 | Closed (above). Residual: C2-4 (docs). |
| B6 | Closed in behaviour; handler unpinned: C2-3. |
| B7 | Closed (removing the `[export]` check fails the new test). |
| B8 | Closed as ruled; wording: C2-5. |

### Human-gate triggers

- C2-1 sits in `relay/__init__.py`; what it misses is `ServerThread`'s (core) wait order, and the
  suggested fix needs no core edit. The cycle-1 trigger (credential fence, site-hop TLS) stands:
  maintainer sign-off before `hs-publish`.
- Cycle 2 of ≤3: a third cycle that does not converge escalates to the maintainer.

## Review cycle 3 (scoped, the cycle-2 remediation) — approved, with follow-ups (2026-11-05)

- **Reviewed commit:** `c872680`  ·  **Range:** `a9d82e7..c872680` (remediation of C2-1..C2-5 and
  the roster guard)  ·  **Base:** `develop`  ·  **Date:** 2026-11-05
- **Verdict:** approved. This approves P1–P6 and the two remediation rounds as built; P7–P10 remain.
  It holds only if the foreman sanctions C3-1 (below). If the foreman does not, C3-1 blocks, and the
  remedy is to take C2-4 back to a docs-only fix.
- **Cycle:** 3 of ≤3. This is the last cycle the run allows. Anything that blocks goes to the maintainer.
- **Mode:** scoped, as the foreman directed. One reviewer worked inline, with no subagent. The findings
  were formed from `GOAL.md`, the spec-excluded diff (`git diff a9d82e7..c872680 -- . ':(exclude)spec/'`),
  `invariants.md`, `AGENTS.md`, the rubric and the runnable tree. `TECH.md` and the cycle-1/2 record
  were read only afterwards, to check closure. Every run used `git archive c872680` copies outside the
  worktree, with the copy's `src/` on `PYTHONPATH`. Each command ran under a process-group kill
  timer. The worktree was untouched until this record.

### Verification run

- **Full suite:** 3.12 with `-n auto`: 1063 passed, 85 skipped (the PostgreSQL-backed tests skip). On 3.14,
  the seven relay files, `test_server_fsm.py` and `test_main.py` with `-n auto`: 246 passed.
- **Fault injection:** real `hs server --forever` upstream (SQLite, evict 10 s), a relay process
  marked a relay before import with one fault patched in, armed by a trigger file once two site
  tasks run, `HYPERSHELL_CONFIG_FILE` holding `[relay.upstream]` so a site copy exists. The faults
  were a scheduler raise, a heart-monitor raise, a heart monitor ending cleanly out of turn, a receiver
  ending cleanly out of turn, a confirm raise, a confirm ending cleanly, and an autoscaler raise.
- **Signals:** SIGTERM at 0.0, 0.3, 0.6, 1.0, 1.5 and 2.5 s after start. SIGTERM then a second
  SIGTERM, and SIGTERM then SIGINT, both with site clients that ignore SIGTERM. SIGTERM during the
  relay thread's own failure abort. `hs server` and `hs cluster` under SIGTERM. One process that
  installs the relay's handler and then runs `ServerApp`.
- **Roster guard:** the upstream heart monitor stepped in-process. Tests: a relay claiming a disconnected
  ordinary client, a relay naming another relay's own row, re-registration after an upstream restart,
  and re-registration after eviction under the same identity.
- **Wire:** this relay against a 2.9.0a4 `hs server` (`git archive 2.9.0a4`) on 8 tasks.
  Heartbeats were cross-unpacked both ways between the 2.9.0a4 and `c872680` `Heartbeat`.
- **Mutations:** one per copy, each run against the test that claims to pin it:
  - `store_utc` removed
  - `install_drain_handlers` made a no-op
  - `check_site()` dropped from `wait_site`
  - `install_terminate_handler()` dropped
  - roster guard disabled
  - `check_site(stopping=True)` dropped from `stop_site`

  **6 of 6 killed.**
- **Flakiness:** the four new process tests were run as eight concurrent copies of the C2-1 test,
  six concurrent copies of the C2-2, C2-3 and C2-4 tests, and once in an `-n 8` batch.
- **Docs:** a Sphinx `-W --keep-going` build, run outside the sandbox (`sphinx_sitemap` needs
  AF_UNIX). That was allowed this time.

### Requirement → evidence matrix (reviewed scope)

| R-ID | Implemented by (this range) | Verified how | Status |
|------|-----------------------------|--------------|--------|
| R1   | `RelayThread.check_site`, `RelayPartEnded`, `wait_site`/`stop_site` | seven real-process faults; unit tests; 2 mutants | ✅ (test flaky: C3-2) |
| R3   | failover through the abort path (no FINISHED) | upstream evicts and reverts in every fault probe | ✅ |
| R6   | `store_utc` wiring pinned | cross-zone test; mutant killed (13.5 h) | ✅ |
| R7   | roster guard in `HeartMonitor.update_roster` | stepped monitor; test; mutant killed; 2.9.0a4 interop | ✅ |
| R17  | site copy removed on SIGTERM/SIGINT | signal probes; test; mutant killed | ✅ (docs: C3-3) |
| R18  | `relay_desc.rst`, `config_param_ref.rst`, `hs relay --help` | read against behaviour; Sphinx `-W` | ⚠ C3-3 |
| R20  | new tests for C2-1..C2-4 and the guard | mutations 6/6; concurrency repeats | ⚠ C3-2 |

Unmapped changes: none. The roster guard is the cycle-2 note's optional hardening, applied to R7.

### Findings

#### [CRITICAL-class by the rubric / CONFIRMED] C3-1 — `hs relay` now captures SIGTERM; AGENTS.md §Signals and invariants.md §8 say it is never captured, and no ruling records the deviation
- **Blocks or follow-up:** follow-up, conditional on the foreman's sanction. Without that sanction it blocks.
- **Where:**
  - `src/hypershell/relay/__init__.py:144-168`: `terminate_handler`, `install_terminate_handler`
    and `end_by_signal`.
  - `:721`: the handler is installed in `RelayApp.run`.
  - `:695-707`: re-delivery in `RelayApp.main`.
  - The texts it contradicts are `AGENTS.md:295` and `.agents/factory/invariants.md:132`:
    "`SIGINT`/`SIGTERM` are deliberately not captured".
- **Evidence:**
  - SIGTERM mid-run and at 0.0–2.5 s after start: the handler runs the abort path (the "Stopping"
    sequence is logged), removes the copy, and the process still dies by SIGTERM (−15).
  - `hs server` and `hs cluster` from the same tree still die by the default action (−15).
  - One process that installs the relay's handler and then runs `ServerApp` in-process gives
    `RelayTerminated` with a traceback and exit 1. The handler is never restored after the relay
    returns. This matters only for in-process reuse, because `hs relay` re-executes into a process
    of its own.
- **Why it is not recorded:** TECH's C2-4 entry calls the change "the signal's semantics kept".
  P9's lockstep item for `AGENTS.md` and `invariants.md` names §10/§11 only. Cycle 2 already routed
  the related §Signals prose (USR1/USR2 never reach the stock flag in a relay) to P9.
- **Why follow-up rather than block:** the code is the intended fix for C2-4. It keeps what the
  invariant protects: no core command captures SIGTERM, nothing goes through `check_signal()`, and
  the relay process still ends by SIGTERM. The fix is text, in files P9 already edits in lockstep.
  The feature does not merge before P10.
- **Remedy (P9):** `AGENTS.md` §Signals and `invariants.md` §8 gain a relay carve-out:
  - SIGUSR1/SIGUSR2 drain through the relay's own handler and never set `RECEIVED`.
  - SIGTERM is caught in `hs relay` only. It ends the relay through its exit path and is then
    re-delivered with the default action.
  - A second SIGTERM ends the process at once.
  - The handler is process-lifetime, one relay per process.

#### [LOW/CONFIRMED] C3-2 — The C2-1 process test is timing-dependent: its fault fires on the site clients' empty bundles, not on "the first result"
- **Blocks or follow-up:** follow-up (P8). The change is to the test only. The behaviour the test
  means to pin held in all seven fault probes, and the fix is one line.
- **Where:**
  - `tests/test_relay_workload.py:522-537`: `FAULTY_RECEIVER_RELAY` raises on every
    `apply_results` call.
  - `:644-650`: the test must see two running site tasks before the receiver dies, and its comment
    says "The first result to reach the relay-local receiver ends it".
  - A site client's collector sends `serialize_tasks([])`, which is `b'[]'` and truthy, every
    bundle wait (`src/hypershell/client.py:417-423`; 1 s here). The receiver then calls
    `apply_results([])`.
- **Evidence:**
  - With the barrier never touched, the faulty relay exits 5 with the injected error and 0 tasks
    completed, in 3 of 3 runs.
  - Of eight concurrent copies, three failed. Two failed with `assert len(clients) == 2`, which got 0
    because the relay had already ended them. One timed out after 240 s waiting for two site tasks,
    because the relay aborted before they were seen.
  - In the `-n 8` batch, one run failed the same way.
  - Sequential runs on an idle host pass. That is why the remediation's "three runs, no flake" saw
    nothing.
- **Fix:** raise only when `results` is non-empty, or arm the fault from a file the test writes
  after it sees the two tasks.

#### [LOW/CONFIRMED] C3-3 — "Only a relay killed outright (SIGKILL) leaves it behind" is not so: a second SIGTERM also leaves the copy
- **Blocks or follow-up:** follow-up (P9 docs). The copy holds no key (0600, checked in cycle 2),
  and the next relay on that database overwrites it.
- **Where:** `docs/_include/relay_desc.rst:19-21` against `relay/__init__.py:146`. The handler resets
  SIGTERM to its default first, by design.
- **Evidence:** SIGTERM, then a second SIGTERM while site clients ignore the first: exit −15, and the
  copy is left. SIGTERM then SIGINT: exit 4, and the copy is removed.
- **Fix:** "killed outright (SIGKILL, or a second SIGTERM while it ends)".

### Notes (not findings)

- **Tasks outlive their site client on abort, as cycle 2 noted (inherited).** This was seen again
  in every fault probe: the barrier tasks were reparented into the relay's process group. The cause
  is that a stock client does not handle SIGTERM (§8). A launcher with a step manager ends them.
- **SIGTERM during the relay thread's own failure abort.** The main thread's `abort()` returns at
  once, because the abort has already started, and the process ends by SIGTERM before the SIGKILL
  escalation runs. Site clients that ignore SIGTERM still exit within 1 s of losing their server, so
  no orphan was left.
- **A dead relay-local receiver makes the abort wait out `STOP_TIMEOUT`** ("Relay-local server did
  not stop within 10.0 seconds - leaving it"). The exit stays bounded: probes exited 0.6–11.6 s
  after the fault, and the C2-1 test bound is 30 s.
- **The roster guard keeps the rule it states.** A roster can still claim a disconnected
  *ordinary* client's row (relay NULL) and mark it connected under the sender. Only a key holder
  forging a heartbeat can do that, which is within the trust model; legitimate flows never produce
  it. P9's "roster on the holder" identity keeps the guard compatible with multi-zone relays. A
  conflicting entry logs a WARNING on every heartbeat.
- **The SIGINT case of the C2-4 test fails if pytest itself runs with SIGINT ignored** (6 of 6 as a
  background job of a non-interactive shell). This is POSIX job semantics, not the relay.

### What held (executed)

- **C2-1 supervision.** In all seven faults the relay exited 5 and left no site client. It sent no
  FINISHED and removed the copy. The upstream evicted the relay and reverted the 2 tasks it held.
  - Fault to exit: scheduler 1.6 s, autoscaler 0.6 s, heart monitor ended out of turn 2.1 s,
    confirm (raise or clean end) 5.0 s, heart-monitor raise 10.2 s, receiver ended out of turn
    11.6 s. The last two are each bounded by the thread's own 10 s wait.
  - The out-of-turn messages name the right pair, for example "hypershell-heartmonitor ended while
    hypershell-scheduler still runs".
  - The chain order matches `ServerThread.run_with_exceptions` (scheduler → heart monitor →
    receiver → confirm → server). Only the server writes sentinels. The returner and the upstream
    heartbeat end only after `wait_site`.
  - A normal drain is not misread. The drain test passed 6 of 6 concurrently and in the full suite.
- **C2-4 signals.**
  - SIGTERM at every startup delay and mid-run: −15, copy removed, no site client left.
  - SIGINT: 4, copy removed. A second SIGINT: 4, copy removed.
  - `hs server` and `hs cluster` are unaffected (−15).
- **Roster guard.**
  - Another relay's leaf is neither re-attributed nor disconnected (test; mutant killed).
  - A relay's own row cannot be claimed by another relay.
  - Re-registration after an upstream restart, and after eviction under the same identity, is
    accepted.
- **Wire.** Against a 2.9.0a4 upstream, 8 of 8 tasks completed by leaves. The upstream listed only
  the relay and logged no errors. A 2.9.0a4 heartbeat unpacks here with `site`/`roster` None, and
  this relay's heartbeat unpacks in 2.9.0a4 with the extra fields ignored.
- **C2-2, C2-3 and C2-4 tests:** 6 of 6 concurrent passes, and each is mutation-killed.
- **Docs.** Sphinx `-W`: 3 warnings, the known pre-existing ones (`manual.rst` twice and
  `cli/task_submit.rst`), none from relay pages. `evict_hint` does not appear in the built HTML.
  The docs contain no `hs task …`, and the diff contains no internal names.

### Cycle-2 closure

| Finding | Status |
|---|---|
| C2-1 | Closed. Every relay-local server thread and the autoscaler are watched, and seven real faults take the bounded failure path. Residual: the test is flaky (C3-2). |
| C2-2 | Closed. The cross-zone process test is mutation-killed (leaf times 13.5 h off). |
| C2-3 | Closed. The drain test holds an unstaged backlog and is mutation-killed. |
| C2-4 | Closed in behaviour. The copy is removed on SIGTERM and SIGINT, and the process ends −15 / 4 (mutation-killed). Residuals: C3-1 (invariant text) and C3-3 (doc sentence). |
| C2-5 | Closed. `evict_hint` is gone from the public docs and the built HTML; the default stays in config. |
| Roster note | The guard was added and is mutation-killed. The ordinary-client roster path remains within the trust model (notes). |
| AGENTS USR1/USR2 note | Still routed to P9, and now carries SIGTERM too (C3-1). |

### Human-gate triggers

- **C3-1** needs the foreman's explicit sanction of the SIGTERM capture. P9's
  `AGENTS.md`/`invariants.md` item must then add §8 (Signals) to its lockstep list.
- No CONFIRMED finding touches a high-blast-radius file. The guard's `server.py` edit held under
  every probe.
- The cycle-1 trigger stands: maintainer sign-off on the credential fence and the site-hop TLS
  before `hs-publish`.
- **Follow-ups for the build:**
  - C3-2: fix the test in P8.
  - C3-1 text and C3-3: in P9.
