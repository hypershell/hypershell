---
slug: zone-queue-primitive
title: Serve the managed queues in-process on a threading primitive (zone, cycle 1)
kind: feature
appetite: big
status: in_progress
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
  verify: uv run pytest -v tests/test_queue.py tests/test_queue_gc.py tests/test_queue_start.py
    tests/test_queue_limits.py tests/test_queue_tls.py && .agents/factory/bin/temp_site.sh
    sh -c "seq 200 | uv run hsx -t 'echo {}' -N4 && seq 200 | uv run hsx -t 'echo
    {}' -N4 --no-tls && uv run hs list --count"
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
  last_reviewed_commit: 43d2d85696dbbbf20550ba7c9a52559677e20ad2
  verdict: changes-requested
  blocked_reason: ''
  cycle: 5
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
- **Remediation (review cycle 2; triage in [`REVIEW.md`](REVIEW.md)).** P1 was reopened for C3
  and C4, and for a local Python 3.11 run.
  - [x] **Red first** (source at `1ffa825`; 6 failed): `tests/test_queue_limits.py` (new) —
        refusal past the reserve, TLS on and off (no `_open_file_limit` hook); accept back-off on
        `EMFILE` and `ENFILE` (about 750,000 accepts in 1 s); and `hs server` under soft = hard
        `RLIMIT_NOFILE` 64 (killed at 60 s, its log holding `Too many open files` and `unable to
        open database file`). `tests/test_queue_start.py` gains `hs server --tls-cert '<none>'`
        plus two clients under the default log level (no ERROR line).
  - [x] **C3(a)** — raise the soft `RLIMIT_NOFILE` at start: **withdrawn** by the foreman at build
        (2026-11-02; clarification in [`REVIEW.md`](REVIEW.md)). The limit is per process and
        inherited. `hsx`, `hs cluster` and an embedding application run the server in the process
        that also starts tasks or launchers, so every task would inherit the raised soft limit
        (`ulimit -n` 256 → 10240 in a child, checked on this host): a default-behaviour change
        below the limit. The reserve of (b) alone protects the server's own files.
  - [x] **C3(b)** — the accept loop admits a connection only when its descriptor number is
        below (soft limit − `RESERVE`, 32). Descriptors are allocated lowest-first, so admitted
        connections never hold any of the top 32, and the server's own files can fail only once
        they alone fill the reserve. A refused connection is closed at once; one WARNING
        ("Refusing new queue connections: open files near the limit of N (32 kept for the
        server)") per 60 s, DEBUG in between. No change below the threshold; none on Windows.
  - [x] **C3(c)** — an accept failing with `EMFILE`/`ENFILE` sleeps `POLL` (0.25 s) before the
        loop retries.
  - [x] **C4** — fallback path. Building the context before "Server listening" would read the
        cert and key at start; base and `12af7fb` read them on the first accept, so the preferred
        path fails its own proviso. The listener logs the first build failure at ERROR ("Queue
        server cannot set up TLS and refuses every connection (TLSError: …)") and every
        connection still fails closed. `server.py` is untouched.
  - [x] **Python 3.11:** full suite run locally (see the gate record below).
  - **Documented constraint: the open-file limit.** The server does not raise its open-file limit.
    Every queue connection holds a descriptor in the server process, so the soft limit bounds the
    connections one server can hold (about (soft limit − 32 − its own files) / 3 clients). At the
    limit, new connections are refused with one WARNING per minute, while admitted clients, the
    state machines, the database and the logs carry on. A client refused partway through
    connecting hangs until eviction, as on base (`issues/client-partial-connect-hang.md`).
  - **Gate record (macOS, spawn; the tree without C3(a), as provisional commit `09bc773`).**
    - Full suite: **549 passed** on each of 3.14.6, 3.13.16, 3.12.13 and 3.11.18.
      `unsupported_internals() == []` on 3.11.18.
    - New tests repeated 12 times (all 15 in the limit and start files): 12/12 green.
    - `hsx` drive in an isolated site, 200 tasks TLS on and `--no-tls`: rc 0, 200 distinct outputs
      each, 400 rows, 0 failed.
    - `parity` vs `b819de6`: identical task tables TLS on and off, rc 0/[0, 0]; four `--help`
      outputs byte-identical. `compat` (server here, client `b819de6`): 200/200 TLS on and off.
    - Throughput gate, 5 interleaved repeats: **PASS**, all 60 runs clean. The machine already
      carried load from processes this build did not start (1-min load average 4.8 before the
      gate, about 15 during it); they were left alone.

      | config | base median [min-max] | feature median [min-max] | delta | tolerance | tree children b/f | tree FDs b/f |
      |---|---|---|---|---|---|---|
      | 4x4 b=1 tls | 262.8 [252.3-267.5] | 263.1 [244.9-270.5] | +0.3 | 25.6 | 2/0 | 117/23 |
      | 4x4 b=1 no-tls | 250.5 [245.8-265.0] | 240.0 [235.9-255.4] | -10.5 | 19.5 | 2/0 | 116/24 |
      | 16x1 b=1 tls | 248.0 [244.9-256.5] | 260.9 [242.2-265.5] | +12.9 | 23.3 | 2/0 | 153/60 |
      | 16x1 b=1 no-tls | 261.9 [250.5-270.3] | 261.0 [257.0-271.2] | -0.9 | 19.8 | 2/0 | 153/59 |
      | 4x4 b=50 tls | 791.7 [736.6-838.1] | 800.1 [781.4-825.7] | +8.4 | 101.5 | 2/0 | 116/22 |
      | 32x1 b=1 tls | 256.6 [237.2-275.9] | 258.4 [245.6-270.0] | +1.8 | 38.7 | 2/0 | 202/107 |

    - **C3 reproduction** (the reviewer's: server soft = hard `RLIMIT_NOFILE`, `b819de6` clients
      with `-N 2`, 300 `echo` tasks, `--no-tls`, server bounded at 90 s, clients 30 s after):

      | limit, clients | server | tasks done | server | clients | state machine died |
      |---|---|---|---|---|---|
      | 128, 44 | base | 271/300 | hung, killed at 90 s | 40 rc 5, 4 hung | no |
      | 128, 44 | here | 205/300 | hung, killed at 90 s | 29 rc 5, 15 hung | **no** (cycle 2: yes, rc 5 at 2.8 s, 70/300) |
      | 64, 24 | base | 270/300 | hung, killed at 90 s | 19 rc 5, 5 hung | no |
      | 64, 24 | here | 276/300 | hung, killed at 90 s | 20 rc 5, 4 hung | **no** (cycle 2: yes, 15/300) |

      The server log has one refusal WARNING and no `Too many open files`, `unable to open
      database file` or uncaught exception. The run still does not finish, as on base: a client
      admitted for some connections and refused for a later one loses its scheduler thread
      (`EOFError`) and then hangs instead of exiting, holding confirmed tasks until the server
      evicts it (600 s by default). That is client-side behaviour, unchanged here.
    - **C4 reproduction** (`hs server … --tls-cert '<none>'` plus one client, default log level):
      the client exits 5 on both builds. Base server output ends with an accepter-thread traceback,
      `TLSError: TLSConfig.cert and TLSConfig.key are required for the server side`. Here it ends
      `ERROR [hypershell.core.queue] Queue server cannot set up TLS and refuses every connection
      (TLSError: TLSConfig.cert and TLSConfig.key are required for the server side)`.
- **Remediation (review cycle 3; triage in [`REVIEW.md`](REVIEW.md)).** P1 was reopened for C5
  and C6, under the maintainer's one further cycle.
  - [x] **Red first** (source at `d3e00f9`): `tests/test_queue_gc.py` (new). The reviewer's two
        probes become four bounded tests, TLS on and off: a short-lived same-process client
        dropped as cyclic garbage each round (`p_gc`), and the body of
        `test_shutdown_releases_port` in a loop (`p_restart`). Each runs 200 rounds in its own
        process group under a lowered collection threshold (10 for the first; 200 and 300 in turn
        for the second, where the stall shows most often). A `faulthandler` watchdog dumps every
        stack and exits after 15 s on one round, so a stall fails the test instead of hanging the
        suite; the test kills the group at 240 s as a backstop. Two more tests: a dropped
        same-process client's decrefs still reach the server (its object count returns to 0), and
        the finalizer's hand-off rule; plus `TestPrivateInternals.test_proxy_finalizer`.
  - [x] **C5 — property:** no thread of the in-process server ever blocks on a connection that
        only its own accept loop can answer, whatever thread the cyclic collector runs a proxy
        finalizer on. **Mechanism:** a queue client's proxies get `_decref` as their finalizer in
        place of the stock `BaseProxy._decref` (the `util.Finalize` that `_incref` registers keeps
        its arguments; only its callback is swapped). When the finalizing thread is **on the server
        side** (item 1), `_decref` hands the stock decref's arguments to a `SimpleQueue` and
        returns; otherwise it runs the stock decref, unchanged, in the finalizer's thread. A daemon
        `hypershell-queue-decref` thread sends handed-off decrefs with the stock decref, in order.
        *(Rewritten by the review-5 remediation: the thread rule replaces the address match of the
        review-3 and review-4 remediations. No address, port or name is read in the decision.)*
  - **Why the property holds,** for every thread the collector may pick (argument rewritten by the
    review-5 remediation to the thread rule the cycle-5 triage ruled; item 1(c) extends the ruling,
    see below):
    1. **The server side.** `_server_side` maps a thread identifier to a count of marks; a thread
       holding a mark is on the server side. Marks are placed in three ways:
       - (a) **Threads a server starts.** The accept loop, each connection handler, the stop thread
         and the decref sender are `_ServerThread`s. `_ServerThread._bootstrap` marks the thread as
         its first statement and unmarks it as its last. The mark precedes the `threading` start-up
         code (`_bootstrap_inner`), and that code is what releases the thread waiting in
         `Thread.start()`. So a server thread is marked before anything can wait on it, and before
         it allocates an object the collector tracks.
       - (b) **Start and stop.** A thread is marked while it is inside a server's start
         (`_serve_in_process`, from just before the bind until the accept loop's `Thread.start()`
         returns) or stop (`_InProcessServer.stop`, its whole body, so all the time it holds
         `_lock`). Marks nest, so the stop thread, already marked by (a), keeps its mark after
         `stop()` returns.
       - (c) **A TLS handshake with a queue server** (beyond the ruling). A thread is marked inside
         `secure_client`, from before its TCP connect until its TLS handshake returns.
    2. **What a server-side wait can depend on.**
       - The accept loop waits on three things. The first is the TLS handshake of the connection it
         has just accepted: a dialing thread of this process, marked by (c), or another process.
         The second is `_lock`, which only the accept loop, handlers and `stop()` take, all marked
         by (a) or (b). The third is a new handler's `Thread.start()`, and that handler is marked by
         (a) from its first statement.
       - The starting thread, between the bind and the accept loop, waits only on the accept loop's
         `Thread.start()` (marked by (a)).
       - `stop()` holds `_lock` and waits on no connection. Handlers wait on their own peers.
    3. **A finalizer on the server side never connects.** It only puts onto a `SimpleQueue`, which
       never blocks and is documented safe to re-enter from a finalizer. It opens no connection,
       takes no lock and starts no thread. So no wait in item 2 can depend, through a finalizer, on
       the thread being waited on: C5's cycle cannot form. That holds whatever address, resolution,
       round-robin answer or TLS mode the client used, so C9 cannot occur.
    4. **Elsewhere the stock decref is safe.** No wait in item 2 is on a thread off the server side.
       Such a thread dials a server whose accept loop is not waiting on it, so its decref completes
       as any client's does.
    5. **The sender** is marked by (a), so a finalizer on it hands off to itself. It waits on accept
       loops, but no server-side thread waits on it, and it holds no lock while it connects. A peer
       that stalls a TLS handshake for other reasons still holds the accept loop, and so the sender,
       as on base (cycle 3 observation, out of scope).
    6. **No hand-off is stranded.**
       - `_serve_in_process` starts the sender (once per process; again if it is not alive) before
         it marks anything, in an ordinary call, never in a finalizer.
       - The finalizer hands off only while a sender is set, and the sender loops for the life of
         the process.
       - So whenever a decref is put on the queue, a sender is alive to send it.
    7. **Fork.** `os.register_at_fork(after_in_child=_forget_after_fork)` gives a forked child no
       marks, a new empty queue, no sender and a new lock. The parent's waiting decrefs are never
       sent from the child. Until the child's own first server starts a sender, every decref it
       finalizes is the stock one. None of the parent's server threads run in the child, so a
       finalizer there cannot be one of the threads a parent accept loop waits on.
    8. **Remote clients.** A process that serves no queues has no sender, so the finalizer is the
       stock decref in the same thread with the same arguments. `QueueClient.connect` records
       nothing. In a serving process, a decref for another process's server is the stock one off
       the server side; on the server side it goes to the sender, which delivers it there (C8,
       re-read by the cycle-5 triage). Wire, auth and TLS are untouched, and `b819de6` clients never
       run this code.
  - **Why (c), beyond the ruling.** The ruled rule, (a) and (b) alone, leaves one wait of the accept
    loop on a thread off the server side: the TLS handshake, which the accept loop runs inline.
    - **Failure.** A same-process TLS client thread may run a collection inside its own
      `secure_client`, after its TCP connect and before its handshake ends. The finalizer's stock
      decref then dials the same server. It waits on an accept loop that is itself waiting on that
      thread's handshake.
    - **Evidence (a) and (b) only.** `dropped-client` with TLS stalled in 2 of 14 runs: 3.11 at round
      58 (pytest) and 3.14 at round 73. Stacks: the client thread in `secure_client` →
      `wrap_socket` → (gc) → `_decref` → `BaseProxy._decref` → `secure_client` → `do_handshake`,
      against `serve` → `accept` → `do_handshake`.
    - **Base.** The same stall occurs at `b819de6` across processes, on the forked manager's
      accepter (2 of 8 runs on 3.11). It is a base hazard of TLS clients. The address match of
      cycles 3–4 hid it for same-process clients by handing those decrefs off on every thread.
    - **With (c):** 20 of 20 runs of 200 rounds pass, 5 on each Python. (c) needs a sender, so it
      changes nothing in a process that serves no queues, where the base hazard remains as on
      `b819de6`.
  - **Scope and limits.**
    - **Covered proxies.** Proxies made by `QueueClient` are covered from the moment
      `QueueClient.connect` swaps their finalizer. A proxy left half-made by a connection failure
      inside the stock constructor keeps the stock finalizer (REVIEW cycle 4 P4, seeded as
      `issues/queue-proxy-finalizer-swap-window.md`). A proxy from another manager class pointed at
      an in-process server also keeps the stock finalizer.
    - **The mark of a new server thread** is its first statement. On 3.11 a collection runs only at
      an allocation the collector tracks, and none precedes the mark. From 3.12 a collection runs at
      an eval-breaker check instead. On 3.12 that breaker is shared by all threads, so a collection
      that another thread scheduled can in principle run at the first one or two checks of a new
      server thread, before its mark. That window is not covered by construction there. The
      four-point probe never landed in it (below).
    - **Exit.** Decrefs handed off at interpreter exit may not be sent before the process ends, as
      the server is stopping then too.
    - **Cached connections.** The sender, not the dropping thread, runs the stock decref's tidy-up of
      its own cached connection for a handed-off decref. A thread's cached connection is closed when
      that thread ends or at server shutdown (`_finalize_in_process` drops the cache), as before.
    - **The sender's life.** The process's first server starts the sender, and it lives as long as
      the process. A server start that fails at the bind leaves it running (and nothing of the
      server).
  - [x] **Private internals.** `unsupported_internals()` adds three checks: `BaseProxy._incref`
        sets `_close` to a `util.Finalize` of `BaseProxy._decref`; `BaseProxy._decref` takes the
        token first (3.11 has six parameters, 3.12+ seven, adding `serial`); and `util.Finalize`
        calls `_callback` with `_args`. `test_proxy_finalizer` checks them live. The swap also
        checks that the callback is `BaseProxy._decref` before it replaces it. *(The review-5
        remediation adds a fourth check, on `Thread._bootstrap`; the finalizer no longer reads the
        token.)*
  - [x] `test_threads_drain_after_shutdown` (added on this branch, not a base test) leaves the
        decref sender out of its count: the sender lives as long as the process, so the test
        failed when it was the first in its process to connect a same-process client.
  - [x] `AGENTS.md` (the server process hosts the manager) names the hand-off.
  - [x] **C6:** `ROADMAP.md` entry for `issues/client-partial-connect-hang.md`, after the
        coordinator-resilience entry it may be subsumed by.
  - **Gate record (macOS; the source as provisional commit `23446e0`, since squashed).**
    - **Red at `d3e00f9`** (the new tests against that source, 5 runs per probe, mode and Python, so
      1000 rounds possible each): every failing run failed through its watchdog within 15.3–20.2 s,
      and none hung. `dropped-client` failed 38/40 runs (no TLS 20/20, by round 82–168; TLS 18/20,
      by round 35–108). `same-port-restart` failed 37/40 (no TLS 17/20, TLS 20/20, by round
      1–164). The stacks are C5's: `serve` itself in `_decref` → `answer_challenge` (handler
      start); a handler in `_decref` at its `_lock` release while `serve` waits on `_lock`; and
      the starting thread in `_decref` inside `_serve_in_process`'s `Thread.start`. `test_finalizer_hands_off_only_when_served_here` fails there
      (no `_decref`); `test_dropped_client_decref_reaches_server` passes there and here.
    - **Green:** each of the four probe tests passed 5 runs of 200 rounds on each of 3.11.18,
      3.12.16, 3.13.18 and 3.14.6 (1000 rounds each, 80/80 runs).
    - `test_shutdown_releases_port` (TLS on and off) and `test_join_after_task_done`: **100/100**
      consecutive pytest runs on each of the four.
    - Full suite: **557 passed** on each of 3.11.18, 3.12.16, 3.13.18 and 3.14.6 (549 before, plus
      the 8 new tests); `unsupported_internals() == []` on all four.
    - `parity` vs `b819de6`: identical task tables TLS on and off, rc 0/[0, 0]; the four `--help`
      outputs byte-identical. `compat` (server here, client `b819de6`): 200/200 TLS on and off.
    - `hsx` drive in an isolated site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
      `HYPERSHELL_CONFIG_FILE=''`; not `temp_site.sh`, META F10), 200 tasks TLS on and `--no-tls`:
      rc 0, 200 distinct outputs each, 400 rows, 0 failed.
    - Throughput gate, 5 interleaved repeats, 3.14.6 `spawn`: **PASS**, all 60 runs clean. Load:
      1-min average 2.6 at the start, median 4.4 over the run, peak 17.8 during the 32-client
      config; another agent's test run in a different worktree was sharing the host, and the
      runs are interleaved so both builds shared it.

      | config | base median [min-max] | feature median [min-max] | delta | tolerance | tree children b/f | tree FDs b/f | own threads b/f |
      |---|---|---|---|---|---|---|---|
      | 4x4 b=1 tls | 260.6 [254.3-276.2] | 268.2 [261.5-281.6] | +7.6 | 21.9 | 2/0 | 117/23 | 7/19 |
      | 4x4 b=1 no-tls | 286.8 [279.8-289.9] | 280.8 [273.1-296.8] | -6.0 | 23.7 | 2/0 | 117/23 | 7/19 |
      | 16x1 b=1 tls | 286.8 [283.9-288.9] | 287.4 [286.4-290.7] | +0.6 | 14.3 | 2/0 | 154/60 | 7/55 |
      | 16x1 b=1 no-tls | 285.0 [283.8-288.7] | 286.2 [285.4-288.3] | +1.2 | 14.2 | 2/0 | 153/59 | 7/55 |
      | 4x4 b=50 tls | 938.7 [938.2-939.4] | 940.5 [937.4-944.6] | +1.8 | 46.9 | 2/0 | 117/22 | 7/19 |
      | 32x1 b=1 tls | 285.7 [281.9-290.2] | 283.9 [282.5-289.5] | -1.8 | 14.3 | 2/0 | 201/108 | 7/103 |

    - **C3 re-check** (limit 64, 24 `b819de6` clients, 300 tasks, `--no-tls`, server stopped at
      90 s): 276/300 done; clients 20 rc 5, 4 hung (the ruled-out partial-connect hang); server
      log one refusal WARNING, no `Too many open files`, no `unable to open database file`, no
      uncaught exception or traceback; 0 children. C3(b) holds, as at `d3e00f9` (294/300 there;
      the count varies run to run).
