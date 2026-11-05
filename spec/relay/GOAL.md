# GOAL — HyperShell Relay: two-level scheduling across administrative sites

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay
- **kind:** feature
- **appetite:** big  ·  *Taken from the shaped seed. It adds a new long-running service that
  composes the client, the server and a launcher in one process, binds a second database at import
  time, and leans on the zone dispatch wire, so `hs-plan` should do its research pass.*
  **Calendar:** the maintainer wants relay shipped in a pre-release on `develop` by about
  **2026-11-13 (SC26)**. Zones Z1 + Z2 land first as 2.9.0a4, then a small security-hardening
  cycle, then this cycle's build — about **5–6 days of build and review**. Twenty-one criteria
  trip the size circuit-breaker twice over, and the topology half (R11–R16) depends on zone
  autoscaling (Z3), which is not scheduled ahead of this cycle. The proposed SC26 cut is
  **Clarification C1** below and is open: `hs-plan` does not start until the maintainer rules it.

## Problem

HyperShell's single shared hand-off tops out on two axes that have nothing to do with task
throughput. First, **connection fan-out**: every connected client holds queue connections, and
so file descriptors, in the one server process. Since Z1 that ceiling is explicit and documented:
the server admits a connection only below its open-file soft limit less a reserve, about
(soft limit − 32 − its own files) / 3 clients, and refuses the rest with a warning. That is on the
order of ~1,000 clients, while leadership-class systems at the DOE labs put ~10,000 nodes on the
floor — we cannot register every node as a first-class client. Second, **administrative
boundaries**: one elastic fleet cannot span centers. A campaign that could draw on Purdue, ACCESS,
and DOE capacity today needs a separate cluster per center; there is no way to prefer the center
with cheaper service units (SU), and if the center hosting the fleet goes down, the campaign goes
down with it — even though the database and plenty of remote capacity survive.

The `zone` feature deliberately stops at single-instance, in-one-server routing: Z2's non-goals
call out the multi-server / one-cluster-per-zone topology, and the lifecycle (`revert_interrupted`,
termination, `latest_server`) assumes a single database owner. That exclusion needs a sanctioned
shape — otherwise users improvise multi-server-shared-database hacks the lifecycle machinery was
never built to survive (`AGENTS.md`: two servers on one database double-schedule and double-retry).

## Outcome / vision

