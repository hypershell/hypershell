---
slug: zone-queue-primitive
title: Serve the managed queues in-process on a threading primitive (zone, cycle 1)
kind: feature
appetite: big
status: blocked
branch: feature/zone-queue-primitive
base: develop
current_phase: P7
last_updated: '2026-11-02'
phases:
- id: P1
  name: 'In-process serve loop: no manager child, clean shutdown and port release'
  status: done
  satisfies:
  - R1
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_queue.py tests/test_queue_start.py tests/test_queue_tls.py
    && .agents/factory/bin/temp_site.sh sh -c "seq 200 | uv run hsx -t 'echo {}' -N4
    && seq 200 | uv run hsx -t 'echo {}' -N4 --no-tls && uv run hs list --count"
- id: P2
  name: Swap the four queues to queue.Queue with a pinned method surface
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_queue.py tests/test_queue_tls.py && .agents/factory/bin/temp_site.sh
    sh -c "seq 200 | uv run hsx -t 'echo {}' -N4 && seq 200 | uv run hsx -t 'echo
    {}' -N4 --no-tls && uv run hs list --count"
- id: P3
  name: Tests for the server state machines that touch the managed queues
  status: done
  satisfies:
  - R7
  depends_on:
  - P2
  parallel: true
  hammerable: false
  hill: downhill
  verify: uv run pytest -v tests/test_server_fsm.py
- id: P4
  name: 'No-behaviour-change proof: workload test, base parity, b819de6 client compatibility'
  status: done
  satisfies:
  - R4
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -v && uv run python tests/bench/bench_queue.py parity --base
    b819de6 --feature HEAD && uv run python tests/bench/bench_queue.py compat --server
    HEAD --client b819de6
- id: P5
  name: Local throughput and footprint gate against b819de6
  status: done
  satisfies:
  - R9
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run python tests/bench/bench_queue.py gate --base b819de6 --feature HEAD
    --repeats 5
- id: P6
  name: 'Multi-node drive, run A: build the cluster-agnostic drive and hand it back
    (external wait follows)'
  status: done
  satisfies:
  - R9
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: downhill
  verify: sh tests/cluster/smoke.sh
- id: P7
  name: 'Multi-node drive, run B: collect and judge the external evidence against
    2.8.3'
  status: pending
  satisfies:
  - R9
  depends_on:
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python tests/cluster/compare.py spec/zone-queue-primitive/evidence/cluster
    --baseline 2.8.3 --candidate feature --control b819de6
review:
  last_reviewed_commit: 12af7fbab7cdc1fd391acf0e8320d40e311b3b2c
  verdict: changes-requested
  blocked_reason: 'review cycle 2: C3, C4 CONFIRMED in core/queue.py; human gate,
    escalated (no convergence)'
  cycle: 2
---
# TECH.md — Serve the managed queues in-process on a threading primitive (zone, cycle 1)

The YAML frontmatter above is the resume ground-truth. Read it with
`uv run python .agents/factory/bin/next_phase.py spec/zone-queue-primitive/TECH.md`.
`hs-build` executes the next actionable phase, runs its `verify:`, updates state with
`set_phase.py`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). The R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). Section references below (§) point there.
- **Backing research:** [`research/00-digest.md`](research/00-digest.md), briefs 01–04, and the
  runnable prototypes in [`research/prototypes/`](research/prototypes/).

## Frontmatter field reference

See [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md). This cycle adds
one convention:

**External wait.** P7 can only pass on evidence produced outside the repo, by the foreman on a real
cluster, from what P6 hands back.

- When `spec/zone-queue-primitive/evidence/cluster/` is absent, `hs-build` marks P7 `blocked` with
  `blocked_reason: "awaiting multi-node evidence (run B)"` and stops. That is not a failed verify,
  so it does not count against `attempts`.
- GOAL R9 needs that evidence only "before the pre-release". The foreman may therefore run
  `hs-review` on P1–P6 while P7 is blocked.

## Conventions (apply to every phase)

