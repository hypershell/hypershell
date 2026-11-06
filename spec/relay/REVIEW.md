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

## Review cycle 4 (scope: P7–P9, fresh count 1 of 3) — approved, with follow-ups (2026-11-06)

- **Reviewed commit:** `d4a2ec8`  ·  **Range:** `8dfd5f8..d4a2ec8`, spec-excluded except
  `spec/relay/drive/`. It holds P7 (the R21 drive, judge and smoke), the merge of `develop` with the
  authkey hardening (`72cb1bb`), site clients through `client_launch()` (`24176a6`), P8 (`1393af6`),
  the P8 probe fix (`8a0f3b3`) and P9 (`d4a2ec8`).  ·  **Base:** `develop`  ·  **Date:** 2026-11-06
- **Verdict:** approved. This approves P7–P9 as built; P10 (recording R21 run B) remains. No finding
  blocks. Each follow-up below says why it does not block. **C4-1 is the one the foreman should rule on
  explicitly:** it is CONFIRMED and MEDIUM, and if it is ruled blocking, the remedy is small and local.
- **Cycle:** the first cycle of a new scope. Cycles 1–3 covered P1–P6 and their remediation, and the
  run allows 3 cycles per scope. `set_phase.py` keeps one counter, so `review.cycle` reads 4 (see
  META F7).
- **Mode:** a fresh blind pass over the range, done inline by one reviewer with no subagent, as the
  foreman directed. The findings were formed from `GOAL.md` (unchanged since shaping: only `62d4a17`
  and `e6a322b` touch it), the diff, `invariants.md`, `AGENTS.md`, the rubric and the runnable tree.
  `TECH.md` and the cycle-1–3 record were read only afterwards, to check the P8/P9 deviations and the
  cycle-3 follow-ups. Every local run used a `git archive d4a2ec8` copy outside the worktree, with the
  copy's `src/` on `PYTHONPATH`, each under a process-group kill timer. The worktree was untouched
  until this record.
- **Host constraint:** the review host's POSIX semaphores are exhausted (`multiprocessing.Lock()`
  raises ENOSPC), so nothing that starts a server ran here. Server-dependent checks were written here
  and run by the foreman on a Linux test cluster at `d4a2ec8`. They are marked *(Linux)*.

### Verification run

- **Suites:**
  - *(Linux)* Full suite, serial, 3.12: 1331 passed, 87 skipped, 0 failed.
  - *(Linux)* Full suite, 3.12, `-n auto` on 32 CPUs: 1327 passed, with 4 timeouts under load. Three
    are known flakes. The fourth, `test_relay_ended_by_a_signal_removes_the_site_config_copy[SIGINT]`,
    passed serially and in the 6× run.
  - *(Linux)* Relay files plus `test_cluster_launch.py` on 3.14, `-n 8`: pass.
  - *(Linux)* `-k 'multizone or site_config_copy'`: 4 passed.
  - *(Linux)* 6× concurrency (outage and workload tests, 6 background copies from `sh`): 18 passed in
    every copy.
  - Here: every relay unit test, plus `test_main.py`, `test_completions.py` and
    `test_cluster_launch.py` (`-m unit`): 289 passed on both 3.12 and 3.14.
- **R21 run B** *(Linux, 3 nodes, 2000 × 1 s, TLS on)*: kill 41/41, pause 41/41, none 39/39.
  `relay_judge.py --require kill,pause,none` passes; it was re-run here on the delivered JSON. The
  records were audited field by field:
  - `env_unreadable` is 0 everywhere and the schema is 2.
  - Peak backlog is 128, which is the bound of 128 (2 clients × 32 × 2).
  - Kill: 112 held tasks, all completed at A, 0 stale.
  - Pause: 94 held, all completed elsewhere, 94 stale.
  - `c.peers` was checked by address (slurm mode).
- **Mutations** (pure logic, here): 38 one-at-a-time mutants against the relay unit tests. 28 killed;
  10 survived. Of the 10, 3 are equivalent or near-equivalent (`drop_superseded`'s select filter;
  either one of the two `drain`-stage passes), and 7 are test gaps (C4-3). *(Linux)* one
  server-dependent mutant: dropping the wait for the first upstream connection survives the outage
  and launch integration tests (9 passed).
- **Fault probes** *(Linux, probe script written here)*, each against a real upstream:
  - wrong key; relay TLS against a `--no-tls` upstream; relay `--no-tls` against a TLS upstream;
    wrong `cafile`;
  - an unreachable upstream at start, then the upstream started on that port;
  - idle CPU with `[relay] poll` 0.1 and 1.
  The two-zone dead-fleet probe was invalid: it bound the relay-local server to 127.0.0.1 with no
  loopback launcher, so even the control's site clients could not connect. C4-1 rests on the FSM
  reproduction below.
- **Throughput** *(Linux, `relay_throughput.py`, 2000 × 0.2 s, 32 executors)*: 9 runs across three
  settings (see the note).
- **Probes here (unit level):**
  - the puller FSM against a scripted upstream (C4-1);
  - the default-zone bound in a two-zone relay (it holds);
  - the judge on edited records (C4-4);
  - `relay.zone` forms through the real configuration loader (table, list, `--zone` with a table,
    env override).
- **Docs:** Sphinx `-W --keep-going`, run outside the sandbox for `sphinx_sitemap`'s AF_UNIX socket:
  the three known warnings only (`manual.rst` ×2, `cli/task_submit.rst`).

### Requirement → evidence matrix (reviewed scope)

