---
status: shaped
kind: feature
appetite: big
lane: public
shaped-as: zone
---

# Zone-aware autoscaling

> **Shaped candidate — not yet a contract.** The shaping conversation already happened with a
> human: the clarifications below are dated and resolved, the appetite and non-goals are agreed,
> and the R-IDs are drafted. What has *not* happened is acceptance into a cycle — `/hs-feature`
> does that, creating the branch and `spec/{slug}/GOAL.md`, and it is `hs-review` that grades the
> result. Until then nothing here is graded and nothing here is locked.
>
> Promotion should re-confirm the scope against current `develop` and cite anything that has
> drifted since this was written, then adopt it largely as written — do **not** re-litigate the
> resolved clarifications.
>
> **Two of the three cycles have shipped.** Zone was cut into three cycles (2026-11-01). Z1, the
> seed's R1 (the in-process queue primitive), landed as
> [`spec/zone-queue-primitive/`](../spec/zone-queue-primitive/GOAL.md) (#76). Z2, the seed's R2–R12
> (demand-driven dispatch, per-client bundle size, the `Zone` table and `--zone`), landed as
> [`spec/zone-dispatch/`](../spec/zone-dispatch/GOAL.md) (#78). Those records hold what shipped and
> why; R1–R12 are retired from this seed. What remains is Z3, R13–R17, below.

- **slug:** zone
- **kind:** feature
- **appetite:** big  ·  *The third of zone's three cycles: per-zone pressure and fleet accounting,
  zone discovery, and the launcher exposure of the target zone, on the same `group`-scoped rails
  the autoscaler already uses. It also carries the autoscaler docs that Z2 moved here (see
  Clarifications, 2026-11-02).*

## Problem

Zones route work from one server: a task submitted with `--zone` runs only on a client of that
zone (`spec/zone-dispatch/`). The elastic fleet does not follow. An autoscaling `hs cluster` takes
one `--zone`, for its submission and every client it launches
(`src/hypershell/cluster/__init__.py:85`, `src/hypershell/cluster/remote.py:846-847`). Its pressure
and remaining-task counts are scoped to the current group but span every zone
(`src/hypershell/cluster/remote.py:486-489`, `:507-511`; `Task.count_remaining` and
`Task.task_pressure` take no zone, `src/hypershell/data/model.py:821`, `:1155`). So a heterogeneous
campaign — CPU, GPU and high-memory steps — cannot grow each resource type's fleet from one server,
and a zoned autoscaling cluster scales on other zones' work as well as its own.

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
*differentiated* sizing and multi-server topologies are separate follow-ups — see Non-goals.

## Acceptance criteria (the contract)

R1–R12 shipped in Z1 and Z2 and are retired; the numbering is kept so citations elsewhere
(`issues/relay.md`, `spec/zone-dispatch/GOAL.md`) still resolve.

- **R13** — WHILE autoscaling is active, the autoscaler SHALL compute task pressure **per zone**,
  scoped to the current task group (extending the existing group-scoped pressure signal with a zone
  filter), and account for each zone's launched/registered client fleet independently.
- **R14** — The autoscaler SHALL discover the zones to scale dynamically from tasks with eligible
  (current-group) work; IF a zone's work is gated behind the current group, THEN it SHALL register
  no pressure and SHALL NOT be scaled up, so resources for a not-yet-active phase are never
  provisioned.
- **R15** — WHEN the autoscaler launches a client for a zone, it SHALL make the target zone available
  to the launcher command both as a `{zone}` command substitution and as a `HYPERSHELL_ZONE`
  environment variable in the launched process (mirroring the dual exposure of `TASK_SLOT`), so a
  user-supplied launcher shim can route the launch to the matching resource. The autoscaler SHALL
  also append `--zone` to the launched client command (alignment Q4, 2026-11-01). The affected
  `docs/_include/*.rst` help snippets and `share/` completions SHALL document `{zone}` /
  `HYPERSHELL_ZONE`, with a worked launcher-shim example (moved here from the seed's R12,
  2026-11-02).
- **R16** — The autoscaler's sizing parameters (max-size, min-size, init-size, factor, timeout, and
  the like) SHALL be interpreted **per zone with uniform values** — the same configured value applied
  independently to each active zone. For the default single-zone case this SHALL be identical to
  today's behavior.
- **R17** — Scale-down SHALL remain automatic and per-zone via the existing idle-exit: a client whose
  zone has no current eligible work idles out and retires, with no zone-specific termination logic.

## Non-goals (no-gos)

- **Multi-server / one-cluster-per-zone topology.** This cycle is a single server instance. Running
  a separate `hsx --zone=…` per resource type against a shared database — and the lifecycle
  re-scoping it would demand (`revert_interrupted`, termination, `latest_server` all assume a single
  DB owner) — is explicitly out of scope here.
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

## Clarifications

The clarifications that governed R1–R12 (the queue primitive, the dispatch mechanism, staging, the
advertised bundle size, older clients) were carried into the Z1 and Z2 GOALs and are recorded
there. Those that bear on Z3:

- **Q:** Name for the concept — `partition`, `profile`, or `zone`? — **A:** **zone** (resolved
  2026-07-21). `partition` is already taken internally by the SQLite DB-rotation feature (`part:N`);
  `zone` also dodges Slurm's "partition" and reads naturally as a placement domain.
- **Q:** Are autoscale sizing parameters per-zone or global? — **A:** Per zone with **uniform**
  values for now — each active zone scales independently under the same configured knobs; per-zone
  *differentiated* values are deferred (resolved 2026-07-21).
- **Note (for `hs-plan`):** zone-aware autoscaling rides rails that already exist — the autoscaler
  already scopes its signal to the current group via `Task.task_pressure(factor, group=…)` and
  `Task.count_remaining(group=…)` (`cluster/remote.py`, `AutoScaler.check`). The core change is a
  parallel `zone=` filter on those, a small `active_zones(group=current)` discovery query, per-zone
  fleet accounting, and `{zone}` / `HYPERSHELL_ZONE` injection at the launch `Popen` (which already
  passes an `env` dict). The autoscaler cannot inject the client's own `--zone` (the launch runs the
  user's shim, which owns the `hs client` argv — building it would hit the "no shared client-argv
  builder" footgun), so the documented shim convention drives *both* the resource routing and
  `hs client --zone=` from the one injected zone value to prevent drift. *(Superseded 2026-11-01 by
  alignment Q4 below: the autoscaler appends `--zone`.)*
- **2026-11-01 — Zone ships in three cycles:** Z1 = R1 (`zone-queue-primitive`), Z2 = R2–R12,
  Z3 = R13–R17 (Geoffrey, alignment cut).
- **2026-11-01 — Autoscaled client zone** (Geoffrey, alignment Q4): the autoscaler appends
  `--zone` to the launched client command. This supersedes the earlier shim-convention note above.
  Z2 already does this for the single zone an autoscaling cluster is given
  (`src/hypershell/cluster/remote.py:846-847`); Z3 does it per launched zone.
- **2026-11-02 — Seed R12's autoscaler docs move to Z3** (`spec/zone-dispatch/GOAL.md`,
  Clarifications). They document R15 (`{zone}` / `HYPERSHELL_ZONE`), so they ship with it; R15
  above carries them.
