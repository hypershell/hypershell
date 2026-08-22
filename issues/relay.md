---
status: shaped
kind: feature
appetite: big
lane: public
shaped-as: relay
---

# HyperShell Relay: two-level scheduling across administrative sites

> **Shaped candidate — not yet a contract.** The shaping conversation already happened with a
> human: the clarifications below are dated and resolved, the appetite and non-goals are agreed,
> and the R-IDs are drafted. What has *not* happened is acceptance into a cycle — `/hs-feature`
> does that, creating the branch and `spec/{slug}/GOAL.md`, and it is `hs-review` that grades the
> result. Until then nothing here is graded and nothing here is locked.
>
> Promotion should re-confirm the scope against current `develop` and cite anything that has
> drifted since this was written, then adopt it largely as written — do **not** re-litigate the
> resolved clarifications.

- **slug:** relay
- **kind:** feature
- **appetite:** big  ·  *Builds directly on the `zone` feature (demand-driven targeted dispatch,
  per-client bundle size, zone-aware autoscaling) — sequence strictly after it. **The delegation
  core (R1–R10) is independently shippable against any plain `--forever` server** and should be
  its own phase; site-topology autoscaling (R11–R16) rides the zones autoscaler rails and lands
  second.*

## Problem

HyperShell's single shared hand-off tops out on two axes that have nothing to do with task
throughput. First, **connection fan-out**: the server gracefully handles on the order of ~1,000
connected client sockets, but leadership-class systems at the DOE labs put ~10,000 nodes on the
floor — we simply cannot register every node as a first-class client. Second, **administrative
boundaries**: one elastic fleet cannot span centers. A campaign that could draw on Purdue, ACCESS,
and DOE capacity today needs a separate cluster per center; there is no way to prefer the center
with cheaper service units (SU), and if the center hosting the fleet goes down, the campaign goes
down with it — even though the database and plenty of remote capacity survive.

The `zone` feature deliberately stopped at single-instance, in-one-server routing: its non-goals
call out the multi-server / one-cluster-per-zone topology and the lifecycle re-scoping it would
demand (`revert_interrupted`, termination, `latest_server` all assume a single DB owner). That
exclusion needs a sanctioned shape — otherwise users improvise multi-server-shared-database hacks
the lifecycle machinery was never built to survive.

## Outcome / vision

A new long-running service, **`hs relay`**, runs at each site (started by the operator — a login
node, a service host, `systemd`; the true server launches nothing across the boundary). Upstream,
the relay is *just a client*: it connects with the upstream auth key and pinned TLS, advertises
its zone(s) and its own (large) bundle size, pulls targeted bundles through the zones dispatch
protocol, confirms, and heartbeats — the upstream dispatch surface does not know relays exist.
Inside, it is a complete little cluster: pulled tasks are inserted into a relay-local SQLite
database from which an embedded server schedules to site clients (with their own zones and bundle
sizes) launched through the site's own launcher shim. Completed tasks flow back upstream
attributed to the node that actually ran them, while in-flight tasks stay attributed to the relay
— so the existing eviction and orphan-recovery machinery *is* the failover story: a site that
dies has its unreturned tasks reverted and re-dispatched to surviving sites.

Under autoscaling, the true server stops launching processes and instead publishes a **desired
topology**: per-site client counts computed from the same group- and zone-scoped pressure signal
zones built, distributed by a **`fill`** policy (saturate sites in declared preference order —
the SU-cost mode) or **`spread`** (distribute across live sites), under per-site `min`/`max`/
`init`. Each relay watches the topology and reconciles only its own entry with its own launcher;
scale-down remains idle-exit; a relay idles at zero clients, still connected, ready to grow.

The arithmetic this buys: ~100 relays × ~100 nodes ≈ 10,000 nodes behind ~100 upstream sockets.
To be explicit about what it does *not* buy: every task still schedules, confirms, and completes
through the true server's database, so the net task-throughput ceiling (~10k tasks/s, less with
PostgreSQL) is unchanged — the relay multiplies fan-out, not row throughput; suitably-sized tasks
remain the operating assumption. Security *improves* at scale: the upstream key and cert material
stop at the relay; leaf nodes only ever hold a per-site key minted fresh by their relay.