- **Remediation (review cycle 4; triage in [`REVIEW.md`](REVIEW.md)).** P1 was reopened for C7 and
  C8 under the maintainer's one further cycle (5), scoped to this diff. The argument above is
  amended for both.
  - [x] **Red first** (source at `aa55e86`), in `tests/test_queue_gc.py`, each probe in its own
        process group under the existing watchdog and backstop:
        - `test_forked_child_never_resends_decrefs` (C7; skipped where `fork` is unavailable;
          TLS, since the backlog is made by a peer that never starts its TLS handshake, which holds
          up the accept loop and so the sender). A serving process drops a client, so its decrefs
          wait; it forks a child that connects, uses and drops a client; then the peer is cut. After
          the child exits, the parent's live client must still hold all four queues.
        - `test_shared_port_number_leaks_nothing` (C8, TLS on and off): a client of a server in
          another process at `127.0.0.1:P` is dropped while this process serves `P` at another
          address (`127.0.0.2` where the host binds it, as on Linux; else the wildcard address,
          which macOS lets listen beside `127.0.0.1`). The other server must be left holding no
          objects. Skipped where neither address can listen.
        - `test_finalizer_hands_off_only_when_served_here`, rewritten for the full-address rule
          (same address, another port, the same port at another address, the wildcard address, an
          ephemeral bind, an unrecorded host) and the connect-time record.
  - [x] **C8 — full-address match, never stranded.** `_serving` lists `(IPv4 address, port)`;
        `QueueClient.connect` records each dialed address's IPv4 address and whether it is this
        host's (`_note_dialed`); `_served_here` is the match (argument items 1–2).
        `_serve_in_process` starts the sender before it lists its server; `QueueClient.connect` no
        longer starts it (item 6). A decref for an address no in-process server answers on is the
        stock one, on the finalizing thread. `AGENTS.md` corrected to match. *(Superseded by the
        review-5 remediation: the thread rule replaced the address match; the sender's start
        stays.)*
  - [x] **C7 — fork.** `os.register_at_fork(after_in_child=_forget_after_fork)` empties the
        listing and gives the child a new queue, no sender and a new lock (item 7).
  - [x] `test_busy_port_raises_from_start` (added on this branch, not a base test) leaves the
        decref sender out of its thread count, as `test_threads_drain_after_shutdown` does: the
        first server of a process now starts it, before the bind. Its docstring's "nothing is left
        running" now reads "nothing of the server".
  - [x] **Seeds**, each with its `ROADMAP.md` entry: P4 as
        `issues/queue-proxy-finalizer-swap-window.md`; P5 as `issues/queue-decref-handoff-delay.md`
        (a different cause from P4's, so not folded); the observation as
        `issues/queue-stale-decref-same-port-restart.md`. All `unshaped`.
  - **Gate record (macOS; the source as provisional commit `fb8dab4`, since squashed).**
    - **Red at `aa55e86`** (the new test module against that source, on each of 3.11.18, 3.12.16,
      3.13.18 and 3.14.6): the three tests fail on every Python with the reviewer's symptoms.
      Fork: "decrefs waiting at the fork: 3" (4 on 3.11), then "objects after the child exited: 1"
      and `RemoteError` puts from the parent's live client. Shared port: "objects left elsewhere: 4;
      decrefs waiting: 4", TLS on and off. The rule test fails on the missing record.
    - **Green:** `tests/test_queue_gc.py` (10 tests) passed **20/20** consecutive runs on each of
      the four Pythons.
    - Full suite: **560 passed** on each of the four (557 before, plus the two probe tests, one
      parametrised over TLS); `unsupported_internals() == []`.
    - `parity` vs `b819de6`: identical task tables TLS on and off, rc 0/[0, 0]; the four `--help`
      outputs byte-identical. `compat` (server here, client `b819de6`): 200/200 TLS on and off.
    - `hsx` drive in an isolated site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
      `HYPERSHELL_CONFIG_FILE=''`), 200 tasks TLS on and `--no-tls`: rc 0, 200 distinct outputs
      each, 400 rows, 0 failed.
    - Throughput gate, 5 interleaved repeats, 3.14.6 `spawn`: **PASS**, all 60 runs clean. It ran
      alone among this build's processes, but the host was shared with other agents' work: the
      1-min load average, sampled every 30 s (54 samples), was min 2.7, median 11.8, max 44.2,
      against median 3.8–4.4 in the two previous gates. The spreads, and so the tolerances, are
      wider than before; the runs are interleaved, so both builds shared the load.

      | config | base median [min-max] | feature median [min-max] | delta | tolerance | tree children b/f | tree FDs b/f | own threads b/f |
      |---|---|---|---|---|---|---|---|
      | 4x4 b=1 tls | 271.2 [262.7-273.8] | 273.9 [269.1-276.6] | +2.7 | 13.6 | 2/0 | 117/23 | 7/20 |
      | 4x4 b=1 no-tls | 267.8 [255.1-275.6] | 271.1 [261.4-277.0] | +3.3 | 20.5 | 2/0 | 116/24 | 7/20 |
      | 16x1 b=1 tls | 275.7 [180.8-277.6] | 263.9 [183.9-274.4] | -11.8 | 96.8 | 2/0 | 153/59 | 7/56 |
      | 16x1 b=1 no-tls | 197.0 [196.1-270.5] | 237.7 [185.2-263.4] | +40.7 | 78.2 | 2/0 | 153/59 | 7/56 |
      | 4x4 b=50 tls | 830.7 [722.2-874.8] | 745.2 [585.8-860.5] | -85.5 | 274.7 | 2/0 | 116/22 | 7/20 |
      | 32x1 b=1 tls | 255.4 [226.7-276.1] | 243.3 [224.6-265.0] | -12.1 | 49.4 | 2/0 | 201/108 | 7/104 |

      The server's own threads are one more than at `4904c97` (19 → 20 in 4x4): the decref sender,
      which the server now starts. Reported, not gated (cycle-1 P3 ruling).
