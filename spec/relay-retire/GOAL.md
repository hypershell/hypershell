# GOAL — Retire a superseded relay, and wake the main thread for signals

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay-retire
- **kind:** fix
- **appetite:** big  ·  *Ruled medium overall; the vocabulary is small | big, so medium rounds up
  (see Clarifications). Two fixes from two seeds in one cycle and one PR, ahead of the 2.9.0a8
  pre-release: the superseded-relay retirement (primary, a small wire addition on the topology
  reply and a hand-back) and the main-thread signal wake (one shared helper across the long-running
  commands). Neither changes the zone-less founding case.*

## Problem

Two defects surfaced during `relay-topology` (#100). Both leave a long-running process holding on
when it should let go, and both reproduce on `develop` (`fb89670`; no source drift since the seeds
were written at `8403388`).

**A superseded relay strands the tasks it holds once its last client leaves.** When two live
relays advertise the same site (in practice a relay restarted while its old process still runs),
the upstream server lets the newest instance hold the site and assigns every identity of the older
instance a target of 0 (`_arrange`, `src/hypershell/cluster/topology.py:153-155`; `entries`,
`:256-257`). The older relay's pull bound counts only its connected site clients
(`UpstreamPuller.bound`, `src/hypershell/relay/upstream.py:318-327`), so it keeps pulling for a
client that has reached its idle timeout and is still finishing. When that client leaves, the
relay-local server reverts the rows staged for it (`Scheduler.revert_unstaged`,
`src/hypershell/server.py:390-398`), and they sit at the old relay: under its zero entry it
launches nothing (`SiteScaler.reconcile`, `src/hypershell/relay/site.py:323-342`), and the
upstream server never reverts them, because orphan recovery runs only on eviction
(`HeartMonitor.check_clients`, `src/hypershell/server.py:908-915`) and a relay with no clients stays
connected by design. A gated group then waits until the old process exits by walltime or SIGTERM
and is evicted (600 s by default). A drain does not free them either, by reading the code: SIGUSR1
ends pulling, but `RelayThread.wait_site` (`src/hypershell/relay/__init__.py:661-668`) waits for a
backlog that, with no client, never empties. Review cycle 3 of `relay-topology` reproduced it 3 of
3 times (finding RV5): 150 s after the new instance joined, 2 tasks remained, both assigned upstream
to the old instance. The cycle shipped it as a documented known limitation
(`docs/cli/relay.rst:76-80`). A site with one relay is not affected.

**A drain signal can be lost while the main thread waits on its worker.** `hs relay`, `hs client`
and `hs server` start their work in a thread and block the main thread in an untimed
`thread.join()` (`relay/__init__.py:1015`, `client.py:1518`, `server.py:1376` and `:1567`,
`cluster/topology.py:579`). CPython runs a Python-level signal handler only on the main thread;
when another thread takes a process-directed signal, the main thread, asleep in an untimed lock
wait, is not woken, so the handler may never run. On Linux a freshly created or forking thread can
take the signal, and the relay produces exactly that burst as its site starts. A CI run on Python
3.12 (Linux, 2026-11-13) hung in `tests/test_relay_db.py::test_relay_never_opens_postgres_primary`
with "Relay did not exit within 60.0 seconds" after SIGUSR1, and no "Drain requested" line. A
thread inside the relay that sends itself SIGUSR1 with `signal.pthread_kill` hangs it 5 of 5 times;
`os.kill` from the same thread drains every time. The same untimed join is in `hs cluster`
(`cluster/local.py:47`, `cluster/ssh.py:64`, `cluster/remote.py:85`) and `hs submit`
(`submit.py:1009`). Pre-existing since the relay landed (#82), and in `hs client` and `hs server`
before that.

## Outcome / vision

**Retire.** The relay is a client of the upstream server with its own channel per instance. When
another instance registers the same site, the server already warns and records the site for the
newest instance; at that moment it also marks the superseded instance retired in the topology reply
that instance reads after every heartbeat. On reading the mark the old relay stops pulling for
every zone, lets its connected site clients finish what they hold (terminating none), hands back
the tasks no client has started so the upstream server requeues them at once rather than after
eviction, and, once its clients have left, sends its final heartbeat under each identity and exits.
The newest instance holds the site throughout. Every old/new pairing of relay and server behaves as
today where one side does not know the mark. The `docs/cli/relay.rst` known-limitation note goes.

**Wake.** Every long-running command whose main thread waits on a worker wakes periodically, so a
signal handler tripped by whichever thread the kernel chose runs within about a second. SIGTERM,
SIGINT, worker-failure reporting and exit statuses are unchanged.

## Acceptance criteria (the contract)

Stable IDs (`R1`, `R2`, …) that survive squash-merge and anchor traceability. EARS phrasing per
[`.agents/factory/ears.md`](../../.agents/factory/ears.md). This cycle **adds** behaviour: no SHALL
in [`spec/relay-topology/GOAL.md`](../relay-topology/GOAL.md) or
[`spec/relay/GOAL.md`](../relay/GOAL.md) is weakened.

**Superseded relay retirement** (seed `relay-superseded-instance-retire`):

- **R1** — WHEN a relay instance registers a site that a live older instance already advertises,
  the upstream server SHALL mark every identity of the older instance retired in the topology it
  serves that instance; the newest instance SHALL hold the site's entries, and the server SHALL log
  the warning naming both, exactly as `relay-topology` R4 specifies today.
- **R2** — WHEN a relay reads a topology that marks it retired, the relay SHALL make no further
  upstream work request for any zone it serves, and SHALL log once that it has been superseded at
  its site.
- **R3** — WHILE retired, the relay SHALL launch no site client and SHALL terminate none; each
  connected site client SHALL finish the tasks it has started and their results SHALL reach the
  upstream server as today.
- **R4** — WHEN retired, the relay SHALL hand back to the upstream server the tasks it holds that
  no site client has started, and the upstream server SHALL make them available to other clients
  without waiting for the relay's eviction.
- **R5** — IF a site client has started a task, THEN that task SHALL NOT be handed back; it SHALL
  run once at that client and its result SHALL reach the upstream server as today.
- **R6** — WHEN a retired relay has no site client left and holds no task, the relay SHALL send its
  final heartbeat under every identity and exit with status 0.
- **R7** — In the RV5 scenario (two live relays at one site, the older one pulling for a client
  past its idle timeout when the newer one registers), every task SHALL complete, the older relay
  SHALL exit with status 0, and the upstream server SHALL evict none of the older relay's
  identities; the tasks the older relay held unstarted SHALL be available upstream within three of
  its heartbeat intervals of its first read of the retired mark.
- **R8** — IF one side of a relay–server pairing does not know the retired mark (a relay of an
  earlier release against this server, or this relay against a server that never sends the mark),
  THEN that pairing SHALL behave exactly as it does today, and the mark SHALL NOT make an older
  relay's topology read fail.
- **R9** — A site advertised by one relay, a relay against a server not autoscaling with relay
  sites, and `hs cluster --autoscaling` with no relays SHALL behave exactly as today; a relay that
  is not marked retired SHALL remain connected at zero site clients per `relay-topology` R5.
- **R10** — The known-limitation note in `docs/cli/relay.rst` SHALL be removed, and the relay docs
  SHALL say what a superseded relay does, in the same commit as the change.

**Main-thread signal wake** (seed `main-thread-signal-wake`):

- **R11** — WHEN SIGUSR1 or SIGUSR2 reaches an `hs relay` process, whichever of its threads takes
  it, the relay SHALL log "Drain requested" within 2 seconds and then drain and exit as it does
  today.
- **R12** — WHEN SIGUSR1, SIGUSR2 or SIGHUP reaches an `hs client`, `hs server` (with or without
  relay sites), `hs cluster` (local, `--ssh`, `--remote` or `--autoscaling`) or `hs submit`
  process, whichever of its threads takes it, the command SHALL act on it within 2 seconds as it
  does today when the main thread takes it.
- **R13** — WHEN SIGTERM or SIGINT reaches any command in R11–R12, the command SHALL end as it does
  today, with the same exit status.
- **R14** — IF a command's worker thread fails, THEN the command SHALL raise that failure with the
  same log line and exit status as today; with no signal, no command in R11–R12 SHALL change its
  output or exit status.
- **R15** — A test SHALL fail on `develop` and pass with the fix, deterministically on Linux and
  macOS, for each of `hs relay`, `hs client` and `hs server`: a thread inside the process sends
  SIGUSR1 to its own thread with `signal.pthread_kill`, and the test asserts the process exits
  successfully within the test's deadline.

## Non-goals (no-gos)

- **No 2.8-line backport.** `hs client` and `hs server` on the 2.8 line share the untimed join; a
  backport would be a separate hotfix against `master`, the maintainer's call later.
- **Which instance holds a duplicated site is unchanged.** The newest instance wins, as
  `relay-topology` R4 rules; this cycle adds only what the older one does.
- **No retirement for identities without an instance key.** Those come from a relay that cannot
  read the mark and keep today's per-zone rule (`Duplicate.relay`,
  `src/hypershell/cluster/topology.py:113-127`).
- **No terminating site clients**, in a retired relay or anywhere else (`relay-topology` R5).
- **No operator-initiated retire** (a command or signal that retires a relay that is not
  superseded), and no change to the relay's drain semantics beyond R11's wake.
- **No change to eviction**, its default period, or orphan recovery.
- **No blocking of signals in worker threads** (`signal.pthread_sigmask`): rejected, since a child
  process inherits the spawning thread's mask, so launched site clients and task processes would
  start with drain and interrupt signals blocked.
- **No SIGTERM / Ctrl-C teardown work** beyond R13's "as today"; that is
  [`issues/process-group-signal-teardown.md`](../../issues/process-group-signal-teardown.md).
- **Not the other relay seeds**: [`issues/relay-abort-races.md`](../../issues/relay-abort-races.md)
  and [`issues/relay-hardening.md`](../../issues/relay-hardening.md) (C5-1, which also lives in the
  puller's gate) stay out.

## Clarifications

- **Q:** One cycle for both seeds? — **A:** Yes: one cycle and one PR that fixes both, before the
  2.9.0a8 release. The maintainer asked in session on 2026-11-13 to "seed the issue and then drive
  another cycle to remediate this finding immediately with a new PR", then "let's get the CI fix
  you mentioned about joining threads in this as well". The size circuit-breaker (15 R-IDs, two
  deliverables) is therefore noted and not offered as a split. Both seeds are marked
  `adopted:relay-retire`, as `writeback-scoped-conditional` did for the seed it subsumed (resolved
  2026-11-13).
- **Q:** What does the server tell a superseded instance, and what does that instance do? —
  **A:** The retire design is the maintainer's, from seed 1's Outcome: the server marks a
  superseded instance retired in its topology reply; the old relay stops pulling, lets clients
  finish, hands back unstarted tasks so they are requeued without waiting for eviction, sends its
  final heartbeat and exits. The hand-back mechanism is left to `hs-plan`, with the constraint that
  old/new wire pairings keep today's behaviour (R8) (foreman ruling under the maintainer's mandate,
  2026-11-13).
