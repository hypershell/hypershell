---
status: shaped
kind: feature
appetite: big
lane: public
shaped-as: relay
---

# Relay site-topology autoscaling: fill and spread across sites

> **Shaped candidate — not yet a contract.** This is the second phase of the `relay` seed,
> carved out when `relay` was promoted (maintainer, 2026-11-04): the delegation core went to
> [`spec/relay/`](../spec/relay/GOAL.md); this remainder needs zone autoscaling (Z3) first. The
> shaping happened with the relay seed — the clarifications below are dated and resolved and the
> R-IDs are drafted. Acceptance into a cycle has not happened.
>
> Promotion should re-confirm the scope against current `develop` (with relay and Z3 landed),
> cite anything that has drifted, and adopt it largely as written — do **not** re-litigate the
> resolved clarifications.

- **slug:** relay-topology
- **kind:** feature
- **appetite:** big  ·  *Rides the zones autoscaler rails (per-zone pressure and fleet
  accounting, Z3) and the relay delegation core; sequence strictly after both.*

## Problem

The relay cycle gives each site an `hs relay` that pulls work, runs a locally-configured fleet
size, and fails over through eviction and orphan recovery. What it does not give is a **campaign
view of capacity across sites**. Each relay sizes its own fleet; the true server, which holds the
pressure signal, cannot say "fill the cheap site first, spill to the next", "spread across what
is live", or "regrow at the surviving site when one goes down". A campaign that should prefer the
center with cheaper service units (SU) has no way to express it, and site failover moves the
tasks but not the capacity.

## Why it was deferred

Carved out of `relay` at promotion (2026-11-04). The topology computation extends zone
autoscaling — per-zone, group-scoped pressure and per-zone fleet accounting (`issues/zone.md`
R13–R17, cycle Z3) — and Z3 is not built and was not scheduled ahead of relay. Building it inside
relay would have meant building Z3's rails too, before SC26. Not pre-existing debt: new capability.

## Outcome / vision

Under autoscaling, the true server stops launching processes and instead publishes a **desired
topology**: per-site client counts computed from the same group- and zone-scoped pressure signal
Z3 builds, distributed by a **`fill`** policy (saturate sites in declared preference order — the
SU-cost mode) or **`spread`** (distribute across live sites), under per-site `min`/`max`/`init`.
Each relay watches the topology and reconciles only its own entry with its own launcher;
scale-down remains idle-exit; a relay idles at zero clients, still connected, ready to grow. A
site that dies drops out of the topology and its share regrows at the survivors.

## Sketch of the acceptance criteria

Draft R-IDs; the bracket gives the `relay` seed's R-ID each one came from.

- **R1** [relay R11] — The autoscale configuration SHALL accept an ordered set of named sites,
  each with `min`/`max`/`init` client counts, and a site-distribution policy: **`fill`**
  (saturate sites in declared order up to `max` before spilling to the next — the SU/cost
  preference mode) or **`spread`** (distribute demand across live sites, respecting per-site
  `min`/`max`).
- **R2** [relay R12] — WHILE autoscaling with sites, the true server SHALL NOT launch client
  processes via a local launcher; it SHALL derive total demand from the existing pressure signal
  (group- and zone-scoped, per Z3) and publish a **versioned desired topology** — per-site (and,
  where zones are active, per-zone) client counts; each connected relay SHALL observe the current
  topology and reconcile only its own site's entry using its own launcher.
- **R3** [relay R13] — Only **live** sites (relay connected and heartbeating) SHALL receive
  topology assignments; IF a site's relay disconnects or is evicted, THEN the topology SHALL be
  recomputed over the remaining live sites while that site's unreturned tasks revert via existing
  orphan recovery — together constituting graceful site failover.
- **R4** [relay R14] — Relay registration/heartbeat SHALL carry the site identity, so the true
  server accounts fleets and liveness per site and presentation can attribute clients and tasks
  to sites. *(The relay cycle's roster forwarding already attributes leaf clients to a site; this
  extends it to per-site accounting and liveness.)*