A user who never runs `hs relay` sees no change whatsoever.

## Acceptance criteria (the contract)

**Delegation core** *(usable against any plain `--forever` server; own phase)*

- **R1** — The system SHALL provide `hs relay`, a long-running per-site service composing three
  roles in one process: an upstream connector (client role), a complete local server instance
  scheduling from a relay-local database, and a local fleet launch/reconcile loop driving the
  site's own launcher command template.
- **R2** — The relay SHALL speak the ordinary client protocol upstream — advertise identity, its
  configured zone(s), and its own bundle size; receive targeted bundles; confirm; heartbeat —
  such that the upstream dispatch surface requires **no relay-specific changes**. A relay serving
  multiple zones requests work per zone.
- **R3** — WHEN a bundle is received, the relay SHALL insert its tasks into the relay-local
  database — preserving task ids, group, zone, tags, and the true server's scheduling stamps —
  and SHALL confirm receipt upstream only after the insert is durable. Upstream in-flight
  attribution (rows stamped with the relay's client id at confirmation) is thereby retained until
  results return, so existing eviction + `revert_orphaned` deliver site failover unchanged.
- **R4** — The relay-local server SHALL schedule inserted tasks to site clients with standard
  mechanics (targeted zone dispatch, per-client bundle size) and SHALL NOT retry locally
  (`max_retries = 0`): the true server remains sole owner of retry policy and the
  `previous_id`/`next_id` chain. Failures return upstream as failures.
- **R5** — The relay SHALL return completed tasks upstream in bundles (bundlesize/bundlewait
  semantics mirroring the client collector), prune returned rows from the local database, and
  tolerate crash-replay: a duplicate return SHALL be harmless (idempotent whole-row update by
  task id).
- **R6** — Returned task records SHALL reflect the executing site client (`client_id`,
  `client_host`, `command`, timing, `exit_status`, output paths, resource fields) while
  preserving the true server's scheduling provenance (`server_id`, `server_host`,
  `schedule_time` as received in the bundle); relay-local scheduling stamps SHALL NOT leak
  upstream — `latest_server()`, `count_connected()`, and the rate/pressure math must remain
  correct with relays in play.
- **R7** — The relay SHALL forward its local client roster upstream — registrations and
  disconnections, attributed to the relay's site — so leaf clients exist in the true server's
  `client` table and `effective_rate_by_client` / `task_pressure` / client-facing presentation
  remain coherent.
- **R8** — The relay SHALL pull demand-driven with a bounded local backlog derived from its live
  local fleet (on the order of connected clients × local bundle size); WHILE the bound is met,
  the relay SHALL NOT pull further work — a relay never hoards tasks the policy would place
  elsewhere, and the cancellation and duplicate-execution windows stay bounded.
- **R9** — WHILE the upstream server is unreachable, the relay SHALL continue operating its site
  — scheduling already-pulled tasks and collecting completions durably — reconnect with backoff,
  and return buffered results on reconnection; a relay restart SHALL NOT lose
  completed-but-unreturned results.
- **R10** — The relay-local database SHALL be private, disposable operational state at a
  dedicated default location; it SHALL NOT default to (or disturb) the user's configured primary
  database, and pruning (R5) SHALL keep it size-stable across campaigns.

**Site-topology autoscaling** *(second phase; composes with zones autoscaling)*

- **R11** — The autoscale configuration SHALL accept an ordered set of named sites, each with
  `min`/`max`/`init` client counts, and a site-distribution policy: **`fill`** (saturate sites in
  declared order up to `max` before spilling to the next — the SU/cost-preference mode) or
  **`spread`** (distribute demand across live sites, respecting per-site `min`/`max`).
- **R12** — WHILE autoscaling with sites, the true server SHALL NOT launch client processes via a
  local launcher; it SHALL derive total demand from the existing pressure signal (group- and
  zone-scoped, per zones) and publish a **versioned desired topology** — per-site (and, where
  zones are active, per-zone) client counts; each connected relay SHALL observe the current
  topology and reconcile only its own site's entry using its own launcher.
