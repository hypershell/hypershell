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
