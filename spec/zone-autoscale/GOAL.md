# GOAL — Zone-aware autoscaling (zone, cycle 3)

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** zone-autoscale
- **kind:** feature
- **appetite:** big  ·  *Taken from the `zone` seed's `big`; this cycle carries the seed's R13–R17
  (the seed's R-ID is given in brackets on each criterion below, as Z2 did). Per-zone pressure and
  fleet accounting, zone discovery, and the launcher exposure of the target zone, on the
  `group`-scoped rails the autoscaler already uses, plus the autoscaler docs Z2 moved here. It
  touches `AutoScaler`, which the relay's site fleets are built on, so `hs-plan` should do its
  research pass. It ships in the same pre-release as `relay-topology`, which builds on it.*

## Problem

Zones route work from one server: a task submitted with `--zone` runs only on a client of that
zone ([`spec/zone-dispatch/`](../zone-dispatch/GOAL.md), #78). The elastic fleet does not follow.
An autoscaling `hs cluster` takes one `--zone`, for its submission and for every client it launches
(`src/hypershell/cluster/__init__.py:85`, `:156-157`; one `ClientLaunch` serves every scale-up,
`src/hypershell/cluster/remote.py:816-823`). Its pressure and remaining-task counts are scoped to
the current group but span every zone (`AutoScaler.check_fixed` and `check_dynamic`,
`src/hypershell/cluster/remote.py:471-473`, `:491-494`; `Task.count_remaining` and
`Task.task_pressure` take no zone, `src/hypershell/data/model.py:841`, `:1189`), and its registered
count spans every zone too (`Client.count_connected`, `src/hypershell/data/model.py:1313`). So a
heterogeneous campaign — CPU, GPU and high-memory steps — cannot grow each resource type's fleet
from one server, and a zoned autoscaling cluster scales on other zones' work as well as its own.

There is a correctness edge as well. A zone whose current-group tasks have no client of that zone
holds the group open (`spec/zone-dispatch/GOAL.md:197`, from Z2's R10). Outside autoscaling that is
expected: the user starts the clients. Under autoscaling today it is a stall the autoscaler cannot
see, because it launches only into its one zone.

## Outcome / vision

Under autoscaling, the elastic fleet grows **per zone**: the autoscaler reads per-zone task pressure
(scoped to the current group, so a zone gated behind the current phase registers none) and launches
clients into each active zone through the user's own launcher — HyperShell hands the shim the target
zone as a `{zone}` template substitution and a `HYPERSHELL_ZONE` environment variable, and the shim
maps it to the right resource (e.g. a `case` on the zone selecting a Slurm partition). Idle clients
in a passed phase self-retire, so a heterogeneous multi-phase campaign provisions each resource type
only while its phase is live — all from one server.

The default single-zone case behaves exactly as it does today. This deliberately stops at
zone-aware autoscaling with **uniform** sizing parameters applied per zone; per-zone
*differentiated* sizing and multi-server and site topologies are separate follow-ups — see
Non-goals.

## Acceptance criteria (the contract)

- **R1** [seed R13] — WHILE autoscaling is active, the autoscaler SHALL compute task pressure **per
  zone**, scoped to the current task group (extending the existing group-scoped pressure signal with
  a zone filter), and account for each zone's launched/registered client fleet independently.
- **R2** [seed R14] — The autoscaler SHALL discover the zones to scale dynamically from tasks with
  eligible (current-group) work; IF a zone's work is gated behind the current group, THEN it SHALL
  register no pressure and SHALL NOT be scaled up, so resources for a not-yet-active phase are never
  provisioned.
- **R3** [seed R15] — WHEN the autoscaler launches a client for a zone, it SHALL make the target zone
  available to the launcher command both as a `{zone}` command substitution and as a
  `HYPERSHELL_ZONE` environment variable in the launched process (mirroring the dual exposure of
  `TASK_SLOT`), so a user-supplied launcher shim can route the launch to the matching resource. The
  autoscaler SHALL also append `--zone` to the launched client command (alignment Q4, 2026-11-01).
  The affected `docs/_include/*.rst` help snippets and `share/` completions SHALL document `{zone}` /
  `HYPERSHELL_ZONE`, with a worked launcher-shim example (moved here from the seed's R12,
  2026-11-02).
- **R4** [seed R16] — The autoscaler's sizing parameters (max-size, min-size, init-size, factor,
  timeout, and the like) SHALL be interpreted **per zone with uniform values** — the same configured
  value applied independently to each active zone. For the default single-zone case this SHALL be
  identical to today's behavior.
- **R5** [seed R17] — Scale-down SHALL remain automatic and per-zone via the existing idle-exit: a
  client whose zone has no current eligible work idles out and retires, with no zone-specific
  termination logic.

## Non-goals (no-gos)

- **Multi-server / one-cluster-per-zone topology.** This cycle is a single server instance. Running
  a separate `hsx --zone=…` per resource type against a shared database — and the lifecycle
  re-scoping it would demand (`revert_interrupted`, termination, `latest_server` all assume a single
  DB owner) — is explicitly out of scope here.
- **Site topology, sites, and any relay behaviour change.** Publishing a desired topology, per-site
  sizing, `fill` / `spread`, and relay reconciliation are `issues/relay-topology.md`, the next cycle.
  Z3 builds none of it (see the 2026-11-12 note below).
- **Per-zone *differentiated* sizing parameters.** The autoscale sizing knobs (max-size, min-size,
  factor, timeout, …) are applied per zone but with **uniform** values in this work; distinct
  per-zone values (e.g. `--max-size gpu=4 cpu=100`) are a later addition.
- **Turnkey resource pinning.** Zone matches a client to tasks; it does not detect node topology,
  set `OMP_*`/`taskset`/`CUDA_VISIBLE_DEVICES`, or validate that a client fits its tasks. That is
  the `TASK_SLOT` line of work (see `spec/task-slot/`).
- **Arbitrary DAGs / per-zone phase progression.** Group stays a single global, serial phase
  pointer; no per-zone or per-pipeline group counters are introduced.
- **Zone as a dependency mechanism.** Zone is a placement/label axis only; it does not express
  ordering or "run after" relationships.
- **Changing the auth/TLS wire model.**
- **A launcher that keeps failing.** Ending `hs cluster` when every launched client fails is
  `issues/cluster-waits-when-every-client-fails.md`; per-zone scaling does not fix it.

## Clarifications

The clarifications that governed the seed's R1–R12 (the queue primitive, the dispatch mechanism,
staging, the advertised bundle size, older clients) were carried into the Z1 and Z2 GOALs and are
recorded there. Those that bear on this cycle, carried from the seed unchanged:

- **Q:** Name for the concept — `partition`, `profile`, or `zone`? — **A:** **zone** (resolved
  2026-07-21). `partition` is already taken internally by the SQLite DB-rotation feature (`part:N`);
  `zone` also dodges Slurm's "partition" and reads naturally as a placement domain.
- **Q:** Are autoscale sizing parameters per-zone or global? — **A:** Per zone with **uniform**
  values for now — each active zone scales independently under the same configured knobs; per-zone
  *differentiated* values are deferred (resolved 2026-07-21).
- **2026-11-01 — Zone ships in three cycles:** Z1 = seed R1 (`zone-queue-primitive`, #76), Z2 = seed
  R2–R12 (`zone-dispatch`, #78), Z3 = seed R13–R17, this GOAL (Geoffrey, alignment cut).
- **2026-11-01 — Autoscaled client zone** (Geoffrey, alignment Q4): the autoscaler appends
  `--zone` to the launched client command (R3). This superseded the seed's earlier shim-convention
  note, in which the shim alone was to drive `hs client --zone=`.
- **2026-11-02 — Seed R12's autoscaler docs move to Z3** (`spec/zone-dispatch/GOAL.md`,
  Clarifications). They document R3 (`{zone}` / `HYPERSHELL_ZONE`), so they ship with it.
- **2026-11-12 — Promoted as `zone-autoscale`** (foreman, `hs-feature`). The seed was `shaped`; its
  R13–R17 are adopted as written, renumbered R1–R5 with the seed ID in brackets (Z2's convention), so
  `issues/relay-topology.md`'s citations of "Z3, R13–R17" resolve through the brackets. The seed's
  R1–R12 shipped in Z1 and Z2 and are not part of this contract. Two non-goals added, both naming a
  neighbouring seed's boundary (relay-topology, all-clients-fail). No R-ID changed.
- **2026-11-12 — Drift since the seed was written** (re-confirmed against `develop` `1ea9145`; scope
  holds: the autoscaler is still single-zone, and nothing yet exposes `{zone}` or `HYPERSHELL_ZONE`):
  - The client launch moved out of `remote.py` into `src/hypershell/cluster/launch.py` with the
    authkey hardening (#80). The seed's "autoscaler appends `--zone`" site
    (`cluster/remote.py:846-847`) is now `client_options` (`cluster/launch.py:347-348`), reached
    through `client_launch()` (`cluster/launch.py:126-197`) from `AutoScalingCluster`
    (`cluster/remote.py:816-823`). The seed's "launch `Popen`, which already passes an `env` dict" is
    now `ClientLaunch.start` (`cluster/launch.py:83-96`; the environment is built at `:93` from
    `ClientLaunch.env`), called by `AutoScaler.scale` (`cluster/remote.py:524-531`).
  - Pressure and counts: seed `cluster/remote.py:486-489` and `:507-511` are now `check_fixed`
    (`:467-485`, counts at `:471-473`) and `check_dynamic` (`:487-522`, counts and pressure at
    `:491-494`). Seed `data/model.py:821` (`count_remaining`) is now `:841`; `:1155` (`task_pressure`)
    is now `:1189`. Its helpers `time_to_completion(group=)` (`:1181`) and `avg_duration()` (`:1163`)
    take no zone; Z2 added the zone predicate `Task._filter_zone` (`:727`).
  - Registered count: `Client.count_connected()` (`data/model.py:1313`) takes no zone and the
    `client` table has no zone column. Z2's dispatcher counts a zone's clients in process
    (`Dispatcher.clients(zone)`, `src/hypershell/core/dispatch.py:412`); relay P9 already uses it
    for the same purpose (`spec/relay/TECH.md`, P9 amendments).
  - New consumer: the relay (#82) builds its site on `AutoScalingCluster` with a fixed policy
    (`src/hypershell/relay/site.py:104-108`) and each further zone's fleet as a fixed-policy
    `AutoScalerThread` over `zone_launch` (`relay/site.py:121-150`); `end_site_clients` reaps
    through `autoscaler.machine.clients` (`relay/site.py:292`). Z3 changes the classes these use.
- **Note (for `hs-plan`, from Z2):** a zone whose current-group tasks have no client of that zone
  holds the group open (`spec/zone-dispatch/GOAL.md:197`, from Z2's R10). Under autoscaling, R2 and
  R4 close it: R2 discovers that zone from its current-group work, and R4 gives every active zone the
  same rules today's single fleet has, including "no clients launched and tasks remaining → launch
  one" (`check_fixed` and `check_dynamic`, `cluster/remote.py:481-483`, `:517-519`). So no active zone
  is left without a client while max-size allows one. Outside autoscaling it remains expected
  behaviour.
- **Note (for `hs-plan`, foreman, 2026-11-12): factor demand from actuation.** The next cycle,
  `issues/relay-topology.md`, builds directly on this one and ships with it in one pre-release: under
  autoscaling with sites, the server stops launching and publishes a versioned per-site, per-zone
  desired topology, and each relay reconciles its own entry by launching clients through its own
  zone-specific launcher. So Z3 SHOULD factor the autoscaler so that per-zone **demand** — the
  desired client count per zone, from the group- and zone-scoped pressure signal (R1, R2) under the
  uniform sizing knobs (R4) — is computed separately from **actuation** — launching a client for a
  zone through the launcher with `{zone}`, `HYPERSHELL_ZONE` and the appended `--zone` (R3). The
  local launch is one actuator; `relay-topology` adds a second on the server (publish, no launch) and
  reuses the per-zone launch path inside the relay (today `relay/site.py::zone_launch`, which already
  differs from the cluster's launch only in `--zone`). This is a design note, not a requirement and
  not a scope change: Z3 builds no topology, site or relay behaviour.
- **Note (for `hs-plan`): relay site fleets must not change.** A relay's site cluster is an
  `AutoScalingCluster` over the relay-local database, whose tasks carry the zones pulled upstream,
  and its further zones already have their own fixed fleets (drift above). If zone discovery (R2)
  ran there unchanged, a relay would launch a second fleet for zones it already serves. Keep the
  relay's fixed per-zone fleets behaving as they do (the relay's own tests stay green, with no
  `relay/` behaviour change); how — an explicit zone set, discovery under some policies only, or
  another seam — is the plan's call.
- **Note (for `hs-plan`): R2 and R4 at the empty edge.** Today init-size and min-size launch clients
  whether or not tasks remain (min-size is checked first, `cluster/remote.py:476`, `:499`). Per zone, R2 forbids
  provisioning a gated zone and R4 requires the single-zone case to be identical to today, including
  a cluster started before any task arrives. The plan must satisfy both; the reading this GOAL
  assumes is that the cluster's own zone (its `--zone`, else the default zone) keeps today's
  init-size and min-size behaviour, and every other zone is sized from its current-group work only.
  Confirm it at the plan gate.
- **Note (for `hs-plan`):** `{zone}` substitutes into the launcher command and its arguments only,
  never into the client's own task template (`-t`, whose `{}` is the task's argument). Pressure
  today divides by one global `avg_duration()`; whether the per-zone signal needs a per-zone
  duration is the plan's call under R1.

## Related materials

- Seed: [`issues/zone.md`](../../issues/zone.md) — this cycle carries its R13–R17.
- Previous cycles: [`spec/zone-queue-primitive/`](../zone-queue-primitive/GOAL.md) (Z1, #76),
  [`spec/zone-dispatch/`](../zone-dispatch/GOAL.md) (Z2, #78).
- Next cycle, built on this one: [`issues/relay-topology.md`](../../issues/relay-topology.md).
- Relay delegation core: [`spec/relay/`](../relay/GOAL.md) (#82), especially `TECH.md` P9 (multi-zone
  relays and per-zone fleets).
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/cluster/remote.py`
  (`AutoScaler`, `AutoScalerThread`, `AutoScalingCluster`); `src/hypershell/cluster/launch.py`
  (`ClientLaunch`, `client_launch`, `client_options`); `src/hypershell/data/model.py`
  (`Task.count_remaining`, `Task.task_pressure` and helpers, an active-zones query);
  `src/hypershell/core/dispatch.py` (`Dispatcher.clients`); `src/hypershell/cluster/__init__.py`
  (`--zone` under `--autoscaling`); `src/hypershell/relay/site.py` (must keep working).
- Docs & assets: the autoscaling section of the cluster docs, `docs/_include/cluster_*.rst`,
  `docs/_include/config_param_ref.rst`, `share/` completions and man pages.
- Neighbouring seed: `issues/cluster-waits-when-every-client-fails.md`.
- Prior art: FireWorks `category`, Globus Compute resource-specific endpoints, HTCondor ClassAd
  matchmaking.
