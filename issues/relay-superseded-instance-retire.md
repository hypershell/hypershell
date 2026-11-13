---
status: unshaped
kind: fix
appetite: small-to-medium
lane: public
---

# A superseded relay strands the tasks it holds once its last client leaves

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

When two live relays advertise the same site, in practice a relay restarted while its old process
still runs, the upstream server lets the newest instance hold the site and assigns every identity
of the older instance a target of 0. The older relay can still end up holding tasks that nothing
will run while it lives. On `develop` (`8403388`):

- **The older instance is assigned zero, whatever it holds.** `_arrange` takes the instance whose
  first identity registered most recently as the site's holder
  (`src/hypershell/cluster/topology.py:153-155`), and `entries` gives every other identity at the
  site `Entry(zone, 0, clients)` (`:256-257`). `warn_duplicates` logs the pair once (`:414-424`).
- **Its pull bound counts connected clients only.** `UpstreamPuller.bound`
  (`src/hypershell/relay/upstream.py:318-327`) is the zone's connected site clients times the
  local bundle size, twice over, and ignores the zone's assignment. A client that has reached its
  idle timeout stays connected while it finishes what it holds, so the superseded relay keeps
  pulling work upstream for it (the gate, `:350-362`).
- **When that client leaves, the pulled tasks sit at the old relay with no client.** The
  relay-local server reverts the rows staged for the client
  (`Scheduler.revert_unstaged`, `src/hypershell/server.py:390-398`: "Reverted 1 staged tasks (zone
  has no client)"), and they stay in the relay's database. Under its 0 entry the relay launches
  nothing (`SiteScaler.reconcile`, `src/hypershell/relay/site.py:323-342`).