- **Rules.**
  - [`AGENTS.md`](../../AGENTS.md) is the constitution.
  - Use the invariants list in [`invariants.md`](../../.agents/factory/invariants.md) (§5, §6,
    §8, §9 and §12 apply here).
  - **No existing test may be modified (R8).** Add new files only, and check with
    `git diff b819de6 --stat -- tests/` showing only new files.
  - **High blast radius:** `core/queue.py`. `server.py`, `client.py`, `submit.py` and `cluster/*`
    must not change at all in this cycle. If a phase seems to need a change there, stop and
    escalate. **Exception (review-1 triage, foreman's ruling):** C2's fix changes
    `server.py` `ServerThread.start` only, so a failure to start the queue is raised in the caller
    and "Server listening" is logged once the queue is ready.
- **Commits and comments.**
  - One phase per `hs-build`; one atomic commit with subject `[feature] Build zone-queue-primitive
    P<n>: …`.
  - No `Co-Authored-By` trailer.
  - Comments are declarative statements. Don't put R-IDs or P-IDs in source.
- **No CLI change**, so there are no `docs/_include` or `share/` edits. Running `hs server|client|submit|cluster
  --help` must give byte-identical output to base.
- **Red first.** Write a phase's tests first, run them against the unchanged code, and record which
  ones are red in the commit body. Then implement.
- **Public repo.** Scripts, results and evidence must never contain host names, user names,
  accounts, partitions or site paths.
- **Tooling.** The harness's venv builds need the package index (`pypi.org`,
  `files.pythonhosted.org`) unless the uv cache is warm.

---

## Phase P1 — In-process serve loop: no manager child, clean shutdown and port release
**Satisfies:** R1, R4, R5, R6 · **Depends on:** —
**Goal:** `QueueServer.start` serves the existing four queues (still `JoinableQueue` in this phase)
from inside the server process, through HyperShell's own accept loop. A `b819de6`-era
`QueueClient` round-trips unchanged, and shutdown releases the port without exiting or touching
stdio. This is the core, small and novel slice: everything else builds on it.