| R-ID | Implemented by (this range) | Verified how | Status |
|------|-----------------------------|--------------|--------|
| R2   | one identity per zone (`served_zones`), per-zone gate, `ZONE_REQUEST_WAIT`, shutdown per zone | unit tests; mutants M17/M21/M22/M34/M35 killed; multizone tests *(Linux)* | ⚠ C4-1 |
| R8   | low-water gate, bound = clients × site bundle × 2, site bundle ≥ threads | mutants M1–M4 killed; run B peak 128 = bound; default zone in a two-zone relay holds (probe) | ✅ (2× is TECH's recorded reading, accepted) |
| R9   | `UpstreamLink` (deadline, transport vs fatal, jittered backoff ≤ cap), WAIT_LINK stages, confirm replay cut-off, `drop_superseded` | link unit tests; mutants M5–M7/M12/M15/M20/M29/M37–M40 killed; outage tests ×6 *(Linux)*; fault probes | ✅ (C4-2) |
| R16  | fixed fleet per zone (`zone_fleet`), supervised, stopped and reaped | unit tests; M24/M31 killed; M19 survived (C4-3) | ✅ |
| R17  | merge with #80: site key via `HYPERSHELL_SERVER_AUTH`, tasks stripped, `fence_site(launches=…)` | run B (e.*: 0 key hits, 0 fenced names, site key in every client env, none in argv/tasks); unit tests; M14 killed; M23 survived (C4-3) | ✅ |
| R18  | `security.rst` relay section, `cli/relay.rst` non-goals, `database.rst`, config ref, help, zsh completion | read against behaviour; Sphinx; scans: only top-level task commands named, no internal names | ✅ |
| R20  | `test_relay_link/outage/connector/fsm/launch/roster/workload/judge` additions | suites above; 6× run; mutations | ⚠ C4-3 |
| R21  | drive, probe, judge, smoke; probe bound mirror | run B 3/3 PASS; judge tests; mirror test; C4-4, C4-5 | ✅ (evidence to be recorded in P10) |

Unmapped changes: none. The merge brings `develop`'s #79/#80 and the 2.9.0a4 bump, which were reviewed
on `develop`. This record checks only their interplay with relay.

### Findings

#### [MEDIUM/CONFIRMED, FSM level] C4-1 — A served zone with no site client is never asked, so it never gets `shutdown`: pulling never ends, and the relay and a non-forever upstream run on
- **Blocks or follow-up:** follow-up. **Foreman to rule.**
  - Why not blocking: it needs a zone fleet that never registers a client (a launcher that fails
    every time). No task is lost or run twice. A drain (SIGUSR1) or SIGINT ends it. The autoscaler logs
    a WARNING for each failed launch.
  - The single-zone form (a relay whose only fleet never connects) has been there since the
    gate of P3–P6, which cycles 1–3 approved. P9 extends it to one bad zone among good ones, where it
    is easier to miss: the other zones finish normally.
- **Where:**
  - `src/hypershell/relay/upstream.py:368`: the gate skips a zone with `bound == 0`.
  - `:392-399`: pulling ends only once every zone has had a `shutdown` reply.
  - `src/hypershell/relay/__init__.py:586-590`: the relay waits for the puller to end.
  - `docs/_include/relay_desc.rst:49` promises that "when the upstream server shuts down, the relay
    finishes … and ends its upstream registration".
- **Scenario:**
  1. A relay serves `cpu` and `gpu`. The `gpu` fleet's launcher fails every time, say a GPU
     request on nodes without GPUs.
  2. Upstream finishes and `dispatcher.finish()` runs. `cpu` gets `shutdown`. `gpu` is never asked
     (bound 0), so the puller loops at the gate.
  3. The relay never sends its FINISHED beats. The upstream `HeartMonitor` keeps the relay
     identities in `beats` and never reaches FINAL.
- **Evidence:** the real `UpstreamPuller`, with a scripted upstream that answers every request with
  `shutdown`:
  - Two zones, clients `{'cpu': 1, 'gpu': 0}`: 20 gate cycles, never FINAL, and the only request is
    `['cpu']`.
  - One zone with 0 clients: the same.
  - An end-to-end probe on Linux was attempted, but the probe was flawed (see *Verification run*).
- **Fix (small):** once any zone has had `shutdown` (upstream finishing is server-wide), finish every
  zone that has no site client and no backlog. Its FINISHED beat then also makes upstream `forget` any
  demand it left. Or, at the least, warn when a served zone has had no site client for a whole
  autoscale period.

#### [LOW/CONFIRMED] C4-2 — A TLS on/off mismatch with the upstream is retried forever as a transport error, not failed fast
- **Blocks or follow-up:** follow-up.
  - Why not blocking: R2 requires fail-fast only for the protocol version, and that holds. The
    relay never starts its site while it waits. Its log names the SSL error or the connect timeout.
- **Where:**
  - `src/hypershell/core/reconnect.py:92`: `TRANSPORT_ERRORS` holds every `OSError`, including
    `ssl.SSLError`.
  - `:416-427`: `attempt`.
- **Evidence** *(Linux, fault probes, real upstream)*:

  | case | outcome | site launches |
  |---|---|---|
  | wrong key | rc 5 after 2.1 s (`AuthenticationError`) | 0 |
  | wrong `cafile` | rc 5 after 1.8 s (`SSLCertVerificationError`) | 0 |
  | relay TLS → `--no-tls` upstream | still retrying at 40 s: `SSLError: [SSL: WRONG_VERSION_NUMBER]`, "trying again" | 0 |
  | relay `--no-tls` → TLS upstream | still retrying at 50 s: each attempt takes the full 30 s deadline (`TimeoutError`) | 0 |

  A plain `hs client` fails at once in both mismatch cases.
- **Fix:** treat `WRONG_VERSION_NUMBER`-class `ssl.SSLError` on the *first* connection as fatal, or
  add a hint ("check `--no-tls` / `relay.upstream.tls` on both ends") to the "Cannot reach" warning
  after N attempts.

#### [LOW/CONFIRMED] C4-3 — Seven behaviours of this range survive mutation (test gaps)
- **Blocks or follow-up:** follow-up (test-only). Why not blocking: the code is right in each case,
  by reading and, where noted, by a probe or the Linux runs. These are the regressions a later
  change could slip through.
- **Survivors:**
  - **M41** *(Linux)*: `relay/__init__.py:510`. Dropping the wait for the first upstream connection
    survives the outage and launch integration tests (9 passed). The behaviour holds *(Linux probe:
    0 launches and no relay-local server before the upstream is up; 1 launch after)*.
  - **M23**: `:502`. A further zone's launch is not fenced. `fence_site(launches=…)` itself is
    tested, but the `RelayThread` wiring is not, so a zone launcher carrying the upstream key would
    go unnoticed (R17).
  - **M19**: `:639`. A further zone's autoscaler is not supervised.
  - **M18**: `:984`. Zone `size` validation removed.
  - **M36**: `store.py:258`. The default-zone `IS NULL` filter, which matters for a multi-zone relay
    serving the default zone. A probe here shows it holds today.
  - **M26/M28**: `reconnect.py:345` (the epoch guard in `mark_down`) and `:421` (a non-transport
    connect error failing the link).
- **Fix:** one test per survivor. The two `drain`-stage passes and the select filter in
  `drop_superseded` are equivalent mutants and need none.

#### [LOW/CONFIRMED] C4-4 — The judge has four ways to pass without proving what it names
- **Blocks or follow-up:** follow-up. Why not blocking: none of the four occurs in run B's records,
  audited field by field (`env_unreadable` 0, `key_in_server_log` present and 0, 3 groups, schema 2).
- **Where and evidence** (`judge_run` on edited `good_run` records): each edit below still passes
  every check.
  - `env_unreadable` = 50 on both leaves. `e.*` (`spec/relay/drive/relay_judge.py:180-192`) never
    reads it, so a site client whose environment could not be read is not checked for the key.
  - `key_in_server_log` absent. `e.logs` reads `or 0`.
  - A one-group workload. `a.group_order` (`:104`) is then vacuous.
  - A schema-1 run. `:195` skips the site-key checks, and `--require` does not ask for schema 2.
- **Fix:** require `env_unreadable == 0`, the field's presence, `groups ≥ 2`, and schema 2 under
  `--require`.

#### [LOW/CONFIRMED] C4-5 — The drive's header still states the pre-P8 bound
- **Blocks or follow-up:** follow-up (comment).
- **Where:** `spec/relay/drive/relay_drive.sh:57`: "a site holds at most its clients x this many
  tasks". Since P8 it is twice that, and `relay_probe.py:95-107` mirrors the doubled bound.

### Notes (not findings)

- **Throughput rider** *(Linux, 9 runs)*. All 9 completed 2000/2000 and exited 0. Settings and
  ratios (relay wall time over direct):
  - default settings: 1.61, 1.22, 1.31;
  - `HYPERSHELL_RELAY_BUNDLEWAIT=1`: 1.78, 1.33;
  - `HYPERSHELL_SERVER_POLL=1`: 3.89, 1.40, 1.55, 1.40.

  The steady case is 1.2–1.4×. Four of the nine runs carry an extra end-of-run tail of 6–9 s, and once
  about 60 s, before the upstream server exits. Neither bundlewait nor the upstream scheduler's
  backoff explains it: both hypotheses were tested and failed. The relay scratch directories were
  node-local and are gone, so the cause is undetermined.
  - It is not a GOAL criterion: throughput is a stated non-goal, and the 1.5× limit is the foreman's
    rider. But it decides whether the rider's gate is reliable on Linux. A stall near the autoscale
    period (60 s) or the reconnect backoff cap (60 s) deserves a look.
  - **Next step for the build:** run `relay_throughput.py` with `HYPERSHELL_LOGGING_LEVEL=DEBUG` and
    the scratch kept, until an outlier occurs. Then read the relay log from "Upstream server is
    shutting down" to "Upstream heartbeat - final" and the upstream log around the relay's FINISHED
    beats.
- **Idle relay** *(Linux)*: 3.9% of a core at the default `[relay] poll` 0.1, 0.9% at 1, with the
  upstream at 0%. There is no spin. The probe's site clients could not connect (see *Verification
  run*), so the puller sat at its 0.1 s gate throughout: this is near the idle upper bound. The `poll`
  docs name only the relay-local scheduler, but the setting also paces the returner and the puller's
  gate.
