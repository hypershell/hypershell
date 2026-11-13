# GOAL — Relay site-topology autoscaling: fill and spread across sites

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay-topology
- **kind:** feature
- **appetite:** big  ·  *Taken from the shaped seed. It rides the zone autoscaler's rails (per-zone,
  group-scoped pressure and the demand / actuation seam, Z3, #99) and the relay delegation core
  (#82), and sequences strictly after both, which have now landed. It adds a publishing actuator
  on the server, a reconciler in the relay, and a new upstream advertisement, so `hs-plan` should
  do its research pass. Z3 ships in the same pre-release as this cycle
  (`spec/zone-autoscale/GOAL.md`, appetite note).*

## Problem

The relay cycle gives each site an `hs relay` that pulls work, runs a locally configured fleet
size, and fails over through eviction and orphan recovery. What it does not give is a **campaign
view of capacity across sites**. Each relay sizes its own fleet: a fixed fleet of `size` clients per
zone, relaunched as they exit (`src/hypershell/relay/site.py:104-111`, `:147-153`). The true server,
which holds the pressure signal, cannot say "fill the cheap site first, spill to the next", "spread
across what is live", or "regrow at the surviving site when one goes down". A campaign that should
prefer the center with cheaper service units (SU) has no way to express it, and site failover moves
the tasks but not the capacity.

Zone autoscaling (Z3) now computes per-zone demand from group- and zone-scoped pressure, separately
from launching (`src/hypershell/cluster/demand.py:95`, `:141`, `:158`; `LaunchActuator`,
`src/hypershell/cluster/launch.py:228-324`). But it only ever acts by starting local processes
through a launcher, and autoscaling is reachable only as `hs cluster --autoscaling` with a launcher
(`src/hypershell/cluster/__init__.py:114`). A server that cannot launch anything itself — one run
off-cluster, on Kubernetes for example — has no way to turn its demand into capacity at the sites
whose relays it is already talking to.

## Outcome / vision

Under autoscaling with sites, the true server stops launching processes and instead publishes a
**desired topology**: per-site, per-zone client counts computed from the same group- and
zone-scoped pressure signal Z3 builds. The server may run off-cluster with no launcher at all. When
a zone needs another client, the server's autoscaler picks the next site for that zone by its
distribution policy — **`spread`** (distribute across live sites, the default) or **`fill`**
(saturate sites in preference order, the SU-cost mode) — and only among sites whose relay serves
that zone. Any relay that connects with the key takes part; the topology needs no site
configuration on the server, and an ordered site list matters only to `fill`.

Each relay is a delegate. It advertises upstream the zones it serves and, for each, its own
`min`/`max`/`init` bounds, configured beside that zone's launcher. It watches the published
topology periodically and reconciles only its own entries, launching through its own zone-specific
launcher. Any number of relays may take part; each zone at each relay scales independently by
pressure. Scale-down stays idle-exit; a relay idles at zero clients, still connected, ready to
grow. A site that dies drops out of the topology and its share regrows at the survivors.

A relay against a server that publishes no topology behaves exactly as the relay cycle ships it.

## Acceptance criteria (the contract)

The R-IDs are the seed's (`issues/relay-topology.md`); the bracket gives the original `relay`
seed's R-ID each came from. R1, R4, R5 and R6 are amended by the maintainer rulings of
2026-11-12 (Clarifications); every other SHALL is the seed's.

- **R1** [relay R11; *amended 2026-11-12*] — The server's autoscale configuration SHALL accept a
  site-distribution policy — **`spread`** (distribute demand across live sites as peers,
  respecting each one's `min`/`max`; the default) or **`fill`** (saturate sites in preference
  order, each up to its `max`, before spilling to the next — the SU/cost preference mode) — and an
  **optional** ordered list of site names that only `fill` reads. Under `fill`, listed live sites
  SHALL come first in declared order and unlisted live sites SHALL follow in the order they first
  registered. The topology SHALL need no server-side site configuration: every relay that connects
  with the key SHALL take part. Each zone a relay serves SHALL accept its own `min`/`max`/`init`
  client counts in the relay's configuration, in that zone's entry beside its launcher and other
  per-zone settings (`[relay.zone.<name>]`, and the relay's own settings for a relay that names
  no zone table); `min` SHALL default to 0. The server SHALL plan with the bounds the relays
  advertise (R4): a site's entry for a zone SHALL stay within that site's advertised `min`/`max`
  for the zone, and a zone's explicit `min` (and its `init` at start) SHALL be held even while the
  zone's work is gated behind the current group; growth above that floor SHALL follow Z3's
  group-scoped demand.
- **R2** [relay R12] — WHILE autoscaling with sites, the true server SHALL NOT launch client
  processes via a local launcher; it SHALL derive total demand from the existing pressure signal
  (group- and zone-scoped, per Z3) and publish a **versioned desired topology** — per-site and,
  where zones are active, per-zone client counts; each connected relay SHALL observe the current
  topology and reconcile only its own site's entries using its own launcher.
- **R3** [relay R13] — Only **live** sites (relay connected and heartbeating) SHALL receive
  topology assignments; IF a site's relay disconnects or is evicted, THEN the topology SHALL be
  recomputed over the remaining live sites while that site's unreturned tasks revert via existing
  orphan recovery — together constituting graceful site failover.
- **R4** [relay R14; *amended 2026-11-12*] — WHEN a relay connects to the upstream server — on
  first connect and on every later reconnect, through the reconnect path the relay cycle built
  (relay R9) — it SHALL send its configuration: the site identity, the zones it serves, and each
  served zone's `min`/`max`/`init` bounds. The true server SHALL record that advertisement where it
  recorded the relay's previous one, overwriting the old values, and SHALL account fleets and
  liveness per site and per zone, plan each zone's distribution over the live sites that serve it
  with their recorded bounds, and attribute clients and tasks to sites in presentation. A zone
  SHALL be assigned only to sites whose relay serves it. The site identity SHALL default to the
  relay host's hostname. WHILE two live relays advertise the same site, the most recently
  registered one SHALL hold the site's topology entries, the older one SHALL be assigned zero (its
  clients retiring through idle exit, never terminated, per R5), and the server SHALL log a warning
  naming both. *(The relay cycle already sends the site on every relay identity's beat and forwards the
  roster attributed to it; this extends it to served zones, bounds, and per-site, per-zone
  accounting and liveness.)*