A new long-running service, **`hs relay`**, runs at each site (started by the operator — a login
node, a service host, `systemd`; the true server launches nothing across the boundary). Upstream,
the relay is *just a client*: it connects with the upstream auth key and pinned TLS, requests
work through Z2's work-request protocol advertising its zone and its own (large) bundle size,
confirms, returns results and heartbeats — the upstream dispatch surface does not know relays
exist. Inside, it is a complete little cluster: pulled tasks are inserted into a relay-local
SQLite database from which an embedded server schedules to site clients (with their own zones and
bundle sizes) launched through the site's own launcher command. Completed tasks flow back upstream
attributed to the node that actually ran them, while in-flight tasks stay attributed to the relay
— so the existing eviction and orphan-recovery machinery *is* the failover story: a site that
dies has its unreturned tasks reverted and re-dispatched to surviving sites, and a late result
from the dead site is refused by the result fence (#70) rather than overwriting the re-run.

Under autoscaling, the true server stops launching processes and instead publishes a **desired
topology**: per-site client counts computed from the group- and zone-scoped pressure signal,
distributed by a **`fill`** policy (saturate sites in declared preference order — the SU-cost
mode) or **`spread`** (distribute across live sites), under per-site `min`/`max`/`init`. Each
relay watches the topology and reconciles only its own entry with its own launcher; scale-down
remains idle-exit; a relay idles at zero clients, still connected, ready to grow.

The arithmetic this buys: ~100 relays × ~100 nodes ≈ 10,000 nodes behind ~100 upstream clients'
worth of connections. To be explicit about what it does *not* buy: every task still schedules,
confirms, and completes through the true server's database, so the net task-throughput ceiling
(~10k tasks/s, less with PostgreSQL) is unchanged — the relay multiplies fan-out, not row
throughput; suitably-sized tasks remain the operating assumption. Security *improves* at scale:
the upstream key and cert material stop at the relay; leaf nodes only ever hold a per-site key
minted fresh by their relay.

A user who never runs `hs relay` sees no change whatsoever.

## Acceptance criteria (the contract)

R1–R18 are the seed's R-IDs, kept for traceability and amended only where `develop` or the zone
cycles have moved under them (each amendment is cited in **Clarifications → Drift**). R19–R21 are
new: a prerequisite the ROADMAP sequences "before relay" and the evidence this cycle owes.
**Which of these this cycle carries is Clarification C1.**

**Delegation core** *(usable against a plain `--forever` server; the seed's own first phase)*

- **R1** — The system SHALL provide `hs relay`, a long-running per-site service composing three
  roles in one process: an upstream connector (client role), a complete local server instance
  scheduling from a relay-local database, and a local fleet launch/reconcile loop driving the
  site's own launcher command template.
- **R2** — The relay SHALL speak the ordinary client protocol upstream — Z2's work-request
  protocol: request work advertising its identity, its configured zone and its own bundle size;
  receive the reply; confirm; return results; heartbeat — such that the upstream dispatch surface
  requires **no relay-specific changes**. Each pull is bounded by the upstream server's reply cap
  (`server.bundlemax`); the relay requests again rather than expecting a larger reply. IF the
  upstream server does not serve the work-request protocol, THEN the relay SHALL exit non-zero
  with the same single version error a client gives (Z2 R5). A relay serving multiple zones
  requests work per zone *(see Drift D2 and C1: the proposed cut serves one zone per relay)*.
- **R3** — WHEN a bundle is received, the relay SHALL insert its tasks into the relay-local
  database — preserving task ids, group, zone, tags, and the true server's scheduling stamps —
  and SHALL confirm receipt upstream only after the insert is durable. Upstream in-flight
  attribution (rows stamped with the relay's client id at confirmation) is thereby retained until
  results return, so existing eviction + `revert_orphaned` deliver site failover unchanged.
- **R4** — The relay-local server SHALL schedule inserted tasks to site clients with standard
  mechanics (Z2's per-client request and bundle size, targeted zone dispatch) and SHALL NOT retry
  locally (`max_retries = 0`): the true server remains sole owner of retry policy and the
  `previous_id`/`next_id` chain. Failures return upstream as failures.
- **R5** — The relay SHALL return completed tasks upstream in bundles (bundlesize/bundlewait
  semantics mirroring the client collector), prune returned rows from the local database, and
  tolerate crash-replay: a duplicate or stale return SHALL be harmless — the upstream result fence
  (#70) classifies it `DUPLICATE` or `STALE` and writes nothing — and a return the upstream server
  defers (`DEFERRED`, #75) SHALL NOT be pruned as if it had landed. *(Amended: the seed relied on
  an idempotent whole-row update by id, which #70 replaced.)*
- **R6** — Returned task records SHALL reflect the executing site client (`client_id`,
  `client_host`, `command`, timing, `exit_status`, output paths, resource fields) and SHALL carry
  the true server's `schedule_time` exactly as received in the bundle, because that stamp is the
  assignment the upstream fence matches results against (#70); a relay-local scheduling stamp in
  its place would make every result `STALE`. Relay-local scheduling stamps SHALL NOT leak upstream
  — `latest_server()`, `count_connected()`, and the rate/pressure math must remain correct with
  relays in play.
- **R7** — The relay SHALL forward its local client roster upstream — registrations and
  disconnections, attributed to the relay's site — so leaf clients exist in the true server's
  `client` table and `effective_rate_by_client` / `task_pressure` / client-facing presentation
  remain coherent. Any new `client` column arrives through the in-place upgrade step (#69):
  nullable, no per-column upgrade code.
- **R8** — The relay SHALL pull demand-driven with a bounded local backlog derived from its live
  local fleet (on the order of connected clients × local bundle size); WHILE the bound is met,
  the relay SHALL NOT pull further work — a relay never hoards tasks the policy would place
  elsewhere, and the cancellation and duplicate-execution windows stay bounded.
- **R9** — WHILE the upstream server is unreachable, the relay SHALL continue operating its site
  — scheduling already-pulled tasks and collecting completions durably — reconnect with backoff,
  and return buffered results on reconnection; a relay restart SHALL NOT lose
  completed-but-unreturned results. *(The seed expected to inherit reconnect/redelivery from
  `coordinator-resilience-retry-backoff`; that seed is still `unshaped` and unscheduled — Drift
  D7. C1 splits R9 into its restart-durability half and its outage-ride-through half.)*
- **R10** — The relay-local database SHALL be private, disposable operational state at a
  dedicated default location; it SHALL be SQLite whatever database provider the user has
  configured (SQLite, PostgreSQL or `timescale`, #71); it SHALL NOT default to (or disturb) the
  user's configured primary database; and pruning (R5) SHALL keep it size-stable across campaigns.

**Site-topology autoscaling** *(the seed's second phase; composes with zones autoscaling, Z3)*

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
  locally-configured fleet size — the delegation core SHALL be fully usable without
  site-topology autoscaling.

**Security & convention**

- **R17** — The relay SHALL authenticate upstream with the upstream key and TLS configuration
  (supporting shared-cert / `cafile` / fingerprint pinning per the distributed-TLS guidance) and
  SHALL mint a fresh site-local auth key for its own fleet; upstream credentials SHALL NOT be
  exposed to site clients (not on their argv, not in their environment).
- **R18** — CLI additions SHALL update the affected `docs/_include/*.rst` help snippets and the
  `share/` completions in the same commit (repo convention), and `docs/security.rst` SHALL gain
  the relay trust model (two auth domains, TLS pinning across the WAN hop, process-table key
  exposure now confined within each site). The docs SHALL state the non-goals users will meet:
  no throughput gain, no cross-site cancellation or output retrieval, and the limits C1 defers.

**Prerequisite and evidence** *(new)*

- **R19** — WHEN a task row upstream is already complete, a confirmation applied to it SHALL NOT
  change its executing-client attribution: a relay's late confirmation SHALL NOT overwrite the
  leaf attribution its own result wrote. The confirmation write SHALL keep the contended-write
  discipline it follows today (`CONTENDED_RERUNS`, #75). *(From
  `issues/late-confirm-overwrites-attribution.md`, which the ROADMAP sequences "before relay" and
  which is still `unshaped` on `develop` — Drift D5; reproduced in-process during #70's review.
  Whether it rides this cycle or lands first on its own is C2.)*
- **R20** — New tests SHALL cover the relay path: durable insert before confirm; no local retry,
  a failure returned upstream and retried there; leaf attribution with the upstream
  `schedule_time` preserved; duplicate and stale returns writing nothing; the R8 bound holding;
  restart durability of completed-but-unreturned results; the upstream-credential fence (R17);
  the relay-local database never touching the configured primary; and the relay re-entering on
  timeout and halting on shutdown. A loopback run of one upstream server and two relays, each with
  site clients, SHALL run a grouped workload that includes a task that fails and is retried, to
  completion, with every task completed once upstream. The existing suite SHALL pass.
- **R21** — Before the pre-release, a **multi-node run on a test cluster** SHALL show, within that
  cluster's job limits: one node runs the upstream server; **two node groups act as two sites**,
  each running its own `hs relay` that launches site clients on its group's nodes with a site-local
  key. On a grouped workload with a failing-and-retried task: (a) every task reaches its final
  state once upstream, with group order kept; (b) every completed row names a leaf client of the
  site that ran it, not a relay; (c) the upstream server holds connections for the relays only,
  not for leaf clients; (d) **failover** — one site's relay is stopped mid-run, it is evicted, its
  unreturned tasks revert and complete at the surviving site, and no task is lost; (e) the upstream
  key appears in no site client's argv or environment. Records land under
  `spec/relay/evidence/` following the zone drives' external-wait convention; the run is the
  foreman's to perform.

## Non-goals (no-gos)

- **Raising the throughput ceiling.** Every task still schedules, confirms, and completes through
  the true database; the relay scales *connection fan-out*, not tasks-per-second. Say so in docs.
- **Cross-site cancellation propagation.** `hs update --cancel` cannot reach tasks already inserted
  at a site — today's in-flight-bundle semantics with a deeper (but R8-bounded) buffer. Documented
  limitation. (`issues/evicted-client-fencing-and-cancel-reach.md` owns the server→client control
  path.)
- **Cross-site captured-output retrieval.** `outpath`/`errpath`/`csvpath` name files on site
  filesystems; the SFTP retrieval behind `hs info` is not extended across sites
  (`issues/turso.md` R13–R15 soften this later).
- **Relay-of-relay.** Two levels only; no deeper hierarchy.
- **Exactly-once execution.** At-least-once is retained: eviction and revert can re-run a task a
  dead or partitioned site was still running. Since #70 the first result from the *current*
  assignment wins and a zombie site's late result is refused as `STALE`; nothing more is promised.
- **The cut-to-confirm window.** A relay that dies after a reply is cut for it and before its
  confirmation lands leaves those rows in flight with no client id until the upstream server
  restarts — the same window as any client today (Z2 PLAN §5;
  `issues/stale-confirm-hides-orphan.md`), larger only by the relay's bundle size. Documented,
  not fixed here.
- **Template forwarding.** The execution template stays site-local client configuration (the
  relay operator sets it, exactly like a standalone `hs client -t`); campaign-specific templating
  across relays is already served by JSON mode (submit-side expansion, clients run verbatim).
  Forwarding templates through the topology channel is a possible follow-up, not this.
- **Per-zone differentiated sizing knobs** (unchanged zones non-goal). Per-**site**
  `min`/`max`/`init` is the only differentiated sizing introduced here.
- **An implicit "local site."** The true server keeps no built-in launcher in relay topologies;
  local capacity = run a relay beside the server. One code path, one topology.
- **Changing the auth/TLS wire model** (pickle-framed RPC, HMAC challenge, no mTLS — as is), and
  **IPv6 on the upstream hop**: the relay dials upstream over IPv4 like every TLS client since #72
  (`issues/ipv6-server-listener.md` owns IPv6).
- **Fixing upstream hypertable write gaps** outside the paths relay uses
  (`issues/hypertable-contended-scheduler-and-cli-writes.md`).

## Clarifications

**Resolved during shaping** (carried from the seed, not re-litigated):

- **Q:** How do leaf clients appear upstream — forward the roster, keep the relay as the only
  registered client, or write leaf ids without `client` rows? — **A:** **Forward the roster**
  (resolved 2026-08-02). Task rows carry the true executing client; the upstream `client` table
  gains those clients attributed to their site, keeping the `effective_rate_by_client` join, the
  pressure math, and observability coherent. Implies a back-compat nullable site-attribution
  column on `client` (now via the #69 upgrade step).
- **Q:** Who owns retries? — **A:** **True server only** (resolved 2026-08-02). The relay's local
  server runs `max_retries=0`; the upstream retry machinery re-dispatches failures (possibly to a
  different site). Single owner of the id chain; no remapping.
- **Q:** Site model — ordered list vs weights, and does the true server keep an implicit local
  launcher? — **A:** **Ordered sites, no implicit local site** (resolved 2026-08-02). `fill`
  order *is* the declared order (SU preference); weights/cost values are a possible follow-up.
- **Q:** Relay buffer depth? — **A:** **Bounded, demand-driven** (resolved 2026-08-02). The bound
  derives from the live local fleet, not a user knob; hoarding breaks fill/spread fidelity and
  widens the cancellation/duplicate windows. WAN latency is amortized by large bundles.
- **2026-11-01 (Z2, maintainer alignment memo)** — "A relay counts as one client advertising a
  large size." Z2's dispatch staging was designed with that in mind.

**Open — block `hs-plan`:**

- **C1 — [NEEDS CLARIFICATION: the SC26 cut.]** The shaped scope does not fit about 5–6 days of
  build and review, and R11–R16 cannot be built on rails that do not exist yet: per-zone pressure
  and fleet accounting are zone's R13–R17 (Z3), which is not scheduled ahead of relay (Drift D3).
  The seed's own split (delegation core first) is the starting point. Options:
  - **A — Smallest relay that demos at SC26 (recommended).** This cycle carries **R1–R6, R8,
    R10, R16 (fixed local fleet only), R17–R21**, with two narrowings: **R2** serves **one
    upstream zone per relay** (default or named; the zone-name-equals-site-name pattern still
    works), and **R9** keeps only its last clause — *a relay restart SHALL NOT lose
    completed-but-unreturned results*. **R6**'s "rate math remains correct" clause becomes a
    documented limitation: until the roster is forwarded, upstream rate, average-duration and ETA
    figures leave out relay-executed tasks (they join on `client`). Deferred to a new shaped seed
    **`issues/relay-topology.md`**, carrying the dated clarifications above: **R7, R9's
    outage ride-through, R11–R15, R16's topology half, and multi-zone relays**. Demo: two sites,
    leaf attribution, site failover, credentials confined — all of R21.
  - **B — A plus the roster (R7).** Upstream presentation and rate math stay coherent at SC26,
    at the cost of a `client` column, a heartbeat field tolerated by mixed-version fleets, and
    roster semantics against the upstream `HeartMonitor` (eviction of a leaf the server never
    talks to). Adds about a day and touches `server.py`'s heart monitor; tightest fit.
  - **C — The seed's whole first phase: R1–R10 + R16–R21**, including outage ride-through
    (reconnect with backoff, redelivery) built inside relay rather than inherited, and multi-zone
    relays (needs one upstream identity per zone, Drift D2). Does not fit the window on any
    reading of Z1's eight and Z2's three review cycles; would ship after SC26.
  - *(Not offered: the whole seed. R11–R16 need Z3 first.)*
- **C2 — [NEEDS CLARIFICATION: where R19 lands.]** (i) **In this cycle** as R19 (recommended:
  small, no wire change, and the relay's leaf attribution is wrong without it); or (ii) as its
  own `fix/` cycle from `issues/late-confirm-overwrites-attribution.md`, landed on `develop`
  before relay's build, and dropped from this GOAL.

**Drift since the seed was written** (re-confirmed 2026-11-04 against `origin/develop` `ab16902`
(2.9.0a3), `origin/feature/zone-queue-primitive` `5b2ed9f` and `origin/feature/zone-dispatch`
`3d7231e`):

- **D1 — Z1 (zone-queue-primitive, approved).** The queues are served in the server process; the
  fan-out ceiling is now an explicit open-file bound with a 32-descriptor reserve and a refusal
  warning, about 3 descriptors per client (Z1 TECH, C3(b) and "Documented constraint"). The
  Problem's "~1,000 sockets" now has a mechanism. The seed's "R1 (queue-primitive swap)"
  dependency is met.
- **D2 — Z2 (zone-dispatch, approved, not landed).** Dispatch is a client *request* answered on
  that client's connection (`request(text)`, JSON, `PROTOCOL = 1`), not a shared `scheduled`
  queue. Consequences for relay: each reply is clamped to `server.bundlemax` (default 1000), so a
  relay's "large size" pulls in several requests (R2, R8); old/new version mismatches are refused
  with one error (R2); the server's demand map is keyed `client_id → (zone, size)` (Z2 PLAN
  §2.2), so one relay identity requesting for two zones would overwrite its own demand — a
  multi-zone relay needs one upstream identity per zone or an upstream change, contradicting R2's
  "no relay-specific changes" (C1); when a non-default zone loses its last client, its staged
  pool is unstaged by id, which helps failover; and a zone with no client **holds the group**
  (Z2 R10), so cross-site failover works only when both sites serve the zone the work is in —
  R21 runs both sites in one zone. Z2's TECH on `origin` is behind the approved local head
  (`b6fcb55`, review cycle 3 approved); only phase status and gate records differ.
- **D3 — Z3 (zone autoscaling) is not built and not scheduled before relay.** Z2's Clarification
  (2026-11-01) moved zone R13–R17 to Z3 and allows deferring it to `issues/zone-autoscale.md`.
  The seed's R11–R16 "ride the zones autoscaler rails"; those rails (per-zone pressure, per-zone
  fleet accounting, `{zone}`/`HYPERSHELL_ZONE`) do not exist yet. Hence C1.
- **D4 — #70 writeback-scoped-conditional.** `Receiver.update_tasks` no longer does
  `Task.update_all(to_dict())`; `Task.apply_results` writes only the 13 `result_columns`
  (`client_id`, `client_host`, `command`, timing, `exit_status`, output paths, `cores_max`,
  `memory_max`), conditioned on the row's `id` and `schedule_time` with `completion_time` NULL,
  and classifies the rest `DUPLICATE`/`STALE`. The seed's return-path note ("restore the upstream
  stamps before the put") is obsolete as written: `server_id`/`server_host` cannot leak through a
  result any more, but the returned `schedule_time` must be the upstream one or nothing lands (R5,
  R6 amended). Leaf attribution via `client_id` is a result column, as #70's seed promised.
- **D5 — The late-confirm overwrite is still open.** `Task.confirm_all` writes `client_id` and
  `client_host` by id unconditionally; `Confirm` and `Receiver` run in no fixed order. ROADMAP
  sequences the fix "near-term, before relay"; its seed is `unshaped`. Hence R19 and C2.
- **D6 — #71 timescale and #75 contended-write recovery.** The upstream `task` table may be a
  TimescaleDB hypertable, where a contended UPDATE can match 0 rows; `confirm_all`, `revert_all`
  and `apply_results` follow `CONTENDED_RERUNS`, and a `DEFERRED` result is held and re-presented
  by the Receiver. Relay adds no upstream task-row write of its own except R19, which keeps the
  pattern; R5 must not treat a deferred return as landed. The relay-local database is pinned to
  SQLite whatever provider is configured (R10).
- **D7 — The resilience seed did not land first.** ROADMAP says "relay R9 is verbatim this
  machinery — build it once here and the relay's upstream connector inherits it", but
  `issues/coordinator-resilience-retry-backoff.md` is `unshaped` and unscheduled. R9's outage
  half has nothing to inherit (C1).
- **D8 — #74 group order.** The scheduler now chooses the lowest incomplete group atomically, so a
  late lower group runs instead of stranding. The seed's "group consistency is free" note still
  holds and is safer: a relay-local server advances to whichever group upstream releases.
- **D9 — #72 TLS IPv4 dial.** The TLS client dials IPv4 only; the relay's upstream hop inherits
  that (non-goal added). The relay's own listener is the ordinary server listener.
- **D10 — #69 upgrade step and #73.** New columns (R7's site attribution) go through the in-place
  upgrade step: nullable or constant default, no per-column code. #73 only changes how the result
  statements are built; no effect on relay beyond throughput headroom.
- **D11 — Touchpoint correction.** `DISTRIBUTED_ROLES` lives in `src/hypershell/core/logging.py`,
  not `__init__.py` as the seed (and `AGENTS.md`) say.
- **D12 — The security-hardening cycle lands between zones and relay.** R17/R18 are planned
  against the tree it leaves; `hs-plan` re-reads `docs/security.rst` and `core/tls.py` at that
  base rather than at `ab16902`.

**Notes for `hs-plan`** (from the seed, updated for drift):

- *Topology mechanics.* The managed queues are point-to-point — a broadcast "scaling channel" is
  wrong-shaped. Model the topology as **versioned state relays fetch** — Z2's request/reply
  surface is the natural carrier — on their heartbeat cadence; versioning makes application
  idempotent. Final mechanism in `TECH.md`. (Only if C1 keeps R11–R16.)
- *Return path.* Retain the upstream `schedule_time` (and other received stamps) per task at
  insert; put it back on the returned record before the put. The upstream Receiver stays unchanged
  by design.
- *Import-time engine.* `data/core.py` builds the engine/`Session` at import from the config
  singleton; the relay process must bind its **local** SQLite (R10) before that import wins — and
  regardless of a configured PostgreSQL/`timescale` provider — while all upstream interaction
  stays queue-only. Likely the same env-steering approach as `temp_site.sh`; flag as the
  highest-risk plumbing.
- *Roster + heartbeat.* Piggyback the roster on the relay's heartbeat or a dedicated message —
  either way `Heartbeat` gains optional fields (site), and JSON pack/unpack must tolerate absence
  for mixed-version fleets. (Only if C1 keeps R7.)
- *`no_confirm` is incompatible with relay mode* — it disables the orphan recovery R3/R13 lean on.
  Refuse it on the relay CLI, and on the upstream server when relays register.
- *Eviction window.* A relay's unreturned backlog exceeds a leaf client's; consider whether
  upstream `evict_after` (default 600 s) deserves a relay-aware default (R8 bounds the blast
  radius either way). R21's failover leg needs a short eviction setting to fit the job limit.
- *Group consistency is free* (D8).
- *Zones composition.* Topology entries are per-site × per-zone where zones are active. A
  dedicated per-relay zone remains a pure configuration pattern (zone name = site name), not a
  mechanism — but see D2 on held groups.

## Related materials

- Seed: [`issues/relay.md`](../../issues/relay.md) (`status: adopted:relay`).
- Dependencies: [`spec/zone-queue-primitive/`](../zone-queue-primitive/GOAL.md) (Z1) and
  [`spec/zone-dispatch/`](../zone-dispatch/GOAL.md) (Z2), both approved, landing as 2.9.0a4;
  [`issues/zone.md`](../../issues/zone.md) R13–R17 (Z3) for R11–R16.
- Related seeds: `issues/late-confirm-overwrites-attribution.md` (R19),
  `issues/coordinator-resilience-retry-backoff.md` (R9), `issues/stale-confirm-hides-orphan.md`,
  `issues/orphan-revert-loses-result.md`, `issues/evicted-client-fencing-and-cancel-reach.md`,
  `issues/ipv6-server-listener.md`, `issues/turso.md`.
- Landed since the seed: #69 (upgrade step), #70 (scoped writeback), #71 (timescale), #72 (TLS
  IPv4 dial), #73 (writeback statements), #74 (group order), #75 (contended-write recovery).
- Source (likely touchpoints, to be confirmed by `hs-plan`): new `src/hypershell/relay.py` (or
  `cluster/relay.py`) — connector, embedded local server composition, returner, reconciler;
  `src/hypershell/__init__.py` (commands dict, `APP_HELP`); `src/hypershell/core/logging.py`
  (`DISTRIBUTED_ROLES`); `src/hypershell/core/dispatch.py` and `core/queue.py` (Z2's request
  surface, unchanged by design); `src/hypershell/server.py` (`Confirm` for R19; `Receiver`
  unchanged); `src/hypershell/data/model.py` (`Task.confirm_all` for R19); `src/hypershell/data/core.py`
  (relay-local DB binding); `src/hypershell/core/config.py` (`[relay]` section);
  `src/hypershell/cluster/remote.py` (launcher reuse; `AutoScaler` only if topology stays);
  `src/hypershell/core/heartbeat.py` (only if the roster stays); `docs/security.rst`,
  `docs/_include/*.rst`, `share/` completions.
- Prior art: **glideinWMS** (per-site frontend/factory feeding a central pool), **HTCondor**
  flocking + glideins, **Balsam** multi-site daemons (closest DOE analog — site agents pull from
  a central service and drive the local batch system), Slurm federation, hierarchical
  work-stealing schedulers.