- **The cut-to-confirm window now also opens on a dropped connection.** A request lost with the
  link (`upstream.py:384-386`) leaves any reply cut for it stamped upstream with no client until the
  upstream restarts. `cli/relay.rst` states this ("or when its connection drops"), and the GOAL lists
  it as a non-goal. It is noted because R9's ride-through makes such drops routine rather than fatal.
- **`[relay.upstream] evict_hint` must not exceed upstream `server.evict`,** and the docs say so. The
  upstream ages a relay by its last heartbeat, which can be up to `heartrate` older than the relay's
  last successful call. So a replay just under the hint can still land after an eviction. If it
  does, it re-claims rows another client is running, and they finish there. This was found by
  reading, and no loss follows.
- **Merge with #80 (focus 1).** It holds:
  - The site key reaches clients only in `HYPERSHELL_SERVER_AUTH` (`ClientLaunch.env`, applied at
    `start()` over the scrubbed `os.environ`). `fence_site` checks every launch's argv and env, and
    the exports.
  - Tasks and `{% %}` commands lose the key (`inherited_task_env`).
  - The link's factory holds the `QueueConfig` read before the scrub.
  - Run B: every site client held its site key in its environment, and no command line or task held
    it.
  - The docs say the launcher must pass the environment, in three places (`relay_help.rst`,
    `relay_desc.rst`, `security.rst`).
  - `zone_launch` matches `AutoScalingCluster`'s launch (unit test).
  - A `HYPERSHELL_RELAY_ZONE` variable replaces a file's `[relay.zone.*]` tables entirely, so
    `--zone gpu` then takes no table settings. That is layered-configuration behaviour.
