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