- **R5** [relay R15; *ruling recorded 2026-11-12*] — Scale-down SHALL remain idle-exit driven: a
  reduced topology entry means the relay stops replacing exiting clients; site clients self-retire
  when no eligible work remains; the relay itself SHALL remain connected at zero local clients
  (scale-to-zero, ready to regrow). Neither the server nor the relay SHALL terminate a running
  client to reconcile a difference: IF a zone's advertised `max` falls below the clients running
  in it, THEN the topology SHALL assign that site-zone no new launches and the excess SHALL retire
  through the existing idle-exit timeout; a raised bound SHALL only let a later topology assign
  more.
- **R6** [relay R16, topology half; *amended 2026-11-12*] — Topology SHALL be optional: against an
  upstream that publishes none (one not autoscaling with sites, or an older server), the relay
  SHALL fall back to its locally-configured fleet size (the behaviour the relay cycle ships).
  *(The seed's "or for a site not declared in policy" clause is dropped: every connected relay
  takes part, Q1 ruling.)*
- **R7** — The server's distribution policy and optional site list, and the relay's per-zone bounds, SHALL be
  documented in `docs/_include/*.rst`, the configuration reference and `share/` completions in the
  same commit as the change.

## Non-goals (no-gos)

- **Weights or cost values per site.** `fill` order *is* the declared order; weights are a possible
  follow-up.
- **An implicit "local site."** The true server keeps no built-in launcher in relay topologies;
  local capacity = run a relay beside the server.
- **Server-side per-site or per-zone sizing knobs** *(amended 2026-11-12)*. Per-site, per-zone
  `min`/`max`/`init` live at the relay and are advertised (R1, R4); the server holds no bounds table
  of its own for sites, and no campaign-wide cap beyond the sum of what the live sites advertise.
  The per-zone *differentiated* sizing knobs of the zones non-goal (e.g. `--max-size gpu=4 cpu=100`
  on one `hs cluster`) stay out: a local, non-relay autoscaling cluster keeps Z3's uniform knobs.
- **Template forwarding through the topology channel** — a possible follow-up.
- Everything the relay cycle already excludes (throughput ceiling, cross-site cancellation and
  output retrieval, relay-of-relay, exactly-once).

## Clarifications

**Resolved during the relay shaping** (carried from the seed, not re-litigated):