- **Single zone unchanged by P9:** `connected=None` (the `client`-table count), the full request
  wait, and no further fleets.
- **The underscore-key seed** (TECH P8: `HYPERSHELL_*` cannot set `num_threads`, `launcher_args`,
  `evict_hint`) is still not filed as an issue. It is for the foreman.

### What held (executed)

- **Fail fast.** At start, a wrong key and an unverifiable certificate end the relay in about 2 s,
  with 0 site launches *(Linux)*. A version error is covered by the connector and link unit tests and by
  `test_relay_against_old_server_exits_with_one_version_error` (serial suite *(Linux)*). An upstream
  that comes back with another key ends the relay (the "comes back unusable" test).
- **An unreachable upstream at start** is retried, and the site starts only once it answers
  *(Linux)*: 0 launches, "Cannot reach", no relay-local server; then "Connected", 1 launch.
- **Bounded backoff** (`backoff_delay`, cap killed by M5), **jitter**, **epochs**, the **stage
  order** after reconnection (beat → replay → drain → pull: M20/M39/M40 killed), and the **replay
  cut-off** at `evict_hint` (M7 killed).
- **R8:** the low-water gate (M1), the gap-sized request never above the bound (M2), factor 2 (M3),
  and the thread floor (M4). Run B: peak 128 against a bound of 128 at both sites, in all three
  variants.
- **Restart detection** (`drop_superseded`; M11 killed): only not-yet-started tasks of the old
  instance are dropped, and ids in the new bundle are kept.
- **P9:**
  - one identity per zone (M17);
  - the roster on the holder only (M16);
  - the per-zone bound and backlog (M34/M35);
  - the short wait (M21) and shutdown per zone (M22);
  - repeated and unknown zones refused (M8/M9/M42);
  - every fleet reaped (M24) and launched for its own zone (M31).
  `relay.zone` forms through the real loader: a table, `--zone` keeping its table's settings, and an
  empty table.
- **Docs and invariants:**
  - The relay trust model in `security.rst`: two auth domains, upstream TLS by `cafile`/fingerprint,
    the site key in the environment, the fence's limits, the roster.
  - The non-goals in `cli/relay.rst`.
  - The C3-1 carve-out in AGENTS §Signals and invariants §8, matching the code: only
    `relay/__init__.py:175` registers SIGTERM.
  - §10/§11 relay notes.
  - Only top-level task commands (`hs list`, `hs info`, `hs wait`, `hs run`, `hs update`) are
    named, and there are no internal names in the diff or drive.
  - Completions: zsh `*--zone`; bash `-n` clean *(Linux)*.
- **The drive and judge** (focus 6):
  - (a) rows = tasks + retries, none incomplete, retries chained, group order, every id executed,
    duplicates only within the failover snapshot.
  - (b) leaf attribution by site and host, and the roster upstream.
  - (c) upstream connections only from the relays (psutil per leaf, and peer addresses).
  - (d) eviction, revert and completion at A, with stale results refused after a pause.
  - (e) no key in any site client or task argv or environment, and the site key handed over in the
    environment.
  These prove R21 (a)–(e) for run B. The probe's bound mirror is right (`relay_bound` =
  `connected × max(1, RELAY_BUNDLESIZE, THREADS) × 2`, pinned by its test).

### Cycle-3 closure

| Finding | Status |
|---|---|
| C3-1 | Closed. The SIGTERM/USR1/USR2 carve-out is in `AGENTS.md` §Signals and `invariants.md` §8. It matches the code: catch, clean up, re-deliver; a second SIGTERM ends the process at once; USR1/USR2 never set `RECEIVED`. No other command registers SIGTERM. |
| C3-2 | Closed. `FAULTY_RECEIVER_RELAY` raises only on non-empty `results` (`tests/test_relay_workload.py:620-624`). The test passed in the serial and 6× runs *(Linux)*. |
| C3-3 | Closed. `relay_desc.rst`, `hs relay --help` and `security.rst` say a second SIGTERM, like SIGKILL, leaves the site config copy. |

### P8/P9 deviations (TECH), checked

- **An unreachable upstream at start is not fatal** (P8). Accepted. It behaves as stated *(Linux
  probe)*; its test gap is in C4-3 (M41).
- **The R8 bound is twice the GOAL's order** (P8). Accepted. The bound is still enforced, documented
  in the config ref, `relay_desc.rst` and `cli/relay.rst`, and held in run B.
- **Upstream loss is not a relay failure** (P8). This is R9 itself. Real failures still end the
  relay (the "comes back unusable" test).
- **`relay.zone` forms, the per-zone client count from the relay-local dispatcher, the short wait,
  shutdown per zone, fleets, heartbeats** (P9). Accepted, with one exception: "shutdown per zone"
  combined with "a zone with no client is skipped" is C4-1.

### Human-gate triggers

