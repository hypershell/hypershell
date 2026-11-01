---
status: shaped
kind: feature
appetite: big
lane: public
shaped-as: zone
---

# Zone-aware demand-driven dispatch on a lighter queue primitive

> **Shaped candidate — not yet a contract.** The shaping conversation already happened with a
> human: the clarifications below are dated and resolved, the appetite and non-goals are agreed,
> and the R-IDs are drafted. What has *not* happened is acceptance into a cycle — `/hs-feature`
> does that, creating the branch and `spec/{slug}/GOAL.md`, and it is `hs-review` that grades the
> result. Until then nothing here is graded and nothing here is locked.
>
> Promotion should re-confirm the scope against current `develop` and cite anything that has
> drifted since this was written, then adopt it largely as written — do **not** re-litigate the
> resolved clarifications.

- **slug:** zone
- **kind:** feature
- **appetite:** big  ·  *New `Zone` table + `task.zone` column, a client-advertised dispatch
  request, a queue-primitive swap, and zone-aware autoscaling. **R1 (the `queue.Queue` swap) is a
  near-zero-change, independently-shippable sub-change** that de-risks the rest and should be its own
  first phase; zone-aware autoscaling (R13–R17) is the final phase, riding the same `group`-scoped
  rails the autoscaler already uses.*

## Problem

HyperShell's founding strength is **extreme-scale homogeneous bag-of-tasks**: one flat list, an
elastic fleet of interchangeable clients, a single shared hand-off. That case must never regress.
But real campaigns are rarely one resource shape end to end — a screening pipeline has CPU steps,
GPU steps, and high-memory steps, and today HyperShell has no way to say "run *these* tasks on
*those* clients." That gap is exactly what pushes users toward one-task-per-`sbatch` frameworks,
whose one real advantage is per-task resource targeting (at the cost of melting the scheduler).

Two concrete limitations block closing that gap:

1. **Dispatch is anonymous and uniformly sized.** Every client pulls from one shared `scheduled`
   queue, so the server cannot steer a task to a client that fits it, and bundle size is a single
   server-side constant (`hsx` launches all clients with the same `bundlesize`) — a 4-core client
   and a 112-core client are handed the same amount of work.
2. **The managed queue primitive doesn't scale by count.** The four managed queues are
   `multiprocessing.JoinableQueue`s living behind the `BaseManager`. Because every access is
   already a proxy RPC, that class's cross-process machinery — an OS pipe (file descriptors) plus a
   background feeder thread per queue — is pure overhead in this configuration. Invisible at four
   queues; fatal the moment you want per-client or per-profile queues, because it multiplies
   feeder threads and file descriptors by the client count.

We want per-resource-type routing and per-client right-sizing **without** conceding the homogeneous
founding case and **without** bloating the manager's footprint.

## Outcome / vision

A user launches a client with a **user-defined `--zone` label** and its own `--bundlesize`; the
client asks the server for work, advertising its identity, its zone, and how much it can take; the
scheduler serves it **only tasks whose zone matches**, **sized to that client's request**, and
**delivered to just that client**. Tasks are stamped with a zone at submit time
(`hs submit --zone=gpu …`). Zones are arbitrary user text (`cpu`, `gpu`, `small`, `large`, `bob`)
stored in a dedicated **`Zone` table mapping a minted UUID to the name — mirroring the existing
`Source` table** — referenced by UUID on each task and **dereferenced to the name for presentation
only** (`hs list` / `hs info`). Label→id resolution is an idempotent, cached get-or-create, so the
submit side and the client side converge on one stable id for a given name.

Underneath, the managed queues move off `multiprocessing.JoinableQueue` to the lighter
threading-`queue.Queue` the manager is designed for (same `put`/`get`/`join`/`task_done` behavior,
no per-queue pipe or feeder thread), so the per-queue footprint drops toward zero and dispatch can
be made per-client without a resource explosion.

Crucially, the **default single-zone path is unchanged**: an unzoned submission and the uniform
fleet `hsx` launches behave exactly as today, at today's throughput and footprint. And **zone is
placement, not dependency** — task-group gating stays global across zones, so ordering semantics do
not change at all.

