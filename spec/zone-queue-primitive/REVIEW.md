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