- **C4-1:** the foreman rules it follow-up or blocking. No CONFIRMED finding touches a
  high-blast-radius file. `core/reconnect.py` and `relay/` are outside the list, and the range edits
  `client.py`, `cluster/remote.py` and `cluster/ssh.py` only through `develop`'s reviewed #80, plus
  `AutoScalingCluster(evict_after=…)`, which all suites passed.
- **The cycle-1 trigger stands:** maintainer sign-off on the credential fence and the site-hop TLS
  before `hs-publish`.
- **Follow-ups for the build:** C4-1 (fix or ruling), C4-2, C4-3, C4-4 and C4-5, plus the throughput
  tail investigation (notes), before or with P10.

## Review cycle 5 (scope P7–P9, count 2 of 3) — approved, with follow-ups (2026-11-06)

- **Reviewed commit:** `ff23d2d`  ·  **Range:** `672418d..ff23d2d`: `fec5ee4` (the cycle-4
  remediation: C4-1, C4-3, C4-5, P9 marked done) and `ff23d2d` (P10: R21 run B's records in
  `spec/relay/evidence/cluster/`, with the P7 and P10 records in TECH)  ·  **Base:** `develop`  ·
  **Date:** 2026-11-06
- **Verdict:** approved. No finding blocks. C4-1, C4-3 and C4-5 are closed. C4-2 and C4-4 stay
  follow-ups by the cycle-4 ruling. One new follow-up (C5-1) qualifies the premise C4-1's fix rests
  on.
- **Cycle:** the second cycle of the P7–P9 scope. `set_phase.py` keeps one counter, so
  `review.cycle` reads 5 (META F7, seen again).
- **Mode:** scoped to the remediation and P10, done inline by one reviewer with no subagent, as the
  foreman directed. The findings were formed first from `GOAL.md` (unchanged since shaping: only
  `62d4a17` and `e6a322b` touch it), the range's diff, `invariants.md`, the rubric and the runnable
  tree. Only then were REVIEW cycle 4 and `TECH.md` read, for the mutant definitions and the closure
  checks. Every local run used a `git archive ff23d2d` copy outside the worktree, with the copy's
  `src/` on `PYTHONPATH`, each under a process-group kill timer. The worktree was untouched until
  this record.
- **Host constraint:** POSIX semaphores are still exhausted here, so nothing that starts a server
  ran here. Server-dependent checks were written here and run by the foreman on a Linux test
  cluster at `ff23d2d`. They are marked *(Linux)*.

### Verification run

- **Suites:**
  - Here: the relay unit tests with `test_main.py`, `test_completions.py` and
    `test_cluster_launch.py` (`-m unit`): 304 passed on 3.12, 3.14 and 3.15. The same count as TECH's
    gate.
  - Here: the new tests (and all of `test_relay_connector.py`), 3 times in each of 6 concurrent
    copies: 18 of 18 passed.
  - *(Linux)* Full suite, 3.12, `-n auto` under load: 1343 passed and 3 failed. The 3 are timing
    tests outside relay that this range does not touch: `test_waits_for_slow_peer_upgrade`,
    `test_shared_port_number_leaks_nothing[tls]` and
    `test_server_keeps_its_files_at_the_open_file_limit`. The serial suite, the relay files on 3.14,
    multizone, the smoke and the 6× run were still running when this was recorded. The foreman
    directed that the record not wait for them, and will forward them.
- **Mutations** (unit level, here; each mutant on its own copy of `src/`):
  - The five named survivors from cycle 4 are now killed, each by its new test, and each still
    survives the cycle-4 test files (205 passed per mutant):
    - M41: the wait for the first upstream connection dropped.
    - M23: `launches=[]` given to `fence_site`.
    - M19: further zones' fleets left out of `check_site`; also a variant that supervises only the
      first zone's autoscaler.
    - M18: the zone `size` check removed; also two partial variants, the `bool` clause dropped
      (killed by `[True]`) and `< 1` turned into `< 0` (killed by `[0]`).
    - M36: the default zone's `IS NULL` match turned into `zone == DEFAULT_ZONE_ID`.
  - The C4-1 rule: the `finish_idle` call dropped; called before any shutdown; the no-client
    condition dropped; the nothing-held condition dropped. All four are killed by
    `test_relay_connector.py`.
- **C4-1 end to end** *(Linux, a scratch probe, never committed)*: cycle 4's scenario, built on the
  `test_relay_workload.py` helpers. A relay serves `cpu` and `gpu`. `gpu`'s launcher is a script that
  fails every launch, so that zone never has a site client. A non-forever upstream (`--restart`)
  runs 4 `cpu` tasks.
  - At `ff23d2d`: 1 passed in 14 s. The upstream server and the relay both exited 0, 9.2 s after the
    relay started. The relay logged "no site client for zone gpu" and its final heartbeats, neither
    identity was evicted, and all 4 tasks completed.
  - With the `finish_idle` call dropped: failed. The upstream server was still running at 148 s
    (`TimeoutExpired`). Its log shows the `gpu` launches exiting with status 1, as intended.
  - A first version of the probe was invalid: it set the launcher to `false` through
    `HYPERSHELL_RELAY_ZONE_GPU_LAUNCHER`. The environment layer reads that value as a boolean, so the
    relay refused it at start ("must be text", reproduced here), and both arms failed alike.
- **Probes here (unit level, the real `Dispatcher`, no server):** the C4-1 rule against an upstream
  that is finishing while rows are still staged for another zone, with and without a database
  (C5-1).