- [x] **Red first.** Create `tests/test_queue.py` (`@mark.unit`) with the following tests, run them
      on the unchanged code, and record the result:
  - `active_children()` count unchanged across `start()`, parametrised over `fork` and `spawn`
    (restore the start method). Red on base.
  - Two `QueueServer`s alive in one process each serve their own queues under `fork`. Red on base.
  - After shutdown, a same-thread `QueueClient` of a second server on the same port works, for both
    `127.0.0.1` and `localhost`. The `localhost` case is red on base.
  - Green regression guards: a round trip on all four queues through `QueueClient`, TLS on and off,
    with a module-local TLS fixture modelled on `test_queue_tls.py` L44–57 and not imported; the
    sentinel reaching a remote consumer; `shutdown()` returning with `sys.stdout`/`sys.stderr`
    identity unchanged; the same port rebinding at once; and accept/handler threads draining to
    baseline within 2 s (poll, don't assert immediately).
  - Bind `('127.0.0.1', 0)` and read `server.address`. Use 0.2 s operation timeouts.
- [x] **`_InProcessServer(Server)` in `core/queue.py`** (§2.1):
  - `stop_event` is created and never set.
  - Add a halt flag, a live-connection set and a lock.
  - `serve()` is a selector loop on the listening socket (`POLL = 0.25`) with a broad per-accept
    `except` and daemon `hypershell-queue-handler` threads.
  - Add a `handle_request` override that removes the connection from the live set.
  - The remote `shutdown` RPC override never exits.
  - `stop()` sets the halt flag, calls `SHUT_RDWR` then close on the listener, and calls
    `SHUT_RDWR` on each live connection: through the TLS socket, else through a socket duplicated
    from the fd, else it skips. It never joins.
  - Add a `_listening_socket(listener)` helper. Adapt `research/prototypes/serve_loop/proto3.py`.
- [x] **`SecureManager._serve_in_process(registry)`** (§2.1 steps 1–5):
  - TLS install only when active.
  - Build the server and set `_address` to the real bound address and `_process = None`.
  - Start the daemon `hypershell-queue-server` thread, set the state to `STARTED`, and set the
    `Finalize`-backed `shutdown`.
  - `_finalize_in_process` stops the server, sets `SHUTDOWN`, and pops `BaseProxy._address_to_local`
    for both the configured and bound addresses under `BaseProxy._mutex`.
- [x] **`QueueServer.start`:**
  - Build a per-instance registry with the existing typeids and lock-free closures. Do not call
    `register`.
  - Run any `initializer` once.
  - Call `_serve_in_process`, then set `ready`.
  - Leave `SecureManager.start`, `_child_bootstrap`, `SecureListener`, `secure_client`,
    `SecureConnection` and `QueueClient` untouched.
- [x] **Docstrings.** Reword "server subprocess" in `SecureManager.start`, in `core/tls.py`
      (L80–82, L214, L224) and in `core/logging.py` `close_inherited_slot_locks` (now a generic
      fork-without-exec helper). Behaviour is unchanged.
- [x] **Private-internals guard** (plan-gate ruling on deviation row 1). `TestPrivateInternals` in
      `tests/test_queue.py` asserts the presence and shape of every CPython private the loop uses:
      `Server.__init__(registry, address, authkey, serializer)` and its `listener`, `address`,
      `mutex`, `id_to_obj`; `Server._handle_request`/`handle_request`/`serve_client`/`shutdown`
      signatures; `'shutdown' in Server.public`; `serve_client` looping on `self.stop_event` and
      exiting through `sys.exit`; the stock listener's `_listener._socket`; `listener_client`;
      `BaseManager._state/_address/_authkey/_serializer` and `State`; `BaseProxy._address_to_local`
      and `_mutex`; `util.Finalize(args=, exitpriority=)`. A CPython change fails it loudly.
- **Built note.** Shutdown pops the proxy cache for the bound address and for the configured host
  with the bound port: with a requested port 0 the configured `(host, 0)` is never a dial address.
- **Remediation (review cycle 1; triage in [`REVIEW.md`](REVIEW.md)).** P1 was reopened for C1, C2
  and the accepted P1 finding.
  - [x] **Red first:** `tests/test_queue_start.py` (new), 9 tests, all red on `2ec014e`: a
        same-process client with another TLS config, the process-wide TLS slot after a server
        start, `start()` on a busy port (TLS on and off), two simulated internals drifts, and
        `hs server` on a busy port (TLS on and off) and on drifted internals.
  - [x] **C1:** the in-process server's TLS config lives on its own `SecureListener`
        (`serve_with`), which builds its server context from it on first accept.
        `_serve_in_process` no longer calls `install_process_context`; the generic manager child
        and clients still use the process-wide slot.
  - [x] **C2:** `_serve_in_process` raises `QueueServerError` (a `RuntimeError`) naming the
        address and the OS error when the bind or listen fails. `ServerThread.start` re-raises a
        start failure in the caller and logs "Server listening" only once the queue is ready; the
        shared exception mapping turns it into one CRITICAL line and `exit_status.runtime_error`.
  - [x] **P1:** `unsupported_internals()` holds the presence and shape checks (signatures and
        compiled names; no socket). `_serve_in_process` runs it before binding and raises
        `QueueServerError` ("…multiprocessing internals are not supported: …").
        `TestPrivateInternals` asserts it reports nothing and keeps the live checks.
- **Verify:** `uv run pytest -v tests/test_queue.py tests/test_queue_start.py
  tests/test_queue_tls.py`, then drive `hsx` with TLS on and with `--no-tls` in a `temp_site` (the
  frontmatter command).
- **Touches:** `src/hypershell/core/queue.py`, `src/hypershell/core/tls.py` (docstrings),
  `src/hypershell/core/logging.py` (docstring), `tests/test_queue.py` (new),
  `tests/test_queue_start.py` (new); remediation also `src/hypershell/server.py`
  (`ServerThread.start`) and `AGENTS.md`.

## Phase P2 — Swap the four queues to queue.Queue with a pinned method surface
**Satisfies:** R2, R3, R4, R6 · **Depends on:** P1
**Goal:** the managed queues become threading `queue.Queue`s with today's bounds. There is no pipe
and no feeder thread. The remote method surface is identical on 3.11–3.14.

- [x] **Red first:** add these tests to `tests/test_queue.py` and run them on P1.
  - The four attributes are `queue.Queue`, with `maxsize == config.size` (`scheduled`, `completed`)
    and `0` (`heartbeat`, `confirmed`).
  - The proxy's `_exposed_` is exactly the nine common methods; `shutdown` is absent on 3.13+.
  - Remote `get(timeout)` on an empty queue raises `queue.Empty`, and remote `put(timeout)` on a
    full bounded queue raises `queue.Full`, each within `timeout + 1.0`.
  - `join()` returns once every item is `task_done`.
  - A round trip in both directions on all four queues, TLS on and off: server-thread put to client
    get, and client put to server-thread get.
- [x] Add `QUEUE_EXPOSED` (the nine) to the registry entries. In `QueueServer.start`, create
      `queue.Queue`s and drop the `multiprocessing.JoinableQueue` import. Retype the `_get_*`
      methods and the `QueueInterface`/`QueueClient` annotations.
- [x] Update `AGENTS.md`. In § Architecture & data flow, "four `JoinableQueue`s over one manager"
      becomes "four threading `queue.Queue`s served in-process by the manager's request handling".
      Note in § Queue transport & security that the server process hosts the manager. Update
      `docs/security.rst` L37–44 to match. These are factual edits; the security posture is
      unchanged.
- **Built note.** The bidirectional round trip already landed in P1 (`test_roundtrip`, TLS on and
  off); P2 adds the type/bound, exposed-surface, `Empty`/`Full` and `join` tests.
- **Verify:** the frontmatter command (queue tests plus `hsx` drives with TLS and `--no-tls`).
- **Touches:** `src/hypershell/core/queue.py`, `tests/test_queue.py`, `AGENTS.md`,
  `docs/security.rst`.

## Phase P3 — Tests for the server state machines that touch the managed queues
**Satisfies:** R7 · **Depends on:** P2
**Goal:** the first direct tests of `Scheduler.post_bundle`, `Confirm`, `Receiver` and
`HeartMonitor`. For each, they show data moving through its queue, re-entry when a queue operation
times out, and a halt on the sentinel. This phase adds tests only; `server.py` is not edited.

- [x] Create `tests/test_server_fsm.py`. Unit tests use `SimpleNamespace(scheduled=Queue(1),
      completed=Queue(1), heartbeat=Queue(), confirmed=Queue())` as stand-ins.
  - Monkeypatch `hypershell.server.DEFAULT_REMOTE_TIMEOUT` to `0.05`.
  - Use `in_memory=True` for Receiver, Confirm and HeartMonitor.
  - Sketches are in research 02 §6.
- [x] **Receiver:**
  - empty → `unload_bundle()` returns `UNLOAD` (re-entry);
  - a packed bundle moves through `UNPACK` → `UPDATE` → `UNLOAD`;
  - `make_sentinel()` → `FINAL`;
  - `ReceiverThread` joins within 2 s after the sentinel.
- [x] **Confirm:** empty → `UNLOAD`; `ClientInfo` pack → `UNPACK`/`UPDATE`; sentinel → `FINAL`;
      the thread form joins.
- [x] **HeartMonitor** (`evict_after=10`):
  - empty → `SWITCH`, then `NEXT`;
  - a beat → `UPDATE` and registered;
  - `should_signal` → `SIGNAL`, which puts one sentinel on `scheduled`. Keep `len(beats)` at or
    below the free slots, to avoid the pre-existing hang at L644.
  - heartbeat sentinel → `FINAL`.
- [x] **Scheduler:**
  - `post_bundle()` on an empty `scheduled` → `LOAD`, and the bundle can be fetched with `get`;
  - on a full `scheduled` → `POST` after the timeout (re-entry);
  - halt: `monkeypatch.setattr(hypershell.core.signal, 'RECEIVED', SIGUSR1)` → `load_bundle()`
    returns `FINAL`. Always restore the flag with monkeypatch, because it is sticky.
- [x] **One `@mark.integration` per FSM** through a started `QueueServer` and a `QueueClient`. The
      client puts or gets, the FSM thread consumes or produces, and the sentinel halts it.
- **Built note.** The Scheduler's integration pass uses a module-local `task_db` fixture (modelled
  on `tests/test_retry_gate.py`, not imported) and `forever_mode=True, poll=1`: it posts three
  one-task bundles to a remote client, then halts through the patched `RECEIVED`. The HeartMonitor
  pass also shows a `FINISHED` beat unregistering the client before the sentinel halts it.