- **Q:** Site model — ordered list vs weights, and does the true server keep an implicit local
  launcher? — **A:** **Ordered sites, no implicit local site** (resolved 2026-08-02, relay
  shaping). `fill` order *is* the declared order (SU preference). *The ordered list stays on the
  server but is optional and read only by `fill`; the per-site bounds it once carried moved to the
  relay (2026-11-12, below).*
- **Q:** Relay buffer depth? — **A:** **Bounded, demand-driven** (resolved 2026-08-02, relay
  shaping); hoarding breaks fill/spread fidelity. Delivered by the relay cycle's R8.
- **2026-11-04 — Carved from `relay`** (maintainer ruling at relay's promotion): *"Still out,
  because it needs Z3: R11–R15 and R16's topology half."* The relay cycle ships the delegation
  core with a locally configured fleet size.

**Ruled at promotion** (2026-11-12):

- **2026-11-12 — Restatement of the vision** (maintainer). The server can run off-cluster
  (Kubernetes, for example), autoscaling in zone mode with no launcher at all. When a zone needs
  another client, the server's autoscaler picks the next site for that zone by the policy (`fill`
  or `spread`). Relays watch the published topology periodically and, as delegates, scale
  themselves by invoking their own zone-specific launcher at their site. Any number of relays; each
  serves some set of zones; each zone at each relay scales independently by pressure. A zone is
  assigned only to sites whose relay serves it. *(Carried into Outcome, R2 and R4.)*
- **2026-11-12 — Per-zone bounds live at the relay** (maintainer ruling; amends R1, R4 and the
  "per-zone differentiated sizing knobs" non-goal). Each zone at a site has its own
  `min`/`max`/`init`, configured at the relay in that zone's entry, alongside its launcher and
  other per-zone settings (the `[relay.zone.<name>]` tables, and the relay's own settings for its
  default zone). The relay advertises the zones it serves and their bounds upstream (when it
  connects; see the next ruling), so the server's planner knows each live site's per-zone envelope when it
  computes and distributes the topology. In the maintainer's words: *"Just like every relay zone
  config entry can including launcher and other things so too each zone at the site can have their
  own min/max/init. The challenge is how will it be negotiated. The autoscaler on the server side
  upstream needs to know these values in order to plan what to scale next and distribute that
  topology. But it seems more elegant to define those at the relay site."*
- **2026-11-12 — Fill order stays on the server** (foreman reading). The 2026-08-02 resolution
  holds: `fill` order is the declared site order in the server's autoscale configuration (the
  campaign's cost preference). Live sites absent from that list: Q1 ruling below.
- **2026-11-12 — Advertise on connect; changes work themselves out** (maintainer ruling; settles
  how bounds are negotiated and what a change mid-run does; carried into R4 and R5). A relay sends
  its configuration (site identity, served zones, per-zone `min`/`max`/`init`) when it connects to
  the upstream server: on first connect and on every later reconnect, riding the graceful-reconnect
  path the relay cycle already built for a bounced relay. The server re-records the advertisement
  in the same place it recorded it before, overwriting the old values. Nothing ever brings down
  active clients to reconcile a difference: if a zone's `max` is lowered below its running clients,
  the topology assigns no new launches there and the relay stops replacing clients that exit; the
  excess retires on its own through the existing idle-exit timeout. Raising a bound lets the next
  topology assign more; lowering one only stops growth. In the maintainer's words: *"I'm not
  interested in any attempt to bring down active clients. We needs to somehow just allow the change
  to organically work itself out - if we lower the max clients on a zone we just let them stop on
  their own due to timeout."*

**Ruled at the GOAL gate** (2026-11-12):

- **2026-11-12 — Q1: relays join by default, and the default policy is `spread`** (maintainer
  ruling; amends R1 and R6). Any relay that connects with the key takes part in the topology; the
  topology needs no server-side site configuration. The server's ordered site list is optional and
  matters only under `fill`: listed sites come first, in declared order, and unlisted live sites
  follow in first-registration order. Under `spread`, every live site is a peer. The seed's R6
  undeclared-site clause is dropped; R6 keeps only its first half (an upstream that publishes no
  topology — not autoscaling with sites, or an older server). In the maintainer's words: *"we
  would actually have a default policy of "spread" and allow relays to join (they have the key!)
  and treat it as the default case without required config."*
