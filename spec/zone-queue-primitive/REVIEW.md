# REVIEW — Serve the managed queues in-process on a threading primitive (zone, cycle 1)

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 6d0ac37f05426d506500582c63ad025628a99fbf  ·  **Base:** `b819de6` (`origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** single blind correctness reviewer (no `debate`, no `completeness` sub-pass), plus an
  orchestrator re-run of the suite, the base/feature drives, and the throughput gate.

## Pre-flight

- **Base resolution.** `TECH.md` records `base: develop`, but the local `develop` ref is stale
  (`222075b`). The review graded `origin/develop` at `b819de6`, the fork point
  (`git merge-base HEAD origin/develop` = `b819de6`).
- **Build state.** `TECH.md` is `status: in_progress` with P1–P6 `done` and P7 `pending`. P7
  collects R9's multi-node evidence, which is produced outside the repo and is not present yet.
  The review covers everything else; that clause is recorded below as pending external evidence.
- **Contract drift.** `git log --oneline b819de6..HEAD -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`. The contract did not move.

## Verification run

Commands actually executed and their outcomes. The reviewer and the orchestrator ran these
independently. "Base" and "feature" builds are frozen, non-editable venvs built from `git archive`
of `b819de6` and `6d0ac37` by `tests/bench/bench_queue.py` (Python 3.14.6, start method `spawn`, macOS).

- `uv run pytest` (full suite, HEAD) → **538 passed** on 3.14.6 (orchestrator and reviewer), and on
  3.12.13 and 3.13.16 (reviewer). The queue, TLS, FSM and workload files alone → **94 passed** on
  3.12 and 3.13 (orchestrator, `uv run --isolated --python 3.1x`). Python 3.11 was not run locally;
  CI covers it.
- `git diff --name-status b819de6...HEAD -- tests/` → added files only. `git diff --quiet b819de6 HEAD
  -- tests/test_queue_tls.py` → identical. No `share/`, `docs/_include/`, `pyproject.toml`,
  `uv.lock` or `__init__.py` change.
- `--collect-only -m "unit or integration"` on the three new test files → 34 + 20 + 1, the same as
  unfiltered: every new test is tagged.
- `bench_queue.py compat --server HEAD --client b819de6 --tasks 200` → **PASS** TLS on and off
  (200/200 exit 0, rc 0/[0, 0]). The reviewer also ran the reverse direction → PASS.
- `bench_queue.py parity --base b819de6 --feature HEAD` → grouped workload with a retried failure,
  two clients: identical normalised task tables TLS on and off, rc 0/[0, 0] on both builds;
  `--help` for `server`, `client`, `submit`, `cluster` byte-identical.
- `temp_site.sh sh -c "seq 200 | uv run hsx -t 'echo {}' -N4 …; … --no-tls …"` → rc 0 both, 200
  distinct outputs each, 400 rows, 0 failed.
- Signal drive (orchestrator, `hs server --forever` + one client, 400 × `sleep 0.2`, signal after
  3 s, TLS on and off, base vs feature):
  - SIGUSR1 → server rc 0, client rc 0 on both builds; feature exits in 1.4 s (base 1.5–3.4 s).
  - SIGINT → server rc 4, client rc 5 on both builds.
  - Child processes while serving: base 2, feature 0.
  - SIGTERM (outside R5's list; recorded as an observation) → base leaves its manager child
    orphaned and still listening on the port, and the client hung until killed at 60 s; feature has
    no child, and the client exits rc 5 within 0.25 s.
- Reviewer probes: `shutdown()` returns at once and the same port rebinds in-process, TLS on and
  off; `sys.stdout`/`sys.stderr` unchanged; a wrong key gives `AuthenticationError` TLS on and off;
  a raw peer receives `#CHALLENGE` first and a bogus request `#FAILURE`, and the server survives
  both; an injected scheduler exception gives rc 5/[5, 5] on both builds; `hsx` SIGUSR1/SIGINT
  match base. Handler threads blocked in a remote `get(timeout)` outlive `shutdown()` until that
  timeout, are daemon, and do not hold the port.
- CPython internals: `TestPrivateInternals` passes on 3.12–3.14; the reviewer compared
  `multiprocessing/managers.py` `Server`/`BaseProxy` across 3.12, 3.13 and 3.14 and found the same
  shape.
- Throughput gate (orchestrator, machine otherwise idle):
  `bench_queue.py gate --base b819de6 --feature HEAD --repeats 5` → **PASS**, all six configs
  clean (every server and client rc 0, every task complete). Steady-state tasks/s (p10–p90 of
  completions), median [min–max]:

  | config | base | feature | delta | tolerance | tree children b/f | tree FDs b/f | own FDs b/f |
  |---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 234.4 [211.8–259.2] | 233.2 [222.4–243.0] | −1.2 | 47.4 | 2/0 | 117/23 | 52/23 |
  | 4x4 b=1 no-tls | 239.5 [234.6–247.0] | 243.5 [226.0–248.0] | +4.0 | 22.0 | 2/0 | 118/23 | 52/23 |
  | 16x1 b=1 tls | 239.8 [232.3–256.2] | 242.2 [215.6–254.5] | +2.4 | 38.9 | 2/0 | 153/60 | 54/60 |
  | 16x1 b=1 no-tls | 252.3 [171.4–264.2] | 254.9 [206.4–266.9] | +2.6 | 92.8 | 2/0 | 153/59 | 52/59 |
  | 4x4 b=50 tls | 804.3 [696.3–839.7] | 828.9 [807.8–833.6] | +24.6 | 143.4 | 2/0 | 116/22 | 51/22 |
  | 32x1 b=1 tls | 269.7 [252.9–271.1] | 255.1 [246.3–266.8] | −14.6 | 20.5 | 2/0 | 201/107 | 52/107 |

  The largest drop, 32x1 tls (−14.6), is inside the tolerance and the two ranges overlap. Tree RSS
  roughly halves (≈205 → ≈100 MB).
- Throughput cross-check independent of `tests/bench` (orchestrator): local `hs cluster` over 5000
  `true` tasks, `-N 16 -b 1`, 5 interleaved repeats per build, 20/20 runs rc 0 and complete →
  TLS on: base 115.6 [110.2–121.4], feature 116.4 [114.0–122.4] tasks/s; TLS off: base 121.9
  [116.0–122.0], feature 117.0 [116.9–122.8]. Equal within spread.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/queue.py` `_InProcessServer`, `SecureManager._serve_in_process`, `QueueServer.start` | `test_start_creates_no_child[fork,spawn]` on 3.12–3.14; signal drive: 0 children (base 2) | ✅ |
| R2 | `QueueServer.start` (`queue.Queue`, same bounds), `QUEUE_EXPOSED` | bounds, empty-`get`/full-`put` timeout and `join`/`task_done` tests; gate tree FDs fall (below) | ✅ |
| R3 | as R1 | `test_roundtrip[tls,no-tls]` on all four queues | ✅ |
| R4 | as R1; handshake and TLS layer are the stock ones | `test_queue_tls.py` unmodified and passing; `compat` both directions, TLS on/off; auth probes | ⚠️ C1 (same-process TLS) |
| R5 | `_InProcessServer.stop`, `_finalize_in_process` | shutdown/rebind probes; SIGUSR1/SIGINT/exception drives match base | ⚠️ C2 (start-failure path) |
| R6 | `tests/test_queue.py` (all `unit`) | collected and passing | ✅ |
| R7 | `tests/test_server_fsm.py` | Receiver, Confirm, HeartMonitor: data, timeout re-entry, sentinel halt; Scheduler: post, re-entry, halt on signal (it consumes no sentinel) | ✅ |
| R8 | no existing test touched | full suite; `parity`; `--help` identical; `test_queue_workload.py` | ✅ (but see C2) |
| R9 (local) | as R1 | orchestrator gate, 5 interleaved repeats × 6 configs (table below): every median within run-to-run spread, tree children 2 → 0, tree FDs lower in every config; hsx cross-check equal within spread | ✅ (see P3 on the FD reading) |
| R9 (multi-node vs 2.8.3) | `tests/cluster/` drive | **pending external evidence** — not present in the repo; see P2 | ⏳ |

Unmapped changes (possible scope creep), none blocking:
- `issues/zone.md`: Z2/Z3 clarifications (dispatch staging, advertised bundle size, older clients,
  autoscaler zone). Planning prose for later cycles. It calls the base manager "forked"; under
  `spawn` (the macOS default) it is spawned.
- `ROADMAP.md`: the three-cycle annotation.
- `tests/cluster/*`: tooling for R9's external clause. Generic and public-safe; not run here
  (`build_archive.sh` downloads a standalone CPython).
- `core/logging.py`, `core/tls.py`: docstring-only (the reviewer's AST comparison with docstrings
  stripped is identical to base). They follow from R1.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`. No AGENTS.md invariant violation was found.

### [MEDIUM/CONFIRMED] C1 — A same-process client's TLS config replaces the serving process's
- **Where:** `src/hypershell/core/queue.py:429` and `:444` (the process-global context in
  `core/tls.py` `install_process_context`).
- **Failure scenario:** the server now installs its TLS config in the process-global slot that
  `QueueClient.connect()` also writes. A client in the same process with a different config (for
  example CA-only, no cert or key) overwrites it, and every later accept builds the server context
  from the client's config and fails. At base the server context lived in the manager child and was
  isolated. CLI paths pass one shared config object (`cluster/local.py:233,252`, the in-memory
  submit path) and are unaffected; the exposure is the Python API embedding a server and a client
  with different TLS configs. It fails closed (no TLS weakening), but "TLS behaviour unchanged"
  (R4, and the new AGENTS.md line) does not hold for that case.
- **Evidence:** server `TLSConfig(cert, key, cafile)` and same-process clients
  `TLSConfig(enabled=True, cafile=cert)`: base venv → `children after start: 1`, both clients OK;
  feature venv → `children after start: 0`, both clients `SSLEOFError [SSL:
  UNEXPECTED_EOF_WHILE_READING]` (orchestrator, 3.14); reviewer: same on 3.12, 3.13, 3.14.
- **Touches invariant / requirement:** R4; high-blast-radius `core/queue.py` (TLS posture: fails
  closed).

### [MEDIUM/CONFIRMED] C2 — A server that cannot bind now hangs silently
- **Where:** `src/hypershell/core/queue.py:430` (the bind moved into the server process), with the
  unchanged `src/hypershell/server.py:848` (`with self.queue:` inside the thread) and `:862` (the
  `ready` wait).
- **Failure scenario:** any exception from `QueueServer.start` (port in use, bad bind address, or
  drift in the CPython internals) is captured by `ServerThread` and `ServerThread.start()` spins on
  `queue.ready` forever. The hang predates this branch, but at base the manager child printed
  `OSError: [Errno 48] Address already in use` to stderr; now the user sees only
  `Server listening on …` and no error. Starting a second `hs server` on the default port is a
  common mistake. A fix only has to surface the exception, so no GOAL criterion stands in its way
  and the deferral exception does not apply.
- **Evidence:** `hs server t.in -p <bound port>` TLS on and off, 12 s bound: base → hung, stderr
  names `Address already in use` (2/2); feature → hung, last stderr line `INFO
  [hypershell.server] Server listening on 127.0.0.1:<port>` (2/2) (orchestrator). The reviewer saw
  the same, and the same with two `hsx` on one port.
- **Touches invariant / requirement:** R5/R8 and the GOAL outcome ("nothing a user … can see
  changes"); high-blast-radius `core/queue.py`, `server.py`.

### [LOW/PLAUSIBLE] P1 — Drift in the CPython internals is loud but not fatal at runtime
- **Where:** `src/hypershell/core/queue.py:312-335` (accept loop), `:557` (`ready`).
- **Failure scenario:** if a private the serve loop relies on changes, `start()` still succeeds and
  sets `ready`. A broken listener layout kills the accept loop after one ERROR line, so clients
  connect into the backlog and never get the challenge. A missing `_handle_request` or
  `serve_client` gives a thread traceback per connection and `EOFError`/`RemoteError` at the
  client. Either way the server stays up, serving nothing. `TestPrivateInternals` pins every such
  private on CI's 3.11–3.14, so no supported version is affected. The trigger was only simulated,
  by patching the internals. Note for triage: Python 3.15 is released, and `requires-python`
  (`>=3.11`) lets it install untested.
- **Evidence:** reviewer: patched `_listening_socket` to raise → `start() returned; ready = True;
  serve thread alive = False`; `hs server` logged `ERROR … Queue server stopped accepting
  connections` and hung. Orchestrator: deleted `Server._handle_request` → `start ok`, `Exception in
  thread hypershell-queue-handler`, client `EOFError`; deleted `Server.serve_client` → client
  `RemoteError`.
- **Touches invariant / requirement:** none directly; for the human: whether a start-time
  self-check or a version guard is in scope.

### [LOW/PLAUSIBLE] P3 — R9's "open file descriptors": the tree count falls, the server process's own count rises
- **Where:** GOAL R9 ("Its process count and open file descriptors SHALL be no higher").
- **Failure scenario:** none on the whole-server reading. The client connections now end in the
  server process instead of the manager child, so the server process's own FD count grows with the
  number of clients (52 → 107 at 32 clients) while the process tree's total falls (201 → 107).
  "Process count" only makes sense for the tree, so the review graded FDs the same way. A
  per-process reading would fail R9 at 16 and 32 clients. The human decides which reading the
  contract means.
- **Evidence:** the gate table above, columns "tree FDs" and "own FDs".
- **Touches invariant / requirement:** R9.

### [—/PLAUSIBLE] P2 — R9's multi-node clause: pending external evidence
- **Where:** GOAL R9, last sentence ("Before the pre-release, a multi-node run on a real cluster
  shows unchanged results and throughput against 2.8.3").
- **Status:** not graded. The evidence is collected outside the repo and is not present at
  `6d0ac37`. It is neither approved nor failed by this review. The human must judge it when it
  arrives, before the pre-release.
- **Evidence:** none in the repo, by design. The cluster drive scripts read as generic and
  public-safe.

## Observations (not findings)

- **SIGTERM** behaves better: at base the manager child survived its server and kept the port, and
  the client hung; now clients exit at once. This is a user-visible change outside R5's listed
  shutdown paths.
- The remote method surface narrows from twelve to the nine in `QUEUE_EXPOSED` (`close`,
  `join_thread`, `cancel_join_thread` are dropped). No client calls them, and `b819de6` clients
  pass `compat`.
- Server thread count rises (7 → 10 in the signal drive) with the accept thread and a handler per
  connection. R9 bounds processes and FDs, not threads.

## Human-gate triggers

- **Triggered.** C1 (`core/queue.py`, TLS) and C2 (`core/queue.py`, `server.py`) are CONFIRMED in
  the high-blast-radius core. Human sign-off is required before any further step, including the
  return to `/hs-build`.
- For human triage: P1 (start-time guard against internal drift: in or out of scope), P2 (the
  multi-node evidence, when it arrives) and P3 (the FD reading of R9).

## Triage (human gate) — cycle 1

The human gate for C1 and C2 (high-blast-radius `core/queue.py`, `server.py`) was **passed by the
foreman's ruling**, as judge under the maintainer's delegation, on 2026-11-02. Remediation returned
to `hs-build`, which reopened P1.

| Finding | Ruling | Disposition |
|---|---|---|
| C1 · CONFIRMED | fix | The server's TLS context belongs to the server instance, bound to its listener and accept path, and never read from or written to the process-wide slot clients use. Red-first unit test from the reviewer's same-process probe. `test_queue_tls.py` stays byte-identical; wire, auth and TLS behaviour unchanged (R4). |
| C2 · CONFIRMED | fix | A bind or listen failure reaches `QueueServer.start()` as an exception before "Server listening" is logged; the server process exits non-zero with one CRITICAL line naming the address and the OS error, through the shared exception mapping and `exit_status` constants. Strictly better than base (which hung). The foreman's ruling covers the `server.py` change this needs. |
| P1 · PLAUSIBLE | accepted, fix | Fail fast at startup: the in-process loop runs the `TestPrivateInternals` checks (one function shared by the test and the server) before binding and exits non-zero like C2. `requires-python` is not capped. |
| P2 · PLAUSIBLE | pending external evidence | No change; the foreman is running the multi-node matrix (P7). |
| P3 · PLAUSIBLE | rejected | R9's footprint counts the server's whole process tree, per the foreman's ruling at the plan gate (TECH P5 "Gate reading"). The server process's own FD count is reported, not gated. |

## Review cycle 2 — changes-requested (2026-11-02)

- **Reviewed commit:** 12af7fbab7cdc1fd391acf0e8320d40e311b3b2c  ·  **Base:** `b819de6` (merge base with
  `origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3. Two cycles have not converged, so this escalates to the human (bounded loop).
- **Mode:** a fresh blind pass over the full spec-excluded diff (`git diff b819de6...HEAD -- .
  ':(exclude)spec/'`) by one blind reviewer (no `debate`, no `completeness` sub-pass). The reviewer
  was not shown cycle 1's findings. The orchestrator separately verified the remediation of C1, C2
  and P1 and re-ran the throughput gate.

### Pre-flight

- **Base resolution.** `origin/develop` has gained `8cdf62a` since the fork, a data-layer change
  that does not touch `core/queue.py`. The branch's own diff was graded against its merge base
  `b819de6`. The local `develop` ref is still stale (`222075b`) and was not used; the skill's
  injected `develop...HEAD` diffstat was ignored again.
- **Build state.** `TECH.md` is `status: in_progress`, P1–P6 `done`, P7 `pending`. As in cycle 1,
  R9's multi-node clause is recorded as pending external evidence. The cluster run is being
  re-staged at `12af7fb` and has no results yet.
- **Contract drift.** `git log --oneline b819de6..HEAD -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`. The contract did not move.
- **Since cycle 1:** one commit, `12af7fb`, the remediation of C1, C2 and P1.

### Cycle 1 findings: remediation check (orchestrator)

| Finding | Status | Evidence |
|---|---|---|
| C1 · same-process TLS | **resolved** | Same-process probe: the server has cert, key and CA, and three CA-only clients run in the same process. It was also run with a CA-only config, and with a config naming missing cert files, pre-installed in the process-wide slot. Result: 24/24 round trips over all four queues in each case, children 0 → 0, on 3.12.13, 3.13.16 and 3.14.6. The same probe on the `6d0ac37` sources fails with `SSLEOFError [SSL: UNEXPECTED_EOF_WHILE_READING]`, so it discriminates. Starting a server leaves the process slot unset (`test_server_start_leaves_process_tls_config`). The blind reviewer found same-process clients with differing TLS configs behave as on base. |
| C2 · silent hang on bind failure | **resolved** | `hs server` on a held port, TLS on and off: rc 5 (`runtime_error`) in 0.5 s, one CRITICAL line `QueueServerError: Cannot listen on 127.0.0.1:<port> ([Errno 48] Address already in use)`, no "Server listening". `hsx` on a held port: the same, rc 5 in 0.5–0.7 s. An unassignable bind address (`203.0.113.7`): rc 5, `[Errno 49] Can't assign requested address`, TLS on and off. Base hangs in each case: cycle 1 showed it, and the reviewer's base drives this cycle had to be killed at 15–20 s. |
| P1 · internals drift | **resolved** | Six simulated drifts, each driven through `hs server`: `del Server.serve_client`, `del Server._handle_request`, a reshaped `Server.__init__`, a reshaped `Listener.__init__`, `del BaseProxy._address_to_local`, and `shutdown` dropped from `Server.public`. Each gave rc 5 in ≤ 0.7 s with one CRITICAL line naming the failed check, no "Server listening", and the port free afterwards. `unsupported_internals() == []` on 3.12–3.14. Python 3.11 was not run locally (see Human gate). |
| P2 · multi-node evidence | pending external evidence | Unchanged. |
| P3 · R9 FD reading | rejected at cycle 1 triage | The blind pass found the same root cause again, this time with a failure scenario cycle 1 did not have (C3 below). |

### Verification run

- **Reviewer:** `uv run pytest` on 3.14 → **543 passed**. On 3.12 and 3.13, `test_queue.py`,
  `test_queue_start.py`, `test_queue_tls.py`, `test_server_fsm.py` and `test_queue_workload.py` →
  **99 passed** each. Python 3.11 was not run (CI only).
- `git diff --diff-filter=MDR b819de6 HEAD -- tests/` is empty, and `test_queue_tls.py` is
  byte-identical. `--collect-only -m "not unit and not integration"` on the four new test files
  collects 0 of 60, so every new test is tagged.
- `bench_queue.py parity` (TLS on and off) and `compat` in both directions → **PASS** (reviewer).
  A grouped workload with a retried failure, run with `b819de6` clients against the new server,
  gives task rows identical to a base-server run.
- **Reviewer CLI and API drives, base vs feature, in throwaway sites.** These covered: start-up on
  a busy port, a bad bind address and an unresolvable host; SIGUSR1 → rc 0 and SIGINT → rc 4 on
  both builds, with the port released; a wrong key and a TLS mismatch, which give the same messages
  and codes as base; an exception exit through the API, after which the port rebinds in 0.00 s; an
  in-process SIGUSR1 drain followed by a same-port `QueueServer` in the same process, with stdio
  unchanged; `run_local` three times on one port, TLS on and off, under fork and spawn; the R1
  child count, `active_children` 0 → 1 on base and 0 → 0 on feature under fork and spawn; and
  footprint (process tree 3 → 1; tree FDs at idle 103 → 10 with TLS and 103 → 9 without).
- **Throughput gate (orchestrator).** Python 3.14.6, start method `spawn`, macOS. Both builds were
  frozen, non-editable venvs built from `git archive` of `b819de6` and `12af7fb`. Two unrelated
  processes held two of the ten cores at 100% for the whole run; runs were interleaved, so both
  builds shared that load. `bench_queue.py gate --base b819de6 --feature 12af7fb --repeats 5` →
  **PASS**. All 60 runs were clean: every server and client exited rc 0 and every task completed.
  Steady-state tasks/s (p10–p90 of completions), median [min–max]:

  | config | base | feature | delta | tolerance | tree children b/f | tree FDs b/f | own FDs b/f | own threads b/f |
  |---|---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 261.0 [243.0–266.8] | 242.4 [231.3–264.1] | −18.6 | 32.8 | 2/0 | 117/23 | 52/24 | 7/19 |
  | 4x4 b=1 no-tls | 265.4 [232.6–267.6] | 261.1 [236.0–267.1] | −4.3 | 35.0 | 2/0 | 117/23 | 52/23 | 7/19 |
  | 16x1 b=1 tls | 233.4 [232.1–257.4] | 236.4 [218.8–259.3] | +3.0 | 40.5 | 2/0 | 154/59 | 53/60 | 7/55 |
  | 16x1 b=1 no-tls | 247.9 [239.8–259.6] | 257.8 [249.9–269.2] | +9.9 | 19.8 | 2/0 | 154/59 | 52/60 | 7/55 |
  | 4x4 b=50 tls | 816.2 [788.5–817.2] | 817.5 [800.5–822.3] | +1.3 | 40.8 | 2/0 | 116/22 | 51/22 | 7/19 |
  | 32x1 b=1 tls | 257.8 [246.7–267.5] | 260.6 [257.1–261.9] | +2.8 | 20.8 | 2/0 | 202/107 | 52/107 | 7/103 |

  - The largest drop is 4x4 b=1 tls at −18.6, inside its 32.8 tolerance, and the two ranges
    overlap. In cycle 1 the same config measured −1.2.
  - Cycle 1's largest drop, 32x1 tls at −14.6, is now +2.8.
  - Tree RSS roughly halves, from ≈205 to ≈100 MB.
  - The server process's own FD and thread counts grow with clients, as in cycle 1 (see C3).
- **Throughput cross-check (orchestrator), independent of `tests/bench`.** Local `hs cluster` ran
  5000 `true` tasks with `-N 16 -b 1` on the same frozen venvs, measured by wall clock, with 5
  interleaved repeats per build and TLS mode. All 20 runs exited rc 0 with 5000/5000 rows at exit
  status 0.

  | TLS | base | feature |
  |---|---|---|
  | on | 121.6 [121.5–121.7] | 122.3 [122.2–122.6] |
  | off | 122.0 [121.8–122.0] | 122.7 [122.6–122.9] |

  The feature was at or above base in every pairing.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/queue.py` `_InProcessServer`, `SecureManager._serve_in_process`, `QueueServer.start` | `test_start_creates_no_child[fork,spawn]` on 3.12–3.14; reviewer probe 0 → 0 (base 0 → 1) under both start methods | ✅ |
| R2 | `QueueServer.start` (`queue.Queue`, same bounds), `QUEUE_EXPOSED` | bounds, timeout and `join`/`task_done` tests; idle server holds 9–10 FDs, with no per-queue pipes or feeder threads | ✅ |
| R3 | as R1 | `test_roundtrip[tls,no-tls]` on all four queues, both directions | ✅ |
| R4 | as R1; stock handshake and TLS layer; C1 fix keeps the server's context on its listener | `test_queue_tls.py` unmodified and passing; `compat` both directions TLS on and off; auth and TLS-mismatch probes match base; same-process TLS probe (C1) | ⚠️ C4 (diagnostics only) |
| R5 | `_InProcessServer.stop`, `_finalize_in_process` | exception, SIGUSR1 and normal exits release the port; same-port rebind in-process; stdio unchanged | ✅ |
| R6 | `tests/test_queue.py`, `tests/test_queue_start.py` (`unit`) | collected and passing | ✅ |
| R7 | `tests/test_server_fsm.py` | as cycle 1, re-run on 3.12–3.14 | ✅ |
| R8 | no existing test touched | full suite; `parity`; `--help` byte-identical; `test_queue_workload.py` | ⚠️ C3 (edge path: open-file limit) |
| R9 (local) | as R1 | orchestrator gate, 5 interleaved repeats × 6 configs: every median within run-to-run spread, all 60 runs clean; tree children 2 → 0; tree FDs lower in every config; independent `hs cluster` cross-check: feature ≥ base in all 20 runs | ✅ (see C3 on the server process's own FDs) |
| R9 (multi-node vs 2.8.3) | `tests/cluster/` drive | **pending external evidence**; the run is being re-staged at `12af7fb` | ⏳ |

Unmapped changes, none blocking. The fail-fast start (`QueueServerError`,
`unsupported_internals()`, the `ServerThread.start` re-raise) maps to the cycle 1 triage of C2
and P1. The "Server listening on …" line now comes after the queue is ready, so it follows "Empty
database - waiting for initial tasks". `QueueServer.start(initializer)` now runs its initializer
in the calling process, but no caller in `src/` passes one. Cycle 1's notes on `tests/bench`,
`tests/cluster`, `issues/zone.md` and `ROADMAP.md` stand.

### Findings

No AGENTS.md invariant violation was found.

#### [MEDIUM/CONFIRMED] C3 — At the open-file limit, the server process's own files fail, and its state machines crash
- **Where:** `src/hypershell/core/queue.py:397-420` (`_InProcessServer.serve`) and `:502-535`
  (`_serve_in_process`). The connections now end in the server process.
- **Failure scenario:** every queue connection now holds its descriptors in the server process.
  At `b819de6` they lived in the manager child.
  - Capacity barely moves, and is if anything slightly better. On the reviewer's counts the base
    child holds 49 + ~3 per client and the feature server holds 10 + ~3 per client, so under one
    `RLIMIT_NOFILE` the feature reaches `EMFILE` a few clients later.
  - What changes is the blast radius. At base, the exhausted child stops taking new connections,
    and the server's database and log files keep working. Now the server's own SQLite and log
    opens fail with `EMFILE`, its Receiver, Scheduler, Confirm and HeartMonitor threads die, and
    the run ends with exit 5.
  - At a 1024 soft limit this comes at roughly 330–340 clients on one server.
  - By reading only, not measured: the accept loop's catch-all `continue` retries `EMFILE` without
    backoff. The stock accepter does the same at base, but there it spins in the child; now it
    spins in the server process.
- **Evidence:** reviewer. The server was given `RLIMIT_NOFILE` 128, then served 300 tasks to 44
  `b819de6` clients with `--no-tls`:

  | | base | feature |
  |---|---|---|
  | Server | no state-machine crash; hung, killed at 90 s | `Uncaught exception from Receiver/Scheduler`, `OSError: [Errno 24] Too many open files` opening its exception log; exit 5 after 2.8 s |
  | Tasks done | 270/300 | 70/300 |
  | Clients | mixed exits | all 44 exit 5 |

  At limit 64 with 24 clients: base completes 285/300; feature completes 15/300, with
  `sqlite3.OperationalError: unable to open database file` from Confirm and HeartMonitor.
  Per-process FDs at idle, 4 clients and 8 clients: base server 51/53/52 and base child 49/61/73;
  feature server 10/22/34.
- **Touches invariant / requirement:** R8 (no behaviour change; every process exits cleanly) on an
  edge path, and R9 footprint. High-blast-radius `core/queue.py`.
- **For the human:** this shares its root cause with cycle 1's P3, which triage rejected as a
  reading of R9. That ruling settled how footprint is graded, not this failure mode. Two choices:
  - **Fix.** For example, raise the soft `RLIMIT_NOFILE` toward the hard limit at server start, or
    stop accepting at `EMFILE` so that the server's own files keep working.
  - **Accept.** Treat it as a documented limit and defer it. The rubric's deferral exception does
    not fit, because the behaviour is new, so this has to be a human ruling.

#### [LOW/CONFIRMED] C4 — A server TLS setup error is logged only at DEBUG
- **Where:** `src/hypershell/core/queue.py:407-409`. A failed accept is logged with `log.debug` and
  dropped. The server context is now built on first accept by the listener (`:195-201`).
- **Failure scenario:** a server whose TLS materials cannot build a context, for example
  `--tls-cert '<none>'`. At base, the manager child's accepter printed `TLSError: TLSConfig.cert
  and TLSConfig.key are required for the server side` to stderr (the accepter thread died with
  it). Now the server log shows only INFO lines, ending with "Server listening on …", while every
  client connection fails. Clients exit 5 on both builds, so only the server-side diagnostic is
  lost.
- **Evidence:** reviewer, `hs server t.in --tls-cert '<none>'` plus `hs client`, base vs feature
  frozen venvs. Output is as described above.
- **Touches invariant / requirement:** R4 (TLS behaviour) and the GOAL outcome ("nothing a user …
  can see changes"). High-blast-radius `core/queue.py`. TLS posture is unchanged: the server still
  fails closed.

### Observations (not findings)

- **Pre-existing, identical on `b819de6` (reviewer):**
  - A same-port restart with TLS can hang. When a dropped client's proxy is garbage-collected
    while another connection is in its TLS handshake, the decref opens a second connection and
    waits. Handshakes run one at a time in the accept path, so both wait for good. It reproduces
    deterministically on both builds.
  - A malformed result on `completed` crashes the Receiver, and the server then hangs.
- The reviewer's process-hygiene note is in the orchestrator's report, not here.

### Human-gate triggers

- **Triggered.** C3 and C4 are CONFIRMED in high-blast-radius `core/queue.py`. Human sign-off is
  required before any further step, including a return to `/hs-build`. This is cycle 2 without
  convergence, so it escalates per the bounded loop.
- **Before publish:** confirm the Python 3.11 CI job is green at `12af7fb`. The start-time check
  requires `Server._handle_request` and the other pinned privates, and a 3.11 that lacked one would
  refuse to start. 3.11 is not available locally, and CI could not be read from this session.
- P2, the multi-node evidence, is still pending.

## Triage (human gate) — cycle 2

The human gate for C3 and C4 (high-blast-radius `core/queue.py`) was **passed by the foreman's
ruling**, as judge under the maintainer's delegation, on 2026-11-02. Remediation returns to
`hs-build`, which reopens P1. Cycle 3 is the last review cycle before the bounded loop stops, so
the remediation stays inside the rulings below and adds nothing else.

| Finding | Ruling | Disposition |
|---|---|---|
| C3 · CONFIRMED | fix | The server process keeps its own files working when connections approach the open-file limit. Three parts, all in the in-process server: (a) at start, before binding, raise the soft `RLIMIT_NOFILE` toward the hard limit (bounded where the hard limit is unlimited or not settable, e.g. by the platform's per-process maximum); a refused raise is logged at DEBUG and is not an error. (b) Reserve a fixed headroom of descriptors for the server's own use: when an accepted connection would cross (soft limit − reserve), close it at once and log one rate-limited WARNING naming the limit; already-admitted clients are unaffected. (c) `EMFILE`/`ENFILE` from `accept` backs off (bounded sleep) instead of retrying in a tight loop. Red-first test: a server subprocess with soft and hard `RLIMIT_NOFILE` both lowered (so (a) is a no-op) and more clients than the headroom allows; the server's database and log keep working, no state machine dies, admitted clients' tasks complete, refused clients exit non-zero, and the server exits 0. No wire, auth, TLS or default-behaviour change below the limit (R8). |
| C4 · CONFIRMED | fix | A server TLS context that cannot be built is visible to the operator at ERROR or above under default logging, as base made it visible. Preferred: build the server context before "Server listening" and fail through the C2 path (exit 5, one CRITICAL line), provided that does not change when TLS materials are read or generated for a working configuration (including `<auto>`). If it would, log the build failure at ERROR once and keep failing connections closed. Test asserts the operator-visible line. |
| P2 · PLAUSIBLE | pending external evidence | Unchanged; the foreman reruns the multi-node matrix (P7) at the remediated head. Runs at `12af7fb` are diagnostic only. |
| Python 3.11 | verify | The build runs the full suite under Python 3.11 locally before closing P1, so the start-time internals check is shown to pass there; CI confirms on the PR. |

Not in scope for this remediation: the two pre-existing behaviours the reviewer observed identically
on `b819de6` (same-port TLS restart hang; malformed `completed` result). The foreman seeds them as
issues.

**Clarification (2026-11-02, at build).** C3(a) was withdrawn by the foreman at build on
2026-11-02. The open-file limit is per process and inherited: `hsx`, `hs cluster` and an
application that embeds the server run it in the process that also starts tasks or launchers, so a
raised soft limit would reach every task, a default-behaviour change below the limit. The server's
own files are protected by the descriptor reserve of (b) alone, with (c)'s back-off. The C4
fallback (ERROR once, connections fail closed) is accepted, since building the context at start
would change when the TLS materials are read. A client cut off partway through connecting hangs
until eviction on both builds; it is seeded as `issues/client-partial-connect-hang.md`.

## Review cycle 3 — changes-requested (2026-11-02)

- **Reviewed commit:** d3e00f982b5de608eb6587d789c5156103741d0e  ·  **Base:** `b819de6` (merge base with
  `origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 3 of ≤3. The bounded loop ends here without convergence, so this escalates to the
  human. No further review↔build cycle runs without the maintainer's decision.
- **Mode:** a fresh blind pass over the full spec-excluded diff (`git diff b819de6...d3e00f9 -- .
  ':(exclude)spec/'`) by one blind reviewer, which was not shown earlier cycles' findings (no
  `debate`, no `completeness` sub-pass). The orchestrator separately checked the cycle-2
  remediation against the triage and its clarification, re-ran the C3 and C4 reproductions against
  base, and ran the gates.

### Pre-flight

- **Base resolution.** The branch was graded against its merge base `b819de6`. The stale local
  `develop` (`222075b`) was not used.
- **Build state.** `TECH.md` is `status: in_progress`, P1–P6 `done`, P7 `pending`. R9's multi-node
  clause is still pending external evidence: the foreman is running the matrix at `d3e00f9`.
- **Contract drift.** `git log --oneline b819de6..HEAD -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`. The contract did not move.
- **Since cycle 2:** `d552903`, which builds C3(b), C3(c) and C4, and `d3e00f9`, which withdraws
  C3(a), documents the open-file limit and seeds `issues/client-partial-connect-hang.md`.

### Cycle 2 findings: remediation check (orchestrator)

C3 drive, the reviewer's from cycle 2. The server runs with soft = hard `RLIMIT_NOFILE`. It serves
`b819de6` clients with `-N 2`, 300 `echo` tasks, `--no-tls`, and is stopped at 90 s; clients are
stopped 30 s after it. Python 3.14.6 frozen venvs; every process ran in its own session.

| limit, clients | server | tasks done | clients exit 5 / hung | server log |
|---|---|---|---|---|
| 128, 44 | base | 206/300, 193/300 | 33 exited / 11 (codes not tallied); 28 / 16 | no error; two children (manager, tracker) |
| 128, 44 | feature | 252/300, 198/300 | 36 exited / 8 (codes not tallied); 27 / 17 | one refusal WARNING; no `Too many open files`, no `unable to open database file`, no uncaught exception; 0 children |
| 64, 24 | base | 288/300 | 22 / 2 | no error |
| 64, 24 | feature | 294/300 | 23 / 1 | one refusal WARNING; nothing else |

In cycle 2 the feature server died at 2.8 s (70/300) and completed 15/300, with its state machines
dead. Both builds now hang at 90 s in every run, on clients that were admitted for some
connections and then refused one. That is the client-side partial-connect hang, which is ruled out
of scope and seeded.

| Finding | Status | Evidence |
|---|---|---|
| C3(b) · descriptor reserve | **resolved** | The drives above. `_admits` (`core/queue.py:468-481`) closes a connection whose descriptor is at or above soft limit − 32. Admitted clients carry on, and one WARNING names the limit. `test_server_keeps_its_files_at_the_open_file_limit` passes on 3.11–3.14 and 3/3 repeats under load. |
| C3(c) · accept back-off | **resolved** | Accept made to fail with `EMFILE`, then `ENFILE`, with a peer pending: 4 attempts in 1 s each. The red-first record was about 750,000. In the drives the reserve kept real `EMFILE` away. |
| C4 · TLS setup failure visible | **resolved** (fallback, as accepted) | `hs server --tls-cert '<none>'` plus two clients, default log level, on 3.14.6 and 3.11.18. Base: one accepter traceback ending `TLSError: TLSConfig.cert and TLSConfig.key are required for the server side`, after which the accepter is dead; one client exits 5 and the other hangs until it is killed at 20 s. Here: exactly one line, `ERROR [hypershell.core.queue] Queue server cannot set up TLS and refuses every connection (TLSError: …)`, no traceback, both clients exit 5. `test_server_cli_tls_context_failure_is_logged` asserts the line. |
| C3(a) · raise the soft limit | **withdrawn** (clarification) | Absent: `src/` only reads the limit (`getrlimit`, `core/queue.py:374`) and never calls `setrlimit`. |
| Python 3.11 | **verified** | Full suite 549 passed on 3.11.18; `unsupported_internals() == []`. |
| P2 · multi-node evidence | pending external evidence | The foreman is running the matrix at `d3e00f9`. Not a finding. |

### Verification run

- **Suite (orchestrator)**, one venv per Python built from the worktree at `d3e00f9`:

  | Python | result |
  |---|---|
  | 3.11.18 | 549 passed |
  | 3.12.16 | 549 passed |
  | 3.14.6 | 549 passed |
  | 3.13.18, run 1 | 548 passed, 1 failed under heavy concurrent load. The failure was in `test_restart.py::test_restart_update_adds_novel_and_runs`, a stdout assertion in an unmodified test. It passed 8/8 alone and 12/12 as a file. |
  | 3.13.18, run 2 | hung at `test_queue.py::test_shutdown_releases_port[no-tls]` with C5's signature (below); stopped. |

  - **Reviewer:** 549 passed on 3.11.18, 3.13.18 and 3.14.6. On 3.12.16, the first run hung the same
    way; the rerun passed 549.
  - `test_queue_limits.py` + `test_queue_start.py`, 3 repeats: 15 passed each time.
- **`parity` and `compat` (orchestrator).**
  - `parity --base b819de6 --feature d3e00f9`: **PASS**. Task tables identical with TLS on and
    off, rc 0/[0, 0]; `--help` byte-identical for `server`, `client`, `submit` and `cluster`.
  - `compat --server d3e00f9 --client b819de6`: **PASS**, 200/200 with TLS on and off, on 3.11.18
    and on 3.14.6.
  - Reviewer: both PASS in both directions on 3.13.18. Its own grouped-retry drive gave identical
    tables, rc 0/[0, 0].
- **Throughput gate (orchestrator).** `bench_queue.py gate --base b819de6 --feature d3e00f9
  --repeats 5`, run on Python 3.14.6 with start method `spawn`. Both builds are frozen,
  non-editable venvs built from `git archive`. Result: **PASS**. All 60 runs were clean: every
  server and client exited rc 0, and every task completed.
  - **Load.** The reviewer had finished before the gate started, so the gate ran alone. The 1-min
    load average was 3.7–6 for most of the run and reached about 30 during the 32-client config,
    much of it from that config's own clients. No process that predated this review held a core.
    Runs were interleaved, so both builds shared the load.
  - **Table.** Steady-state tasks/s (p10–p90 of completions), median [min–max]:

  | config | base | feature | delta | tolerance | tree children b/f | tree FDs b/f | own FDs b/f | own threads b/f |
  |---|---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 265.2 [254.7–272.2] | 266.9 [264.1–271.4] | +1.7 | 17.5 | 2/0 | 117/23 | 52/23 | 7/19 |
  | 4x4 b=1 no-tls | 271.4 [267.4–273.9] | 267.8 [266.1–278.4] | −3.6 | 13.6 | 2/0 | 117/24 | 52/23 | 7/19 |
  | 16x1 b=1 tls | 268.2 [261.2–279.8] | 266.6 [250.8–273.6] | −1.6 | 22.8 | 2/0 | 153/60 | 52/59 | 7/55 |
  | 16x1 b=1 no-tls | 267.9 [250.0–269.2] | 257.1 [251.1–265.2] | −10.8 | 19.2 | 2/0 | 153/59 | 53/59 | 7/55 |
  | 4x4 b=50 tls | 759.0 [728.1–834.9] | 817.1 [714.0–840.4] | +58.1 | 126.4 | 2/0 | 116/22 | 51/22 | 7/19 |
  | 32x1 b=1 tls | 257.3 [250.3–267.3] | 253.9 [251.7–263.6] | −3.4 | 17.0 | 2/0 | 202/107 | 52/107 | 7/103 |

  - The largest drop is 16x1 b=1 no-tls, at −10.8 against a tolerance of 19.2; the two ranges
    overlap.
  - Tree RSS roughly halves, from ≈205 MB to ≈100 MB.
  - The server's own FDs and threads grow with clients, as in earlier cycles. They are reported,
    not gated, under the cycle-1 P3 ruling.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/queue.py` `_InProcessServer`, `SecureManager._serve_in_process` | reviewer probe: children 0 → 0 under fork and spawn, TLS on and off, 3.11 and 3.14; `hs server` tree: base 2 children, feature 0; `test_start_creates_no_child` | ✅ |
| R2 | `QueueServer.start` (`queue.Queue`, same bounds), `QUEUE_EXPOSED` | reviewer probe: types, bounds (2/2/0/0), `Empty`/`Full` after 0.31 s remote, `join`/`task_done` | ✅ |
| R3 | as R1 | round trip on all four queues, both directions, TLS on and off, 3.11 and 3.14 | ✅ |
| R4 | as R1; per-listener TLS context | `test_queue_tls.py` unmodified and passing; `compat` both directions; C4 resolved | ✅ |
| R5 | `_InProcessServer.stop`, `_finalize_in_process` | SIGUSR1 drain rc 0, SIGINT rc 4 on both builds, port free; stdio kept; same-port rebind | ⚠️ C5 (a same-port restart in one process can deadlock at start) |
| R6 | `tests/test_queue.py`, `test_queue_start.py`, `test_queue_limits.py` | collected, tagged and passing on 3.11–3.14 | ⚠️ C5 (the port-reuse test can hang) |
| R7 | `tests/test_server_fsm.py` | reviewer: data, timeout re-entry and sentinel halt for Receiver, Confirm and HeartMonitor; Scheduler post, re-entry on `Full`, halt on signal | ✅ |
| R8 | no existing test touched | `git diff --diff-filter=M b819de6...d3e00f9 -- tests/` empty; suite; `parity`; `--help` byte-identical | ⚠️ C5 (a hung suite run is not a bounded exit) |
| R9 (local) | as R1 | orchestrator gate (above); reviewer gate subset and `hsx` probe equal within spread | ✅ |
| R9 (multi-node vs 2.8.3) | `tests/cluster/` drive | **pending external evidence**, run by the foreman at `d3e00f9` | ⏳ |

Unmapped changes: none new. The reviewer listed the fail-fast start (`QueueServerError`, the
`ServerThread.start` re-raise) and the descriptor admission control as changes with no R-ID. They
implement the cycle-1 triage of C2 and P1 and the cycle-2 triage of C3(b) and C3(c), so they are
covered by those rulings and are not scope creep. Earlier cycles' notes on `tests/bench`,
`tests/cluster`, `issues/zone.md` and `ROADMAP.md` stand.

### Findings

No AGENTS.md invariant violation was found.

#### [HIGH/CONFIRMED] C5 — A garbage-collected client proxy in the server's own process can deadlock the accept loop
- **Where:** `src/hypershell/core/queue.py`: `serve` at `:457` (`with self._lock`) and `:462-463`
  (handler `Thread.start()`); `handle_request` at `:491` (`with self._lock`); `_serve_in_process`
  at `:593` (serve-thread `start()`).
- **Failure scenario:**
  - A `QueueClient` in the server's own process and its proxies refer to each other, so once the
    client is dropped they wait for the cyclic GC.
  - The GC runs their decref finalizer (`BaseProxy._decref`) in whichever thread triggers the
    collection. The finalizer dials the server and blocks until it gets the challenge (or, with
    TLS, the handshake).
  - Only the accept loop can answer it. When the collecting thread is the accept loop itself, a
    handler that holds `_lock` while the loop waits for it, or a thread being started by the loop
    or by `_serve_in_process`, nothing ever accepts that connection.
  - The server stops accepting for good, and `QueueServer.start()` can hang.
  - At `b819de6` the manager child serves the queues and answers the decref, so the same sequence
    completes.
  - This is new behaviour, not the pre-existing TLS restart hang observed identically on base in
    cycle 2: base passes every probe below, with TLS and without.
- **Evidence:**
  - Reviewer `p_gc.py`: each round a short-lived same-process client puts one item, then becomes
    garbage.
    - Feature: hung at round 7 (3.11), 60 (3.12) and 20 (3.14) at default GC thresholds, and at
      round 3 on 3.13 with threshold 50. Base: 100 rounds OK at threshold 50.
    - Stacks: `serve` `:463` → `Thread.start` → `Finalize` → `_decref` → `answer_challenge`; or a
      handler at `:491` in `_decref` while `serve` waits at `:457`.
  - Reviewer `p_restart.py`: the body of `test_shutdown_releases_port` in a loop.
    - Feature: hung at round 111 (3.11, no TLS), 112 (3.11, TLS) and 10 (3.13, TLS). Base: 120
      rounds OK, TLS on and off.
    - Stack: main thread at `:593` in `Thread.start`; the new thread in `_decref` →
      `secure_client`.
  - Orchestrator re-run on the 3.14.6 frozen venvs: `p_gc.py` at threshold 50 hangs the feature at
    round 12 (handler at `:491` in `_decref` → `answer_challenge`, `serve` at `:457`); base runs
    200 rounds OK. `p_restart.py` ran 200 rounds without a hang on both builds there.
  - Unprovoked:
    - The orchestrator's second 3.13 suite run stalled for more than 10 minutes at
      `test_shutdown_releases_port[no-tls]`.
    - The reviewer's first 3.12 run stalled the same way.
    - Each time the pytest process held a LISTEN socket and its own un-accepted ESTABLISHED
      connection to it.
- **Exposure:**
  - No shipped CLI path was found that triggers it. `hs server`, `hsx` and `--no-db` keep their
    in-process client alive for the server's lifetime, and `run_local` 50 times on one port did not
    hang.
  - Exposed:
    - the Python API, wherever a client is created and dropped next to a `ServerThread`
      (`ClientThread`, `submit_from(queue_config=…)`);
    - R5's same-port restart in one process;
    - the project's own suite: `test_shutdown_releases_port`, and `test_join_after_task_done`'s
      throwaway client. That is a CI-hang risk on each of four Pythons.
  - Severity: graded HIGH by the reviewer and kept here for the suite exposure. It would be MEDIUM
    weighed on CLI exposure alone.
- **Touches invariant / requirement:** R5, R6, R8 (bounded exit); high-blast-radius `core/queue.py`.

#### [LOW/CONFIRMED] C6 — The new seed has no `ROADMAP.md` entry
- **Where:** `issues/client-partial-connect-hang.md`; `ROADMAP.md`.
- **Failure scenario:** `AGENTS.md` (the artifact table) requires one `ROADMAP.md` entry per issue,
  written by whoever defers it. Every other seed in `issues/` has one; this seed has none, so the
  ordered index does not list it.
- **Evidence:** `grep -n client-partial-connect-hang ROADMAP.md` → nothing. A loop over `issues/*.md`
  finds no other seed without a reference (reviewer and orchestrator).
- **Touches invariant / requirement:** project convention (`AGENTS.md`); not core, no gate.

### Observations (not findings)

- **Covered by rulings (META F9):** none of the blind findings restates the cycle-1 P3 ruling, the
  C3(a) withdrawal or the partial-connect-hang ruling.
  - The reviewer noted that the seed's figures (feature 205/300 vs base 271/300 at limit 128) were
    not reproduced. They vary from run to run: this cycle measured base 206 and 193, feature 252
    and 198.
  - The behaviour behind them is the client partial-connect hang, which is ruled out of scope and
    seeded.
- **Pre-existing, identical on both builds (reviewer):** a silent TCP peer on a TLS server blocks
  every other client's connect until that peer closes, because the handshake runs in the accept
  path.
- The busy-port start now fails at once with rc 5 instead of hanging, per the cycle-1 C2 ruling.
  `SIGINT` exits faster (0.1 s vs 1.1 s), a timing difference only.
- **Commit trailer.** Commit `d552903` carries a `Co-Authored-By` trailer, against the convention in
  `TECH.md`. Every commit on the branch is signed. Rewording a pushed commit is the foreman's call.
- **Process hygiene.**
  - The reviewer left one wait loop running and stopped it when asked.
  - Three of the orchestrator's own monitor processes outlived their monitor. Signalling them was
    refused inside the sandbox, and they were stopped outside it.
  - Nothing from this review is still running.

### Human-gate triggers

- **Triggered.** C5 is CONFIRMED in high-blast-radius `core/queue.py`. This is cycle 3 of ≤3
  without convergence, so the bounded loop stops here. Remediation, deferral or any further cycle
  needs the maintainer's decision.
- P2, the multi-node evidence, is still pending.

## Triage (human gate) — cycle 3

Review did not converge within the bounded loop's three cycles, so the foreman stopped the run and
escalated to the maintainer. On 2026-11-02 the maintainer authorized **one further cycle (4), scoped
to C5 and C6 only**, and a pre-release cut from `develop` without this feature in the meantime. The
human gate for C5 (high-blast-radius `core/queue.py`) was then passed by the foreman's ruling, as
judge under that authorization. Remediation returns to `hs-build`, which reopens P1. Cycle 4 reviews
the remediation and the diff since `d3e00f9`; it is the last cycle, and a non-converged cycle 4 stops
the feature again.

| Finding | Ruling | Disposition |
|---|---|---|
| C5 · CONFIRMED | fix | **Property:** no thread of the in-process server (the accept loop, a connection handler, or a thread the server starts) ever blocks on a connection that only the server's own accept loop can answer, whatever thread the cyclic GC runs a proxy finalizer on. The build picks the mechanism; the obvious candidates are completing a same-process proxy's decref without dialing the listener, or keeping the accept loop free of handshakes, locks and thread starts that a finalizer can stall while deferring same-process decrefs off server threads. Whatever the mechanism, it must not change the wire, auth or TLS behaviour, a remote client's decref, or behaviour for `b819de6` clients (R4, R8). **Red-first tests:** the reviewer's two probes (a same-process client dropped to garbage each round under a low GC threshold, and the same-port stop and restart loop), turned into bounded tests that *fail* within a time limit instead of hanging, and that hang or fail at `d3e00f9`. **Gate before P1 closes:** each probe test passes ≥ 200 rounds on 3.11, 3.12, 3.13 and 3.14; `test_shutdown_releases_port` and `test_join_after_task_done` pass 100 consecutive runs on each of those versions; and the full suite, parity, compat and the throughput gate are re-run. |
| C6 · CONFIRMED | fix | Add the `ROADMAP.md` entry for `issues/client-partial-connect-hang.md`, in the form AGENTS.md requires. |
| P2 · PLAUSIBLE | pending external evidence | Unchanged. The foreman reruns the multi-node matrix (P7) at the remediated head; runs at `d3e00f9` are diagnostic only. |

Out of scope for cycle 4: everything else, including the observations already ruled (cycle 1 P3,
cycle 2 C3(a), the client partial-connect hang) and the pre-existing behaviour seen identically on
`b819de6`.

## Review cycle 4 — changes-requested (2026-11-02)

- **Reviewed commit:** 4904c97466d2de19e184699f05bdf41f7ede6bdf  ·  **Base:** `b819de6` (merge base with
  `origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 4, the one further cycle the maintainer authorized past the ≤3 bound, scoped to C5 and
  C6. It did not converge, so the feature stops here pending the maintainer's decision.
- **Mode:** remediation check of C5 and C6 by the orchestrator, plus one fresh blind reviewer over
  the delta `git diff d3e00f9..4904c97 -- . ':(exclude)spec/'`, with the full spec-excluded diff
  `b819de6...4904c97` as context. The reviewer was not shown earlier cycles' findings. Findings
  outside the delta, or restating a settled ruling, are Observations; the exception is a new HIGH or
  CRITICAL defect in the delta's interaction with existing code.

### Pre-flight

- **Base resolution.** Graded against the merge base `b819de6`; the stale local `develop` was not
  used.
- **Build state.** `TECH.md` is `status: in_progress`, P1–P6 `done`, P7 `pending`. R9's multi-node
  clause is pending external evidence: the foreman's matrix at `4904c97` is queued. Not a finding.
- **Contract drift.** `git log --oneline b819de6..4904c97 -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`.
- **Since cycle 3:** `4904c97` only (C5 hand-off of same-process decrefs, C6 ROADMAP entry). It is
  signed and carries no `Co-Authored-By` trailer.
- **Working tree.** `share/man/man1/{hs,hsx,hyper-shell}.1` are modified in the worktree by another
  agent and are in no commit. They were left alone; every run here used `git archive` extracts of
  `4904c97`, `d3e00f9` and `b819de6`.

### Cycle 3 findings: remediation check (orchestrator)

**C5 — resolved.** The property in the ruling holds for every proxy a `QueueClient` makes: no thread
of an in-process server blocks on a connection that only its own accept loop can answer, whatever
thread the cyclic collector runs a proxy finalizer on.

- **The argument (TECH P1) was read and attacked, not re-run.**
  - The finalizer reads `_serving` when it runs. A server is listed before it binds and unlisted
    only after its listener is closed. Listed means a `SimpleQueue.put`: no connection, lock or
    thread start. A stock decref runs only for a port that no in-process listener holds at that
    moment. No case was found where it reaches one.
  - The decref sender is a client-side thread and holds no lock while it dials. A finalizer run on
    the sender either hands off again or reaches no in-process server. So the accept loop's waits
    (TLS handshake, `_lock`, a handler's start) never depend on themselves.
  - Several servers in one process, ephemeral and fixed ports, and same-port restarts: a stock
    decref that read `_serving` just before another server bound could reach that server. It then
    waits on that server's accept loop, which another thread is starting and which no finalizer can
    stall. That is a delay, not a deadlock.
  - TLS: `secure_client` and `SecureListener.accept` take no lock, so the sender and the accept loop
    share none.
  - Remaining gaps:
    - P4 below: the finalizer is swapped only after the stock proxy constructor returns.
    - A proxy from another manager class keeps the stock finalizer. TECH's stated limit; HyperShell
      makes none.
- **Reviewer, by execution.** A profile hook forced a collection, with a dropped same-process client
  pending, at four points:
  - the accept loop (in `_admits`);
  - a handler (in `Queue.put` under `serve_client`);
  - the thread starting a same-port server, between its bind and the accept loop's start;
  - the remote-shutdown stop thread, inside `stop()` under `_lock`.

  Results:
  - `4904c97`: 40 rounds × TLS on and off × 3.11–3.14, all passed. Every stock decref ran on
    `hypershell-queue-decref`.
  - `d3e00f9`: the accept-loop, starter and stop points deadlock, with the decref inline on the
    stack.
- **Cycle 3's probes**, against `4904c97` and `d3e00f9` as red control:
  - Each run had its own process group and a bounded timeout.
  - `p_gc.py` ran at threshold 50 × 200 rounds. It has no TLS mode, so a copy took a `tls`
    argument; nothing else changed.
  - `p_restart.py` ran 300 rounds.

  | Python | build | `p_gc` no TLS | `p_gc` TLS | `p_restart` no TLS | `p_restart` TLS |
  |---|---|---|---|---|---|
  | 3.11.18 | d3e00f9 | hang, round 10 | OK | OK | hang, round 2 |
  | 3.11.18 | 4904c97 | OK | OK | OK | OK |
  | 3.12.16 | d3e00f9 | hang, round 4 | OK | hang, round 54 | hang, round 214 |
  | 3.12.16 | 4904c97 | OK | OK | OK | OK |
  | 3.13.18 | d3e00f9 | hang, round 5 | hang, round 56 | OK | OK |
  | 3.13.18 | 4904c97 | OK | OK | OK | OK |
  | 3.14.6 | d3e00f9 | hang, round 12 | hang, round 6 | OK | OK |
  | 3.14.6 | 4904c97 | OK | OK | OK | OK |

  - Red control: 10/16 runs hang, and every Python has at least one. The stacks are C5's:
    `_serve_in_process` `:593` → `Thread.__init__` → `Finalize` → `_decref` → `answer_challenge`, and
    `serve` `:457` waiting on a handler in `_decref`.
  - Extended, feature only: `p_gc` at thresholds 10, 50 and 700 and `p_restart`, 400 rounds,
    2 repeats each, on four Pythons with TLS on and off. 66/66 OK. One more run failed only with
    `EADDRNOTAVAIL`: concurrent probes had exhausted the host's ephemeral ports. Its three re-runs
    passed.
  - Several servers in one process (orchestrator's `p_multi.py`: 4 lanes, ephemeral and same-port
    restarts, short-lived clients dropped from many threads):
    - `d3e00f9`: 8/8 runs stuck (C5).
    - `4904c97`: 0/8 stalls. The live-client errors seen there are the Observation below.
- **`tests/test_queue_gc.py`** (reviewer): `test_queue.py` + `test_queue_gc.py` 38 passed on each of
  3.11–3.14. Against `d3e00f9` on 3.14, the two TLS stall probes fail, so the tests catch what they
  target. `test_dropped_client_decref_reaches_server` and
  `test_finalizer_hands_off_only_when_served_here` together passed 60/60 consecutive runs on 3.14.

**C6 — resolved.**
- `ROADMAP.md` has an entry for `issues/client-partial-connect-hang.md` in the form `AGENTS.md`
  requires: a heading, problem prose, and the Horizon / Depends / Refs line. Its `**Seed:**` link
  points at an existing file whose `status:` is `unshaped`.
- It sits after the coordinator-resilience entry.
- Every file in `issues/` is now referenced from `ROADMAP.md`.

### Verification run

- **Suite (orchestrator).** One venv per Python from the `4904c97` extract (`uv sync --frozen`):
  - 3.11.18, 3.12.16, 3.13.18 and 3.14.6: **557 passed** each, run two at a time.
  - `git diff --diff-filter=M b819de6...4904c97 -- tests/` is empty: no base test is modified.
    `test_queue.py` was added on this branch.
- **Repeats.** `test_shutdown_releases_port[no-tls,tls]` and `test_join_after_task_done`: 50/50
  consecutive pytest runs on each of 3.11, 3.13 and 3.14 (150 test executions per Python).
- **`parity --base b819de6 --feature 4904c97`: PASS.**
  - Task tables are identical with TLS on and off, rc 0/[0, 0] on both builds.
  - `--help` is byte-identical for `server`, `client`, `submit` and `cluster`.
- **`compat --server 4904c97 --client b819de6`: PASS.** 200/200 tasks exit 0, rc 0/[0, 0], with TLS
  on and off.
- **A remote client's decref is the stock path (reviewer).**
  - Setup: the server ran in a separate process, with clients from the `d3e00f9` and `4904c97`
    venvs on 3.14 and 3.11, TLS on and off. Tracing used `setprofile` and logged
    `ForkingPickler.dumps` and `send_bytes`.
  - Once the one wrapper frame (`core/queue.py` `_decref`) is removed, the traces are identical:
    the same thread (MainThread), the same `BaseProxy._decref` arguments, the same `Client` or
    `secure_client` call, the same handshake sizes, and byte-identical decref messages.
  - The server's object count goes 4 → 0 with both builds, and no sender starts.
  - `b819de6`'s client-side code is AST-identical to `d3e00f9`'s.
- **Private internals.**
  - `unsupported_internals() == []` on 3.11–3.14 (reviewer and suite).
  - The stdlib `_incref`, `_decref`, `_after_fork` and `Finalize.__init__`/`__call__` are identical
    on 3.12–3.14.
  - 3.11's `_decref` lacks `serial`; `*args` and the token-first check both cover it.
- **`test_threads_drain_after_shutdown`** is not a base test (`git show b819de6:tests/test_queue.py`
  fails; added in `bf0cae9`).
  - The change excludes exactly one thread by name, the process-lifetime sender. Handler threads are
    renamed by `accept_connection` and never take that name.
  - In a fresh process the old count fails only because of the sender.
  - The change does not weaken the check.
- **Throughput gate (orchestrator).** `bench_queue.py gate --base b819de6 --feature 4904c97 --repeats 5`,
  Python 3.14.6, start method `spawn`, frozen non-editable venvs from `git archive`: **PASS**. All 60
  runs were clean: every server and client exited rc 0, and every task completed.
  - **Load.** The gate ran alone: the reviewer had finished, no probe of this review was running,
    and a psutil scan just before it found none. The 1-min load average was sampled every 30 s
    (48 samples): min 2.2, median 3.8, max 17.6. The maximum came during the 32-client config,
    mostly from its own clients. Runs were interleaved, so both builds shared the load.
  - **Table.** Steady-state tasks/s (p10–p90 of completions), median [min–max]:

  | config | base | feature | delta | tolerance | tree children b/f | tree FDs b/f | own FDs b/f | own threads b/f | tree RSS MB b/f |
  |---|---|---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 280.6 [276.1–288.2] | 283.6 [273.2–292.7] | +3.0 | 19.5 | 2/0 | 117/23 | 53/23 | 7/19 | 199.5/95.6 |
  | 4x4 b=1 no-tls | 285.7 [270.5–295.2] | 280.7 [276.7–290.5] | −5.0 | 24.7 | 2/0 | 117/24 | 52/23 | 7/19 | 194.6/91.7 |
  | 16x1 b=1 tls | 282.0 [281.4–287.2] | 283.3 [280.7–288.2] | +1.3 | 14.1 | 2/0 | 153/59 | 53/59 | 7/55 | 202.3/98.7 |
  | 16x1 b=1 no-tls | 285.0 [282.8–287.3] | 285.5 [279.7–287.5] | +0.5 | 14.2 | 2/0 | 153/59 | 52/59 | 7/55 | 196.3/93.1 |
  | 4x4 b=50 tls | 934.5 [932.7–936.6] | 932.6 [930.3–934.2] | −1.9 | 46.7 | 2/0 | 117/22 | 52/22 | 7/19 | 204.1/100.1 |
  | 32x1 b=1 tls | 286.3 [285.4–287.4] | 286.0 [280.2–291.9] | −0.3 | 14.3 | 2/0 | 201/107 | 52/108 | 7/103 | 206/102.7 |

  - The largest drop is 4x4 b=1 no-tls, at −5.0 against a tolerance of 24.7; the ranges overlap.
  - Tree RSS roughly halves.
  - The server's own FDs and threads grow with clients, as before. They are reported, not gated,
    under the cycle-1 P3 ruling.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/queue.py` `_InProcessServer`, `SecureManager._serve_in_process` | `test_start_creates_no_child[fork,spawn]` in the suite on 3.11–3.14; gate: tree children 2 → 0 | ✅ |
| R2 | `QueueServer.start` (`queue.Queue`, same bounds), `QUEUE_EXPOSED` | suite (bounds, `Empty`/`Full` timeouts, `join`/`task_done`) on 3.11–3.14 | ✅ |
| R3 | as R1 | round-trip tests TLS on and off; `compat` TLS on and off | ✅ |
| R4 | as R1; per-listener TLS context; stock decref for remote clients | `test_queue_tls.py` unmodified and passing; `compat`; remote-decref trace identical to `d3e00f9` | ✅ |
| R5 | `_InProcessServer.stop`, `_finalize_in_process`, `_decref` hand-off | C5 property held under forced collections on every server thread; probes above; 50/50 repeats of `test_shutdown_releases_port` on three Pythons | ✅ |
| R6 | `tests/test_queue.py`, `test_queue_gc.py`, `test_queue_start.py`, `test_queue_limits.py` | collected, tagged and passing on 3.11–3.14; the new GC tests fail at `d3e00f9` | ✅ |
| R7 | `tests/test_server_fsm.py` | suite on 3.11–3.14 | ✅ |
| R8 | no existing test touched | no base test modified; suite; `parity`; `--help` byte-identical | ⚠️ C7, C8 (library-only edge cases in a serving process) |
| R9 (local) | as R1 | gate (above): every median within tolerance, tree children 2 → 0, tree FDs and RSS lower in every config | ✅ |
| R9 (multi-node vs 2.8.3) | `tests/cluster/` drive | **pending external evidence**: the foreman's matrix at `4904c97` is queued | ⏳ |

Unmapped changes: none. The `AGENTS.md` lines document the hand-off (see C8 on their accuracy), and
the `ROADMAP.md` entry is C6.

### Findings

No AGENTS.md invariant violation was found. Severity is set against exposure: none of these is on
a path the CLI takes (`hs server`, `hs client`, `hsx`, `hs cluster` never fork a serving process,
never reuse a port within one process, and keep their in-process client for the server's life).

#### [LOW/CONFIRMED] C7 — A forked child re-sends the decrefs it inherits, and the server releases objects that live clients hold
- **Where:** `src/hypershell/core/queue.py`:
  - `:531`, the module-level `_decrefs` queue;
  - `:564-570`, `_start_decref_sender`, which starts again in a forked child;
  - `:835-836`, where `QueueClient.connect` starts the sender whenever the inherited `_serving` is
    non-empty.
- **Failure scenario:**
  - A process that serves queues forks while handed-off decrefs are still waiting.
  - When the child connects a `QueueClient`, its inherited `_serving` starts a sender in the child,
    which sends the same decrefs again.
  - The server's refcounts fall below the number of live proxies, and it disposes of queues that
    clients in the parent still hold. Their next call fails with `RemoteError` (`KeyError`).
  - At `d3e00f9` a decref runs synchronously, so a fork has nothing to inherit.
- **Evidence:**
  - Reviewer `p10/fork_dup.py`, TLS on, 3.14 and 3.11. A silent TCP peer in another process holds
    up the inline TLS accept (pre-existing behaviour), so the sender backs up.
  - Output: "waiting in the hand-off queue at fork: 3". After the child exits, 1 of 4 idents is
    left, and the parent's live client gets `put on completed/heartbeat/confirmed FAILED
    RemoteError ["KeyError: '…'"]`.
  - Orchestrator re-run on 3.14: the same output.
- **Exposure:** code that embeds the library and forks a serving process (e.g. a `fork`-context
  `multiprocessing.Process`). The backlog has to exist at the fork; the reproduction made it with a
  stall.
- **Touches invariant / requirement:** R8 (no behaviour change); high-blast-radius `core/queue.py`.

#### [LOW/CONFIRMED] C8 — A handed-off decref can wait for a sender that never starts
- **Where:**
  - `src/hypershell/core/queue.py:548`: the hand-off decides by port number alone, and hands off
    everything while an ephemeral bind is listed as 0.
  - `:835-836`: the sender starts only in `QueueClient.connect`, and only when `_serving` is
    non-empty.
  - `AGENTS.md:283-285`.
- **Failure scenario:**
  - A process connects a client to a server in another process before it serves anything, so no
    sender starts.
  - It then serves queues on the same port number on another address, or binds an ephemeral port
    while that client's proxies are collected.
  - That client's decrefs are queued and never sent. The other server keeps the referents, and the
    stock decref's tidy-up of the dropping thread's cached connection never runs.
  - `AGENTS.md` says that outside the hand-off "it is the stock decref"; here it is neither.
- **Evidence:**
  - Reviewer `p9/probe9.py` on 3.14, re-run by the orchestrator with the same result:
    - `4904c97`: "remote server objects 1 s after the client was collected: 4; decrefs waiting: 4;
      sender: None".
    - `d3e00f9`: 0 objects left.
- **Exposure:** an unusual library configuration. The cost is a server-side refcount leak and one
  cached connection per thread. Correctness of the queues is unaffected.
- **Touches invariant / requirement:** R8; the delta's own `AGENTS.md` text; high-blast-radius
  `core/queue.py`.

#### [LOW/PLAUSIBLE] P4 — The finalizer is swapped only after the stock proxy constructor returns
- **Where:** `src/hypershell/core/queue.py:573` (`_defer_decref`), applied at `:837-840`.
- **Failure scenario:**
  - Inside the registered `_get_*` method the proxy is already increfed with the stock finalizer.
    The method then sends its own decref on a fresh connection.
  - If that connection fails while the server is alive, `connect()` raises, and the half-made proxy
    keeps the stock finalizer, held by the traceback.
  - If the cyclic collector later runs it on the accept loop, C5 recurs.
  - `AGENTS.md:283-285` ("never connects to them from there") overclaims by this window.
- **Evidence:**
  - Reviewer `p6/probe7.py` with a `ConnectionResetError` injected on that one decref, on 3.14 with
    TLS on and off: the watchdog fires; `hypershell-queue-server` is stuck in `_admits` → (gc) →
    `BaseProxy._decref` → `answer_challenge`/`secure_client`; new clients hang.
  - A natural trigger was not reproduced: `p6/probe8.py` with a lowered `RLIMIT_NOFILE` made 98
    refusals, all on the first handshake.
  - The window existed at `d3e00f9` too, where every proxy kept the stock finalizer, so this is an
    incompleteness of the fix, not a regression.
- **Touches invariant / requirement:** C5's property (narrow window); for the human.

#### [LOW/PLAUSIBLE] P5 — The hand-off delays same-process decrefs past their server, onto a same-port successor
- **Where:** `src/hypershell/core/queue.py:554-561` (one sender, one connection per decref, in
  order).
- **Failure scenario:**
  - A decref collected while its own server is alive used to reach that server at once. Now it
    waits for the sender.
  - Under decref churn the sender falls behind. The decref then reaches whatever server holds the
    port when it is sent, which can be a same-key successor in the same process.
  - When the successor's queues reuse the old ids, it releases a live client's queue: the class in
    the Observation below.
- **Evidence:**
  - Orchestrator `p_multi_attr.py`: 1,800 servers, 4 lanes, same-port restarts, 3.13 and 3.14, no
    TLS. Collections ran only in one dedicated thread, so neither build can deadlock.
  - Each decref that reached a server other than its issuer was attributed by when it was
    collected:

    | Python | build | stale, collected while own server listened | stale, collected after it stopped | live-client `RemoteError` |
    |---|---|---|---|---|
    | 3.13.18 | d3e00f9 | — | 4593 | 9 |
    | 3.13.18 | 4904c97 | 2160 and 2882 | 1598 and 2746 | 43 and 43 |
    | 3.14.6 | d3e00f9 | — | 4257 | 0 |
    | 3.14.6 | 4904c97 | 2678 | 2220 | 22 |

  - With normal collection thresholds the deferred share was 0–36 of about 3,200–3,500 stale
    decrefs per run.
  - The increase is statistical. No single failure was traced to a deferred decref.
- **Touches invariant / requirement:** R5 (same-port restart in one process), R8; for the human.

### Observations (not findings)

- **Stale decrefs after a same-port, same-key restart in one process.**
  - Mechanism: a dropped same-process client of the old server, collected after the new server
    starts, sends decrefs under the old ids to the new server. When a new queue reuses an old id
    (frequent in one address space), a live client's next call fails with `RemoteError`
    (`KeyError`).
  - It is outside the delta: it reproduces at `d3e00f9` (P5 table; reviewer's `stale_mech.py` shows
    both builds send the stale decref).
  - It was not seen at `b819de6`: 0 failures over about 1,500 servers per run, with 396–591 stale
    decrefs. That build cannot reliably restart on the same port in one process either; a start
    fails with `EOFError` from the child.
  - So it comes with R5's new same-port restart. It needs the same key, the same port and live
    clients on the successor:
    - `run_local` uses a random key, and 150 `run_local` runs on one port passed;
    - no CLI path restarts in-process.
  - Recommended: the foreman seeds it.
- The hand-off makes decref delivery asynchronous. In the collector-thread stress the feature run
  took 103–158 s against 31 s at `d3e00f9` for the same work. No CLI path drops clients at that
  rate, and the gate is unaffected.
- **Pre-existing, identical on `b819de6`:** the TLS accept loop does its handshake inline, so a
  silent TCP peer stalls every new connection, and now the decref sender too, until it disconnects.
- **Process hygiene.**
  - Concurrent probe runs exhausted the host's ephemeral ports once (`EADDRNOTAVAIL`). The affected
    runs were discarded and re-run alone.
  - The reviewer's first launch used a runner whose name shadowed a stdlib module. It failed on
    import before starting anything.
  - Nothing from this review is still running (psutil scan).

### Human-gate triggers

- **Triggered.** C7 and C8 are CONFIRMED in high-blast-radius `core/queue.py`. This is the final
  authorized cycle, so the feature stops here. Remediation, deferral or acceptance of C7 and C8 (and
  triage of P4 and P5) needs the maintainer's decision.
- C5 and C6 are resolved. Every gate passed: suite, repeats, parity, compat and throughput.
- P2, the multi-node evidence, is still pending.

## Triage (human gate) — cycle 4

Cycle 4, the authorized final cycle, did not converge: C5 and C6 are resolved, and the blind pass
found two LOW CONFIRMED defects inside the delta. The foreman stopped and escalated. On 2026-11-02
the maintainer chose to fix C7 and C8, seed the rest, and run **one further review (cycle 5) scoped
to the C7/C8 remediation diff only**. The foreman rules as judge under that authorization.
Remediation returns to `hs-build`, which reopens P1. A non-converged cycle 5 stops the feature again.

| Finding | Ruling | Disposition |
|---|---|---|
| C7 · CONFIRMED | fix | A forked child of a process that serves queues never sends a decref it inherited from its parent: hand-offs queued before the fork, and the hand-off state itself, are discarded in the child (e.g. `os.register_at_fork(after_in_child=…)`), and the child's own later proxies follow the normal rules. Red-first test: the reviewer's reproduction (a live parent client keeps its queues usable after a forked child exits), bounded; skipped where `fork` is unavailable. |
| C8 · CONFIRMED | fix | The hand-off matches on the **full listener address** a proxy's token names, not the port alone, and a queued hand-off is never stranded: the sender starts on the first enqueue (or the enqueue path otherwise guarantees a live sender), not only in `connect()`. A decref for any address this process does not serve is the stock path, on the finalizing thread, byte-for-byte as before. Correct the `AGENTS.md` sentence to match. Red-first test: the reviewer's reproduction (a client of a remote server whose port number equals an in-process server's port leaks no objects on the remote server). The C5 property and its tests must still hold. |
| P4 · PLAUSIBLE | seed | The finalizer swap happens after the proxy constructor returns; an injected connection failure inside that window leaves a stock finalizer. Pre-existing window, fault-injection only. Seed as `unshaped` with the reviewer's scenario. |
| P5 · PLAUSIBLE | seed | The hand-off can delay decrefs past their own server's lifetime under collection confined to one thread (stress only). Seed as `unshaped`, or fold into P4's seed if the build finds they share a cause. |
| Observation · stale decref after same-key, same-port restart | seed | Present at `d3e00f9`, not at `b819de6`; no CLI path (`run_local` uses a random key). Seed as `unshaped` with the reviewer's reproduction. |
| P2 · PLAUSIBLE | pending external evidence | Unchanged; the foreman reruns the multi-node matrix at the remediated head. |

Every seed added gets its `ROADMAP.md` entry in the same commit (C6's rule). Out of scope for cycle 5:
everything outside the C7/C8 diff, and every ruling already made.

## Review cycle 5 — changes-requested (2026-11-02)

- **Reviewed commit:** 43d2d85696dbbbf20550ba7c9a52559677e20ad2  ·  **Base:** `b819de6` (merge base with
  `origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 5, the last exception to the bounded loop the maintainer authorized, scoped to the C7/C8
  remediation diff. It did not converge, so the feature stops here pending the maintainer's decision.
- **Mode:** remediation check of C7 and C8 by the orchestrator, including an attempt to break TECH
  P1's argument (items 1–8), plus one fresh blind reviewer over the delta
  `git diff aa55e86..43d2d85 -- . ':(exclude)spec/'`, with the full spec-excluded diff
  `b819de6...43d2d85` as context. The reviewer was not shown earlier cycles' findings. Findings
  outside the delta, or restating a settled ruling, are Observations.

### Pre-flight

- **Base resolution.** Graded against the merge base `b819de6`; the stale local `develop` was not
  used.
- **Build state.** `TECH.md` is `status: in_progress`, P1–P6 `done`, P7 `pending`. R9's multi-node
  clause waits on the foreman's matrix at `43d2d85`. Not a finding.
- **Contract drift.** `git log --oneline b819de6..43d2d85 -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`.
- **Since cycle 4:** `43d2d85` only. It is signed and carries no `Co-Authored-By` trailer.
- **Working tree.** Clean. Every run used `git archive` extracts of `43d2d85`, `aa55e86`, `d3e00f9`
  and `b819de6`, with one `uv sync --frozen` venv per commit and Python.
- **Environment.** On this host, connections to the host's own non-loopback addresses are
  intercepted by a local network filter (they connect and never reach the listener), so every
  reachability probe ran over loopback and the wildcard address. Probes that must bind a second
  address bind the LAN address but never connect to it.

### Cycle 4 findings: remediation check (orchestrator)

**C7 — resolved, as ruled.**
- `os.register_at_fork(after_in_child=_forget_after_fork)` (`core/queue.py:620-634`) gives a forked
  child an empty listing, a new queue, no sender and a new lock. The parent's waiting decrefs are
  never sent from the child, and the child's own proxies follow the normal rules.
- The red-first test `test_forked_child_never_resends_decrefs` is bounded (watchdog and process-group
  backstop) and skipped where `fork` is unavailable.
- **Reproduction, rebuilt from cycle 4's prose** (`fork`): a serving process drops a client while its
  decrefs wait, forks a `fork`-context child that connects, uses and drops a client, and then the
  parent's live client is checked. TLS on: the backlog is made by a silent TCP peer holding the
  inline TLS accept, as in cycle 4. TLS off: the probe holds the parent's sender at a gate.

  | build | 3.11.18 | 3.12.16 | 3.13.18 | 3.14.6 |
  |---|---|---|---|---|
  | `aa55e86` (red control), TLS on and off | fail ×2 | fail ×2 | fail ×2 | fail ×2 |
  | `43d2d85`, TLS on and off | pass ×2 | pass ×2 | pass ×2 | pass ×2 |

  - Red signature on every Python: "decrefs waiting at the fork: 3"; the child inherits a listing of 1
    and 3 waiting decrefs and starts a sender; after it exits, 1 of 4 objects is left and the live
    client's `completed`, `heartbeat` and `confirmed` puts fail with `RemoteError`.
  - Feature: the child inherits a listing of 0 and nothing waiting; 4 objects are left and every put
    succeeds.
- **A forked child that itself serves queues** (`fork --child-serves`), 4 Pythons × TLS on and off,
  8/8 pass. The child's first server starts its own sender. A collection forced on the child's own
  accept loop, with a dropped same-process client pending, returns. The child's server ends with 0
  objects once its clients are dropped. The parent's live client keeps 4 objects and working puts.

**C8 — the stranding is resolved; the full-address match is not exact (C9).**
- **Never stranded: holds.** `_serve_in_process` starts the sender (`:713`) before it lists its server
  (`:715`), nothing is handed off while nothing is listed, and the sender loops for the life of the
  process. `QueueClient.connect` no longer starts it.
- **Reproduction, rebuilt from cycle 4's prose** (`c8`): a client of a server in another process at
  `127.0.0.1:P` connects before this process serves anything, then is collected while this process
  serves. 4 Pythons × TLS on and off; "left" is the other server's object count 1 s later.

  | this process serves | `aa55e86` (red control) | `43d2d85` | stock decref ran on (`43d2d85`) |
  |---|---|---|---|
  | `P` at the LAN address | 4 left, 4 waiting, no sender (8/8) | 0 left (8/8) | the finalizing thread |
  | `P` at the wildcard address | 4 left, 4 waiting, no sender (8/8) | 0 left (8/8) | the sender |
  | an ephemeral bind at the wildcard address, collected mid-bind | 4 left, 4 waiting, no sender (8/8) | 0 left (8/8) | the sender |
  | an ephemeral bind at `127.0.0.1`, collected mid-bind | 4 left, 4 waiting, no sender (8/8) | 0 left (8/8) | the sender |
  | `P` at the LAN address, the dialed host unresolved at connect | n/a | 0 left (8/8) | the sender |

- **"The stock path, on the finalizing thread, for any address this process does not serve":**
  - Holds for a resolvable address at another IP (row 1) and for a process that serves nothing
    (`remote`, 16/16 on both builds): the stock decref runs on the dropping thread, nothing is
    handed off, and no sender thread exists.
  - Rows 2–5 are wider than the reach, which TECH P1 item 2 states as a design choice: a wildcard
    listener beside another process's specific bind (macOS allows it), a bind in progress at port 0,
    and an unresolved host all hand off. Each decref is still delivered. See Observations.
- **The port-only fallback does not reintroduce C8.** An unresolved or unrecorded host matches on
  its port alone, but a sender is alive whenever anything is listed, so the decref is delivered
  (row 5). A real client reaches the fallback only when `gethostbyname` fails where the connect
  succeeded: a transient resolver failure, or a TLS client of an IPv4-mapped literal or an
  AAAA-only name. `create_connection` reaches the IPv4 listener through `::ffff:127.0.0.1`, and the
  record is `None`. "Unrecorded" cannot happen for a proxy that carries the hand-off finalizer.
- **`AGENTS.md`** was corrected, but it now overstates the match ("matched on the full address"); C9.
- **Red-first test** `test_shared_port_number_leaks_nothing`: on macOS it falls back to the wildcard
  address, so there it checks delivery, not the stock path; only Linux (`127.0.0.2`) exercises the
  stock path.

**TECH P1's argument, attacked item by item.**
- **Item 1 (the listing covers every address a connection can reach): holds.** The pre-bind entry
  resolves the host as the bind does (`''` → `0.0.0.0`), and after the bind the entry is the bound
  address.
- **Item 2 (the record is "the same resolution the stock connect uses"): fails for TLS.**
  `_note_dialed` records `gethostbyname`, the name's first IPv4 address. `secure_client` dials with
  `socket.create_connection`, which tries every address the name resolves to, in order. A TLS
  client of a name with several addresses can reach an in-process server at an address the record
  does not name. That is C9.
- **Items 3–5: hold** wherever item 2 does.
- **Item 6: holds** (the C8 table).
- **Item 7: holds** (the C7 table and the serving child).
- **Item 8: holds.** In a process that serves nothing, the finalizer is the stock decref on the same
  thread with the same arguments. The only addition is one name lookup and one probe bind per
  connect.
- **Several servers in one process: no stall.** Three extra servers at `127.0.0.1`, the wildcard
  address and `localhost` each had a dropped client pending at every forced collection.
- **IPv6.**
  - Both builds' listeners are AF_INET only, and an `::1` bind fails cleanly on the feature with
    nothing listed.
  - `localhost`, dual-stack here, matches.
- **The sender started before listing: holds** (by reading). A collection during
  `_start_decref_sender` sees nothing of the new server listed, and any other listed server already
  started the sender.

**C5 still holds for every configuration it was ruled on; C9 is the exception.**
- **Probe:** the cycle-4 probe rebuilt (`gc4`). With collection disabled elsewhere, one collection is
  forced, with a dropped same-process client pending, at each of the four points: the accept loop
  (`_admits`), a handler (server-side `Queue.put`), the starting thread between its bind and its
  accept loop (same-port restart), and the remote-shutdown stop thread inside `stop()` under `_lock`.
- **Address forms, 10 rounds each:**
  - bind and dial `127.0.0.1`;
  - `localhost` both sides;
  - wildcard bind, dial `127.0.0.1` or `localhost`;
  - bind `127.0.0.1`, dial `0.0.0.0`;
  - bind `''`, dial `localhost`;
  - dial `::ffff:127.0.0.1` (TLS only);
  - three extra servers.
- **`43d2d85`:**
  - 240/240 runs pass: 4 Pythons × 4 points × the address forms × TLS on and off.
  - Every stock decref ran on the main thread or on `hypershell-queue-decref`.
- **Red control `d3e00f9` (3.14):** the accept, starter and stop points stall with the decref inline
  (6/8); the handler point passes.
- **`aa55e86` baseline (3.11, 3.14):** 16/16 pass.
- **`tests/test_queue_gc.py`:** passes in each full suite, and 20/20 consecutive runs on each of
  3.11.18 and 3.14.6 (below).

### Verification run

- **Suite (orchestrator).** `43d2d85` extract, venv activated, two Pythons at a time: 3.11.18,
  3.12.16, 3.13.18 and 3.14.6 each **560 passed**. `git diff --diff-filter=M b819de6...43d2d85 --
  tests/` is empty: no base test is modified.
- **`tests/test_queue_gc.py` repeats:** 20/20 consecutive runs (10 passed each) on each of 3.11.18
  and 3.14.6, the two run side by side after the suites.
- **`parity --base b819de6 --feature 43d2d85`: PASS.** Task tables identical with TLS on and off,
  rc 0/[0, 0] on both builds; `--help` byte-identical for `server`, `client`, `submit` and
  `cluster`.
- **`compat --server 43d2d85 --client b819de6`: PASS.** 200/200 tasks exit 0, rc 0/[0, 0], TLS on
  and off.
- **Throughput gate (orchestrator).** `bench_queue.py gate --base b819de6 --feature 43d2d85 --repeats
  5`, Python 3.14.6, start method `spawn`, frozen non-editable venvs from `git archive`: **PASS**, all
  60 runs clean.
  - **Load.** The gate started at a 1-min load average of 4.05, below the bound of 6. Sampled every
    30 s (52 samples): min 4.05, median 6.83, max 30.82. The peaks were other agents' builds; this
    review's only concurrent work was single-process probes (the orchestrator's and the blind
    reviewer's). Runs were interleaved, so both builds shared the load.

  | config | base | feature | delta | tolerance | tree children b/f | tree FDs b/f | own threads b/f | tree RSS MB b/f |
  |---|---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 250.7 [224.5–273.2] | 264.9 [239.1–275.2] | +14.2 | 48.7 | 2/0 | 117/23 | 7/20 | 199.4/95.7 |
  | 4x4 b=1 no-tls | 267.3 [263.9–277.9] | 263.6 [259.5–267.7] | −3.7 | 14.0 | 2/0 | 117/23 | 7/20 | 194.5/91.6 |
  | 16x1 b=1 tls | 276.6 [251.1–280.3] | 276.5 [262.6–281.6] | −0.1 | 29.2 | 2/0 | 153/59 | 7/56 | 203.5/99.6 |
  | 16x1 b=1 no-tls | 262.0 [248.7–271.8] | 264.7 [252.3–269.3] | +2.7 | 23.1 | 2/0 | 153/60 | 7/56 | 196.6/93.7 |
  | 4x4 b=50 tls | 818.9 [788.0–842.5] | 818.6 [772.9–827.0] | −0.3 | 54.5 | 2/0 | 116/22 | 7/20 | 204.2/100.3 |
  | 32x1 b=1 tls | 247.0 [239.5–259.4] | 251.0 [242.5–273.8] | +4.0 | 31.3 | 2/0 | 201/107 | 7/104 | 207/103.4 |

  Steady-state tasks/s, median [min–max]. The server's own threads include the decref sender, which
  the server now starts (20 in 4x4). They are reported, not gated (cycle-1 P3 ruling).
- **Seeds.** `ROADMAP.md` has an entry for each of the three new seeds
  (`queue-proxy-finalizer-swap-window`, `queue-decref-handoff-delay`,
  `queue-stale-decref-same-port-restart`), each with a heading, problem prose, the Horizon / Depends
  / Refs line and a `**Seed:**` link to an existing `status: unshaped` file. Every file in `issues/`
  is referenced from `ROADMAP.md`. The blind reviewer graded the three as prose and found them
  accurate.

### Requirement → evidence matrix (the delta)

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `_serve_in_process`, the hand-off | suite on 3.11–3.14; gate: tree children 2 → 0 | ⚠️ C9 (a stall of the in-process server, TLS client of a multi-address name) |
| R4 | stock decref when nothing is listed | `remote` probe, 16/16; `compat` TLS on and off; `test_queue_tls.py` unmodified | ✅ |
| R5 | sender before listing; `register_at_fork` | C7 and C8 tables; same-port-restart starter point, 240/240 | ✅ |
| R6 | `test_queue_gc.py` (two new probes, rewritten rule test) | tagged `unit`; red at `aa55e86`, green at `43d2d85` | ✅ |
| R8 | no base test touched | suite; `parity`; `--help` byte-identical | ⚠️ C9 (library-only) |
| R9 (local) | as R1 | gate above | ✅ |
| R9 (multi-node vs 2.8.3) | `tests/cluster/` drive | **pending external evidence**: the foreman's matrix at `43d2d85` | ⏳ |

Unmapped changes: none. The three seeds and their `ROADMAP.md` entries are the cycle-4 triage's
deferrals.

### Findings

No AGENTS.md invariant violation was found.

#### [MEDIUM/CONFIRMED] C9 — The address match can be narrower than the reach: a TLS client of a multi-address name brings back C5's stall
- **Where:** `src/hypershell/core/queue.py`:
  - `:563-566`, `_note_dialed`: it records `socket.gethostbyname(host)`, the name's first IPv4
    address only.
  - `:569-582`, `_served_here`: it matches that one address against the listing.
  - `:598`: the stock decref runs inline when the match fails.
  - `:257`, `secure_client`: it dials with `socket.create_connection`, which tries every address
    the name resolves to, in order.
  - `AGENTS.md:283-288` ("matched on the full address").
- **Failure scenario:**
  - A same-process client dials a host name with two IPv4 addresses, with TLS (the default).
  - The first address does not serve the port and fails fast (refused, or unreachable). The second
    is an in-process server bound to that address, or to the wildcard address when the first is
    not this host's.
  - The connection reaches the in-process server. The record names the first address, so
    `_served_here` is False.
  - When the cyclic collector runs the dropped client's proxy finalizers on a server thread, the
    stock decref dials the server from that thread: C5's self-deadlock. A DNS round-robin answer
    that differs between the connect and the record does the same, TLS or not (by reading).
  - At `aa55e86` the match was by port, so these decrefs were handed off.
- **Evidence:** the host's resolver cannot be changed here (no hosts-file edit), so the name's
  answer is supplied in-process: `getaddrinfo` returns both addresses and `gethostbyname` the first,
  as a real resolver does for two A records. The code under test is unmodified.
  - **Orchestrator, forced collection** (`gc4`, 3 rounds, TLS). Two configurations: a wildcard bind
    dialed as [`169.254.1.1`, `127.0.0.1`], and a bind at `127.0.0.1` dialed as [`169.254.1.1`,
    `127.0.0.1`].
    - `43d2d85`, every Python: the accept, starter and stop points stall (24/24). The handler point
      passes (8/8).
    - The accept loop's stack: `serve` → `_admits` → (gc) → `Finalize` → `queue.py:598 _decref` →
      `BaseProxy._decref` → `secure_client` → `do_handshake`.
    - `aa55e86`: 32/32 pass.
  - **Blind reviewer**, independently (bind `127.0.0.1`, name [LAN address, `127.0.0.1`]):
    - Forced: stall on `43d2d85` with 3.11, 3.13 and 3.14; `aa55e86` passes.
    - **Natural collection, no forced collection** (the suite's `probe_dropped_client` shape,
      threshold 10): `43d2d85` stalls at round 66 on 3.13. The orchestrator's re-run on 3.14 stalled
      at round 66 too:
      - a handler, in `handle_request` (`:496`) → (gc) → `_decref:598` → `secure_client` →
        `answer_challenge`, waits for its decref connection;
      - the accept loop, which accepted that connection, waits at `serve:462` on `_lock`;
      - new clients hang in `connect`.
    - `aa55e86`: 200/200 rounds on 3.13 and 3.14.
- **Exposure:** library use only. The only CLI path with an in-process client, the local `hs
  cluster`, dials `('localhost', port)`, which the bind and the record resolve alike. It needs TLS,
  a multi-address name and a same-process client collected as cyclic garbage on a server thread.
  - Rated MEDIUM, a bug on an edge path: it is C5's stall (HIGH at cycle 3) on a narrow
    configuration.
  - CONFIRMED although the resolver answer is simulated. Unlike P4's injected fault, the code under
    test runs unmodified and the simulation stands in for a host's resolver configuration. Two
    independent reproductions, one with natural collection.
- **Touches invariant / requirement:** the C5 property and its ruling ("whatever thread the cyclic
  GC runs a proxy finalizer on"); R1, R8; the C8 ruling's full-address clause; high-blast-radius
  `core/queue.py`.
- **Not prescribed, for the build:** the record could hold every IPv4 address the name resolves to,
  as `create_connection` would try them, or fall back to the port for a name with more than one.
  Round-robin needs a decision.

### Observations (not findings)

- **Wider than the reach** (C8 table, rows 2–5): a decref for an address another process serves is
  handed off rather than run on the finalizing thread when this process listens on the same port
  at the wildcard address beside that process's specific bind, binds port 0 at the same or the
  wildcard address, or could not resolve the host. It is delivered (0 left), so C8 does not recur.
  The ruling's "on the finalizing thread" is met only for non-matching addresses. TECH P1 item 2
  chose this for safety. The judge decides whether the wording or the code yields.
- **A forked child keeps the in-process server's port** (outside the delta: identical at `aa55e86`).
  - Repro: a `fork`-context child of a serving process inherits the listening socket. While the
    child lives after the server shuts down, the port cannot be re-bound (`EADDRINUSE`), and a new
    client connects and then gets no reply.
  - `b819de6`: the re-bind succeeds and the connect is refused.
  - This is R5's port release. HyperShell never forks a serving process: it launches tasks and
    clients with `subprocess` and exec, and the socket is `O_CLOEXEC`. Library-only.
  - Recommended: the foreman seeds it.
- **A same-port restart leaves a stale cached connection under another name** (outside the delta:
  identical at `aa55e86`).
  - `_finalize_in_process` drops cached proxy connections only under the bind host and the bound
    address.
  - A same-process client that dials the server by another name (bind `127.0.0.1`, dial
    `localhost`; or bind the wildcard, dial `127.0.0.1`) reuses its thread's connection to the old
    server after a same-port restart. Its next proxy call fails with `EOFError`.
  - No CLI path restarts in-process. Recommended: fold into `queue-stale-decref-same-port-restart`.
- **Already recorded** (cycle 4, not restated as findings): the inline TLS handshake on the accept
  loop; the stale decref after a same-key restart (seeded).
- **Process hygiene.** Every probe, suite and bench ran in its own process group under a timeout.
  - An early fork probe hung its child on a `faulthandler` watchdog inherited across the fork; the
    group was killed and the probe disarmed the watchdog for the fork. The blind reviewer hit the
    same once.
  - The first suite attempt ran without the venv on `PATH`; its CLI tests could not find `hs`.
    Discarded and re-run.
  - A psutil scan after the last run found nothing from this review still running.

### Human-gate triggers

- **Triggered.** C9 is CONFIRMED in high-blast-radius `core/queue.py`, inside the delta and new in
  it. This was the last authorized exception to the bounded loop, so the feature stops here. Fixing,
  deferring or accepting C9 needs the maintainer's decision.
- C7 is resolved as ruled. C8's stranding is resolved, and its full-address clause is broken only
  by C9. Every gate passed: suite on four Pythons, `test_queue_gc.py` repeats, parity, compat and
  throughput.
- P2, the multi-node evidence, is still pending.

## Triage (human gate) — cycle 5

Cycle 5 did not converge: C7 is resolved, C8's stranding is resolved, and C9 (MEDIUM, CONFIRMED)
shows that the address match itself is the fragile part, because C8 and C9 are two faces of
deciding "is this my server?" from addresses. The foreman stopped and escalated. On 2026-11-02 the
maintainer chose to **replace the address match with a thread-based rule**, with **one further
review (cycle 6)** scoped to that change and a **pre-agreed fallback**: if cycle 6 finds only LOW or
MEDIUM defects with library-only exposure (no shipped CLI path), they are seeded with `ROADMAP.md`
entries and the feature proceeds to publish without another cycle. A HIGH or CRITICAL finding, or
any finding reachable from a shipped CLI path, stops the feature.

| Finding | Ruling | Disposition |
|---|---|---|
| C9 · CONFIRMED | fix by redesign | **Rule:** a queue client's proxy decref is handed off to the `hypershell-queue-decref` sender if and only if the finalizer runs (a) on a thread the in-process queue server owns (the accept loop, a connection handler, any thread the server starts), or (b) on any thread while it is inside the server's start or stop critical section (a thread-local marker set for the duration). Every other decref is the stock path on the finalizing thread, byte for byte. **No address, port or name matching anywhere**, so remove `_serving`'s address use, `_note_dialed` and `_served_here`'s address logic. The sender's lifecycle (started by the server before it can accept; C7's `register_at_fork` reset in the child) stays. **Property:** no server-owned thread, and no thread inside start/stop, ever dials a listener, so C5 holds by construction whatever the client's address, resolution or TLS mode. TECH P1's argument is rewritten to this rule. **Red-first tests:** C9's reproduction (a same-process TLS client of a multi-address name, with a GC forced on a server thread) fails at `43d2d85` and passes after the change, plus the existing C5, C7 and C8 tests. Forced-GC at the accept loop, a handler, the starter and stop: ≥ 200 rounds on each of 3.11–3.14, TLS on and off. |
| C8 (re-read) | covered | Under the thread rule, a remote client's decref finalized on a non-server thread is stock (no stranding, no leak). A decref finalized on a server thread for a *remote* server goes to the sender and is delivered, so nothing leaks. Keep C8's test. |
| Observation · forked child keeps the listener | seed | `unshaped`, with a `ROADMAP.md` entry and the reviewer's reproduction (port not re-bindable after shutdown, unlike `b819de6`). |
| Observation · stale cached connection after a same-port restart under another name | fold | Append to the existing same-port restart seed (`issues/queue-stale-decref-same-port-restart.md`) as a second symptom. |
| P2 · PLAUSIBLE | pending external evidence | Unchanged; the foreman runs the multi-node matrix at the remediated head once the test cluster is back from maintenance. |

### Foreman's note — ratification of (c), 2026-11-03

> The build extended the ruled thread rule with case (c). `secure_client` marks its thread from TCP
> connect until the TLS handshake returns, so a decref finalized inside a same-process client's own
> handshake is deferred. With (a)+(b) alone, the existing C5 test stalled 2 of 14 runs: the client's
> stock decref dialed the server whose accept loop was waiting on that very handshake. (c) applies
> only when a sender exists, i.e. in a process that serves queues. It changes nothing for a process
> that serves no queues, and nothing in wire, auth or TLS. The foreman ratifies (c) as within the
> ruling's property (no in-process thread blocks on a connection only this process's accept loop can
> answer). It is in scope for cycle 6.

## Review cycle 6 — changes-requested (2026-11-03)

- **Reviewed commit:** bc2ed5258a6ca5244ff0f78ad200fa6672fa5d41  ·  **Base:** `b819de6` (merge base with
  `origin/develop`)  ·  **Date:** 2026-11-03
- **Verdict:** changes-requested. One finding, C10 (HIGH, CONFIRMED, library-only): on Python 3.12
  a new server thread can run a collection before its mark. It stalled the suite's own same-port
  restart test once in 21 runs on 3.12. Graded HIGH, it does not meet the cycle-5 triage's
  pre-agreed fallback, so the feature stops pending the maintainer. Weighed on CLI exposure alone it
  would be MEDIUM, which would meet it (see Human-gate triggers).
- **Cycle:** 6, the further cycle the maintainer authorized at the cycle-5 triage, scoped to the
  thread-based decref redesign.
- **Mode:** a remediation check of C9 and of the thread rule ((a), (b) and the ratified (c)) by the
  orchestrator, including an attempt to break TECH P1's rewritten argument, plus one fresh blind
  reviewer over the delta `git diff 49b4409..bc2ed52 -- . ':(exclude)spec/'`, with the full
  spec-excluded diff `b819de6...bc2ed52` as context. The reviewer was not shown earlier findings or
  commit subjects. Findings outside the delta, or restating a settled ruling, are Observations.

### Pre-flight

- **Base resolution.** Graded against the merge base `b819de6`; the stale local `develop` was not
  used.
- **Build state.** `TECH.md` is `status: in_progress`, P1–P6 `done`, P7 `pending`. R9's multi-node
  clause still waits on the foreman's matrix. Not a finding.
- **Contract drift.** `git log --oneline b819de6..bc2ed52 -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`.
- **Since cycle 5:** `bc2ed52` only. It is signed and carries no `Co-Authored-By` trailer.
  `git diff --diff-filter=M b819de6...bc2ed52 -- tests/` is empty: no base test is modified.
- **Working tree.** Clean apart from this review's own edits. Every run used `git archive` extracts of
  `bc2ed52`, `43d2d85`, `aa55e86` and `b819de6`, with one `uv sync --frozen` venv per commit and
  Python (3.11.18, 3.12.16, 3.13.18, 3.14.10).
- **Environment.** As in cycle 5, connections to the host's own non-loopback addresses are
  intercepted here, so every reachability probe ran over loopback and the wildcard address.

### Cycle 5 findings: remediation check (orchestrator)

**C9 — resolved.** The address match is gone: `_serving`, `_dialed`, `_note_dialed` and
`_served_here` are removed, and `_decref` (`core/queue.py:594`) reads only the calling thread's marks
and whether a sender exists.
- **Red control.** C9's configuration (`gc4`, TLS, a name resolved in-process to `0.0.0.1` then
  `127.0.0.1`; the code under test unmodified), 20 rounds per point:

  | build | accept | handler | starter | stop |
  |---|---|---|---|---|
  | `43d2d85`, 3.11–3.14 | stall ×4 | pass ×4 | stall ×4 | stall ×4 |
  | `bc2ed52`, 3.11–3.14 | pass ×4 | pass ×4 | pass ×4 | pass ×4 |

  The `43d2d85` stalls are C9's, at round 0. At 200 rounds, `bc2ed52` passes the same configuration
  on every point and Python (`gc4` matrix below).
- **C8 (re-read by the cycle-5 triage): holds.** A client of a server in another process, collected
  on an unmarked thread of a serving process, runs the stock decref there (`c8` at the wildcard
  address: "stock decref ran on MainThread", 0 left). Collected mid-bind, inside the start's mark, it
  is handed off and delivered (`c8 --ephemeral`, wildcard and `127.0.0.1`: sender, 0 left). 4 Pythons
  × TLS on and off, 24/24.

**The thread rule, attacked (TECH P1 items 1–8).**
- **Item 1(a), threads a server starts: fails on 3.12 (C10).** The mark is `_bootstrap`'s first
  statement (`:577`), and the argument rests on "nothing before the mark allocates an object the
  collector tracks". That is the 3.11 trigger (a collection runs at the allocating thread's own
  allocation). On 3.12 the eval breaker is shared by all threads, so a collection that another
  thread scheduled runs at the first eval-breaker check of whichever thread next holds the GIL. That
  can be the `RESUME` of a new server thread's `_bootstrap` (`:574`), before the mark. On 3.13 and
  3.14 the breaker is per thread, and the window is closed. TECH names this window as a known limit
  on 3.12 that the probes never landed in; this cycle provoked it into C5's stall, and the suite's own
  same-port restart test hit it once with no provocation.
- **Item 1(b), start and stop: holds.** `gc4` starter and stop points (below). Nested: `stop()`
  called from a handler (marks on the handler 1 → 1 around it; a forced collection there hands off),
  and a second server started from a handler, with a collection forced between its bind and its
  accept loop while a dropped client of an earlier server on the same port is pending (handler
  marks 2; no stall). 4 Pythons × TLS on and off, 8/8.
- **Item 1(c), the TLS handshake (ratified above): holds.** A collection forced inside a same-process
  TLS client's connect, with a dropped client of another process's server pending: handed off and
  delivered (0 left), 4/4 Pythons. In a process that serves nothing it is the stock decref on the
  dialing thread (no sender, 0 left), 4/4; and when that client is mid-handshake with the very server
  the decref dials, both `bc2ed52` and `b819de6` stall the same way across processes on 3.14 (the base TLS
  handshake stall, out of scope).
- **Items 2–4: hold, with item 1(a)'s exception.** The waits are as listed. The handler-start wait
  (`serve` → `Thread.start()`, `:476`) is where C10's stall forms; the starting thread's wait on the
  accept loop's start (`:717`) is the other place, reached by a same-port restart (the suite's own
  failure, and the blind reviewer's `accept-restart`).
- **Item 5 (the sender): holds.** It is marked, nothing server-side waits on it.
- **Item 6 (nothing stranded): holds.** `_serve_in_process` starts the sender before its mark
  (`:710`), and `_decref` hands off only while a sender is set. Remote decrefs collected on a server
  thread (the accept loop, a handler) are delivered by the sender: 0 left in every round, 4 Pythons
  × TLS on and off, 16/16.
- **Item 7 (fork): holds.** The cycle-5 fork probe, adapted as the remediation's C7 test is (the
  parent's waiting decrefs are made by a collection inside a mark): 3–4 decrefs waiting at the fork,
  the child inherits 0 marks, 0 waiting and no sender, and the parent's live client keeps 4 objects
  with every put working. A child that serves starts its own sender; a collection forced on its own
  accept loop hands off, and its server ends with 0 objects. 4 Pythons × TLS on and off, 16/16.
- **Item 8 (remote clients): holds.** A process that serves nothing: the finalizer is `_decref`, the
  stock decref runs on the dropping thread, nothing is handed off, there is no sender thread and
  nothing is recorded (`remote`, 8/8).
- **Marks balance.** At the end of every probe above, every mark belongs to a live `_ServerThread`
  with a count of 1 (52/52 checks).
- **Several servers in one process, and address forms:** see the `forms` matrix below.

### Verification run

- **Suite (orchestrator).** `bc2ed52` extract, venv on `PATH`, two Pythons at a time: 3.11.18,
  3.12.16, 3.13.18 and 3.14.10 each **563 passed**.
- **`tests/test_queue_gc.py` repeats,** 20 consecutive runs each, 3.12.16 and 3.14.10 side by side:
  - 3.14: 20/20 (13 passed each).
  - **3.12: 19/20.** Run 2 failed `test_same_port_restart_never_stalls[tls]`: its watchdog fired in
    round 138 with C10's stacks (below). 20 more runs of that test (TLS on and off) passed on each of
    3.12 and 3.13, so the rate on 3.12 is about 1 in 21 runs of the TLS case.
- **`parity --base b819de6 --feature bc2ed52`: PASS.** Task tables identical with TLS on and off, rc
  0/[0, 0] on both builds; `--help` byte-identical for `server`, `client`, `submit` and `cluster`.
- **`compat --server bc2ed52 --client b819de6`: PASS.** 200/200 tasks exit 0, rc 0/[0, 0], TLS on and
  off.
- **Forced-collection matrices, `bc2ed52`.**
  - The remediation's `gc4` matrix rebuilt (the cycle-5 probe `zqp5_probe.py gc4`, unmodified), 200
    rounds per point, each of 3.11–3.14: TLS on and off at `127.0.0.1` (32 runs) and C9's
    configuration (16 runs); plus the test module's own forced-collection probe at `127.0.0.1`, TLS on
    and off, and the multi-address name (12 runs). **60/60 pass**, 12,000 rounds.
  - Address forms and several servers (`gc4`, 10 rounds per point): `127.0.0.1`; `localhost` both
    sides; wildcard bind dialed as `127.0.0.1` and as `localhost`; bind `127.0.0.1`, dial `0.0.0.0`;
    bind `''`, dial `localhost`; three extra servers (at `127.0.0.1`, the wildcard address and
    `localhost`, each with a dropped client pending at every collection); and `::ffff:127.0.0.1`
    (TLS). 4 Pythons × 4 points × TLS on and off: **240/240 pass**.
  - Every stock decref in both matrices ran on the main thread or on `hypershell-queue-decref`.
- **Throughput gate (orchestrator).** `bench_queue.py gate --base b819de6 --feature bc2ed52 --repeats
  5`, Python 3.14.6, start method `spawn`, frozen non-editable venvs from `git archive`: **PASS**, all
  60 runs clean.
  - **Load.** The gate started at a 1-min load average of 4.87, below the bound of 6. Sampled every
    30 s (54 samples): min 4.87, median 8.5, max 32.8. During the two 4x4 b=1 configs (17 samples):
    min 4.87, median 5.5, max 6.8, against a median of 5.29 over the build's gate. The later peaks
    came from this review's single-process window probes, the blind reviewer's probes and another
    worktree's test run; runs were interleaved, so both builds shared the load.
  - **The build's 4x4 no-TLS margin (−14.1 against 14.2) did not reproduce:** −3.7 against 21.0,
    with the base's median 12.2 lower than in the build's run and the feature's 1.8 lower.

  | config | base | feature | delta | tolerance | tree children b/f | tree FDs b/f | own threads b/f | tree RSS MB b/f |
  |---|---|---|---|---|---|---|---|---|
  | 4x4 b=1 tls | 264.0 [246.8–277.4] | 268.4 [252.9–279.3] | +4.4 | 30.6 | 2/0 | 117/23 | 7/20 | 197.7/95.9 |
  | 4x4 b=1 no-tls | 270.8 [265.6–273.1] | 267.1 [257.3–278.3] | −3.7 | 21.0 | 2/0 | 117/23 | 7/20 | 193.5/92.0 |
  | 16x1 b=1 tls | 271.0 [218.8–285.0] | 244.8 [224.6–259.4] | −26.2 | 66.2 | 2/0 | 153/60 | 7/56 | 202.0/99.9 |
  | 16x1 b=1 no-tls | 217.0 [212.5–236.3] | 220.7 [211.2–263.5] | +3.7 | 52.3 | 2/0 | 153/59 | 7/56 | 196.7/93.9 |
  | 4x4 b=50 tls | 800.6 [754.0–865.8] | 836.4 [765.1–855.1] | +35.8 | 111.8 | 2/0 | 116/22 | 7/20 | 202.5/100.2 |
  | 32x1 b=1 tls | 248.4 [214.0–269.5] | 239.1 [222.6–264.2] | −9.3 | 55.5 | 2/0 | 201/107 | 7/104 | 206.6/103.8 |

  Steady-state tasks/s, median [min–max]. The 16x1 and 32x1 spreads, and so their tolerances, are
  wide: those configs ran during the load peaks. Server threads unchanged from `43d2d85` (20 in 4x4).

### Requirement → evidence matrix (the delta)

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `_ServerThread`, marks, `_decref` | suite on 3.11–3.14; gate: tree children 2 → 0; C9 red control | ⚠️ C10 (a stall of the in-process server, Python 3.12, library use) |
| R4 | `secure_client` adds marks only; stock decref with no sender | `compat` TLS on and off; `test_queue_tls.py` unmodified; `remote`, 8/8 | ✅ |
| R5 | `stop()` marked; sender before any mark; `register_at_fork` | starter and stop points; nested probe; fork probes, 16/16 | ⚠️ C10: on 3.12 a same-port restart can hang in `start()` (1 in 21 runs of the suite's TLS restart test) |
| R6 | `test_queue_gc.py` (forced-collection, multi-address, rule test) | tagged `unit`, bounded; red at `43d2d85`, green here | ⚠️ the forced points all fall after the mark, so C10's window is untested; `test_same_port_restart_never_stalls[tls]` is flaky on 3.12 |
| R8 | no base test touched | suite; `parity`; `--help` byte-identical | ⚠️ C10 (library-only) |
| R9 (local) | as R1 | gate above | ✅ |
| R9 (multi-node vs 2.8.3) | `tests/cluster/` drive | **pending external evidence** | ⏳ |

Unmapped changes: none. The new seed and the two amended seeds are the cycle-5 triage's
dispositions; the blind reviewer graded them as prose and reproduced both described behaviours.

### Findings

No AGENTS.md invariant violation was found.

#### [HIGH/CONFIRMED] C10 — On Python 3.12 a collection can run on a new server thread before its mark, bringing back C5's stall
- **Where:** `src/hypershell/core/queue.py`:
  - `:574-577`, `_ServerThread._bootstrap`: the mark is the first statement, after the function's
    entry (`RESUME`) and the `threading.get_ident()` call, both eval-breaker checks on 3.12. The
    comment at `:575-576` holds on 3.11 and 3.13+, not on 3.12.
  - `:594-597`, `_decref`: an unmarked thread runs the stock decref.
  - `:476`, `serve`: the accept loop waits in `Thread.start()` for the new handler.
  - `:717`, `_serve_in_process`: the starting thread waits likewise for the accept loop.
- **Failure scenario:**
  - Python 3.12. A same-process `QueueClient` is dropped as cyclic garbage while its server serves.
  - A collection scheduled by one thread is run by a new server thread at its first eval-breaker
    check. This is most frequent when another thread allocates tracked objects and releases the GIL
    inside one C call, with no eval-breaker check in between: SQLite's `fetchall` does this (a row
    tuple per row, the GIL released around each step), as the server's own database thread does
    through SQLAlchemy. The suite's same-port restart test hit it without any such thread.
  - The accept loop admits a connection and starts a handler. When the handler first gets the GIL it
    runs the pending collection at `_bootstrap`'s entry, before its mark.
  - The dropped client's finalizers run the stock decref, which dials this server. The accept loop is
    in `Thread.start()` waiting for that same handler: no connection is ever served again, and new
    clients hang in `connect`.
  - The same can happen to a new accept loop, which the starting thread waits on, when a dropped
    client of an earlier server on the same port is pending (a same-port restart).
- **Evidence:**
  - **The suite's own test, natural collection, no probe scaffolding.** `test_same_port_restart_never_stalls[tls]`
    on 3.12 stalled in round 138 of one run in 21 (the `gc20` repeats above). Stacks: the main thread
    in `probe_same_port_restart` → `QueueServer.start` → `_serve_in_process` (`:717`) →
    `Thread.start` → `wait`; the new accept loop in `_bootstrap` (`:574`) → `util.Finalize` →
    `_decref` (`:597`) → `BaseProxy._decref` → `secure_client` (`:267`) → `do_handshake`. A dropped
    client of the previous server on that port was collected on the new accept loop before its mark;
    its stock decref dialed the new server, whose accept loop it was. 0 of 20 on 3.13 and 0 of 20 on
    3.14.
  - **Orchestrator, natural collection with a driver** (`zqp6_window.py`: a thread running SQLite `fetchall`, the
    main thread connecting same-process clients, `gc.set_threshold(500, 1, 1)`; a `gc.callbacks` hook
    only records where each collection lands, and parks each used client so that a collection always
    has one pending; nothing in `hypershell` is patched):

    | build, Python | dropped client | stalled runs | pre-mark collections per run |
    |---|---|---|---|
    | `bc2ed52`, 3.12, no TLS | cyclic (`client.cycle = client`) | 3/4 | 123–4,107 |
    | `bc2ed52`, 3.12, TLS | cyclic | 0/4 | 294–1,292 (0–9 with garbage pending) |
    | `bc2ed52`, 3.12, no TLS / TLS | freed by the next collection of any generation | 2/2 / 1/2 | 42–573 |
    | `bc2ed52`, 3.12, without the SQLite thread | cyclic | 0/2 | 0 |
    | `bc2ed52`, 3.11, 3.13, 3.14, TLS on and off | both | 0/12 | 0 |
    | `43d2d85`, 3.12, TLS on and off | both | 0/4 | (address match) |

    The "freed by any collection" variant clears the proxies' back-reference to their client, so a
    young holder decides when it is collected; the decision under test is unchanged. Each stall's
    stacks: the handler in `_bootstrap` (`:574`) → `util.Finalize` → `_decref` (`:597`) →
    `BaseProxy._decref` → `Client` → `answer_challenge` (no TLS), or → `secure_client` (`:267`) →
    `do_handshake` (TLS); the accept loop in `serve` (`:476`) → `Thread.start` → `wait`; the main
    thread in `connect`.
  - **Blind reviewer, independently:**
    - Forced at the new thread's first instruction (a `sys.monitoring` PY_START hook calls
      `gc.collect()` once; the code under test unmodified), 3.12: the handler stalls with TLS and
      without, with the same stacks; a same-port restart variant hangs inside `QueueServer.start()`.
      `43d2d85` passes all three.
    - Natural landings with a SQLite thread (collections that ran on a new server thread before its
      mark, per handler starts): 3.12 at threshold 50, 3 of 31,060; at threshold 5, 598 of 20,240;
      3.11, 3.13 and 3.14, 0 of 21,000–31,000. Its natural stall attempts (2,170 rounds) did not stall.
    - The delta's tests pass on 3.12 (their forced points all come after the mark).
- **Exposure: library-only.** Reasoning:
  - A stall needs a same-process `QueueClient` whose proxies are finalized while its server still
    accepts connections, on Python 3.12.
  - `hs server`, `hs client`, `hs submit` and the ssh, remote and autoscaling clusters have no
    same-process client of their own server, so `_decref` never runs on a server thread there.
  - `hsx` and the local `hs cluster` hold a server and a same-process client (the `ClientThread`; in
    `--no-db` mode also the `LiveSubmitThread`'s client). `LocalCluster` and `ServerThread` keep both
    `QueueClient`s referenced until the server has joined, so their decrefs reach only a stopped server.
  - Library routes: R5's same-port restart in one process (no CLI path restarts in-process); an
    application that drops a client of its own in-process server; `run_local()` called twice in one
    process on the same port (the first run's client, collected during the second run, dials the
    second server).
- **Severity: HIGH,** by two tests. R5's own scenario, a new server on the same port in the same
  process, can hang in `start()` on 3.12, so R5 is intermittently unmet there. And the project's suite
  is exposed: the new `test_same_port_restart_never_stalls[tls]` fails on about 1 in 21 runs on 3.12
  (bounded by its watchdog, so a red CI job rather than a hang). Cycle 3 kept C5 HIGH for its suite
  exposure, noting it "would be MEDIUM weighed on CLI exposure alone"; the same holds here. The blind
  reviewer graded C10 MEDIUM before the suite failure surfaced. It is new in the delta: at `43d2d85`
  the address match handed these decrefs off on every thread, and at `b819de6` the queues are served
  by a separate process.
- **Touches invariant / requirement:** the C5 property and its cycle-5 ruling ("C5 holds by
  construction"); R1, R5, R8; R6's coverage; high-blast-radius `core/queue.py`.
- **Not prescribed, for the build:** mark the new thread from the thread that starts it, or treat a
  thread that is still bootstrapping as on the server side (the blind reviewer's suggestion: its
  identifier is not yet in `threading._active`).

### Observations (not findings)

- **The base TLS handshake stall, on both builds.** A collection inside a TLS client's handshake, in a
  process that serves nothing, whose stock decref dials the same remote server: `bc2ed52` and
  `b819de6` both stall across processes (3.14). (c) does not apply there (no sender), as TECH states.
  Pre-existing; out of scope.
- **One sender for every server-side decref.** On server threads, decrefs for servers in other
  processes are now handed to the same sender, so a slow remote target delays in-process decrefs
  queued behind it. Delay only; the `queue-decref-handoff-delay` seed's territory.
- **The seeds read true.** The blind reviewer reproduced `queue-forked-child-keeps-listener` (re-bind
  `EADDRINUSE` and no reply while the child lives; `b819de6` re-binds and refuses) and the second
  symptom in `queue-stale-decref-same-port-restart` (bind `127.0.0.1`, dial `localhost`: `EOFError`).
  Both `unshaped`; the `ROADMAP.md` links resolve.
- **Process hygiene.** Every probe, suite and bench ran in its own process group under a timeout.
  - One run of the nested probe (no TLS, 3.14) ended on its own assertion, the handler hook not
    reached in round 3, with no stall; three reruns and the matrix's eight runs passed. A probe
    artifact, not counted.
  - The window probe's repeated runs shared one log name, so the per-run record is the matrix output.
  - A psutil scan after the last run found nothing from this review, or from the blind reviewer,
    still running.

### Human-gate triggers

- **Triggered.** C10 is CONFIRMED in high-blast-radius `core/queue.py`, inside the delta and new in
  it.
- **The pre-agreed fallback (cycle-5 triage) is not met as graded.** C10 is library-only (no shipped
  CLI path reaches it, a classification the blind reviewer reached independently), but it is graded
  HIGH for R5 and the suite exposure on 3.12, and a HIGH finding stops the feature. If the maintainer
  weighs it on CLI exposure alone, as cycle 3 said C5 would have been (MEDIUM), the fallback applies:
  seed C10 with a `ROADMAP.md` entry and proceed to publish. Before publishing on that basis, the
  3.12 flake in `test_same_port_restart_never_stalls[tls]` should be dealt with, since CI runs 3.12.
- C9 is resolved. The suite on four Pythons, parity, compat and throughput passed. The
  `test_queue_gc.py` repeats passed 20/20 on 3.14 and 19/20 on 3.12, the one failure being C10.
- P2, the multi-node evidence, is still pending.

## Triage (human gate) — cycle 6

Cycle 6 confirmed the thread rule on 3.11, 3.13 and 3.14, but found C10 (HIGH, CONFIRMED,
library-only): on 3.12 a collection scheduled by another thread can run inside a new
`_ServerThread._bootstrap` before its mark. The pre-agreed fallback excluded HIGH, so the foreman
stopped. On 2026-11-03 the maintainer chose option (a): **a narrow fix verified by targeted checks,
without a seventh blind review**. The foreman rules as judge under that authorization.

| Finding | Ruling | Disposition |
|---|---|---|
| C10 · CONFIRMED | fix, narrow | In `_decref`, when a sender exists, also hand off a decref finalized on a thread that `threading` has not yet registered as started (it is still bootstrapping: its ident is not in `threading._active`, or `current_thread()` is a dummy for it). Deferring is always safe because the sender delivers it, and this closes the window before the `_ServerThread` mark on every Python. No other behavioural change. If the check needs another CPython private, add it to `unsupported_internals()` and its test. **Red-first:** the cycle-6 reviewer's 3.12 reproductions (the forced collection at a new thread's first instruction, and the SQLite fetchall-thread stall) fail at `bc2ed52` on 3.12 and pass after the fix. **Targeted gates:** `tests/test_queue_gc.py` and `tests/test_queue.py` pass 50 consecutive runs on 3.12; the full suite passes 3 consecutive runs on 3.12 and once on each of 3.11, 3.13 and 3.14; parity and compat pass against `b819de6`. The foreman verifies the evidence before publish in place of a seventh review cycle, and REVIEW.md records that the fix landed on the maintainer's ruling without a blind re-review. |

## Quick review of the C10 fix (scoped, blind)

The maintainer chose option (a) for C10, a narrow fix without a seventh blind review cycle. On
2026-11-03 the maintainer added this quick review, blind and scoped to the C10 fix's diff (`371d1ec`),
on top of that. **Verdict: approve**, with six findings. The foreman ruled on each as judge.

| # | Finding | Ruling |
|---|---|---|
| 1 | LOW. `_not_started` misses a stale non-dummy entry: on 3.12, when `threading` is first imported on a thread it did not start and that thread ends, `_active` keeps that thread's `_MainThread` under its identifier; a new thread that reuses it reads as started at its first instruction, reopening C10's window for a `_ServerThread`. | Fix: also count the entry as not started when its native identifier is not the calling thread's, where threads have native identifiers. Red first on the reviewer's probe, made a subprocess test. |
| 2 | LOW. On a thread `threading` did not start (and a main thread known only by a `_DummyThread`), every decref is handed off once the process has served, decrefs for remote servers included, and the stock `tls.connection.close()` then runs on the sender. Not reachable from the CLI. | Document only: the `_decref` docstring and TECH's "Scope and limits". No code change. |
| 3 | NIT. The `_INTERNALS` registry check would pass on a CPython that registers a thread from the parent side. | Fix: also require `'_active' not in _names(threading.Thread.start)`. |
| 4 | NIT. `AGENTS.md` describes the unregistered-thread rule as "a new thread before its mark" only; `test_thread_registry`'s docstring says a reused identifier never maps to a `Thread` that `threading` started, which finding 1 contradicts. | Fix both texts. |
| 5 | NIT. `test_collection_scheduled_elsewhere_never_stalls` cannot exercise its target outside 3.12 (0 landings on 3.13). | Fix: skip it outside 3.12, saying why. |
| 6 | Not new. A same-port successor receives the old server's decrefs. | Already seeded in `issues/queue-stale-decref-same-port-restart.md`; no change, recorded only. |

The work and its evidence are recorded in TECH.md under P1 ("Follow-up: quick review of the C10 fix").

**2026-11-04.** Cycle 7 (scoped quick review) — approved; its NIT/LOW follow-ups landed in `7dd85c1` under the foreman's rulings; recorded in the FSM 2026-11-04.

## Review cycle 8 (scoped) — approved (2026-11-04)

- **Reviewed commit:** f45a5cffab88d73df0308daf94d1c966025a5453  ·  **Base:** `b61dbbe` (merge base with
  `origin/develop`; the stale local `develop` was not used)  ·  **Date:** 2026-11-04
- **Verdict:** approved. No findings. Two observations, neither in the delta.
- **Cycle:** 8, scoped to everything since the cycle-7 approval at `7dd85c1`: the develop merges
  `ec75169` (#70, #71), `0fc0577` (#72–#74) and `f45a5cf` (#75), the test adaptation `fab14f8`, and
  the cycle-7 record `bc8fdbf`. Develop's own content was reviewed in its own cycles and is out of
  scope.
- **Mode:** one blind reviewer, inline, with no nested subagents. Inputs were `GOAL.md`, the diffs and
  the runnable repo; `PLAN.md`, `TECH.md` (beyond its FSM block), `research/` and `META.md` were not
  read. Every run used a `git archive` extract of `f45a5cf` (and of `7dd85c1` for one control), with
  frozen offline venvs on 3.12.16 and 3.14.10 built from a private copy of the uv cache. Each run had
  its own process group and a kill timer.

### Pre-flight

- **Contract drift.** `git log --oneline b61dbbe..f45a5cf -- spec/zone-queue-primitive/GOAL.md`
  shows only the shaping commit `da987c2`.
- **Commits since cycle 7.** `ec75169`, `bc8fdbf`, `0fc0577`, `fab14f8`, `f45a5cf`: all signed, none
  with a `Co-Authored-By` trailer. `git diff --diff-filter=M --name-only b61dbbe f45a5cf -- tests/` is
  empty, so no base test is modified (R8).
- **Z1's own delta across the merges.** `git diff b819de6 7dd85c1 -- src tests` (Z1 against its base
  at approval) and `git diff b61dbbe f45a5cf -- src tests` (Z1 against its base now) are identical
  except for hunk offsets and three places: `secure_client` (the #72 conflict), one context line in
  `ServerThread.start_threads` (#74's docstring), and `fab14f8`'s edits to `tests/test_queue_gc.py`.
  The same comparison over `AGENTS.md` and `ROADMAP.md` gives 146 lines each side, identical except
  for offsets and context. Nothing else in Z1 moved across the three merges.
- **No conflict markers.** `git grep -E '^(<<<<<<<|>>>>>>>|=======$)' f45a5cf -- src tests AGENTS.md
  ROADMAP.md issues .agents` is empty.

### 1. `0fc0577`: the `secure_client` resolution (`core/queue.py:247-285`)

- **The marks still surround the dial and the handshake.** `_mark()` (`:260`) comes before the socket
  is made. The `try … finally: _unmark()` (`:261-277`) covers the IPv4 socket (`:265`),
  `setblocking(True)`, `connect` (`:268`) and `wrap_socket` (`:272`). The fingerprint check and the
  HMAC challenge stay outside the marks, as they were at `7dd85c1`.
- **#72 is intact.** The three dial lines and their comment are byte-identical to `b61dbbe`'s.
  `tests/test_queue_address_family.py` passes on 3.12 (8 passed). With the dial reverted to
  `socket.create_connection` inside the marks (mutation M3 below), three of its eight tests fail.
- **Close on failure is correct.** The inner `except Exception: raw.close(); raise` covers `connect`
  and `wrap_socket`, as in #72. If the socket cannot be made there is nothing to close, and the outer
  `finally` still unmarks. `_unmark` runs on every exit. M2 shows that the suite catches its loss.
- **`ROADMAP.md`** keeps both sides: Z1's five queue entries, then #72's IPv6 entry.

### 2. `fab14f8`: the two adapted tests, proved by mutation

Each mutation was applied to a scratch copy of the `f45a5cf` extract, and the copy's `src` was put
first on the path (confirmed by `hypershell.core.queue.__file__`). The four targeted tests ran on 3.12:
T1 `test_finalizer_hands_off_only_server_side`, T2 `test_multi_address_name_never_stalls`, and T3
`test_forced_collection_on_server_side_never_stalls[no-tls|tls]`.

| Mutation | What it breaks | T1 | T2 | T3 ×2 | Other |
|---|---|---|---|---|---|
| none (green control) | — | pass | pass | pass | address-family 8/8 pass |
| M1: `_mark()` after `connect` | the dial runs unmarked | **FAIL** (`seen == [True, True, False]`) | pass | pass | |
| M2: `_unmark()` only on success | a refused dial leaves its mark | **FAIL** (`_server_side == {ident: 1}`) | pass | pass | |
| M3: dial by `socket.create_connection` (#72 reverted, marks kept) | IPv4-only dial | **FAIL** (the dial never reaches the spy) | **FAIL** (its path assertion: `multi_address_dials` empty, all 40 points pass) | pass | address-family **3/8 FAIL** |
| M4: `_unmark()` after `connect`, before `wrap_socket` | the handshake runs unmarked | pass | pass | pass | survives; see Observation 1 |
| M5: C9's rule, hand off only when `gethostbyname(token host)` is `127.0.0.1` | a decision by address | **FAIL** | **FAIL**: stall at round 0, accept: accept loop → `collect_at` → `_decref` (stock) → `secure_client` → `do_handshake` | pass | |
| M6 (test): the probe's `socket.socket.connect` redirect removed | the probe's path | pass | **FAIL** (`gaierror` in `secure_client` → `raw.connect`) | pass | |

- **T1 is still meaningful.** Its spy now wraps `socket.socket.connect` and passes every other address
  through. It fails when the dial runs unmarked (M1), when a failed dial keeps its mark (M2), and when
  the dial stops being the IPv4 socket's own `connect` (M3). `monkeypatch` removes the class
  attribute on undo, because `socket.socket` has no `connect` of its own.
- **T2 is still meaningful and still discriminates.** Under M5, the rule C9 replaced, T2 stalls with
  C9's own stack while T3 (a dial to `127.0.0.1`) passes. The connect redirect is load-bearing (M6).
  The new assertion catches a probe that no longer dials through the name (M3), so T2 cannot pass
  without exercising its path.
- Logs: `/tmp/claude-501/z1rev8/logs/red-*-3.12.log`.

### 3. `f45a5cf`: the auto-merged `server.py`, `AGENTS.md` and `ROADMAP.md`

- **`server.py` is coherent.**
  - Z1's two hunks against `b61dbbe` are unchanged from those against `b819de6`: "Server listening"
    moved from `__init__` to `start()` once the queue is ready, and `start()`'s re-raise
    (`server.py:949-961`).
  - #75's changes are confined to `Confirm.update_info` (`:422`, `confirm_all`) and `Receiver` (`held`;
    on `QueueEmpty` with held results it goes to UPDATE, `:511`; the shutdown warning; outcomes in
    `update_tasks`). None of them touches `ServerThread.start`, `start_threads`, `stop` or
    `run_with_exceptions`.
  - #74's reordered `start_threads` (`:963`) and its `wait_scheduling` (`:979`) run after `start()`
    returns. `wait_scheduling` re-raises through the same `Thread.join` that `start()` uses.
    `QueueInterface.ready` is never reset, so `start()`'s loop cannot spin after a fast clean exit.
- **`AGENTS.md`** has no duplicated headings. Z1's text ("`ServerThread.start` re-raises", `:299`; the
  thread rule including "any thread in a TLS handshake", `:308`) matches the code at HEAD. #75's 0-row
  UPDATE bullet and Z1's server-process bullet do not overlap.
- **`ROADMAP.md`** has no duplicated headings. #75's entry appears once. The two issue paths that
  occur twice are cross-references, as on `b61dbbe`. Every `issues/` seed link resolves.

### 4. Z1 against #75's per-row writes and held results

- `Confirm` and `Receiver` run on `hypershell.core.thread.Thread`s, not `_ServerThread`s. A decref
  finalized there takes the stock path, before and after #75. Held results are `Task` objects, not
  proxies, so they never reach `_decref`.
- On the server side, `completed` and `confirmed` are local `queue.Queue`s. `Receiver.unload_bundle`'s
  `get(timeout=…)` raises `queue.Empty` (imported as `QueueEmpty`, `server.py:57`), which is what the
  held-retry branch expects. The sentinel puts in `wait_receiver` and `stop` are local.
- The stop order is unchanged: submitter, scheduler, heart monitor, the sentinel to the receiver, the
  sentinel to confirm, then the exit of `with self.queue`. Held results are dropped with #75's warning
  when the sentinel arrives, while the queue server is still up.
- In memory every outcome is `APPLIED`, so the same-process `LiveSubmitThread` client never sees a held
  result. Z1's R7 state-machine tests run in memory and are unaffected; they pass in the suites below.

### Verification run

| Gate | Result |
|---|---|
| `tests/test_queue_address_family.py`, 3.12 | 8 passed |
| `tests/test_queue_gc.py` + `tests/test_queue.py`, 3.12, 5 consecutive runs | 50 passed ×5 (52.3 s, 54.2 s, 53.0 s, 51.4 s, 54.2 s) |
| full suite, 3.12.16 | 767 passed, 79 skipped (8 min 0 s) |
| full suite, 3.14.10 | 764 passed, 82 skipped (8 min 5 s) |
| `unsupported_internals()` | `[]` on 3.12.16 and 3.14.10 |

Every skip on 3.12 needs a server database (PostgreSQL or TimescaleDB, which this host lacks) or a
Python with 2.8.3 to regenerate a fixture. 3.14 also skips three Z1 probes, as designed: two need
3.12's shared flag for pending collections, and one needs a Python whose `threading` does not register
the main thread under its own identifier. The known timing flake `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` was not
seen in either full run. Logs: `/tmp/claude-501/z1rev8/logs/`.

### Requirement → evidence matrix (this cycle's delta)

| R-ID | Touched by the delta | Evidence |
|---|---|---|
| R1 | no (`_serve_in_process` unchanged) | Z1's delta identical across merges; suites pass |
| R3 | through `secure_client` | `test_queue.py` ×5 on 3.12; full suites |
| R4 | `secure_client` dial (#72) | address-family 8/8; TLS queue tests pass unmodified; marks verified by T1 + M1/M2 |
| R5 | `ServerThread.start` unchanged; #74/#75 stop paths | §3–4; full suites |
| R6 | `fab14f8` (Z1's own test) | mutation table, §2 |
| R7 | #75 changed `Confirm`/`Receiver` | `test_server_fsm.py` passes in both suites |
| R8 | no base test modified | `--diff-filter=M` empty; full suites |
| R2, R9 | not touched | — |

### Findings

None.

### Observations (not findings)

1. **The handshake half of a TLS client's mark has no test.** M4 unmarks after `connect` and before
   `wrap_socket`, and T1, T2 and T3 all pass. The same mutation applied to `7dd85c1` also passes all
   four (`red-m4-at-7dd85c1-3.12.log`): T1's spy refused the dial there too, before any handshake. The
   gap predates this delta and is not introduced by `fab14f8`. A probe that forces a collection on a
   same-process TLS client's thread between `connect` and the end of `wrap_socket` would cover it.
2. **T2's precondition is stricter than it needs to be.** Since `fab14f8` the probe never dials the
   name's first address; `getaddrinfo` and `gethostbyname` only answer with it. T2 still skips where
   `failing_address()` finds no address that fails at once, though any non-serving address would do.
   Since #72 a real TLS client dials only a name's first IPv4 address, so T2 now models a name whose
   connect reaches the server while the resolver's first answer is another address (round-robin
   DNS). It still discriminates (M5).

### Human-gate triggers

- None triggered: no CONFIRMED finding. P7 (the multi-node run) is still pending, as before.

## CI-found remediation (Linux, 2026-11-04)

- **Finding (CONFIRMED on a Linux test-cluster node, Rocky 8, Python 3.12.13).** PR #76's CI hung on
  all four Pythons in `tests/test_queue_gc.py`'s TLS stall probes and ran into the 20-minute job
  limit. The cause is Nagle's algorithm with Linux delayed ACK. The client side of the
  `multiprocessing` handshake ends with write, write, read (the `_WELCOME`, then the caller's
  request), and neither Z1's TLS path nor the stock listener and client set `TCP_NODELAY`, so every
  new connection waits about 40 ms for the server's delayed ACK. TLS 1.3 adds a second stall: the
  server writes its session tickets, then the challenge, as separate writes. It is not a deadlock:
  each probe round opens 14–18 connections, the per-round watchdog never fired, and the time adds up.
  It is not test-only either: every queue connection on Linux pays about 40 ms, and stock 2.8.x pays
  it too.
- **Evidence (Linux).** Raw write-write-read: 40.8 ms with Nagle, 0.0 ms with `TCP_NODELAY`. Stock
  `multiprocessing` first request/reply: 40.7 ms against 0.1 ms. The four failing tests with
  `TCP_NODELAY` forced through a `sitecustomize` patch: 4 passed in 35.8 s, where without it they ran
  past 300 s.
- **Ruling (foreman, 2026-11-04).** Fix it in Z1: set `TCP_NODELAY` on every queue socket, on both
  ends, plain and TLS, through the smallest seam where the stock `Listener`/`Client` makes the socket,
  with no global monkeypatch of the standard library, skipping non-TCP families. Old clients stay
  compatible: a socket option only, no wire change. Red first with a deterministic socket-option
  test; no CI dependence on timing.
- **Fix.** `core/queue.py`: `_no_delay` on the raw socket before the TLS handshake in
  `SecureListener.accept` and before `connect` in `secure_client`. Cleartext managers now select a
  `hypershell-plain` serializer key registered in `listener_client` (the same mechanism as
  `hypershell-tls`), whose pair is the stock `Listener` and `Client` with the option set on the
  accepted or dialed socket before the authentication handshake. The serializer key never crosses
  the wire.
- **Evidence (local, macOS).** Recorded in TECH.md under P1 ("CI-found remediation (Linux)"). The
  foreman re-runs the Linux check on the test cluster; CI is the final proof.
- **FSM.** P1 reopened and closed again; status `in_review` for a scoped review of this delta. The
  cycle-8 verdict is untouched.

## Review cycle 9 (scoped) — changes-requested (2026-11-04)

- **Reviewed commit:** 3a35be1309044f4a622d07eba9e641371cc38932  ·  **Base:** `b61dbbe` (merge base
  with `origin/develop`, unchanged since cycle 8)  ·  **Date:** 2026-11-04
- **Verdict:** changes-requested. One CONFIRMED MEDIUM finding in `core/queue.py` (human gate) and
  one CONFIRMED LOW docs finding.
- **Cycle:** 9. The scope is everything since the cycle-8 approval at `f45a5cf`: `2f1285f` (the P7
  evidence and its summary), `9f36de4` (the roadmap stamp) and `3a35be1` (the CI-found remediation
  that sets `TCP_NODELAY` on every queue socket). The substance is `3a35be1`'s
  `src/hypershell/core/queue.py` and `tests/test_queue.py`. The P7 evidence was checked for
  sanitisation only.
- **Mode:** one blind reviewer, inline, with no nested subagents. The inputs were `GOAL.md`, the
  spec-excluded diff `f45a5cf..3a35be1`, the `AGENTS.md` invariants and the runnable repo.
  `PLAN.md`, `TECH.md` (beyond its FSM block), `research/` and `META.md` were not read before the
  findings were formed. Every run used `git archive` extracts of `3a35be1`, `f45a5cf`, `b819de6` and
  `2.8.4` in a scratch directory, a throwaway 3.12.13 venv built with `uv run --frozen`, and the
  worktree's 3.14.6 venv, used read-only.
- **Not run by the reviewer:** `bench_queue.py compat`, as the foreman directed. The foreman's run,
  approved by the maintainer, is cited in §1.

### Pre-flight

- **Contract drift.** `git log --oneline develop..HEAD -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`.
- **Commits in scope.** `2f1285f`, `9f36de4` and `3a35be1` are signed (`%G?` = `U`), and none has a
  trailer. `git diff --diff-filter=M --name-only b61dbbe 3a35be1 -- tests/` is empty, so no base test
  is modified (R8). `tests/test_queue_tls.py` is unchanged since `b819de6` (R4). The only test change
  is the new test in the branch-new `tests/test_queue.py`.

### 1. Wire compatibility (executed)

- **Harness.** A script of about 120 lines, read in full, with four roles: a HyperShell server, a
  HyperShell client, a stock `BaseManager` server (`get_server().serve_forever()` over four
  `queue.Queue`s) and a stock `BaseManager` client. The two stock roles assert that `hypershell` was
  never imported.
- **Protocol on each of the four queues.**
  1. The server side puts `server->{name}`.
  2. The client gets it and calls `task_done`, so the server's `join()` returns.
  3. The client puts `client->{name}`, and the server gets it.
  4. The server puts the sentinel on `scheduled`, and the client must receive it.
- **Results.** Every pair passed on 3.12.13 and on 3.14.6, with both exit codes 0 and the expected
  serializer on each side:

  | Server | Client | Cleartext | TLS |
  |---|---|---|---|
  | `3a35be1` | `3a35be1` | PASS | PASS |
  | `3a35be1` | `b819de6` | PASS | PASS |
  | `3a35be1` | `2.8.4` | PASS | PASS |
  | `3a35be1` | stock `multiprocessing` | PASS | n/a |
  | `b819de6` | `3a35be1` | PASS | PASS |
  | `2.8.4` | `3a35be1` | PASS | PASS |
  | stock `BaseManager` | `3a35be1` | PASS | n/a |

  The serializer key never crosses the wire. A `3a35be1` client (`hypershell-plain`) and a stock
  client (`pickle`) are interchangeable against the same servers.
- **Foreman's run** (approved by the maintainer; attributed, not repeated): `bench_queue.py compat`,
  `b819de6` against `3a35be1` in both directions, TLS on and off. All four combinations passed, with
  200/200 tasks at exit 0 and every rc 0. Server `b819de6` with client HEAD took 6.6 s with TLS and
  4.0 s without. Server HEAD with client `b819de6` took 4.0 s with TLS and 3.7 s without.

### 2. The new test discriminates (mutation)

Each mutation was applied to a scratch copy of the `3a35be1` extract, with the copy's `src` first on
the path (confirmed by `hypershell.core.queue.__file__`). `test_connections_disable_nagle` ran on 3.12.

| Mutation | `[no-tls]` | `[tls]` |
|---|---|---|
| none (green control) | pass | pass |
| M1: `_PlainListener.accept` without `_no_delay_connection` | **FAIL** | pass |
| M2: `_plain_client` without `_no_delay_connection` | **FAIL** | pass |
| M3: `SecureListener.accept` without `_no_delay(raw)` | pass | **FAIL** |
| M4: `secure_client` without `_no_delay(raw)` | pass | **FAIL** |
| M5: `SecureManager` falls back to `'pickle'` when TLS is off | **FAIL** | pass |
| M6: `_no_delay` sets nothing | **FAIL** | **FAIL** |

The test catches all six. It checks the proxies' long-lived connection on both ends. The short-lived
connections (`dummy`, `create`, `incref`, `decref`) go through the same `listener_client` pair.

### 3. Edge probes (executed)

- **AF_UNIX.** A `_PlainListener` and `_plain_client` pair on a filesystem socket round-trips. The
  option is skipped on both ends, both descriptors stay blocking, and the open-descriptor count is
  unchanged.
- **An authkey on the listener.** The handshake runs inside the stock `accept`, and the option is set
  after it on both ends. Stock `Client` against `_PlainListener`, and `_plain_client` against stock
  `Listener`, both with authkeys, round-trip.
- **Descriptor duplication.** `_no_delay_connection` was called 500 times each on a pipe connection
  (not a socket), a closed connection and a live one, and once on an object whose `fileno()` raises
  `ValueError`. It never raised, and the open-descriptor count did not change. `_plain_client` rejects
  a `str` authkey with the stock `TypeError`, before dialing.
- **Proxy unpickling.** A pickled cleartext proxy now names `hypershell-plain`. In a fresh `-I`
  interpreter, unpickling it raises `KeyError: 'hypershell-plain'`. At `f45a5cf` the same unpickle got
  past the key and failed authentication, as any proxy unpickled outside a spawn child does.
  HyperShell has no path that pickles a proxy:
  - clients run in-process (`ClientThread`) or as `Popen` argv (`cluster/remote.py`,
    `cluster/ssh.py`, the autoscaler);
  - `src/` has no `multiprocessing.Process` or pool;
  - payloads are bytes;
  - a forked child inherits the registration.

  Dropped. The docs consequence is under F2.
- **`_INTERNALS`.** The new entry checks a key this module registers at import, so it cannot fail by
  itself. The pair also relies on two stock behaviours that no check covers: a manager's `Server`
  builds its listener without an authkey, and the stock `Connection` exposes a descriptor. A change to
  either only leaves the option unset; it does not break the connection. `unsupported_internals()` is
  `[]` on 3.12 and 3.14. Not a finding.
- **Latency on macOS loopback.** Connect plus `dummy` takes a median 3.0 ms at `3a35be1` and 2.9 ms at
  `f45a5cf`. The stall the change removes does not show on this host's loopback. The Linux evidence is
  the builder's record above.

### Findings

**F1 — MEDIUM — CONFIRMED — a process-wide socket default timeout breaks every cleartext queue
connection.**

- **Where.** `src/hypershell/core/queue.py:85-98` (`_no_delay_connection`; `socket.socket(fileno=fd)`
  at `:93`), reached from `_PlainListener.accept` (`:340`) and `_plain_client` (`:351`).
- **Cause.** CPython applies `socket.getdefaulttimeout()` to every new socket object. When it is not
  `None`, the new object switches its descriptor to non-blocking. That flag belongs to the open file
  description, which the `os.dup` copy shares with the connection's own descriptor, so it outlives
  the duplicate's close. The stock `SocketListener.accept` and `SocketClient` call
  `setblocking(True)` for exactly this reason, and the stock `Connection` reads with a blocking
  `os.read`.
- **Failure.** In any process that has called `socket.setdefaulttimeout(t)`, every cleartext queue
  connection fails. One example is an application that embeds HyperShell through its documented
  Python API (`docs/api/index.rst`).
  - As a client: `QueueClient.connect()` raises `BlockingIOError: [Errno 35] Resource temporarily
    unavailable` in the authentication handshake.
  - As a server: the handler's handshake read fails, so an unmodified client gets
    `OSError('bad message length')` and nothing reaches the queue. Clients shown failing: a stock
    `multiprocessing` client (`-I`, no HyperShell), and `3a35be1`'s own client running without the
    timeout.
- **Scope.** TLS is unaffected: the option goes on the raw socket directly, and the same probe with
  TLS passes. Under the same probe, `f45a5cf`, `b819de6` and `2.8.4` all pass, server and client in
  one process, and `f45a5cf` also passes against the stock client. The regression is introduced in
  this range.
- **Causality.** A scratch copy of `3a35be1` that restores blocking mode on the duplicate before
  closing it passes the probe.
- **Contract.** R8 (no behaviour change) and R4 (a client built from the base connects to the new
  server). `core/queue.py` is high-blast-radius, so a human gate is required.
- **Direction (the builder's call).** Set the option without leaving the shared description
  non-blocking. Either restore blocking mode, or set the option where the stock code still holds the
  socket object. Add a unit test that runs the cleartext round trip under `socket.setdefaulttimeout`.
- **Commands.** `probe_timeout.py inproc` was run on each extract with the extract's `src` on
  `PYTHONPATH`. The script calls `socket.setdefaulttimeout(30)`, starts a cleartext `QueueServer`,
  connects a `QueueClient`, and does `put`/`get`. The split-process runs, with the timeout set only in
  the server or only in the client, and the stock `-I` client run against a server with the timeout
  set, show each side alone.

**F2 — LOW — CONFIRMED — docs no longer match the cleartext path.**

- (a) **`docs/security.rst:130-133`** says that with TLS off, `SecureManager` "is byte-for-byte
  equivalent to a stock `BaseManager` with the default `pickle` serializer". It now selects
  `hypershell-plain`: `SecureManager(..., tls=None)._serializer == 'hypershell-plain'`. The wire is
  still the stock one (§1), but the serializer statement is false, and a pickled cleartext proxy now
  needs `hypershell.core.queue` to rebuild (§3). `3a35be1` updated the class docstring but not this
  page.
- (b) **`AGENTS.md:313-316`** says "Both ends write twice before a read during the handshake".
  Cleartext connections were instrumented, logging `Connection._send` and `_recv` per thread. The
  client writes twice in a row (`_WELCOME`, then its request). The server never writes twice in a
  row on a connection. The server's double write is TLS 1.3's session tickets followed by the
  challenge, as `_no_delay`'s own docstring (`queue.py:71-77`) states correctly.
- (b, continued) The bullet's lead claim, "every queue connection ... on both ends", holds for
  cleartext only on POSIX. On Windows, `_no_delay_connection` skips (its own comment at `:90`). This
  was not executed here.

### Requirement → evidence matrix (this cycle's delta)

| R-ID | Touched by the delta | Evidence |
|---|---|---|
| R1, R2, R5, R7 | no | suites below |
| R3 | `secure_client`, `SecureListener.accept`, the cleartext pair | `test_queue.py` on 3.12 and 3.14; §1 matrix |
| R4 | the serializer key and the cleartext pair | §1: 12 pairs × 2 Pythons PASS; the foreman's compat run; `test_queue_tls.py` unchanged; **F1** breaks it under a default timeout |
| R6 | new `test_connections_disable_nagle` | §2: 6/6 mutations caught |
| R8 | no base test modified | full suite; **F1** is a behaviour change |
| R9 | the motivation (the Linux delayed-ACK stall) | the builder's Linux record; not measurable on macOS loopback. The P7 multi-node evidence is at `f45a5cf`, before the remediation |

**Scope creep.** None. The change serves R9 and CI health. `PLAIN_SERIALIZER` joins `__all__`.

### P7 evidence sanitisation (`2f1285f`)

- **Contents.** 2,466 JSON records (18 server, 2,448 client) and `SUMMARY.txt`. Every JSON key and
  string value was enumerated: labels, run ids, versions, roles, start method and numbers only.
- **Scan.** The evidence and every spec line added in the range were searched for:
  - site and cluster names, and user names;
  - home, scratch and project paths;
  - `.edu` hosts and IPv4 addresses;
  - scheduler terms (partition, account, qos, `sbatch`, `srun`);
  - tool prefixes.

  There were no hits in the evidence. The only hits in the spec text were false positives:
  `127.0.0.1` in the cycle-8 table, and a checklist line that names the check.
- **Summary.** `SUMMARY.txt` says "a test cluster" and names no site, account or node.
- **Verdict.** Sanitised.

### Verification run

| Gate | Result |
|---|---|
| `tests/test_queue.py tests/test_queue_gc.py tests/test_queue_address_family.py`, 3.12.13 | 60 passed (58.8 s) |
| the same three files, 3.14.6 | 57 passed, 3 skipped (48.6 s) |
| full suite, 3.12.13 | 769 passed, 79 skipped (8 min 1 s); cycle 8 had 767, and the new test adds 2 |
| wire-compat matrix (§1), 3.12.13 and 3.14.6 | 12/12 PASS on each |
| mutation (§2) | 6/6 caught |
| default-timeout probe (F1) | `3a35be1` FAIL; `f45a5cf`, `b819de6`, `2.8.4` pass; restore-blocking scratch copy passes |
| `unsupported_internals()` | `[]` on 3.12.13 and 3.14.6 |

### Human-gate triggers

- **F1** is CONFIRMED in `core/queue.py`, part of the high-blast-radius core. A human must sign off
  before any further step. Route: `/hs-build` to remediate F1 and F2, then a scoped cycle 10 over the
  fix.

## Review cycle 10 (scoped) — approved (2026-11-04)

- **Reviewed commit:** 1e79da67f4334c4d4eabd36639c0756ab8abf8b6  ·  **Base:** `b61dbbe` (merge base
  with `origin/develop`, unchanged since cycle 8)  ·  **Date:** 2026-11-04
- **Verdict:** approved. No CONFIRMED finding. One PLAUSIBLE LOW docs note for triage (P1). Cycle-9
  F1 and F2 are closed.
- **Cycle:** 10. The scope is `696f24d..1e79da6`: the single commit `1e79da6`, the remediation of
  cycle 9's F1 (a process default socket timeout left the cleartext connection's shared descriptor
  non-blocking) and F2 (docs). The whole `_no_delay_connection` / `_PlainListener` / `_plain_client`
  path at HEAD was re-checked, since F1 lived there.
- **Mode:** scoped to the remediation, one blind pass done inline by the orchestrator, with no
  subagent (the foreman's direction). The inputs were `GOAL.md`, the spec-excluded diff
  `696f24d..1e79da6`, the `AGENTS.md` invariants and the runnable repo. `PLAN.md`, `TECH.md` (beyond
  its FSM block), `research/` and `META.md` were not read. Cycle 9's section was read only after the
  findings below were formed, to check its findings are closed.
- **Runs:** the worktree's 3.14.6 venv (read-only) and a scratch 3.12.13 venv built from `uv.lock`
  (`uv sync --frozen --offline`). Pre-fix comparisons used a scratch pytest plugin that puts back the
  `696f24d` body of `_no_delay_connection`; the tree was never edited. Pytest ran with the venv's
  `bin` first on `PATH`, since two `test_queue_address_family.py` tests shell out to `hs`.

### Pre-flight

- **Contract drift.** `git log --oneline develop..HEAD -- spec/zone-queue-primitive/GOAL.md` shows
  only the shaping commit `da987c2`.
- **Commit in scope.** `1e79da6` is signed (`%G?` = `U`) and has no trailer.
  `git diff --diff-filter=M --name-only b61dbbe 1e79da6 -- tests/` is empty (R8), and
  `tests/test_queue_tls.py` is unchanged since `b819de6` (R4). The only test change is in the
  branch-new `tests/test_queue.py`: `test_roundtrip`'s body moves into a `roundtrip()` helper it
  still calls, and four new tests (six items with their parameters) are added, each tagged `unit`.

### 1. The remediation holds (executed)

- **Descriptor state.** A probe called `_no_delay_connection` on a live stock TCP connection under
  no default timeout, `0.0` and `30.0`, with the connection preset blocking and non-blocking. It
  compared `F_GETFL`, `F_GETFD`, `os.get_inheritable`, `os.get_blocking` and ten socket options
  (`SO_KEEPALIVE`, `SO_REUSEADDR`, `SO_SNDBUF`, `SO_RCVBUF`, `SO_LINGER`, `SO_OOBINLINE`,
  `SO_RCVLOWAT`, `SO_SNDLOWAT`, `SO_NOSIGPIPE`, `TCP_NODELAY`) before and after. On 3.12.13 and
  3.14.6, in all six cases, only `TCP_NODELAY` changed. The blocking mode, the status flags and the
  close-on-exec flag are untouched, including a connection that was already non-blocking.
- **An exception inside.** With `_no_delay` patched to raise, the exception propagates, the mode is
  restored (blocking and non-blocking presets) and the open-descriptor count is unchanged.
- **Both ends, one process.** Under each default timeout, `_PlainListener.accept` and
  `_plain_client` hand back blocking descriptors, for TCP and for `AF_UNIX` (which round-trips with
  an authkey).
- **The default timeout in one process only.** A HEAD server and a HEAD client in separate
  processes, the timeout set only in the server and then only in the client: 50 rounds of `put`/`get`
  plus a fresh client per round, both pass on 3.12 and 3.14. A stock `multiprocessing` client run
  with `-I` (no HyperShell imported) against a HEAD server that has the timeout puts on all four
  queues, and the server gets every item, on 3.12 and 3.14. With the `696f24d` body (run on 3.14.6)
  the server-only split run and the stock-client run both fail with `OSError: bad message length`,
  as cycle 9 recorded.
- **Threads and descriptors.** Sixteen threads, each opening 20 clients and doing 5 round trips per
  client in both directions through an in-process cleartext `QueueServer`, under a 30 s default
  timeout and without one: 0 errors on 3.12 and 3.14, and the open-descriptor count returns to its
  baseline after the server stops. With the `696f24d` body (3.14.6) all 16 threads fail with
  `BlockingIOError`. 500 `_PlainListener`/`_plain_client` pairs under the timeout leave the count
  unchanged.
- **Races.** The duplicate's window (non-blocking between `socket.socket(fileno=)` and
  `os.set_blocking`) exists only while the connection is private to the accepting or dialing thread:
  `serve()` hands the connection to a handler thread after `accept` returns, and every `_plain_client`
  caller (`connect`, proxies, `create`, `incref`, `decref`) dials a fresh connection.
- **Python versions.** 3.12.13 and 3.14.6 behave the same in every probe. 3.11 is not installed on
  this host and was not run; CPython applies the default timeout to a socket built on a descriptor in
  the same way there, and `os.get_blocking` / `os.set_blocking` are long-standing on POSIX. The
  `tests (3.11)` CI job was still pending when this was written.
- **Wire.** Not re-run. The remediation adds `os.get_blocking` and `os.set_blocking` on a local
  descriptor and sends no byte; the stock `-I` client run above and the new stock-pair tests cover the
  connection layer. Cycle 9's matrix at `3a35be1` (12 pairs against `b819de6`, `2.8.4` and stock, on
  3.12 and 3.14) stands.

### 2. The new tests discriminate (mutation)

The selected tests ran with a scratch plugin replacing `_no_delay_connection`; the tree was not
edited.

| Mutation | Result (3.12.13 and 3.14.6) |
|---|---|
| none (green control) | all pass |
| `696f24d` body (no restore) | **4 fail**: `test_roundtrip_under_default_timeout[no-tls]`, `test_no_delay_connection_keeps_blocking_mode[blocking]`, `test_plain_listener_under_default_timeout`, `test_plain_client_under_default_timeout` |
| restore by forcing blocking (3.14.6) | **1 fails**: `test_no_delay_connection_keeps_blocking_mode[non-blocking]` |

The `[tls]` round trip passes under every mutation, as it should: TLS sets the option on the raw
socket and never takes this path. The `default_timeout` fixture restores the previous default in a
`finally`, so no test leaks it.

### 3. Notes (dropped)

- **`_cut` (`core/queue.py:500`)** builds a socket object on a duplicate with `socket.fromfd`, so
  under a default timeout it too leaves a live connection's description non-blocking. It shuts both
  directions down at once, so the handler sees end of file or a broken pipe either way. Server stop
  with live connections under the timeout is exercised by `test_roundtrip_under_default_timeout[no-tls]`
  and the thread probe above. Not a finding.
- **The test helper `no_delay()` (`tests/test_queue.py:273`)** uses `socket.fromfd` and so clears
  the blocking mode under `default_timeout`. In `test_no_delay_connection_keeps_blocking_mode` it runs
  after the blocking assertion, so the test is unaffected. Not a finding.

### Findings

**P1 — LOW — PLAUSIBLE — `docs/security.rst` says the cleartext pair sets `TCP_NODELAY` on every TCP
connection without the platform qualifier.**

- **Where.** `docs/security.rst:132-133`: "Its pair is the stock listener and client, with
  `TCP_NODELAY` set on each TCP connection."
- **Why.** On Windows the cleartext path skips the option: `_no_delay_connection` returns when the
  connection has no descriptor to duplicate (`core/queue.py:96`, its own comment names Windows), and
  `docs/install.rst:82` lists Windows as supported. The same commit qualified `AGENTS.md:313` ("where
  the platform allows it"), which was cycle 9's F2(b) point, but not this page.
- **Evidence.** By reading only; there is no Windows host here, so it was not executed. It does not
  block: the wire and the behaviour are unaffected. Suggested wording: "set on each TCP connection
  where the platform allows it".

### Cycle-9 findings

- **F1 — closed.** Every F1 scenario now passes on 3.12 and 3.14: in-process, the timeout in the
  server only, the timeout in the client only, and the stock `-I` client against a server that has the
  timeout (§1). The new tests fail against the pre-fix body (§2).
- **F2 — closed.** (a) `docs/security.rst` now says that `SecureManager` selects
  `'hypershell-plain'` when TLS is off (`SecureManager(..., tls=None)._serializer` is
  `'hypershell-plain'` on both Pythons), and that the framing, handshake and wire are the stock ones.
  The serializer key appears on the wire only inside a pickled proxy, which HyperShell never sends
  (cycle 9 §3). (b) `AGENTS.md:313-321` and the `_no_delay` docstring now say that the client writes
  twice before a read and the server does so only under TLS 1.3, which agrees with cycle 9's
  instrumented trace and the stock `deliver_challenge` / `answer_challenge` order, and the bullet
  carries the platform qualifier. The
  public page's missing qualifier is P1.

### Requirement → evidence matrix (this cycle's delta)

| R-ID | Touched by the delta | Evidence |
|---|---|---|
| R1, R2, R5, R7, R9 | no | suites below |
| R3 | the cleartext pair | `test_roundtrip` and `test_roundtrip_under_default_timeout`, TLS on and off, 3.12 and 3.14 |
| R4 | the cleartext pair | the stock `-I` client and split-process probes (§1); `test_queue_tls.py` unchanged; cycle 9's matrix stands |
| R6 | four new tests in `test_queue.py` | §2: they fail against the pre-fix body and against a forced-blocking restore |
| R8 | no base test modified | full suite below |

**Scope creep.** None. The change is the F1 fix, its tests, and the F2 docs.

### Verification run

| Gate | Result |
|---|---|
| `tests/test_queue.py tests/test_queue_gc.py tests/test_queue_address_family.py`, 3.12.13 | 66 passed (59.5 s); cycle 9 had 60, and the new tests add 6 |
| the same three files, 3.14.6 | 63 passed, 3 skipped (48.8 s) |
| full suite, 3.12.13 | 775 passed, 79 skipped (7 min 52 s); cycle 9 had 769 |
| descriptor-state probe, 3 timeouts × 2 presets | only `TCP_NODELAY` changes, 3.12 and 3.14 |
| split-process and stock `-I` client probes | HEAD pass; `696f24d` body fails |
| thread probe (16 × 20 clients) | HEAD 0 errors, descriptors back to baseline; `696f24d` body 16/16 fail |
| mutations (§2) | both caught |
| `unsupported_internals()` | `[]` on 3.12.13 and 3.14.6 |

### Human-gate triggers

- No CONFIRMED finding this cycle. Cycle 9's F1 sat in `core/queue.py` (high-blast-radius core), so
  the human sign-off it required still governs the step to `/hs-publish`. This is cycle 10, past the
  rubric's 2–3-cycle bound, and the human gate already governs the loop. P1 is for triage.

## Review cycle 11 (scoped) — approved (2026-11-05)

- **Reviewed commit:** c84a4eedea2dfc2f8e5e1ba201e864526c63add4  ·  **Base:** `b61dbbe` (merge base
  with `origin/develop`, unchanged since cycle 8)  ·  **Date:** 2026-11-05
- **Verdict:** approved. No CONFIRMED and no PLAUSIBLE finding. Cycle 10's P1 (docs qualifier) is
  closed.
- **Cycle:** 11. The scope is `256df35..c84a4ee`. The only commit outside `spec/` is `c84a4ee`, which
  touches `tests/test_groups.py` (a base test, changed by the foreman's ruling below) and
  `docs/security.rst`.
- **Mode:** scoped to that commit. One blind pass was done inline by the orchestrator, with no
  subagent, at the foreman's direction. The inputs were `GOAL.md`, the spec-excluded diff
  `256df35..c84a4ee`, the `AGENTS.md` invariants and the runnable repo. `TECH.md` was read only after
  the findings below were formed, to transcribe the ruling and check its gate record.
- **Runs:** scratch `git archive` extracts of `c84a4ee` with their own venvs built from `uv.lock`
  (`uv sync --frozen`): 3.12.13 and 3.14.6, plus a separate 3.12.13 extract for mutations. Each
  pytest run had the venv's `bin` first on `PATH`, since the tests shell out to `hs`, and ran in its
  own process group under a perl kill timer. The worktree was never edited.

### CI finding and ruling (recorded here; until now only in TECH.md)

- **Finding (CI, Linux).** PR #76's run `41971072285` at `229ded8` failed one test on 3.13 and 3.14:
  `tests/test_groups.py::test_group_parallel_execution_within_group`, "Expected > 10s for parallel
  execution, took 9.7s" (3.13) and "took 9.8s" (3.14). The rest of each job passed (770 passed, 83
  skipped). The commit before it, `1e79da6`, had passed, and `229ded8` changed only `spec/`. The test
  predates this branch. Its timing check was a *lower* bound on the whole run
  (`elapsed > 4 + 3*2`), so it timed process startup and the scheduler's backoff between groups.
  It did not time parallelism. The faster `TCP_NODELAY` connections put a Linux run just under 10 s.
  The assertion was miscalibrated; nothing in the code regressed.
- **Ruling (foreman, 2026-11-05).** The test asserts the properties it is named for, from the task
  times in the database, with no wall-clock bound. This is the first change on the branch to a test
  that predates it: `git diff --diff-filter=M --name-only b61dbbe c84a4ee -- tests/` now lists
  `tests/test_groups.py`.
- **CI at `c84a4ee`.** Run `41974105408` passed all 14 jobs, including `tests (3.11)` through
  `tests (3.14)`.

### 1. The new test checks what it is named for (mutation)

`task_intervals()` (`tests/test_groups.py:282`) reads `group`, `start_time` and `completion_time`
for every task with `hs list … --json`. The test (`:295`) asserts four groups of two tasks. It then
asserts **(a)** that within each group the two run intervals overlap (`max(starts) < min(ends)`,
`:323`) and **(b)** that each group's last completion comes before the next group's first start
(`:326-330`).

| Variant (3.12.13, scratch extract) | New test | Old test (`256df35` body) |
|---|---|---|
| unchanged, `-N8` | pass | (fails on Linux CI, see above) |
| `-N1` in place of `-N8` | **fails (a)**: "Group 1 ran serially"; the second task started 0.97 ms after the first completed | **passes** (14.9 s) |
| `Task.increment_group` with no hold on incomplete tasks, next group = lowest unscheduled one | **fails (b)**: "Group 2 started before group 1 completed" (5.2 s); (a) held | fails (5.8 s against 10 s) |

The old assertion never tested parallelism within a group: a single executor passes it, because
eight serial seconds plus the backoff clear 10 s. It did catch group ordering, and the new test
still catches it, now directly. **Coverage lost:** only a floor on the total run time, which in
practice was a floor on the scheduler's backoff between groups. That is not a GOAL property; R9
asks for the opposite direction. The return code, the sorted output, the three group-transition
log lines and the 8 + 8 per-task completion lines are still asserted.

### 2. Robustness (executed and by reading)

- **Margins.** A scratch probe ran the same workload three times on 3.12.13 and printed the
  margins. Overlap within each group was 1.010–1.032 s, against a threshold of 0, and the two
  starts were at most 18 ms apart. The gap between groups was 1.999–2.025 s, against 0. The failing
  margin under `-N1` is about 1 ms.
- **Clock granularity.** Times are `datetime.now().astimezone()` taken by the client
  (`client.py:755` start, `:784` completion) and stored to the microsecond. Both checks use a strict
  `<`, so a tie fails rather than passes. A tie would need two causally ordered events, with a
  database round trip between them, to land in the same microsecond.
- **A missing time.** The helper asserts that both times are set before it parses them
  (`tests/test_groups.py:288`), so a missing time fails with the record shown, not with a
  `TypeError`. Pytest rewrites asserts throughout a test module, helper included. In a completed run
  every row has both times: the client sets them on the normal path and on both early-exit paths
  (`client.py:728-736`).
- **Format and time zone.** `hs list --json` emits `isoformat(sep=' ')` (`core/types.py:60`). On the
  test's SQLite site, `DateTime(timezone=True)` comes back naive (probe:
  `"2026-11-05 00:36:53.259280"`, `tzinfo` None). `datetime.fromisoformat` parses that form, with or
  without a fraction, on 3.11+, which is the project's floor. Every value comes from columns of one
  type, so naive and aware values never mix. A server database would give aware values throughout,
  but this test always runs on the `temp_site` SQLite file. CI's `tests (3.11)` passed.
- **Dropped.** The times are naive local wall-clock time, so a DST fall-back or a clock step during
  the 15 s run could misorder them. That is pre-existing storage behaviour, the CI runners use UTC,
  and it is speculative, so it is not a finding.

### 3. Docs

`docs/security.rst:132-135` now says that the cleartext pair sets `TCP_NODELAY` "on each TCP
connection where the platform allows it". That matches the code. `_no_delay`
(`core/queue.py:70-82`) acts on `AF_INET`/`AF_INET6` stream sockets only and ignores `OSError`.
`_no_delay_connection` (`:85-107`) returns where there is no descriptor to duplicate (Windows). The
two ends are `_PlainListener.accept` (`:350`) and `_plain_client` (`:361`). A Sphinx HTML build of the
extract renders the sentence and raises no warning on this page; the two warnings it does raise are
pre-existing toctree warnings on other pages. **Cycle 10 P1: closed.**

### Contract note (not a finding)

GOAL R8 reads "the existing test suite SHALL pass with no existing test modified". `c84a4ee` modifies
`test_group_parallel_execution_within_group` under the foreman's ruling. The ruling holds on the
merits: the replacement is strictly stronger on both properties (§1), and all it gives up is a
latency floor. However, `GOAL.md` carries no clarification for it, and the contract-drift check shows
only the shaping commit `da987c2`. A dated R8 clarification in `GOAL.md` would let the locked
contract and the branch agree before publish. That edit is the human's to make, not the reviewer's.

### Requirement → evidence matrix (this cycle's delta)

| R-ID | Touched by the delta | Evidence |
|---|---|---|
| R1–R7, R9 | no | CI at `c84a4ee` green on 3.11–3.14 and the server-database jobs |
| R8 | one base test modified, by ruling | §1 and the contract note; `tests/test_groups.py` 11 passed on 3.12 and 3.14 |

**Scope creep.** None. The change is the ruled test fix and cycle 10's P1 docs wording.

### Verification run

| Gate | Result |
|---|---|
| `test_group_parallel_execution_within_group`, 3.12.13 | 10/10 pass (15.0–16.1 s) |
| the same test, 3.14.6 | 4/4 pass (15.3–16.2 s) |
| `tests/test_groups.py`, 3.12.13 | 11 passed (63.0 s) |
| `tests/test_groups.py`, 3.14.6 | 11 passed (62.4 s) |
| margin probe, 3 runs, 3.12.13 | overlap ≥ 1.010 s, gap ≥ 1.999 s |
| mutations (§1) | `-N1` fails (a); the ordering mutant fails (b) |
| Sphinx HTML build | no warning on `security.rst` |
| CI `41974105408` at `c84a4ee` | 14/14 jobs passed |

### Human-gate triggers

- No CONFIRMED finding, and `c84a4ee` touches no high-blast-radius file. A base test was changed
  under the foreman's ruling, so the human gate already holds this step. This is cycle 11, past the
  rubric's 2–3-cycle bound, and the human gate governs the loop.