- **Remediation (review cycle 5; triage in [`REVIEW.md`](REVIEW.md)).** P1 was reopened for C9 under
  the maintainer's redesign ruling, with one further review (cycle 6) scoped to this change. The
  argument above is rewritten to the thread rule; item 1(c) is beyond the ruling (see "Why (c)").
  - [x] **Red first** (source at `43d2d85`), in `tests/test_queue_gc.py`:
        - `test_multi_address_name_never_stalls` (C9; TLS). The forced-collection probe with
          clients dialing a name resolved in-process as a name with two IPv4 addresses is: an
          address to which a connection fails at once, then `127.0.0.1`. `gethostbyname` answers
          the first, as the cycle-5 reviewer simulated. The server binds `127.0.0.1`. Skipped where
          no address fails a connection at once.
        - `test_forced_collection_on_server_side_never_stalls` (TLS on and off). One collection
          is forced, with a dropped same-process client pending, at each of four points in turn:
          the accept loop (`_admits`), a handler (a served `Queue.put`), the thread starting a
          same-port server between its bind and its accept loop, and the stop thread inside
          `stop()` under `_lock`. 10 rounds in the suite, 200 in the gate below.
        - `test_finalizer_hands_off_only_server_side`, which replaces the address-rule test. It
          covers marks and their nesting, a `_ServerThread` marked before `_bootstrap_inner`, the
          TLS handshake mark, and no hand-off without a sender, for tokens of any address.
  - [x] **C9 — thread rule.**
        - `_serving`, `_dialed`, `ANY_ADDRESS`, `_ipv4`, `_is_own_address`, `_note_dialed` and
          `_served_here` are removed, and `QueueClient.connect` records nothing.
        - `_server_side` holds the marks (items 1–3). The accept loop, handlers, the stop thread
          and the sender are `_ServerThread`s. `_serve_in_process` marks its bind and accept-loop
          start, and `stop()` marks its body.
        - `_finalize_in_process` loses its listing key. The sender's start before any mark, and
          C7's `register_at_fork` reset (now of the marks), stay.
  - [x] **TLS handshake mark** (item 1(c), beyond the ruling): `secure_client` marks the thread
        from before its TCP connect until its TLS handshake returns.
  - [x] **Private internals:** a fourth check, `Thread.start` runs `Thread._bootstrap()` (taking
        only `self`) on the new thread, which `_ServerThread` overrides.
  - [x] **C7 test, adapted:** the probe made its waiting decrefs by collecting on the main thread,
        which the address match handed off. It now collects inside a mark. The property tested is
        unchanged.
  - [x] **C8 test** kept unchanged (the cycle-5 triage's re-read).
  - [x] `AGENTS.md`: the hand-off sentence describes the thread rule.
  - [x] **Seeds:**
        - The forked-child observation as `issues/queue-forked-child-keeps-listener.md`
          (`unshaped`), with its `ROADMAP.md` entry and the reviewer's reproduction. It still
          reproduces here.
        - The stale-cached-connection observation folded into
          `issues/queue-stale-decref-same-port-restart.md` as a second symptom (still reproduces;
          `ROADMAP.md` entry amended).
        - `issues/queue-decref-handoff-delay.md` and its entry reworded to the thread rule, with
          their measurements kept as taken under the address match.
  - **Gate record (macOS; the source as committed in this phase's commit).**
    - **Red at `43d2d85`** (the new test module against that source, on each of 3.11.18, 3.12.16,
      3.13.18 and 3.14.10): `test_multi_address_name_never_stalls` fails on every Python in round 0
      at the accept point with C9's stack: `serve` → `_admits` → (gc) → `_decref` (`:598`) →
      `secure_client` → `do_handshake`. The rule test fails (no marks). The plain-address
      forced-collection test passes there, as the address match covers it.
    - **The ruled rule alone ((a) and (b), no (c))**: `dropped-client` with TLS stalled 2 of 14 runs
      (see "Why (c)"). `b819de6` stalls the same way across processes, 2 of 8 runs on 3.11.
    - **Forced-GC four-point probe, ≥ 200 rounds each.**
      - The cycle-5 reviewer's `gc4` probe, unmodified, 200 rounds per point, on each of the four
        Pythons: TLS on and off at `127.0.0.1` (32 runs), and C9's configuration (TLS, a name
        resolved to `0.0.0.1` then `127.0.0.1`; 16 runs). 48/48 pass, 9,600 forced collections.
        Every stock decref ran on the main thread or the sender.
      - This module's forced-collection probe, 200 rounds × 4 points, on each of the four
        Pythons: `127.0.0.1` TLS on and off, and the multi-address name with TLS. 12/12 pass,
        9,600 forced collections. A first 3.13 multi-address run stopped at round 81 with a
        `BrokenPipeError` (no stall): an ephemeral port was reused, and the main thread's cached
        connection under the dialed name reached the old server. That is the second symptom now
        in the same-port restart seed. The probe now drops that cache entry after each point.
    - **`dropped-client` with TLS, natural collection:** 20/20 runs of 200 rounds, 5 on each Python.
    - **`tests/test_queue_gc.py` (13 tests):** 20/20 consecutive runs on each of 3.11.18 and 3.14.10,
      run side by side.
    - **Full suite:** **563 passed** on each of the four (560 before, plus the forced-collection test
      on TLS on and off and the multi-address test); `unsupported_internals() == []` on all four.
    - **`hsx` drive** in an isolated site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
      `HYPERSHELL_CONFIG_FILE=''`), 200 tasks with TLS on and with `--no-tls`: rc 0, 200 distinct
      outputs each, 400 rows, 0 failed.
    - `parity` vs `b819de6`: identical task tables TLS on and off, rc 0/[0, 0]; the four `--help`
      outputs byte-identical. `compat` (server here, client `b819de6`): 200/200 TLS on and off.
      Both ran against the phase commit before it was amended to record them (META F5).
    - **Throughput gate**, 5 interleaved repeats, 3.14.10 `spawn`, frozen venvs from `git archive`:
      **PASS**, all 60 runs clean. It started at a 1-min load average of 1.56, below the bound of 6.
      The load was sampled every 30 s (48 samples): min 1.56, median 5.29, max 17.17. The peaks
      came from other agents' work on the host; the runs are interleaved, so both builds shared
      the load. 4x4 no-TLS is the closest call (−14.1 against a tolerance of 14.2).

      | config | base median [min-max] | feature median [min-max] | delta | tolerance | tree children b/f | tree FDs b/f | own threads b/f | tree RSS MB b/f |
      |---|---|---|---|---|---|---|---|---|
      | 4x4 b=1 tls | 279.5 [269.6-288.6] | 275.6 [272.5-288.5] | -3.9 | 19.0 | 2/0 | 122/26 | 7/20 | 198.7/96.7 |
      | 4x4 b=1 no-tls | 283.0 [278.8-284.4] | 268.9 [268.1-278.9] | -14.1 | 14.2 | 2/0 | 118/25 | 7/20 | 193.1/91.5 |
      | 16x1 b=1 tls | 271.7 [270.7-282.3] | 274.5 [247.9-286.0] | +2.8 | 38.1 | 2/0 | 158/63 | 7/56 | 201.7/99.5 |
      | 16x1 b=1 no-tls | 272.5 [266.4-280.6] | 272.6 [271.1-278.0] | +0.1 | 14.2 | 2/0 | 155/61 | 7/56 | 195.1/94.5 |
      | 4x4 b=50 tls | 925.6 [858.2-927.8] | 925.1 [923.8-926.2] | -0.5 | 69.6 | 2/0 | 121/25 | 7/20 | 203.5/100.9 |
      | 32x1 b=1 tls | 279.9 [276.5-288.6] | 284.7 [279.0-289.9] | +4.8 | 14.0 | 2/0 | 206/111 | 7/104 | 206.4/103.5 |

      The server's own threads are unchanged from `43d2d85` (20 in 4x4): the thread rule adds no
      thread.
- **Verify:** `uv run pytest -v tests/test_queue.py tests/test_queue_gc.py tests/test_queue_start.py
  tests/test_queue_limits.py tests/test_queue_tls.py`, then drive `hsx` with TLS on and with `--no-tls` in a `temp_site` (the
  frontmatter command).
- **Touches:** `src/hypershell/core/queue.py`, `src/hypershell/core/tls.py` (docstrings),
  `src/hypershell/core/logging.py` (docstring), `tests/test_queue.py` (new),
  `tests/test_queue_start.py` (new); remediation also `src/hypershell/server.py`
  (`ServerThread.start`) and `AGENTS.md`; review-2 remediation `tests/test_queue_limits.py` (new);
  review-3 remediation `tests/test_queue_gc.py` (new) and `ROADMAP.md`; review-4 remediation
  `tests/test_queue_start.py`, `ROADMAP.md` and three seeds in `issues/`; review-5 remediation
  `tests/test_queue_gc.py`, `AGENTS.md`, `ROADMAP.md`, one new seed and two amended seeds in `issues/`.

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
