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