- **Q:** Which commands does the signal wake cover? — **A:** Every long-running command on
  `develop` whose main thread blocks in an untimed `join()` while relying on Python-level signal
  handlers: `hs relay`, `hs client`, `hs server` (with and without sites), and `hs cluster` (local,
  ssh, remote/autoscaling) and `hs submit` if they share the pattern; the plan may cover them with
  one shared helper. The red-then-green test is required for relay, client and server at least
  (R15). Shaping confirmed `hs cluster` and `hs submit` share the pattern (R12) (foreman ruling
  under the maintainer's mandate, 2026-11-13).
- **Q:** Backport to the 2.8 line? — **A:** No 2.8-line backport in this cycle; it would be a
  separate hotfix, the maintainer's call later. Recorded as a non-goal (foreman ruling under the
  maintainer's mandate, 2026-11-13).
- **Q:** Appetite? — **A:** Medium overall (foreman ruling under the maintainer's mandate,
  2026-11-13). The GOAL vocabulary is small | big and medium rounds up, so the header reads `big`:
  `hs-plan` does its research pass, and the round-up is bookkeeping, not added scope (shaping,
  2026-11-13). The seeds' own frontmatter (`small-to-medium`, `small`) is left as written.
- **Q:** What happens to the RV5 known-limitation note and the `relay-topology` ruling? — **A:**
  The RV5 known-limitation note in `docs/cli/relay.rst` is removed by this cycle (R10), and the
  `relay-topology` GOAL's ruling ("Ruled at review cycle 3 (2026-11-13)") stays as history
  (foreman ruling under the maintainer's mandate, 2026-11-13).
- **Q:** Does a retired relay exiting contradict `relay-topology` R5 ("the relay itself SHALL
  remain connected at zero local clients")? — **A:** No. R5 governs scale-down of a relay that
  holds its site; a retired relay is a new state that exists only in the duplicated-site case, and
  R9 restates R5 for every relay not marked retired. `relay-topology` R4's "clients retiring
  through idle exit, never terminated" holds under R3 (shaping, 2026-11-13; for the maintainer's
  sign-off at this gate).
- **Q:** What bound does R7 set? — **A:** Two observable halves. Binary: no eviction of the older
  relay's identities, every task complete, the older relay exits 0. Timed: its unstarted tasks are
  available upstream within three of its heartbeat intervals of its first read of the retired mark,
  the seed's "a few heartbeats" made concrete (shaper's proposal, 2026-11-13; for the maintainer's
  sign-off at this gate).
- **Q:** May a retired relay keep feeding its local backlog to the clients still connected, or must
  it hand all of it back? — **A:** Left to `hs-plan`: the seed records that either answer meets the
  outcome, and R4, R5 and R7 bind whichever is chosen (shaping, 2026-11-13).
- **Q:** The wake interval? — **A:** Left to `hs-plan`, held to R11–R12's 2-second bound; the seed
  notes one second matches `STOP_CHECK_INTERVAL` (`src/hypershell/relay/__init__.py:120`)
  (shaping, 2026-11-13).

## Related materials

- Seeds: [`issues/relay-superseded-instance-retire.md`](../../issues/relay-superseded-instance-retire.md)
  (primary) and [`issues/main-thread-signal-wake.md`](../../issues/main-thread-signal-wake.md), both
  `status: adopted:relay-retire`.
- Found in: [`spec/relay-topology/`](../relay-topology/GOAL.md) (#100): `REVIEW.md` finding RV5
  and its evidence row for R4; GOAL Clarifications, "Ruled at review cycle 3 (2026-11-13)"; R4 and
  R5, which this cycle extends. Built on [`spec/relay/`](../relay/GOAL.md) (#82).
- CI evidence: `tests/test_relay_db.py::test_relay_never_opens_postgres_primary[postgres]`
  (the drain at `:126-129`, `DEADLINE` at `:47`), Python 3.12 on Linux, 2026-11-13.
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/cluster/topology.py`
  (`_arrange`, `entries`, `warn_duplicates`, `Duplicate`, `serve_with_sites`);
  `src/hypershell/core/topology.py` (`reply_text`, `read_topology`);
  `src/hypershell/relay/upstream.py` (`UpstreamPuller` bound and gate, `UpstreamHeartbeat.fetch`);
  `src/hypershell/relay/__init__.py` (`wait_site`, `finish_upstream`, drain and terminate handlers,
  `RelayApp.run`); `src/hypershell/relay/site.py` (`SiteScaler.reconcile`);
  `src/hypershell/server.py` (`HeartMonitor.update_client` and `check_clients`,
  `Scheduler.revert_unstaged`, `serve_from`, `serve_forever`); `src/hypershell/core/dispatch.py`;
  `src/hypershell/core/signal.py`; `src/hypershell/core/thread.py` (`Thread.join`);
  `src/hypershell/client.py` (`run_client`); `src/hypershell/cluster/local.py`, `ssh.py`,
  `remote.py`; `src/hypershell/submit.py`.
- Docs: `docs/cli/relay.rst` (the note at `:76-80`).
- Neighbouring seeds, out of scope: `issues/process-group-signal-teardown.md`,
  `issues/relay-abort-races.md`, `issues/relay-hardening.md`.
