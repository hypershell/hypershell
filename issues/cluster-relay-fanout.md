---
status: unshaped
kind: feature
appetite: big
target: unscheduled
lane: public
---

# `hs cluster` could bring up its own relays, to fan out past one server's client connections

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim. Not for 2.9.0: a maybe on
> the roadmap.

## Problem

One server holds every client connection. The queue is built on `multiprocessing.managers`
(`SecureManager`, `src/hypershell/core/queue.py:1002`), which serves each connection on a thread of
its own (`Server.serve_client`, pinned at `core/queue.py:468`); `core/queue.py:546` already manages
the open-file limit. In practice one server tops out near 1,000 connected clients (maintainer
observation, not yet measured in this repository), so an allocation of 10,000 nodes with a client
per node cannot be served by one server directly.

Relays lift that ceiling. Upstream, a relay is one connection per zone it serves; at its site it
runs a server of its own for its clients (`src/hypershell/relay/__init__.py`). One server with 100
relays of 100 clients each reaches 10,000 clients. Today every relay is started by hand:
`hs cluster` (and `hsx`, its alias) launches clients and never relays. A user who wants that tree
inside one allocation has to start the server, split the nodes, start each relay with its own
launcher arguments and keys, and tear it all down again.

## Why it was deferred

Not for 2.9.0. Relays came into the 2.9 alphas to join separate administrative sites (#82), with
autoscaling across relay sites in 2.9.0a8 (#100). Using them for fan-out inside one allocation is a
second use the 2.9 cycle did not scope. This is a gap, not a defect: nothing on `develop` is broken.
Raised by the maintainer at SC26 (2026-11-15), first as a separate `hsxl` command.

## Outcome / vision

One command brings up a server, R relays and their clients inside a single allocation, sized by the
user, and takes the whole tree down when the run ends; `hsx` has it because it is `hs cluster`.

Shaping questions, none agreed:

- **Interface.** A flag on `hs cluster` (for example `--relays N`) in place of a new `hsxl` command.
  `hsx` is an alias for `hs cluster`, so a second entry point would repeat its options. Whether the
  tree should be inserted on its own above some client count is open.
- **Splitting the nodes.** The hard part. Each relay's launcher must target its own slice of the
  allocation, or the relays contend for every node. Relay launcher arguments substitute only
  `{zone}` today (`src/hypershell/relay/site.py:39`). Options: a `{nodelist}` or `{relay}`
  placeholder filled from a split of `SLURM_JOB_NODELIST`, or `hs cluster` writing each relay's
  launcher arguments itself. Zones should not double as relay ids: zones route tasks, and these
  relays exist only to spread connections.
- **Sizing.** Reuse the server's autoscaling with relay sites (#100), where the server publishes
  client targets and each relay launches to match, in place of a second sizing path in
  `hs cluster`.
- **Keys.** A relay takes its upstream key from `[relay.upstream]` only (there is no `-k`), and
  `hs cluster` hands launched clients their key through the environment or ssh standard input
  (#80). Relays launched by `hs cluster` need the same hand-off, and probably a site key per relay.
- **Relay database.** Each relay keeps a database, `<lib>/relay/<host>.db` by default, and it is
  load-bearing: the relay's received assignments and its outbox of results not yet returned upstream
  live there (`src/hypershell/relay/store.py`). It cannot simply be `:memory:`. With R relays in one
  job it belongs on node-local disk, not a shared home directory or Lustre.
- **Losing a relay.** A relay that dies takes its clients' in-flight tasks, and any results still in
  its outbox, with it. The server requeues what a superseded relay hands back
  (`src/hypershell/server.py:446`). What it does for a relay that simply disappears (heartbeat
  eviction of its upstream identity) needs confirming first, and `hs cluster` would need to relaunch
  a dead relay as a relay relaunches a client.
- **The next bottleneck.** With 100 upstream connections in place of 10,000, the upstream database
  becomes the limit, since every task's state still lands there. Measure on PostgreSQL or
  TimescaleDB; SQLite is unlikely to hold at this scale. The end-of-run tail
  (`issues/relay-shutdown-tail.md`) adds up across relays.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN `hs cluster` is given a relay count R and a launcher, it SHALL start one server,
  R relays and their clients within the allocation, and SHALL stop all of them when the run ends.
- **R2** — WHEN `hs cluster` launches relays, each relay's clients SHALL run only on the nodes
  assigned to that relay.
- **R3** — WHEN `hs cluster` launches a relay, no key SHALL appear on any command line.
- **R4** — WHEN a relay launched by `hs cluster` exits before the run ends, its unfinished tasks
  SHALL be requeued upstream and the relay SHALL be launched again.

## Notes

- Related: `issues/relay-shutdown-tail.md`, `issues/relay-test-strength.md`; `hs relay` (#82),
  autoscaling per zone (#99) and across relay sites (#100), key hand-off (#80).
- Found by: out-of-cycle; maintainer request, SC26, 2026-11-15.
