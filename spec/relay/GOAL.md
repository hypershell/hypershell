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
  cycle, then this cycle's build — about **5–6 days of build and review**. The maintainer ruled
  the seed's **whole first phase** in scope for that pre-release (C1, 2026-11-04), knowing it is a
  tight fit. The site-topology half (seed R11–R15 and R16's topology half) needs zone autoscaling
  (Z3) and moved to [`issues/relay-topology.md`](../../issues/relay-topology.md).

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
work through Z2's work-request protocol advertising its zone(s) and its own (large) bundle size,
confirms, returns results and heartbeats — the upstream dispatch surface does not know relays
exist. Inside, it is a complete little cluster: pulled tasks are inserted into a relay-local
SQLite database from which an embedded server schedules to site clients (with their own zones and
bundle sizes) launched through the site's own launcher command. Completed tasks flow back upstream
attributed to the node that actually ran them, while in-flight tasks stay attributed to the relay
— so the existing eviction and orphan-recovery machinery *is* the failover story: a site that
dies has its unreturned tasks reverted and re-dispatched to surviving sites, and a late result
from the dead site is refused by the result fence (#70) rather than overwriting the re-run. A
relay that loses its upstream keeps its site running and hands the results over when the link
returns.

The arithmetic this buys: ~100 relays × ~100 nodes ≈ 10,000 nodes behind ~100 upstream clients'
worth of connections. To be explicit about what it does *not* buy: every task still schedules,
confirms, and completes through the true server's database, so the net task-throughput ceiling
(~10k tasks/s, less with PostgreSQL) is unchanged — the relay multiplies fan-out, not row
throughput; suitably-sized tasks remain the operating assumption. Security *improves* at scale:
the upstream key and cert material stop at the relay; leaf nodes only ever hold a per-site key
minted fresh by their relay.

Site-topology autoscaling — the true server publishing per-site client counts under `fill` /
`spread` — is the next step and is not part of this cycle
([`issues/relay-topology.md`](../../issues/relay-topology.md)).

A user who never runs `hs relay` sees no change whatsoever.

## Acceptance criteria (the contract)

R1–R10 and R16–R18 are the seed's R-IDs, kept for traceability and amended only where `develop`
or the zone cycles have moved under them (each amendment is cited in **Clarifications → Drift**).
R11–R15 are **reserved, not carried**: they moved with R16's topology half to
`issues/relay-topology.md` (C1). R19–R21 are new: a prerequisite the ROADMAP sequences "before
relay", and the evidence this cycle owes.

**Delegation core** *(usable against a plain `--forever` server)*

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
  SHALL request work per zone, with demand for each zone it serves held independently upstream.
  *(How, against Z2's demand map, is design question DQ1 — not a cut.)*
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
  `coordinator-resilience-retry-backoff`; that seed is still `unshaped` and unscheduled, so this
  cycle builds it — Drift D7, DQ2.)*
- **R10** — The relay-local database SHALL be private, disposable operational state at a
  dedicated default location; it SHALL be SQLite whatever database provider the user has
  configured (SQLite, PostgreSQL or `timescale`, #71); it SHALL NOT default to (or disturb) the
  user's configured primary database; and pruning (R5) SHALL keep it size-stable across campaigns.

**Site-topology autoscaling** — **R11–R15: not carried.** Moved to
[`issues/relay-topology.md`](../../issues/relay-topology.md) (`shaped`, depends on Z3); the IDs
stay reserved here so the seed's numbering survives.

- **R16** *(local-fleet half; amended)* — Against an upstream that publishes no topology — every
  upstream, this cycle — the relay SHALL maintain its own locally-configured fleet size through
  its launcher; the delegation core SHALL be fully usable without site-topology autoscaling.
  *(The topology half — reconciling a published per-site entry, falling back for a site not in
  policy — moved to `issues/relay-topology.md`.)*

**Security & convention**

- **R17** — The relay SHALL authenticate upstream with the upstream key and TLS configuration
  (supporting shared-cert / `cafile` / fingerprint pinning per the distributed-TLS guidance) and
  SHALL mint a fresh site-local auth key for its own fleet; upstream credentials SHALL NOT be
  exposed to site clients (not on their argv, not in their environment).
- **R18** — CLI additions SHALL update the affected `docs/_include/*.rst` help snippets and the
  `share/` completions in the same commit (repo convention), and `docs/security.rst` SHALL gain
  the relay trust model (two auth domains, TLS pinning across the WAN hop, process-table key
  exposure now confined within each site). The docs SHALL state the non-goals users will meet:
  no throughput gain, no cross-site cancellation or output retrieval, and no site-topology
  autoscaling yet.

**Prerequisite and evidence** *(new)*

- **R19** — WHEN a task row upstream is already complete, a confirmation applied to it SHALL NOT
  change its executing-client attribution: a relay's late confirmation SHALL NOT overwrite the
  leaf attribution its own result wrote. The confirmation write SHALL keep the contended-write
  discipline it follows today (`CONTENDED_RERUNS`, #75). *(From
  `issues/late-confirm-overwrites-attribution.md`, which the ROADMAP sequences "before relay" and
  which is still `unshaped` on `develop` — Drift D5; reproduced in-process during #70's review.
  Rides this cycle per C2.)*
- **R20** — New tests SHALL cover the relay path: durable insert before confirm; no local retry,
  a failure returned upstream and retried there; leaf attribution with the upstream
  `schedule_time` preserved; duplicate and stale returns writing nothing; the R8 bound holding;
  roster forwarding and its removal on disconnect; a multi-zone relay receiving each zone's work
  and only that; operation through an upstream outage with results delivered on reconnection;
  restart durability of completed-but-unreturned results; the upstream-credential fence (R17);
  the relay-local database never touching the configured primary; the late-confirm fence (R19);
  and the relay re-entering on timeout and halting on shutdown. A loopback run of one upstream
  server and two relays, each with site clients, SHALL run a grouped workload that includes a task
  that fails and is retried, to completion, with every task completed once upstream. The existing
  suite SHALL pass.
- **R21** — Before the pre-release, a **multi-node run on a test cluster** SHALL show, within that
  cluster's job limits: one node runs the upstream server; **two node groups act as two sites**,
  each running its own `hs relay` that launches site clients on its group's nodes with a site-local
  key. On a grouped workload with a failing-and-retried task: (a) every task reaches its final
  state once upstream, with group order kept; (b) every completed row names a leaf client of the
  site that ran it, not a relay, and the leaf clients appear upstream attributed to their site;
  (c) the upstream server holds connections for the relays only, not for leaf clients;
  (d) **failover** — one site's relay is stopped mid-run, it is evicted, its unreturned tasks
  revert and complete at the surviving site, and no task is lost; (e) the upstream key appears in
  no site client's argv or environment. Records land under `spec/relay/evidence/` following the
  zone drives' external-wait convention; the run is the foreman's to perform.

## Non-goals (no-gos)

- **Site-topology autoscaling** — seed R11–R15 and R16's topology half: ordered sites with
  `min`/`max`/`init`, `fill`/`spread`, a versioned published topology, per-site liveness and
  accounting, topology-driven scale-down. It needs zone autoscaling (Z3) and lives in
  [`issues/relay-topology.md`](../../issues/relay-topology.md).
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
- **Per-zone differentiated sizing knobs** (unchanged zones non-goal).
- **An implicit "local site."** Local capacity = run a relay beside the server. One code path.
- **General coordinator resilience.** R9 builds the relay's own upstream reconnect and redelivery;
  making ordinary clients and the server survive coordinator blips stays with
  `issues/coordinator-resilience-retry-backoff.md`.
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
  launcher? — **A:** **Ordered sites, no implicit local site** (resolved 2026-08-02). Carried to
  `issues/relay-topology.md` with the rest of the site model.
- **Q:** Relay buffer depth? — **A:** **Bounded, demand-driven** (resolved 2026-08-02). The bound
  derives from the live local fleet, not a user knob; hoarding breaks fill/spread fidelity and
  widens the cancellation/duplicate windows. WAN latency is amortized by large bundles.
- **2026-11-01 (Z2, maintainer alignment memo)** — "A relay counts as one client advertising a
  large size." Z2's dispatch staging was designed with that in mind.

**Ruled at promotion** (maintainer, 2026-11-04):

- **C1 — Scope for the SC26 pre-release.** Shaping offered A (smallest demo relay: one zone per
  relay, restart durability only, no roster), B (A plus the roster) and C (the seed's whole first
  phase), and recommended A as the fit for about 5–6 days. The maintainer ruled: *"option **C**,
  the seed's full first phase (R1–R10 plus R16–R21, including outage ride-through and multi-zone
  relays). That is the target for the pre-release before SC26, even though you judged it a tight
  fit."* Out, because it needs Z3: R11–R15 and R16's topology half, seeded as
  `issues/relay-topology.md` (`shaped`) with its ROADMAP entry (resolved 2026-11-04).
- **C2 — Where R19 lands.** Options were (i) inside this cycle or (ii) its own `fix/` cycle
  first. The maintainer ruled: *"R19 rides inside the relay cycle."* (resolved 2026-11-04).
- **Multi-zone conflict with Z2 is a design question, not a cut** (maintainer, 2026-11-04): the
  plan resolves it — DQ1 below.

**Design questions for `hs-plan`** (plan-level; each must be settled in `PLAN.md`/`TECH.md`, none
narrows a requirement):

- **DQ1 — Multi-zone relays against Z2's demand map (R2).** Z2 keys staging demand
  `client_id → (zone, size)` (Z2 PLAN §2.2), so one relay identity requesting for two zones
  overwrites its own demand, and `dispatcher.forget(client_id)` on its eviction would unstage only
  the last zone it named. Resolve by either **(a)** the relay presenting **one upstream identity
  per zone** it serves (each with its own requests, confirms and liveness), keeping the upstream
  untouched; or **(b)** a **minimal additive change** to the demand map (e.g. keyed by
  `(client_id, zone)`), compatible with Z2 — an ordinary single-zone client behaves exactly as Z2
  specifies, the wire protocol and `PROTOCOL` number do not change, and Z2's tests pass
  unmodified. If (b), R2's "no relay-specific changes" holds because the change is general, not
  relay-aware; `TECH.md` records why it stays inside that line. Either way, failover (R3, R21 (d))
  must revert and unstage every zone the relay held.
- **DQ2 — Outage ride-through (R9).** Nothing to inherit (D7). Decide the reconnect and backoff
  loop, what the relay does with its local fleet while disconnected (keep running, stop pulling),
  and how buffered results are re-presented on reconnection — against the risk that upstream has
  evicted the relay meanwhile, so many returns come back `STALE` by design. Shape it so the
  resilience seed can later lift it rather than duplicate it.
- **DQ3 — Roster semantics against the upstream `HeartMonitor` (R7).** Leaf clients exist
  upstream but never heartbeat it directly. Decide how their liveness is represented (through the
  relay's heartbeat or a dedicated message), that the monitor never evicts a leaf on its own
  (eviction stays the relay's, and a relay eviction disconnects its roster), and that
  `count_connected()` and the rate math stay correct (R6).

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
  §2.2), so a multi-zone relay needs one identity per zone or an additive change (DQ1); when a
  non-default zone loses its last client, its staged pool is unstaged by id, which helps failover;
  and a zone with no client **holds the group** (Z2 R10), so cross-site failover works only when
  a surviving site serves the zone the work is in — R21 runs both sites in a shared zone. Z2's
  TECH on `origin` is behind the approved local head (`b6fcb55`, review cycle 3 approved); only
  phase status and gate records differ.
- **D3 — Z3 (zone autoscaling) is not built and not scheduled before relay.** Z2's Clarification
  (2026-11-01) moved zone R13–R17 to Z3 and allows deferring it to `issues/zone-autoscale.md`.
  The seed's R11–R16 "ride the zones autoscaler rails"; those rails (per-zone pressure, per-zone
  fleet accounting, `{zone}`/`HYPERSHELL_ZONE`) do not exist yet. Hence R11–R15 and R16's
  topology half moved to `issues/relay-topology.md` (C1).
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
  sequences the fix "near-term, before relay"; its seed is `unshaped`. Hence R19, in this cycle
  (C2).
- **D6 — #71 timescale and #75 contended-write recovery.** The upstream `task` table may be a
  TimescaleDB hypertable, where a contended UPDATE can match 0 rows; `confirm_all`, `revert_all`
  and `apply_results` follow `CONTENDED_RERUNS`, and a `DEFERRED` result is held and re-presented
  by the Receiver. Relay adds no upstream task-row write of its own except R19, which keeps the
  pattern; R5 must not treat a deferred return as landed. The relay-local database is pinned to
  SQLite whatever provider is configured (R10).
- **D7 — The resilience seed did not land first.** ROADMAP says "relay R9 is verbatim this
  machinery — build it once here and the relay's upstream connector inherits it", but
  `issues/coordinator-resilience-retry-backoff.md` is `unshaped` and unscheduled. R9's outage
  half is built in this cycle (C1, DQ2).
- **D8 — #74 group order.** The scheduler now chooses the lowest incomplete group atomically, so a
  late lower group runs instead of stranding. The seed's "group consistency is free" note still
  holds and is safer: a relay-local server advances to whichever group upstream releases.
- **D9 — #72 TLS IPv4 dial.** The TLS client dials IPv4 only; the relay's upstream hop inherits
  that (non-goal added). The relay's own listener is the ordinary server listener.
- **D10 — #69 upgrade step and #73.** New columns (R7's site attribution) go through the in-place
  upgrade step: nullable or constant default, no per-column code. #73 only changes how the result
  statements are built; no effect on relay beyond throughput headroom.
- **D11 — Touchpoint correction.** `DISTRIBUTED_ROLES` lives in `src/hypershell/core/logging.py`;
  a new `relay` role (if it writes its own log) is added there. The seed lists it under
  `__init__.py`, and `AGENTS.md` ("Working on this codebase as an agent") reads the same way;
  fixing `AGENTS.md` waits for the plan.
- **D12 — The security-hardening cycle lands between zones and relay.** R17/R18 are planned
  against the tree it leaves; `hs-plan` re-reads `docs/security.rst` and `core/tls.py` at that
  base rather than at `ab16902`.

**Notes for `hs-plan`** (from the seed, updated for drift):

- *Calendar.* The scope is the maintainer's call (C1), made knowing it is tight. Plan phases so
  the delegation core (R1–R6, R8, R10, R17, R19) is reviewable before R7, R9's outage half and
  multi-zone, so review can start early; this orders the work and cuts nothing.
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
  for mixed-version fleets (DQ3).
- *`no_confirm` is incompatible with relay mode* — it disables the orphan recovery R3 leans on.
  Refuse it on the relay CLI, and on the upstream server when relays register.
- *Eviction window.* A relay's unreturned backlog exceeds a leaf client's; consider whether
  upstream `evict_after` (default 600 s) deserves a relay-aware default (R8 bounds the blast
  radius either way). R21's failover leg needs a short eviction setting to fit the job limit. R9's
  ride-through and eviction pull in opposite directions (DQ2).
- *Group consistency is free* (D8).
- *Zones composition.* A dedicated per-relay zone remains a pure configuration pattern (zone name
  = site name), not a mechanism — but see D2 on held groups.

## Related materials

- Seed: [`issues/relay.md`](../../issues/relay.md) (`status: adopted:relay`).
- Carved remainder: [`issues/relay-topology.md`](../../issues/relay-topology.md) (`shaped`;
  seed R11–R15 and R16's topology half; depends on Z3 and this cycle).
- Dependencies: [`spec/zone-queue-primitive/`](../zone-queue-primitive/GOAL.md) (Z1) and
  [`spec/zone-dispatch/`](../zone-dispatch/GOAL.md) (Z2), both approved, landing as 2.9.0a4.
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
  surface; unchanged unless DQ1 picks (b)); `src/hypershell/server.py` (`Confirm` for R19,
  `HeartMonitor` for the roster; `Receiver` unchanged); `src/hypershell/data/model.py`
  (`Task.confirm_all` for R19, `Client` site attribution); `src/hypershell/core/heartbeat.py`
  (optional site/roster fields); `src/hypershell/data/core.py` (relay-local DB binding);
  `src/hypershell/core/config.py` (`[relay]` section); `src/hypershell/cluster/remote.py`
  (launcher reuse); `docs/security.rst`, `docs/_include/*.rst`, `share/` completions.
- Prior art: **glideinWMS** (per-site frontend/factory feeding a central pool), **HTCondor**
  flocking + glideins, **Balsam** multi-site daemons (closest DOE analog — site agents pull from
  a central service and drive the local batch system), Slurm federation, hierarchical
  work-stealing schedulers.