- **R13** — Only **live** sites (relay connected and heartbeating) SHALL receive topology
  assignments; IF a site's relay disconnects or is evicted, THEN the topology SHALL be recomputed
  over the remaining live sites while that site's unreturned tasks revert via existing orphan
  recovery — together constituting graceful site failover.
- **R14** — Relay registration/heartbeat SHALL carry the site identity, so the true server
  accounts fleets and liveness per site and presentation can attribute clients and tasks to
  sites.
- **R15** — Scale-down SHALL remain idle-exit driven: a reduced topology entry means the relay
  stops replacing exiting clients; site clients self-retire when no eligible work remains; the
  relay itself SHALL remain connected at zero local clients (scale-to-zero, ready to regrow).
- **R16** — Topology SHALL be optional: against an upstream that publishes none (a plain
  `--forever` server, or a site not declared in policy), the relay SHALL maintain its own
  locally-configured fleet size — the delegation core (R1–R10) SHALL be fully usable without
  site-topology autoscaling.

**Security & convention**

- **R17** — The relay SHALL authenticate upstream with the upstream key and TLS configuration
  (supporting shared-cert / `cafile` / fingerprint pinning per the distributed-TLS guidance) and
  SHALL mint a fresh site-local auth key for its own fleet; upstream credentials SHALL NOT be
  exposed to site clients (not on their argv, not in their environment).
- **R18** — CLI additions SHALL update the affected `docs/_include/*.rst` help snippets and the
  `share/` completions in the same commit (repo convention), and `docs/security.rst` SHALL gain
  the relay trust model (two auth domains, TLS pinning across the WAN hop, process-table key
  exposure now confined within each site).

## Non-goals (no-gos)

- **Raising the throughput ceiling.** Every task still schedules, confirms, and completes through
  the true database; the relay scales *connection fan-out*, not tasks-per-second. Say so in docs.
- **Cross-site cancellation propagation.** `hs update --cancel` cannot reach tasks already inserted
  at a site — today's in-flight-bundle semantics with a deeper (but R8-bounded) buffer. Documented
  limitation.
- **Cross-site captured-output retrieval.** `outpath`/`errpath`/`csvpath` name files on site
  filesystems; the `hs task info` SFTP retrieval path is not extended across sites.
- **Relay-of-relay.** Two levels only; no deeper hierarchy.
- **Exactly-once execution.** At-least-once is retained (eviction/revert plus zombie returns
  resolve last-writer-wins, exactly as today).
- **Template forwarding.** The execution template stays site-local client configuration (the
  relay operator sets it, exactly like a standalone `hs client -t`); campaign-specific templating
  across relays is already served by JSON mode (submit-side expansion, clients run verbatim).
  Forwarding templates through the topology channel is a possible follow-up, not this.
- **Per-zone differentiated sizing knobs** (unchanged zones non-goal). Per-**site**
  `min`/`max`/`init` is the only differentiated sizing introduced here.
- **An implicit "local site."** The true server keeps no built-in launcher in relay topologies;
  local capacity = run a relay beside the server. One code path, one topology.
- **Changing the auth/TLS wire model** (pickle-framed RPC, HMAC challenge, no mTLS — as is).

## Clarifications

- **Q:** How do leaf clients appear upstream — forward the roster, keep the relay as the only
  registered client, or write leaf ids without `client` rows? — **A:** **Forward the roster**
  (resolved 2026-08-02). Task rows carry the true executing client; the upstream `client` table
  gains those clients attributed to their site, keeping the `effective_rate_by_client` join, the
  pressure math, and observability coherent. Implies a back-compat nullable site-attribution
  column on `client` (schema pattern per the zones `Zone` note).
- **Q:** Who owns retries? — **A:** **True server only** (resolved 2026-08-02). The relay's local
  server runs `max_retries=0`; the upstream retry machinery re-dispatches failures (possibly to a
  different site). Single owner of the id chain; no remapping.
- **Q:** Site model — ordered list vs weights, and does the true server keep an implicit local
  launcher? — **A:** **Ordered sites, no implicit local site** (resolved 2026-08-02). `fill`
  order *is* the declared order (SU preference); weights/cost values are a possible follow-up.