- **The upstream server does not put them back.** They stay assigned to the old relay's live
  identity, and orphan recovery runs only when an identity is evicted (`HeartMonitor.check_clients`,
  `src/hypershell/server.py:908-915`). A relay with no clients stays connected by design (it "stays
  connected, ready to grow", `docs/cli/relay.rst:72-73`; "a relay with no site client in any zone
  never asks at all", `src/hypershell/relay/upstream.py:224`), so it keeps beating and is never
  evicted.

If those tasks are in the active group, the group cannot finish and every later group stays gated
until the old process exits by walltime or SIGTERM. It then leaves its identities to eviction
rather than finishing upstream, because it still holds tasks (`RelayThread.finish_upstream`,
`src/hypershell/relay/__init__.py:748-752`), and eviction (600 s by default) reverts them.

A drain does not free them either, by reading the code, not tested: SIGUSR1 ends pulling at the
gate (`src/hypershell/relay/upstream.py:342-344`), but `RelayThread.wait_site` then waits for the
relay's backlog to empty or scheduling to end (`src/hypershell/relay/__init__.py:661-668`), and with
no client the backlog never empties.

The review reproduced it 3 of 3 times on the branch (12 × `sleep 12`, relay A at `max = 3` with a
3 s idle timeout, then A2 joining the site): 150 s after A2 joined, 2 tasks were still remaining,
both assigned upstream to A's identity, while A2 made 13 launches against work it could not reach.

## Why it was deferred

Found at review cycle 3 of `relay-topology` (#100), finding RV5, after the bounded review loop was
spent. The maintainer ruled it a known limitation of the duplicated-site case for that cycle
(`spec/relay-topology/GOAL.md`, Clarifications, "Ruled at review cycle 3 (2026-11-13)"), and #100
ships it documented as such in `docs/cli/relay.rst:76-80`. The puller's bound is relay-cycle code
(#82); the zero assignment and the zero-client fleet under a topology came with #100. A site with
one relay is not affected.

## Outcome / vision

The relay is a client of the upstream server, with its own channel per instance. When another
instance registers the same site, the server already warns and records the site for the newest
instance; at that moment it should also tell the old instance to retire.

The channel is the topology read. The old relay reads its own entries after every heartbeat, even
with no clients (`UpstreamHeartbeat.fetch`, `src/hypershell/relay/upstream.py:850-894`). The
`shutdown` flag in work replies cannot carry it: it is server-wide (`_reply`,
`src/hypershell/core/dispatch.py:143-146`, set once the server finishes, `:344`), and a clientless
relay makes no work requests to hear it. So the server marks the superseded instance's entries in
the topology reply as retired (`reply_text`, `src/hypershell/core/topology.py:227-241`).

On reading that mark the old relay:

- stops pulling at once, for every zone;
- lets its connected site clients finish what they hold, and terminates none;
- hands the tasks no client has started back upstream, so the upstream server requeues them at
  once rather than after the eviction period;
- exits cleanly once its clients have left, with a final heartbeat under each identity.

The newest instance keeps the site throughout. An older relay that does not know the mark, or an
older server that never sends it, behaves exactly as today. The zone-less founding case (`hs
cluster --autoscaling` with no relays) is untouched. The `docs/cli/relay.rst` limitation note goes.

## Sketch of the acceptance criteria

- **R1** — When a relay instance registers a site that a live older instance already advertises,
  the upstream server shall mark every identity of the older instance as retired in the topology
  it serves that instance, and the newest instance shall hold the site's entries as today.
- **R2** — When a relay reads a topology that marks it retired, the relay shall make no further
  upstream work request for any zone, and shall log once that it has been superseded at its site.
- **R3** — While retired, the relay shall launch no site client and shall terminate none; each
  connected site client shall finish the tasks it holds and return their results upstream.
- **R4** — When retired, the relay shall hand back to the upstream server every task it holds that
  no site client has started, and the upstream server shall make those tasks available to other
  clients without waiting for the relay's eviction.
- **R5** — A task a site client has started shall never be handed back; it runs once, and its
  result reaches the upstream server as today.
- **R6** — When a retired relay has no site client left and holds no task, it shall send its final
  heartbeat under every identity and exit with status 0.
- **R7** — In the RV5 scenario (two live relays at one site, the older one pulling for a client
  past its idle timeout), every task shall complete within a bound well inside the upstream
  server's eviction period, while the older relay is still alive when it is superseded.
- **R8** — A relay that does not read the retired mark shall behave against a new server as it
  does today, and a new relay against a server that never sends the mark shall behave as it does
  today; the mark shall not make an older relay's topology read fail.
- **R9** — A site advertised by one relay, a relay against a server not autoscaling with relay
  sites, and `hs cluster --autoscaling` with no relays shall behave exactly as today.
- **R10** — The known-limitation note in `docs/cli/relay.rst` shall be removed, and the relay docs
  shall say what a superseded relay does.

## Notes

- **The maintainer's design** (2026-11-13, in session): the relay is a client of the upstream
  server with its own per-instance channel. When another instance registers the same site the
  server already warns and re-records the site for the newest instance, and at that moment it
  should send the old instance a retire signal.
- **Compatibility, by reading the code.** `read_topology` takes only `version`, `zones` and
  `checks`, and each entry's `target` and `seen` (`src/hypershell/core/topology.py:262-273`), so an
  added field is ignored by a relay of this release. Whether the mark is a field on each entry or a
  top-level one is for `/hs-plan`.
- **The hand-back mechanism is open for shaping.** Today a relay hands nothing back: it either
  sends its final heartbeat holding nothing, or leaves its identities to eviction
  (`src/hypershell/relay/__init__.py:748-755`), and a final heartbeat does not revert anything the
  identity held (`HeartMonitor.update_client`, `src/hypershell/server.py:796-806`). The outcome may
  need a small protocol addition, for example a message that names the returned task ids under the
  identity, conditional on their still being assigned to it. Any such addition is held to R8: an
  older relay never sends it and still works as today, and a new relay falls back to today's
  behaviour against a server without it.
- Which identities are superseded is the instance-keyed case only (`Duplicate.relay`,
  `src/hypershell/cluster/topology.py:113-127`). Identities that advertise no instance key come
  from a relay that cannot read the mark, and keep today's per-zone rule.
- Open question for shaping: whether a retired relay should also stop handing its remaining local
  backlog to the clients still connected (so they idle out sooner), or let them take it. The
  outcome above hands back only what no client has started; either answer meets it.
- Open question for shaping: the R7 bound. The review's harness used a 120 s eviction period; a
  bound of a few heartbeats is the intent.
- Related: [`relay-abort-races.md`](relay-abort-races.md) (the conditional revert under a relay's
  own server) and [`relay-hardening.md`](relay-hardening.md) (idle zones ended early, C5-1, which
  also lives in the puller's gate).
- Related: [`main-thread-signal-wake.md`](main-thread-signal-wake.md), on the drain signal itself.
- Found by: `relay-topology` review cycle 3, finding RV5 (`spec/relay-topology/REVIEW.md`), with
  its evidence matrix row for R4 marked ❌.