- **P10 evidence:** `relay_judge.py spec/relay/evidence/cluster --require kill,pause,none` from the
  clean archive: kill PASS (41 checks), pause PASS (41), none PASS (39), `relay_judge: PASS (3 runs)`,
  exit 0, under the project's 3.15 and a system 3.9 with `-I`. Every key and every string value of
  the 15 records was listed (see *Evidence judgement*).
- **Docs:** Sphinx `-W --keep-going` on 3.12, outside the sandbox for `sphinx_sitemap`'s AF_UNIX
  socket: the three known warnings only (`manual.rst` ×2, `cli/task_submit.rst`).

### Requirement → evidence matrix (reviewed scope)

| R-ID | Implemented by (this range) | Verified how | Status |
|------|-----------------------------|--------------|--------|
| R2   | `finish_idle`: an idle zone ends once another has had `shutdown` | 5 new unit tests; 4 rule mutants killed; e2e *(Linux)*; probes | ✅ (C5-1) |
| R16  | every zone's fleet supervised (test only) | M19 killed | ✅ |
| R17  | every zone's launch fenced (test only) | M23 killed | ✅ |
| R8   | per-zone backlog of the default zone (test only) | M36 killed | ✅ |
| R9   | the site starts only once the upstream answers (test only) | M41 killed | ✅ |
| R18  | `relay_desc.rst`: the shutdown is heard per zone; the all-zones-idle residual | read against the code and tests; Sphinx | ✅ |
| R20  | the C4-3 tests | mutations above; 6× concurrency here | ✅ |
| R21  | run B's 15 records, `SUMMARY.txt`, P10 | judge from a clean archive; records audited | ✅ |

Unmapped changes: none.

### Findings

#### [LOW/CONFIRMED, unit level] C5-1 — An idle zone ended at another zone's `shutdown` gives up rows still staged for it upstream
- **Blocks or follow-up:** follow-up.
  - Why not blocking: it takes an upstream that finishes with rows still staged (drained by
    SIGUSR1/2, a non-forever run stopped on a failed group, or a server without a database), and a
    zone with no site client at that gate pass. With a database the rows go back unscheduled when
    the upstream exits: nothing is lost or run twice. On a normal end (every task complete) nothing
    is staged and the rule is exact. Before C4-1 each of these cases was a hang.