- **Note (for `hs-plan`, from Z2):** a zone whose current-group tasks have no client of that zone
  holds the group open (`spec/zone-dispatch/GOAL.md`, notes). Per-zone scaling is what closes that
  gap under autoscaling; outside autoscaling it remains expected behaviour.

## Related materials

- Shipped: [`spec/zone-queue-primitive/`](../spec/zone-queue-primitive/GOAL.md) (Z1, #76) and
  [`spec/zone-dispatch/`](../spec/zone-dispatch/GOAL.md) (Z2, #78).
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/cluster/remote.py`
  (`AutoScaler` — per-zone pressure via a `zone=` filter parallel to the existing `group=`, dynamic
  zone discovery, per-zone fleet accounting, and `{zone}` / `HYPERSHELL_ZONE` launcher injection at
  the launch `Popen`; `AutoScalingCluster` — the launched client command);
  `src/hypershell/data/model.py` (`Task.count_remaining`, `Task.task_pressure` and their helpers
  gain a zone filter; an active-zones query); `src/hypershell/cluster/__init__.py` (`--zone` under
  `--autoscaling`).
- Docs & assets: the autoscaling section of the cluster docs, `docs/_include/*.rst` help snippets,
  `share/` completions and man pages.
- Prior art / motivation: FireWorks `category` (worker pulls only its category — placement
  orthogonal to the DAG), Globus Compute resource-specific endpoints, and HTCondor ClassAd
  matchmaking (requirements/rank) — the well-trodden shape of "route work to the resource that
  fits."