Under autoscaling, the elastic fleet grows **per zone**: the autoscaler reads per-zone task pressure
(scoped to the current group, so a zone gated behind the current phase registers none) and launches
clients into each active zone through the user's own launcher — HyperShell hands the shim the target
zone as a `{zone}` template substitution and a `HYPERSHELL_ZONE` environment variable, and the shim
maps it to the right resource (e.g. a `case` on the zone selecting a Slurm partition). Idle clients
in a passed phase self-retire, so a heterogeneous multi-phase campaign provisions each resource type
only while its phase is live — all from one server.

This deliberately stops at **single-instance, in-one-server** zone routing, the queue-primitive
reduction, and zone-aware autoscaling with **uniform** sizing parameters applied per zone.
Multi-server / one-cluster-per-zone topologies, per-zone *differentiated* sizing parameters, and
turnkey resource pinning are separate follow-ups — see Non-goals.

## Acceptance criteria (the contract)

- **R1** — The manager's task queues SHALL be backed by threading `queue.Queue` rather than
  `multiprocessing.JoinableQueue`, preserving the existing `put`/`get`/`join`/`task_done` behavior
  over the `BaseManager`, and SHALL NOT allocate a per-queue OS pipe or spawn a per-queue feeder
  thread. This SHALL be landable on its own, ahead of the dispatch/zone work.
- **R2** — The default configuration — an unzoned submission consumed by the uniform fleet `hsx`
  launches — SHALL behave as it does today, with no throughput or manager-footprint regression.
- **R3** — WHEN a client requests work, it SHALL advertise its identity and its own bundle size, and
  the scheduler SHALL size the returned bundle from the **requesting client's** advertised value
  rather than a single server-wide constant.
- **R4** — The scheduler SHALL deliver each work bundle to the specific client that requested it
  (targeted delivery); a client SHALL NOT receive tasks it did not request.
- **R5** — The system SHALL accept an arbitrary user-defined text `--zone` label both on submission
  (stamped onto tasks) and on a client (declaring the tasks it will accept).
- **R6** — Zone labels SHALL be stored in a dedicated `Zone` table that maps a minted UUID to the
  text name (mirroring `Source`), and each task SHALL reference its zone by UUID via a `zone` column
  on `task`.
- **R7** — Zone name→id resolution SHALL be an idempotent, cached get-or-create, so repeated
  references to the same label (submit-side stamping and client-side request matching) resolve to
  one stable UUID without redundant rows or writes.
- **R8** — WHEN a zoned client requests work, the scheduler SHALL serve it only tasks whose zone
  matches the client's zone; zone SHALL constrain task **selection** only.
- **R9** — Zone SHALL NOT affect task-group dependency gating: `current_group` / `increment_group`
  remain global across zones, so group ordering is preserved exactly as today.
- **R10** — Tasks submitted without `--zone` SHALL belong to a reserved **default zone** (mirroring
  the reserved `Source` ids), and an unzoned client SHALL run them; existing behavior and existing
  databases SHALL continue to work.
- **R11** — User-facing presentation (`hs list`, `hs info`, and equivalents) SHALL display the zone
  by **name**, resolved from the `Zone` table; the raw UUID SHALL NOT be required in user output.
- **R12** — The CLI change SHALL update the affected `docs/_include/*.rst` help snippets and the
  shell completions under `share/` in the same commit (per repo convention), documenting `--zone`,
  per-client bundle size, and the autoscaler's `{zone}` / `HYPERSHELL_ZONE` launcher exposure
  (including a worked launcher-shim example).
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
  user-supplied launcher shim can route the launch to the matching resource.
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

- **Q:** Name for the concept — `partition`, `profile`, or `zone`? — **A:** **zone** (resolved
  2026-07-21). `partition` is already taken internally by the SQLite DB-rotation feature (`part:N`);
  `zone` also dodges Slurm's "partition" and reads naturally as a placement domain.
- **Q:** Are the queue swap (R1) and the zone/dispatch feature separable? — **A:** Yes, but handled
  together deliberately: the dispatch/zone feature is only worth building *on* the lighter primitive,
  so R1 lands first as its own phase and the rest builds on it (resolved 2026-07-21).