- **Q:** Relay buffer depth? — **A:** **Bounded, demand-driven** (resolved 2026-08-02). The bound
  derives from the live local fleet, not a user knob; hoarding breaks fill/spread fidelity and
  widens the cancellation/duplicate windows. WAN latency is amortized by large bundles.
- **Note (for `hs-plan`):** *topology mechanics.* The managed queues are point-to-point
  (one consumer takes a message) — a broadcast "scaling channel" is wrong-shaped. Model the
  topology as **versioned state relays fetch** (e.g. a managed `_get_topology()` call or a field
  on the zones request-return surface) on their heartbeat cadence; versioning makes application
  idempotent. Final mechanism in `TECH.md`.
- **Note (for `hs-plan`):** *return-path mechanics.* Upstream `Receiver.update_tasks` does
  `Task.update_all(to_dict())` — whole-row overwrite by id. Keep the upstream Receiver unchanged
  by having the relay's returner restore the upstream stamps (retained from the received bundle)
  before the put (R6).
- **Note (for `hs-plan`):** *import-time engine.* `data/core.py` builds the engine/`Session` at
  import from the config singleton; the relay process must bind its **local** SQLite (R10) before
  that import wins, while all upstream interaction stays queue-only. Likely the same env-steering
  approach as `temp_site.sh`; flag as the highest-risk plumbing.
- **Note (for `hs-plan`):** *roster + heartbeat.* Piggyback the roster on the relay's heartbeat
  or a dedicated message — either way `Heartbeat` gains optional fields (site), and JSON
  pack/unpack must tolerate absence for mixed-version fleets.
- **Note (for `hs-plan`):** *`no_confirm` is incompatible with relay mode* — it disables the
  orphan recovery R3/R13 lean on. Refuse it on both the upstream server (when relays register)
  and the relay CLI.
- **Note (for `hs-plan`):** *eviction window.* A relay's unreturned backlog exceeds a leaf
  client's; consider whether upstream `evict_after` deserves a relay-aware default (R8 bounds the
  blast radius either way).
- **Note (for `hs-plan`):** *group consistency is free.* Upstream `increment_group` cannot
  advance while relay-held tasks are incomplete, so a relay-local database only ever holds tasks
  of the current group; local gating never diverges from upstream.
- **Note (for `hs-plan`):** *zones composition.* Topology entries are per-site × per-zone where
  zones are active (composing zones R13–R16 with R11–R12 here). A dedicated per-relay zone
  remains a pure configuration pattern (zone name = site name), not a mechanism.

## Related materials

- Dependency: [`zone.md`](zone.md) — R1 (queue-primitive swap), R3/R4 (targeted
  demand-driven dispatch: the relay *is* a zones client upstream), R13–R17 (zone-aware
  autoscaling rails the topology computation extends). This cycle picks up zones' declared
  non-goal ("multi-server / one-cluster-per-zone topology") as its sanctioned shape.
- Source (likely touchpoints, to be confirmed by `hs-plan`): new `src/hypershell/relay.py` (or
  `cluster/relay.py`) — connector, embedded local server composition, returner, reconciler;
  `src/hypershell/__init__.py` (commands dict, `APP_HELP`, `DISTRIBUTED_ROLES`);
  `src/hypershell/cluster/remote.py` (`AutoScaler` → demand-to-topology computation, publication,
  no-launcher site mode); `src/hypershell/server.py` (`HeartMonitor` site liveness; `Confirm` /
  `Receiver` unchanged by design); `src/hypershell/core/heartbeat.py` (optional site field);
  `src/hypershell/data/model.py` (`Client` site attribution, roster classmethods — task-state
  predicates untouched); `src/hypershell/core/config.py` (`[relay]` section; `[autoscale]` site
  table + distribution policy); `src/hypershell/data/core.py` (relay-local DB binding);
  `docs/security.rst`, `docs/_include/*.rst`, `share/` completions.
- Prior art: **glideinWMS** (per-site frontend/factory feeding a central pool), **HTCondor**
  flocking + glideins, **Balsam** multi-site daemons (closest DOE analog — site agents pull from
  a central service and drive the local batch system), Slurm federation, hierarchical
  work-stealing schedulers. The two-level pull architecture is the well-trodden answer to both
  the fan-out ceiling and the administrative-boundary problem.