- **2026-11-12 — Site naming** (foreman reading; confirm at the plan gate; carried into R4).
  `[relay] site` is optional and defaults to the relay host's hostname. A hostname is stable across
  restarts, so a bounced relay reclaims its own site under the Q2 ruling, and it reads well in
  logs. Two relays on one host must set `site` explicitly. The maintainer asked whether a hostname
  or a UUID suits better; a UUID would make every restart a new site until the old one is evicted.
  *(This is already how `relay_site` behaves, `relay/upstream.py:159-161`, `core/config.py:203`.)*
- **2026-11-12 — Q2: the most recent registration holds a shared site** (maintainer ruling; amends
  R4). When two live relays advertise the same site, the most recently registered one holds the
  site's topology entries. The older one is assigned zero, and its clients retire through idle
  exit, never terminated. A warning names both.
- **2026-11-12 — Zone floor** (maintainer ruling; amends R1). A relay zone's explicit `min` (and
  `init` at start) is a floor held even while the zone's work is gated behind the current group:
  keeping clients warm is the operator's choice. The default `min` is 0, so gated zones get nothing
  unless an operator asks. Growth above the floor follows Z3's demand.

**Foreman readings for the plan gate** (shaping, 2026-11-12; they stand as recorded):

- **Server knobs under sites.** With sites, the server's `[autoscale]` fixed/dynamic `policy`,
  `factor` and `period` keep their Z3 meaning for computing each zone's demand. Its own
  `size.init`/`min`/`max` do not apply (the server launches nothing, and Z3's own-zone init/min is
  for a launching server); each zone's total is bounded by the sum of its live sites' advertised
  `max` (non-goal above).
- **Local fallback with bounds (R6).** Without a topology the relay sizes each zone's fleet locally
  from that zone's `init`/`min`/`max`, through the same fixed-policy fleet it runs today
  (`relay/site.py:104-111`, `:147-153`, which already take `init`/`min`/`max`, today all equal to
  `size`). `size` stays as shorthand for `init` = `min` = `max`, so a relay configured only with
  `size` behaves exactly as today.

**Ruled at the plan gate** (2026-11-12):

- **2026-11-12 — Q1: relay launches always expose their zone** (foreman ruling; PLAN §5, deviation
  3). Relay launches always expose `{zone}` and `HYPERSHELL_ZONE`, with or without a topology. That
  is one launch path, matching Z3's exposure on every launch. Fleet sizes and argv are unchanged;
  the only addition to a site client's environment is that variable. The plan was approved with all
  five deviation rows as written. Deviation 4 (the group-read rule duplicated in
  `demand.scheduler_group`) stays honest only through its equivalence test, which runs in P1's
  verify.