- **R5** [relay R15] — Scale-down SHALL remain idle-exit driven: a reduced topology entry means
  the relay stops replacing exiting clients; site clients self-retire when no eligible work
  remains; the relay itself SHALL remain connected at zero local clients (scale-to-zero, ready to
  regrow).
- **R6** [relay R16, topology half] — Topology SHALL be optional: against an upstream that
  publishes none, or for a site not declared in policy, the relay SHALL fall back to its
  locally-configured fleet size (the behaviour the relay cycle ships).
- **R7** — The autoscale site table and policy SHALL be documented in `docs/_include/*.rst`, the
  configuration reference and `share/` completions in the same commit as the change.

## Non-goals (no-gos)

- **Weights or cost values per site.** `fill` order *is* the declared order; weights are a
  possible follow-up.
- **An implicit "local site."** The true server keeps no built-in launcher in relay topologies;
  local capacity = run a relay beside the server.
- **Per-zone differentiated sizing knobs** (unchanged zones non-goal). Per-**site**
  `min`/`max`/`init` is the only differentiated sizing introduced.
- **Template forwarding through the topology channel** — a possible follow-up.
- Everything the relay cycle already excludes (throughput ceiling, cross-site cancellation and
  output retrieval, relay-of-relay, exactly-once).

## Clarifications

- **Q:** Site model — ordered list vs weights, and does the true server keep an implicit local
  launcher? — **A:** **Ordered sites, no implicit local site** (resolved 2026-08-02, relay
  shaping). `fill` order *is* the declared order (SU preference).
- **Q:** Relay buffer depth? — **A:** **Bounded, demand-driven** (resolved 2026-08-02, relay
  shaping); hoarding breaks fill/spread fidelity. Delivered by the relay cycle's R8.
- **2026-11-04 — Carved from `relay`** (maintainer ruling at relay's promotion): *"Still out,
  because it needs Z3: R11–R15 and R16's topology half."* The relay cycle ships the delegation
  core with a locally-configured fleet size.
- **Note (for `hs-plan`):** *topology mechanics.* The managed queues are point-to-point — a
  broadcast "scaling channel" is wrong-shaped. Model the topology as **versioned state relays
  fetch** — Z2's request/reply surface is the natural carrier — on their heartbeat cadence;
  versioning makes application idempotent.
- **Note (for `hs-plan`):** *zones composition.* Topology entries are per-site × per-zone where
  zones are active (composing Z3's per-zone pressure with R1–R2 here). A dedicated per-relay zone
  remains a pure configuration pattern (zone name = site name), not a mechanism; remember that a
  zone with no live client holds the task group (Z2 R10).
- **Note (for `hs-plan`):** *multi-zone relays.* Whatever the relay cycle chose for a relay
  serving several zones (its design question DQ1: one upstream identity per zone, or an additive
  demand-map change) is the unit the topology counts; read `spec/relay/TECH.md` first.

## Notes

- Depends on: `issues/zone.md` R13–R17 (Z3, zone-aware autoscaling), not yet built, and
  `spec/relay/` (the delegation core), landed in #82 (2.9.0a6).
- Related: `spec/relay/GOAL.md` (Clarifications C1, D3). The original `relay` seed was retired when
  #82 landed; `git log --diff-filter=D -- issues/relay.md` recovers it.
- Source (likely touchpoints): `src/hypershell/cluster/remote.py` (`AutoScaler` →
  demand-to-topology computation, publication, no-launcher site mode); the relay module's
  reconciler; `src/hypershell/server.py` (`HeartMonitor` site liveness);
  `src/hypershell/core/heartbeat.py` (site field); `src/hypershell/core/config.py` (`[autoscale]`
  site table + distribution policy).
- Found by: `relay` promotion (`/hs-feature`, 2026-11-04).