- **Verify:** `uv run pytest -v tests/test_server_fsm.py`.
- **Touches:** `tests/test_server_fsm.py` (new).

## Phase P4 — No-behaviour-change proof: workload test, base parity, b819de6 client compatibility
**Satisfies:** R4, R8 · **Depends on:** P2
**Goal:** prove that nothing a user or client can see has changed. That means the full suite with
no existing test modified, unchanged help output, a grouped workload with a fail-then-retry task
reaching the same final states as base, and `b819de6` clients completing a workload against the new
server.

- [x] **`tests/test_queue_workload.py`** (`@mark.integration`), in a `temp_site`:
  - Submit a grouped workload. Use `#HYPERSHELL: group:N` tags as in `tests/test_groups.py`.
    Include one task that fails on its first attempt and succeeds on retry, for example a marker
    file in the site (`test -f m || { touch m; exit 3; }`), with `-r 1`.
  - Start `hs server` and two `hs client`s on loopback.
  - Assert: every task reaches its expected final state (the retried task has 2 rows, attempt 2
    exits 0, attempt 1 is `retried`); no result is lost (rows and completion counts); and every
    process exits 0 within 60 s.
- [x] **`tests/bench/bench_queue.py`** (never collected). Adapt
      `research/prototypes/bench/bench.py`.
  - A venv builder per git ref: `git archive` into `$TMPDIR`, then `uv sync --frozen --no-dev
    --no-group docs --no-editable`. `HEAD` means the committed feature.
  - Subcommand **`parity`**: run the same grouped/retry workload on base and feature. Diff the
    normalised task table (`args`, `group`, `attempt`, `exit_status`, `retried`, completed or not;
    ignore ids and times). Bound every process's exit.
  - Subcommand **`compat`**: start a server from `--server` and clients from `--client` (TLS on and
    off). Require every task to exit 0 and every rc to be 0.
  - Use exit codes from `cmdkit.app.exit_status` where the harness imports hypershell; otherwise
    exit 0 or 1.