**Drift since the seed was written** (re-confirmed against `develop` `22684fd`, with relay #82 and
Z3 #99 landed; scope holds):

- **D1 — Z3's seam is the carrier, as planned.** `read_zone_loads` (`cluster/demand.py:95-138`) is
  the only DB read; `fleet_demand` (`:158-169`) is pure and takes per-zone fleet sizes as an
  injected mapping; `zone_demand` (`:141-155`) grows a zone by at most one per check. `LaunchActuator`
  (`cluster/launch.py:228-324`; `launched()` `:268`, `apply()` `:299`) is the actuation side.
  `spec/zone-autoscale/TECH.md:168-190` ("Forward compatibility") lays out this cycle as a
  `PublishActuator` with the same `launched()`/`apply()` surface on the server and a
  `LaunchActuator` per relay. That note predates ruling 2: its "per-site `min`/`max`/`init`" are now
  the relays' advertised per-zone bounds.
- **D2 — The group the server reads is the scheduler's.** Z3's F1 ruling: the autoscaler reads
  `Scheduler.active_group` (`server.py:343-351`) through `scheduler_group`
  (`cluster/remote.py:899`), else `Task.starting_group` (`data/model.py:722`;
  `AutoScaler.current_group`, `cluster/remote.py:522-529`). A publishing server must keep that read.
- **D3 — Autoscaling is reachable only through `hs cluster`.** `-A/--autoscaling` is "used with
  `--launcher`" (`cluster/__init__.py:114`) and `AutoScalingCluster` always builds a launch
  (`cluster/remote.py:881-893`). A server with sites and no launcher (R2, the restatement) needs an
  entry point; which command carries it is the plan's call, documented under R7.
- **D4 — `[autoscale] policy` is taken.** It already names `fixed`/`dynamic` (`core/config.py:176-185`);
  the `fill`/`spread` setting needs its own key.
- **D5 — The relay's per-zone settings.** `[relay.zone.<name>]` accepts only `launcher`,
  `launcher_args` and `size` (`relay/__init__.py:228`, `zone_options` `:232-264`); R1 adds the bounds
  there. The relay refuses a `size` below 1, relay-wide and per zone (`relay/__init__.py:941-943`,
  `:984-986`), because its R8 pull gate never asks for work with no site client. Under a topology
  the server's demand, not the relay's pull, starts growth, so R5's zero is reachable there; the
  floor stays for the no-topology fallback.
- **D6 — Site identity exists; served zones, bounds and per-zone counts do not.** Every relay
  identity's beat carries `site` (`core/heartbeat.py:76`); the `HeartMonitor` writes it to the
  relay's `client` row (`server.py:769-798`; columns `site`, `relay`, `data/model.py:1299-1302`).
  A relay is one upstream identity per zone (relay DQ1 (a), `spec/relay/PLAN.md` §2.6), so the zone
  is implicit in which identity beats. Roster entries carry id, host and state, no zone
  (`core/heartbeat.py:50-55`), and the relay-local `client` table has no zone column
  (`spec/relay/TECH.md`, P9 amendments). So the server has no per-site, per-zone fleet count to
  feed `fleet_demand`; R4's accounting has to bring one. Upstream `Dispatcher.clients(zone)` counts
  relay identities, not site clients.
- **D7 — The relay's fleets are two shapes.** The first zone's fleet is the site
  `AutoScalingCluster` (`relay/site.py:79-114`, `discover_zones=False`); each further zone's is a
  `zone_fleet` (`:147-153`) over `zone_launch` (`:122-144`). `zone_launch` does not pass Z3's
  `expose_zone`, so relay launchers see no `{zone}` / `HYPERSHELL_ZONE` today; Z3 left that keyword
  to this cycle (`spec/zone-autoscale/TECH.md:185-186`).
- **D8 — The seed's dependency note is stale.** It says Z3 is "not yet built"; Z3 landed as #99
  (`22684fd`).

**Notes for `hs-plan`** (from the seed, updated):

- *Topology mechanics.* The managed queues are point-to-point — a broadcast "scaling channel" is
  wrong-shaped. Model the topology as **versioned state relays fetch** — Z2's request/reply surface
  is the natural carrier — on their heartbeat cadence; versioning makes application idempotent.
- *Zones composition.* Topology entries are per-site × per-zone where zones are active. A dedicated
  per-relay zone remains a pure configuration pattern (zone name = site name), not a mechanism;
  remember that a zone with no live client holds the task group (Z2 R10).
- *Multi-zone relays.* The relay cycle chose one upstream identity per zone (DQ1 (a)); that
  identity is the natural unit for a site-zone's liveness and advertised bounds.
- *Relay fleets must keep working without a topology.* As Z3 kept them: the relay's tests stay
  green with no topology published (R6).

## Related materials

- Seed: [`issues/relay-topology.md`](../../issues/relay-topology.md) (`status: adopted:relay-topology`).
- Built on: [`spec/zone-autoscale/`](../zone-autoscale/GOAL.md) (Z3, #99), especially `TECH.md`
  "The demand / actuation seam" and "Forward compatibility"; [`spec/relay/`](../relay/GOAL.md)
  (#82), especially Clarifications C1 and D3, and `PLAN.md` §2.6–2.7 (DQ1, DQ3).
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/cluster/demand.py`
  (unchanged seam); `src/hypershell/cluster/launch.py` (`LaunchActuator`, a publishing sibling);
  `src/hypershell/cluster/remote.py` (`AutoScaler`, a no-launcher site mode);
  `src/hypershell/relay/` (`__init__.py` zone options and bounds, `site.py` fleets and reconcile,
  `upstream.py` advertisement and topology fetch); `src/hypershell/server.py` (`HeartMonitor`
  site liveness, topology serving); `src/hypershell/core/heartbeat.py` (served zones, bounds,
  counts); `src/hypershell/core/config.py` (`[autoscale]` site list and distribution policy,
  `[relay.zone.<name>]` bounds).
- Docs & assets: `docs/cli/relay.rst`, the autoscaling section of the cluster docs,
  `docs/_include/config_param_ref.rst`, `docs/_include/relay_*.rst`, `share/` completions.
