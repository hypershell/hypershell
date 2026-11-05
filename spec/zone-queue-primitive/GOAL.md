# GOAL — Serve the managed queues in-process on a threading primitive (zone, cycle 1)

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** zone-queue-primitive
- **kind:** feature
- **appetite:** big  ·  *One seam, `core/queue.py`'s `QueueServer`, plus the first tests of the
  queue primitive and the server-side state machines that use it. Taken from the `zone` seed's
  `big`. The seam is small, but it sits in the untested concurrency core and on the CPython manager
  internals, so `hs-plan` should still do its research pass. This is the first of three cycles that
  together deliver the `zone` seed; it carries the seed's R1 only.*

## Problem

The `zone` seed (per-zone, per-client dispatch) needs a managed-queue primitive whose cost does not
grow with the number of queues. The four managed queues (`scheduled`, `completed`, `heartbeat`,
`confirmed`) are `multiprocessing.JoinableQueue`s. Each one carries an OS pipe and a background
feeder thread. That cost is invisible at four queues but would multiply with per-client or per-zone
queues.

The seed called the swap to a threading `queue.Queue` "near-zero-change", on the premise that
"every access is already a proxy RPC". **That premise is wrong**, and the cycle has to be sized
for the real mechanism. Re-verified on `develop` at `b819de6`:

- `QueueServer.start` (`src/hypershell/core/queue.py` L379–391) creates the four
  `JoinableQueue`s, registers them, and calls `BaseManager.start()` through `SecureManager.start`
  (L281–287). That **starts a separate manager process** to serve them. Its bootstrap
  (`_child_bootstrap`, L243–255) installs the TLS context and closes inherited log-slot locks in
  that child.
- The server's own threads use the **parent's queue handles directly**, not proxies: the
  scheduler's bundle post (`src/hypershell/server.py` L254), `Confirm` (L345–346), the receiver
  (L443–444), the heartbeat monitor (L570–571, sentinel at L644), and shutdown sentinels
  (L898–919). Only remote clients go through proxies (`src/hypershell/client.py` L212–213, and the
  submit and heartbeat paths).
- So the `multiprocessing` pipe and feeder thread are what carry every bundle from the server
  process to the manager process. Swapping in `queue.Queue` behind the same shape was tested on
  Python 3.13 and 3.14. Under `fork` the client never sees the server's put (the `get` times out
  with `Empty`). Under `spawn`, `start()` fails with `cannot pickle '_thread.lock' object`. At
  `b819de6` the unmodified server shows one child process after `start()` under both start
  methods.
- The stock `BaseManager.serve_forever()` cannot simply be run in a thread of the server process
  instead: it ends with `sys.exit(0)` and resets `sys.stdout` (CPython
  `multiprocessing/managers.py`).

The queue swap is therefore a change to *where the queues are served*, not a type substitution.
It lands as its own cycle, ahead of dispatch and zones, because it is the riskiest part of the
seed and everything after it builds on it.

## Outcome / vision

The server process holds the four queues itself as threading `queue.Queue`s. The server's threads
use them directly. Remote clients reach the very same objects through the manager, under the same
registered names, the same wire protocol, the same authentication, and the same TLS behaviour.
There is no separate manager process, no per-queue pipe, and no feeder thread. Nothing a user or a
client can see changes: same CLI, same wire, same results, the same shutdown and sentinel
ordering, and throughput the same or better.

This also prepares the next cycle. Its request callable must run in the server process to reach
per-zone pools, and after this cycle the manager's request handling already runs there. That is
motivation only; nothing in this GOAL depends on it.

## Acceptance criteria (the contract)

- **R1** — WHEN the queue server starts, it SHALL serve the four managed queues from within the
  server process and SHALL NOT create a child process. Observable: the server process's child
  process count (e.g. `multiprocessing.active_children()`) is the same immediately before and
  after `start()`, under both the `fork` and `spawn` start methods.
- **R2** — The four managed queues SHALL be threading `queue.Queue` objects, with no per-queue OS
  pipe and no per-queue feeder thread. They SHALL keep today's bounds (`scheduled` and `completed`
  bounded by the configured queue size; `heartbeat` and `confirmed` unbounded) and today's
  operations: `put` and `get` with a timeout (raising `queue.Full` / `queue.Empty` when it
  expires), and `join` / `task_done`.
- **R3** — An item put by a server thread SHALL be received by a remote client, and an item put by
  a remote client SHALL be received by a server thread, through each of the four queues, both with
  TLS enabled and with `--no-tls`.
- **R4** — The manager wire protocol, the authentication handshake, the registered queue names, and
  TLS behaviour SHALL be unchanged. A client built from the base commit (`b819de6`) SHALL connect
  to the new server and complete a workload, and the existing TLS queue tests SHALL pass
  unmodified.
- **R5** — WHEN the queue server shuts down (normal completion, a cooperative-signal drain, or an
  exception exit), it SHALL stop accepting connections and release its listening port, so a new
  server can bind the same port in the same process. It SHALL return to its caller without exiting
  the process and without replacing `sys.stdout` or `sys.stderr`. The existing shutdown and
  sentinel ordering (`AGENTS.md`, Concurrency model) SHALL be preserved.