- [x] Check that `hs server|client|submit|cluster --help` output is byte-identical between the
      base venv and the feature venv. Make this part of `parity`.
- **Built note.** `parity` runs the grouped/retry workload TLS on and off (10 rows each: 9 tasks
  plus the retry) and checks the four `--help` outputs; `compat` runs 200 `echo` tasks TLS on and
  off. `test_queue_workload.py` also asserts group ordering (each group's last completion precedes
  the next group's first schedule) and the retry's `previous_id`/`next_id` link.
- **Verify:** `uv run pytest -v` (the full suite), then `bench_queue.py parity` and `compat` (the
  frontmatter command). Also confirm that `git diff b819de6 --stat -- tests/` lists only new files.
- **Touches:** `tests/test_queue_workload.py` (new), `tests/bench/bench_queue.py` (new).

## Phase P5 — Local throughput and footprint gate against b819de6
**Satisfies:** R9 (single-host clause) · **Depends on:** P4
**Goal:** a reproducible single-host benchmark showing that throughput didn't regress and that
process count and FDs are no higher.

- [x] Add a `gate` subcommand to `tests/bench/bench_queue.py`, ported from the prototype's
      `one_run` and sampler.
  - **Setup:** pre-submit M `true` tasks, then run `hs server --restart`. Detect readiness by
    LISTEN through psutil, with no probe connection. Start N loopback clients.
  - **Metric:** p10–p90 tasks/s from the DB timestamps. Also report the full span.
  - **Peaks:** sample the server's own and whole-tree children, threads, FDs and RSS.
  - **Context:** record the Python version and the `multiprocessing` start method.
- [x] **Configs:**
  - 4×4 b=1, TLS on and off;
  - 16×1 b=1, TLS on and off;
  - 4×4 b=50 (client `-b 50 -w 1`, M=20000), TLS on;
  - 32×1 b=1, TLS on.
  - M=5000 unless stated. 5 repeats, with the builds interleaved A B A B.
- [x] **Thresholds** (PLAN §6), printed per config and exiting non-zero on any failure:
  - **Throughput:** pass when the feature median ≥ the base median − max(base spread, feature
    spread, 5% of the base median).
  - **Footprint:** peak tree children and peak tree FDs for the feature must be ≤ base.
  - **Reported only:** RSS, tree threads, and own threads and FDs.
- [x] Put the gate output (a table, no host names) in the commit body.
- **Gate reading (plan-gate ruling, foreman).** GOAL R9's "its process count and open file
  descriptors" means the **server's whole process tree**: on base the queue sockets and handler
  threads live in the manager child, so only tree totals compare like with like. The gate therefore
  fails on peak tree children or peak tree FDs above base, and reports the server process's own
  thread and FD counts (which rise by about 3 per connected client, by design) without gating them.
- **Built note.** `gate` also requires every run clean (server and client rc 0, rows = M, none
  incomplete), takes `--only CONFIG` and `--out FILE.jsonl`, and prints Python version and start
  method per build. Laptop spreads are wide (30-77 tasks/s at b=1), so the 5% floor never binds.
- **Verify:** `uv run python tests/bench/bench_queue.py gate --base b819de6 --feature HEAD
  --repeats 5`. About 30 min.
- **Touches:** `tests/bench/bench_queue.py`.

## Phase P6 — Multi-node drive, run A: build the cluster-agnostic drive and hand it back
**Satisfies:** R9 (multi-node clause, preparation) · **Depends on:** P5
**Goal:** a self-contained, site-neutral drive that any launcher can run, rehearsed locally. After
this phase, `hs-build` **stops and hands back** what the foreman needs to submit. This is an
external wait.

- [x] **`tests/cluster/build_archive.sh`** (POSIX sh). Takes `HSA_REF` (a git ref: `2.8.3`,
      `b819de6` or the feature head), `HSA_PYTHON` (e.g. `3.12`), optional `HSA_PLATFORM` (to
      cross-build for the nodes) and `HSA_OUT`. It produces one `tar.gz` containing:
  - a relocatable standalone interpreter (`python/`);
  - `site/`, from `uv export --frozen --no-dev --no-group docs --no-emit-project --no-hashes` plus
    `uv pip install --target site`, then `compileall`;
  - `LABEL`.

  The archive holds no absolute paths and no console scripts.
- [x] **`tests/cluster/drive.sh`** (POSIX sh). Adapt `research/prototypes/bench/drive.sh` to the
      PLAN §2.6 interface, with only `HSD_*` variables. Changes from the prototype:
  - **Remove every host-name record:** no `hostname` field, and `server.ready` holds only the port.
    `HSD_CLIENT_INDEX` is required for clients.
  - Add `HSD_DB`, and refuse a DB path outside `HSD_LOCAL` unless `HSD_ALLOW_SHARED_DB=1`.
  - Record the hypershell version, the Python version and the start method.
  - Unpack once per node behind a `mkdir` lock.
  - Keep logs node-local, copying them to results only on failure.
  - Bound every wait (`HSD_WAIT`, `HSD_TIMEOUT`, `HSD_IDLE`).
- [x] **`tests/cluster/compare.py`** (stdlib only). Usage: `RESULTS --baseline L --candidate L
      [--control L]`. Group runs by (TLS, N, T, M, bundle), then:
  - **Results identical:** the same histogram across every run and label, rows = M, 0 incomplete,
    and every rc 0.
  - **Throughput:** the R9 rule on p10–p90 medians, with at least 3 runs per label (with fewer,
    exit 2, "insufficient runs").
  - **Footprint:** tree children and FDs ≤ baseline.
  - Exit 0 or 1 accordingly.
- [x] **`tests/cluster/smoke.sh`**, the local rehearsal:
  - Build the archive for `HEAD` (and, when `HSD_SMOKE_BASELINE` is set, for `b819de6` too).
  - Run one server and two clients as separate node-local dirs under `$TMPDIR` with a shared
    results dir, TLS on and off, M=2000.
  - Assert `compare.py` passes its identical-results check, and that a grep for
    `$(hostname)`/`$USER` finds nothing in the results.
- [x] **Hand-back:** put the run-A brief in the commit body and the `hs-build` report:
  - the archive commands for `2.8.3`, `b819de6` and the feature head;
  - the variable table;
  - the run matrix: TLS on; N ∈ {16, 128, ≥256 up to the ceiling}; M ≥ 20000; ≥3 runs per label,
    interleaved;
  - where to put the results: copy each run's `server.json` and `client-*.json` into
    `spec/zone-queue-primitive/evidence/cluster/<run_id>/`, never logs.

  Then mark P6 done and stop.
- **Built notes.**
  - The archive also carries `REVISION` (the commit id). `drive.sh` unpacks once per node and
    archive (`hsd-pkg-<archive name>` under `HSD_LOCAL`) and byte-compiles there, so a cross-built
    archive (`HSA_PLATFORM`, no `.pyc`) still imports from bytecode. Per-run state lives in
    `hsd-<run_id>`.
  - `compare.py` also requires one client record per expected client, takes `--results-only`
    (used by the smoke), and exits 2 on fewer than three runs for any label it judges.
  - `server.crt` in the shared run dir carries the server node's host name in its SAN: it is a
    transport file, never evidence. Only `server.json` and `client-*.json` are copied.
  - `build_archive.sh` needs the package index plus the standalone-Python downloads (uv's release
    mirror or GitHub). The run-A hand-off is in the P6 commit body.
- **Verify:** `sh tests/cluster/smoke.sh`.
- **Touches:** `tests/cluster/build_archive.sh`, `tests/cluster/drive.sh`,
  `tests/cluster/compare.py`, `tests/cluster/smoke.sh` (all new).

## Phase P7 — Multi-node drive, run B: collect and judge the external evidence against 2.8.3
**Satisfies:** R9 (multi-node clause) · **Depends on:** P6
**Goal:** commit the foreman's multi-node results as evidence and show unchanged results and
throughput against 2.8.3, using `b819de6` as the attribution control.

- [ ] If `spec/zone-queue-primitive/evidence/cluster/` is absent: `set_phase.py --phase P7
      --status blocked` with `blocked_reason: "awaiting multi-node evidence (run B)"`. Stop.
- [ ] Check that the evidence is sanitized: no host names, user names, accounts, partitions or site
      paths. If any is found, stop and ask; don't scrub silently.
- [ ] Run `compare.py`. Write a short `evidence/cluster/SUMMARY.txt` with the per-config table,
      median, spread and verdict for each label, and the footprint peaks.
- [ ] On a regression at high N only, don't tune in-cycle. Record it and escalate to the foreman.
      This is PLAN §5's main risk.
- **Verify:** the frontmatter command (`compare.py … --baseline 2.8.3 --candidate feature --control
  b819de6` exits 0).
- **Touches:** `spec/zone-queue-primitive/evidence/cluster/` (new data only).

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: a clean tree, on `branch`, with `base` reachable.
3. Execute every `[ ]` in the phase, consulting `PLAN.md` and `research/`.
4. Run the phase's `verify:`. Never advance on a checkbox alone.
5. Amend this file through `set_phase.py` if reality diverges. Stop and escalate only on a
   `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`, make one `[feature]` commit, stop and
   report. After P6, the report is the run-A hand-back.