- **Where:**
  - The premise: `src/hypershell/relay/upstream.py:238` ("The upstream server finishes for every
    zone at once"), the same sentence at `tests/test_relay_connector.py:559`, and
    `spec/relay/TECH.md:1674`.
  - The rule: `upstream.py:383-394` (`finish_idle`), called at `:361`.
  - The server: `src/hypershell/core/dispatch.py:303-305`. `finish()` sets one flag for every pool,
    but a zone's request is told to shut down only once that zone's own pool is empty, and invariant
    §6 says so ("requests drain their zone's staged tasks, then get `shutdown`").
    `src/hypershell/server.py:1157-1164`: the staged revert at exit takes stamped rows only, and only
    with a database. Without a database, `finish()` runs as soon as the task file is submitted
    (`:1086-1091`, `:1149-1153`).
- **Scenario:** a relay serves `cpu` and `gpu`. The upstream is drained while rows are staged for
  `gpu` (the `gpu` identity asked earlier, when it had a site client). `cpu` asks and is told to shut
  down. At that gate pass `gpu` has no site client (its fleet is still launching, or its one client is
  between exit and relaunch) and holds nothing, so `finish_idle` ends it. A `gpu` request a moment
  later would have been served those rows.
- **Evidence** (the real `UpstreamPuller` driven against the real `Dispatcher`, in process):
  - With a database: two rows staged for `gpu`, then `finish()`, clients `{cpu: 1, gpu: 0}`. The
    puller reaches FINAL with both identities finished, and 2 rows are still staged for `gpu`. A
    `gpu` request then gets 2 tasks and `shutdown: false`.
  - Without a database: a 4-task bundle on the inbound queue, and a default-zone request of size 1
    (one cut, three staged). Then `finish()`, as `wait_heartbeat` does. Zones `(gpu, default)`,
    clients `{gpu: 1, default: 0}`. The puller reaches FINAL and 3 default rows are left;
    `take_staged()` returns `[]` (unstamped), so nothing puts them back. They do not run in that
    server's run, and nothing is logged.
- **Fix:**
  - Required: state the premise exactly in the docstring, the test docstring and TECH. A shutdown
    reply means the upstream is finishing in every zone, but each zone is first served what is still
    staged for it.
  - Optional: end an idle zone only once it has had no site client for a whole site period, so a
    launching or relaunching fleet is not cut off.

### Notes (not findings)

- **An upstream restart between the shutdown reply and the finish.** A zone that has had `shutdown`
  stays finished across a reconnection (P9's behaviour); C4-1 extends that to idle zones, and the
  relay then winds down, returning what it holds. Consistent, not a regression.
- **A client count flapping between 0 and 1** matters only in C5-1's case. On a normal end the
  upstream has nothing left for the zone.
- **The all-zones-idle residual** is stated accurately in `relay_desc.rst` and TECH: with no site
  client in any zone the relay never asks, so it hears no shutdown. A request always takes at least
  one task (`dispatch.py`: `size = max(1, …)`). The rst could add that a non-forever upstream server
  waits for such a relay too, since its identities still beat.
- **The new start-up tests pass `site_config=''`, which is falsy,** so `RelayThread` falls back to
  `<configured database file>.site.toml`. In the test process that is the developer's default
  database path, and `release()` removes that file if it exists. No real relay writes that name, so
  nothing is lost in practice. A `temp_site` fixture would keep the test off the real path.
- **A zone launcher set through the environment as a bare `false` or `true` is read as a boolean,**
  and the relay refuses it at start with a clear error ("relay.zone.gpu.launcher must be text"). A
  path stays text. This is how the environment layer types every setting; it is noted because it
  misled the first end-to-end probe.
- **M41's test sleeps 2.5 s.** It is not timing-dependent for correct code, because the link never
  comes up until the test raises it. A mutant reaches the site in milliseconds, so it is killed
  however loaded the host. The cost is the 2.5 s.

### Evidence judgement (P10)

- **Passes from a clean checkout.** Yes: see *Verification run*. The judge is stdlib only.
- **No internal names.** The only string values in the 15 JSON files are the variants, the roles,
  the site letters `A`/`B`, `slurm`, `2.9.0a4`, `3.12.13`, `d4a2ec8` and the three run ids. Grep of
  the records and `SUMMARY.txt` for cluster, account, partition, path, host and user patterns: no
  match. The range's other added lines are clean too.
- **The Slurm job number in the run ids is acceptable** in a public repo. It is a site-local
  sequence number. Without the cluster's name, which appears nowhere, it identifies no system or
  person, and it grants nothing. Keeping the records exactly as the drive wrote them is worth more
  than a nonce.
- **`SUMMARY.txt` states what the records show.** Its build, Python, nodes, workload, settings,
  checks, wall times, rows and peak backlog/bound match field by field. Kill: snapshot 112 (rows
  confirmed to B, incomplete), B evicted and reverted once, 112 completed at A; B's 96 local rows
  and 16 pending results also sum to 112. Pause: 94 held, all completed at A, 94 stale. None: 2003
  executions of 2003 ids, no duplicate. Two wordings go slightly beyond a single field:
  - "B's 94 late results": the probe counts the upstream log's stale refusals server-wide. B is the
    only source, since A was never evicted and the none run has 0.
  - "The transport … was never copied" is a statement of provenance, which the records cannot show.

### What held (executed)

- **C4-1 rule:** a zone with no site client ends only after some zone's shutdown (both zone
  orders), never before. A zone with site clients still asks for itself. A zone holding tasks ends
  only once they are done. The single-zone and all-zones-idle relays never ask, and end on a halt.
- **Single zone unchanged:** a single-zone puller reaches FINAL from its own shutdown reply
  (`request`), so `finish_idle` never runs for it.
- **Docs:** the range names only top-level commands (none at all) and no internal names.
- **TECH's P7/P10 record:** it matches the delivered records, and the P10 verify now requires all
  three variants.

### Cycle-4 closure

| Finding | Status |
|---|---|
| C4-1 | Closed. Once any zone has had `shutdown`, the gate ends every zone with no site client and nothing held. Tests and four rule mutants (unit), plus the dead-fleet scenario end to end *(Linux)*: green with the fix (both processes exit 0 in 9.2 s), red without it (upstream still running at 148 s). C5-1 qualifies its premise; it does not reopen it. |
| C4-2 | Follow-up by ruling (TLS on/off mismatch retried, not failed fast). Unchanged. |
| C4-3 | Closed for the ruled list: M41, M23, M19, M18 and M36 are each killed by a new test and survive the cycle-4 tests. M26/M28 (`core/reconnect.py`) were outside the ruling and remain. |
| C4-4 | Follow-up by ruling (the judge's four vacuity cases). Run B's records show none of the four: `env_unreadable` 0, `key_in_server_log` present and 0, 3 groups, schema 2. |
| C4-5 | Closed. `relay_drive.sh:57` says "clients x this many x 2 tasks", which matches `relay_probe.py`'s mirror and run B's 128 = 2 × 32 × 2. |

### Human-gate triggers

- No CONFIRMED finding touches a high-blast-radius file. The range edits only `relay/upstream.py`,
  tests, one docs include and `spec/`.
- **The cycle-1 trigger stands:** maintainer sign-off on the credential fence and the site-hop TLS
  before `hs-publish`.
- **Follow-ups for the build:** C5-1 (the wording at least), C4-2, C4-4, C4-3's M26/M28, and the
  throughput tail investigation (cycle-4 notes).

## Review cycle 6 (scope P7–P9, count 3 of 3) — approved (2026-11-06)

- **Reviewed commit:** `ef92e40`  ·  **Range:** `ff23d2d..ef92e40`: `eabd3bd` (the cycle-5 record),
  `a91e225` and `8ec3cf4` (TECH only), and `ef92e40` (the `running_tasks` fix, with its TECH note)  ·
  **Base:** `develop`  ·  **Date:** 2026-11-06
- **Verdict:** approved. No finding. The diagnosis held on every point that could be checked here,
  and the fix is test-only: `src/` and `docs/` are unchanged since `ff23d2d`.
- **Cycle:** the third and last cycle the run allows for the P7–P9 scope. `set_phase.py` keeps one
  counter, so `review.cycle` reads 6 (META F7).
- **Mode:** inline, one reviewer, no subagent, as the foreman directed. The diagnosis was checked
  from `client.py`, the test module, the relay's site-client defaults and the Debian and Ubuntu
  dash sources before the builder's TECH note was weighed.
- **Host constraint:** POSIX semaphores are still exhausted here, so no integration test ran here.
  PR #82's CI on `ef92e40` was still running at this record; the foreman holds that result.

### Verification run

- **The launch:** `client.py:829` runs every task as `Popen(command, shell=True)`, that is
  `/bin/sh -c COMMAND`. The relay's site clients have one executor each (`relay.num_threads = 1`) and
  a relay-local bundle size of 1 by default.
- **The waiter's argv:** `waiting_tasks` and `gated_workload` build `'PYTHON' 'waiter.py' 'BARRIER'
  LABEL`. The shell strips the quotes, so the waiter's argv holds `str(barrier)` as an element of
  its own, exactly the string the helper compares. No other site process carries it as an element:
  the launchers `exec` down to `hs client`, whose command line has no barrier.
- **Debian and Ubuntu dash:** `0004-SHELL-Disable-sh-c-command-sh-c-exec-command-optimiza.diff` is
  active in the `debian/patches/series` of Debian's dash 0.5.12-12 (sources.debian.org) and of
  Ubuntu noble's package (git.launchpad.net, `ubuntu/noble`), which is `ubuntu-latest`. Here, macOS
  `/bin/sh` (bash) and `/bin/dash` (unpatched) both exec a lone command.
- **Stand-in** (scratch, never committed): a parent Python process runs `waiting_tasks` commands with
  `Popen(shell=True)`, and the real helpers count its children. The new helper is imported from
  `ef92e40`; the mutant is the helper at `ff23d2d` (the substring match, which is the only change to
  the file):

  | Shell | Tasks | Live shells | Old (substring) | New (exact) |
  |---|---|---|---|---|
  | `/bin/sh` (bash), lone command | 3 | 0 | 3 | 3 |
  | `/bin/dash`, lone command | 3 | 0 | 3 | 3 |
  | `/bin/dash`, `cmd; :` | 3 | 3 | 6 | 3 |
  | `/bin/sh`, `cmd; :` | 3 | 3 | 6 | 3 |
  | `/bin/dash`, `cmd; :` | 2 | 2 | **4** | 2 |
  | `/bin/dash`, `cmd; :` | 1 | 1 | **2** | 1 |

  With the shell kept alive, the mutant counts every task twice: 4 with two tasks (never 2, the
  deterministic timeout), and 2 with one (the vacuous `>= 2`). The new match counts each task once
  under every shell.
- **Suites (here, 3.15):** `pytest -m "not integration"` over the whole tree: 845 passed, 3
  skipped and 10 errors. The 10 are in `test_card_info.py` and `test_zone_display.py`, which need
  `hs` on `PATH`. Those two files were re-run with the venv's `bin/` on it: 12 of 12 passed. That
  makes 855 passed. `test_relay_workload.py` (all 15 tests
  integration) and `test_relay_outage.py`, which imports the helper, collect cleanly.

### Requirement → evidence matrix (reviewed scope)

| R-ID | Implemented by (this range) | Verified how | Status |
|------|-----------------------------|--------------|--------|
| R20  | `running_tasks` counts a task once, whatever `/bin/sh` is (test only), so the suite can pass on `ubuntu-latest` | source of the launch and the argv; dash patch series; stand-in and mutant; callers read | ✅ (CI pending) |

Unmapped changes: none. The TECH entries are records.

### Findings

None.

### Notes (not findings)

- **The callers, under the exact match:**
  - `>= 2`, at `:716`, `:760`, `:804` and `:958`: each runs 2 site clients with one executor and 4
    waiting tasks, so 2 is reachable and stays reached until the barrier. It is what these callers
    already meant on bash and macOS, where they pass. On Ubuntu the substring match let one task
    satisfy them; that vacuity is gone.
  - `== 2`, at `:858` (drain: two clients) and `:1129` (multi-zone: one client in each of two
    zones): 2 is the most the site can run, so the state is stable while the barrier is absent.
    For the multi-zone test, 2 now means one task running in each zone.
  - Truthy, at `:377`, `:446`, `:1004` and `test_relay_outage.py:180`: these now wait for the waiter
    process itself rather than a shell about to exec it. That is stricter, and costs milliseconds.
  - None can wait forever on a correct relay, and none can now pass with fewer tasks than it
    names.
- **The docstrings still hold.** Drain: both site clients busy (`== 2`), with an unstaged backlog
  at the relay when SIGUSR1 is sent. Multi-zone failover: a task running in each zone, both zones
  settled upstream, then the kill.
- **`scan_site` and `scan_site_key` keep the substring match, rightly.** They are leak scans: a
  shell kept alive holds the task's environment too, so counting it widens what is checked for the
  key, and their thresholds (`tasks >= 1`) are lower bounds.
- **TECH's local check reads "3 tasks, 5 processes by the old match".** The stand-in here gives 6
  (three shells and three waiters). The conclusion is unchanged; the figure could be corrected to 6
  when TECH is next touched.
- **Taken as stated, not re-derived here:** the CI counts (1346 passed, 2 failed on each version),
  the test cluster's `/bin/sh`, and TECH's staging arithmetic for the drain backlog. The backlog
  condition (`unstaged > 0`) is unchanged by this range.

### What held (executed)

- The diagnosis: the shell stays alive only where `sh` is a patched dash, and the substring match
  then counts the shell as a second task. Reproduced with dash kept alive, and the patch is
  confirmed active on `ubuntu-latest`.
- The fix: one count per task under bash, unpatched dash and a dash kept alive. The `ff23d2d` helper
  reintroduces the double count.

### Cycle-5 closure

| Finding | Status |
|---|---|
| C5-1 | Follow-up, unchanged: this range touches no product code or wording. |
| C4-2, C4-4, C4-3's M26/M28 | Follow-ups, unchanged. |

### Human-gate triggers

- No high-blast-radius file is touched: one test helper and `spec/`.
- **The cycle-1 trigger stands:** maintainer sign-off on the credential fence and the site-hop TLS
  before `hs-publish`.
- **Gate on this cycle:** PR #82's CI on `ef92e40`, green on all four Python versions. This is the
  last review cycle the run allows for P7–P9.