- **R6** — New tests tagged `unit` SHALL cover the queue primitive: R1's child-process check;
  R3's round trip on each queue with TLS on and off; `get` on an empty queue and `put` on a full
  bounded queue each raising within their timeout; `join` returning once every item has been
  `task_done`; the sentinel reaching a remote consumer; and R5's clean shutdown and port reuse.
- **R7** — New tests SHALL cover the server-side state machines that touch the managed queues (the
  scheduler's bundle post, `Confirm`, the receiver, and the heartbeat monitor). For each, the tests
  SHALL show that it moves data through its queue, re-enters its state when a queue operation
  times out, and halts on the sentinel.
- **R8** — No behaviour change: the existing test suite SHALL pass with no existing test modified,
  and the CLI help and options SHALL be unchanged. A server and two clients on loopback SHALL run a
  grouped workload that includes a task that fails and is retried, to completion. Every task SHALL
  reach the same final state as on the base commit, no result SHALL be lost, and every process
  SHALL exit cleanly within a bounded time.
- **R9** — Throughput and footprint SHALL not regress. On a single-host benchmark of many short
  tasks, the new server's throughput SHALL be no lower than the base commit's (median of repeated
  runs; a difference within run-to-run spread counts as equal). Its process count and open file
  descriptors SHALL be no higher. Before the pre-release, a multi-node run on a real cluster shows
  unchanged results and throughput against 2.8.3.

## Non-goals (no-gos)

- **Zones, targeted delivery, and per-client bundle sizing and advertising.** The `zone` seed's
  R2–R12, the next cycle.
- **Zone-aware autoscaling.** The `zone` seed's R13–R17, the cycle after that.
- **Any wire, authentication, or TLS change.** Clients must not need to change.
- **Replacing `BaseManager`,** or any rewrite of the queue transport. That is a separate, later
  item.
- **New queues or a request/dispatch surface.** This cycle moves the existing four queues; it adds
  none.

## Clarifications

- **2026-11-01 — Zone ships in three cycles:** Z1 = R1 (this GOAL), Z2 = R2–R12, Z3 = R13–R17
  (Geoffrey, alignment cut). The seed stays a live candidate for Z2 and Z3.
- **2026-11-01 — R1 keeps `BaseManager` by serving it inside the server process with our own
  loop; the forked manager child goes away** (Geoffrey, alignment Q1a). Approved direction, input
  to `hs-plan` rather than a requirement: HyperShell's own serve loop runs around
  `multiprocessing.managers.Server`'s accepter in the server process, in place of
  `serve_forever()`. The four `queue.Queue`s are shared directly by the server threads and served
  to remote clients under the existing registered names.
- **2026-11-01 — Problem 2 / R1 "near-zero-change" wording corrected:** server threads use the
  queues directly across the fork. Verified by experiment (see Problem), and the seed is corrected
  to match.
- **2026-11-01 — Z1 ships in 2.9.0a2.**
- **Note (for `hs-plan`):** two duties of the child's bootstrap need a decision once the child is
  gone. TLS context installation must still happen in the process that serves. Closing inherited
  log-slot locks existed only because of the fork.
- **Note (for `hs-plan`):** a remote client's blocking `get(timeout=…)` now holds a manager
  handler thread in the server process, alongside the scheduler, receiver, and heartbeat threads.
  R9 is the check that this does not cost throughput.
- **2026-11-05 — R8: one existing test rewritten, by foreman ruling (SC26 run).**
  - **Test:** `test_group_parallel_execution_within_group` asserted a wall-clock *floor* (`elapsed > 10 s`). That floor measured the scheduler's backoff between groups, not a stated behaviour. This branch's faster connections (TCP_NODELAY) put Linux CI runs at 9.7–9.8 s.
  - **Change:** the test now asserts what it is named for, from task times in the database: the two tasks in each group overlap, and each group finishes before the next starts. The old test passed with one worker (`-N1`), so it never checked parallelism; the new one fails there.
  - **Scope:** behaviour is unchanged, and no other existing test is modified. The cycle 11 review recorded this.

## Related materials

- Seed: [`issues/zone.md`](../../issues/zone.md) — this cycle carries its R1 only; the seed stays
  `shaped` for Z2 and Z3.
- Source: `src/hypershell/core/queue.py` (`QueueServer`, `SecureManager`, `_child_bootstrap`,
  `QueueClient`); `src/hypershell/server.py` (`Scheduler`, `Confirm`, the receiver, the heartbeat
  monitor, `ServerThread` shutdown); `src/hypershell/client.py` (the client's queue users, unchanged).
- Tests: `tests/test_queue_tls.py` (must pass unmodified); new queue and FSM tests per R6–R7.
- Invariants: `AGENTS.md`, "Concurrency model" (finite timeouts and re-entry, shutdown and sentinel
  ordering) and "Queue transport & security" (auth and TLS must not weaken).
- CPython: `multiprocessing/managers.py` (`BaseManager.start`, `Server.serve_forever`,
  `Server.accepter`).