- **Q:** Dispatch mechanism — a persistent per-client "inbox" queue (client puts a request on a
  shared request queue, server puts a matched bundle on that client's own queue) versus a
  synchronous `request_work(uuid, zone, bundlesize)` RPC that returns the bundle by value on the
  request's own connection? — **A:** Deferred to `TECH.md`, but the shaping conversation established
  the trade: R1's `queue.Queue` swap makes per-client inbox queues cheap (no feeder/FD per client),
  **and** a synchronous request-return RPC needs *no* per-client server object at all — the reply
  rides back on the connection the request arrived on — which also removes per-client inbox teardown
  and per-inbox shutdown-sentinel fan-out. **Recommendation: the request-return RPC**; final call in
  `TECH.md`.
- **Note (for `hs-plan`):** `Zone` mirrors `Source` in `data/model.py` — `id` (minted via `uuid()`),
  a unique `name`, `created`; classmethods paralleling `from_id`/`new`/`reserved`; a reserved
  default-zone id in the spirit of `DIRECT_SOURCE_ID`/`STDIN_SOURCE_ID`. `task` gains a `zone`
  column beside `source`. Adding the table + column is a schema change like the one that introduced
  `Source` (see the source-schema research under `spec/cli-cluster-restart-repeat-update/`); confirm
  the `initdb` / back-compat path (nullable-with-default so old databases still open).
- **Note (for `hs-plan`):** every path is manager-mediated — `LocalCluster` binds and connects
  `('localhost', port)` (`cluster/local.py`), so there is no fork-shared-queue path that would
  require `multiprocessing.Queue`. R1 is safe on local and remote alike. `queue.Queue` retains the
  `join()`/`task_done()` the submit-flush drain relies on.
- **Note (for `hs-plan`):** the touched code — `server.py` (`Scheduler`), `client.py` (client FSM),
  `core/queue.py` (`QueueServer`) — is the load-bearing, **untested** concurrency core flagged in
  `AGENTS.md`. Add FSM/queue tests alongside (ideally before) the dispatch change, and keep the
  finite-timeout, re-enter discipline on every blocking queue/request op.
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
  `hs client --zone=` from the one injected zone value to prevent drift.

## Related materials

- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/data/model.py`
  (`Source` as the pattern to mirror; new `Zone` model; `Task` + new `zone` column; `Task.next` /
  `select_new` / `select_failed` gain a zone filter — selection only, not `increment_group` /
  `current_group`); `src/hypershell/server.py` (`Scheduler.load_bundle`, the demand-driven request
  loop); `src/hypershell/client.py` (client FSM: advertise identity/zone/bundlesize, receive a
  bundle); `src/hypershell/core/queue.py` (`QueueServer` — the `queue.Queue` swap and the
  request/dispatch surface); `src/hypershell/submit.py` and the `task`/`cluster` CLI (`--zone`);
  `src/hypershell/cluster/*` (per-client `--zone`/`--bundlesize` propagation);
  `src/hypershell/cluster/remote.py` (`AutoScaler` — per-zone pressure via a `zone=` filter parallel
  to the existing `group=`, dynamic zone discovery, per-zone fleet accounting, and `{zone}` /
  `HYPERSHELL_ZONE` launcher injection at the launch `Popen`).
- Precedent: the `Source` name↔UUID table introduced in `spec/cli-cluster-restart-repeat-update/`
  (`research/02-source-schema.md`, `research/07-uuidv7-source-ids.md`,
  `research/08-presentation-source-display.md`) — the closest existing analog for stamping tasks
  with a minted id and dereferencing it for display.
- Docs & assets: task/CLI reference under `docs/`, `docs/_include/*.rst` help snippets, `share/`
  completions and man pages.
- Prior art / motivation: FireWorks `category` (worker pulls only its category — placement
  orthogonal to the DAG), Globus Compute resource-specific endpoints, and HTCondor ClassAd
  matchmaking (requirements/rank) — the well-trodden shape of "route work to the resource that
  fits." The `queue.Queue`-vs-`multiprocessing.Queue` point mirrors what `SyncManager.Queue()`
  already does.
