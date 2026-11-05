# GOAL — Zone-aware, demand-driven dispatch (zone, cycle 2)

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** zone-dispatch
- **kind:** feature
- **appetite:** big  ·  *Taken from the `zone` seed's `big`; this cycle carries the seed's R2–R12
  (the seed's R-ID is given in brackets on each criterion below). Target: about 3–3.5 days, shipping
  in 2.9.0a3 or a4. It touches the untested concurrency core (`server.py` `Scheduler`, `client.py`,
  `core/queue.py`), adds a table and a column, and changes the dispatch wire, so `hs-plan` should do
  its research pass. Fourteen criteria trips the size circuit-breaker; criteria marked
  **(deferrable)** are the ones that move to Z3 if the cycle overruns, and the rest are
  load-bearing.*

## Problem

The `zone` seed's Problem 1 still stands: **dispatch is anonymous and uniformly sized.** Every
client pulls from one shared `scheduled` queue, so the server cannot steer a task to a client that
fits it, and the bundle size is one server-side constant, so a 4-core client and a 112-core client
are handed the same amount of work. A campaign with CPU, GPU and high-memory steps cannot say "run
*these* tasks on *those* clients" from one server, which is what pushes users to
one-task-per-`sbatch` frameworks.

Z1 (`zone-queue-primitive`) removed the seed's Problem 2: the managed queues are served in the
server process as threading `queue.Queue`s, with no manager child. A callable served by the manager
now runs in the server process and can reach state the scheduler holds there. That is the
precondition for a client to *request* work and be answered on its own connection.

The homogeneous bag-of-tasks case is HyperShell's founding strength and must not regress.

## Outcome / vision

A client is launched with an optional user-defined `--zone` label and its own `-b` bundle size.
When it is ready for work it asks the server, saying who it is, which zone it serves, and how much
it can take. The server answers that request, on that client's connection, with up to that many
tasks from that zone only. Tasks are stamped with a zone at submit time (`hs submit --zone=gpu …`).
Zone names are arbitrary user text, stored once in a `Zone` table that maps a minted UUID to the
name, mirroring `Source`. Each task references its zone by UUID, and `hs list` / `hs info` show
the name.

An unzoned submission run by the uniform fleet `hsx` launches behaves exactly as it does today, at
today's throughput and footprint. Zone is placement, not dependency: task-group gating stays global
across zones.

This cycle stops at single-server, in-process zone routing. Zone-aware autoscaling is Z3.

## Acceptance criteria (the contract)

**Default path**

- **R1** [seed R2] — The default configuration SHALL behave as it does today: an unzoned
  submission, run by `hs cluster` / `hsx` or by `hs server` with `hs client`s given no `--zone` and
  no advertised size, SHALL run every task to the same final state, in the same group order, with
  no result lost. On a single-host benchmark of many short tasks, throughput SHALL be at least 95%
  of 2.8.3's (median of repeated runs), and the server's process count and open file descriptors
  SHALL be no higher than 2.8.3's.

**Demand-driven dispatch**

- **R2** [seed R3] — WHEN a client is ready for work, it SHALL request it from the server,
  advertising its identity, its zone, and its bundle size, which is its existing `-b`. The server
  SHALL size the reply from that client's advertised value. WHEN a client advertises no size, the
  server SHALL use its own `-b`. Any explicitly configured `-b` (command line, environment, or
  config file) SHALL be advertised; only the built-in default advertises no size.
- **R3** [seed R3; transport condition] — The server SHALL bound every reply by a server-side cap,
  so that no reply carries more tasks than the cap whatever a client advertises. The cap's default
  SHALL NOT shrink any bundle the default configuration delivers today. The request's arguments and
  its reply SHALL be JSON-compatible data only (strings, numbers, lists, mappings), not
  application objects.
- **R4** [seed R4] — The server SHALL deliver each bundle only to the client that requested it, in
  reply to that request. A client SHALL NOT receive tasks it did not request, and no task SHALL be
  delivered to more than one client for the same attempt. Requests SHALL be served
  first-come-first-served. IF fewer matching tasks are ready than the client asked for, THEN it
  SHALL receive the ones that are ready (a partial bundle). IF none are ready, THEN the request
  SHALL return empty within a finite time, and the client SHALL ask again.
- **R5** [Q3] — WHEN a client too old to request work (2.8.x, or any build before this cycle)
  connects, the server SHALL disconnect it cleanly and log that the client is too old, and SHALL
  keep serving other clients. The old client SHALL exit within a bounded time rather than wait
  forever. Conversely, WHEN a client of this cycle connects to an older server (2.8.x or 2.9.0a2),
  it SHALL exit non-zero within a bounded time with one clear error naming the version mismatch,
  with no fallback path.

**Zones**

- **R6** [seed R5] — `hs submit` and `hs task submit` SHALL accept `--zone <name>` and stamp it on
  the tasks they submit. `hs client` SHALL accept `--zone <name>`, declaring the zone it serves.
  Clients launched by `hs cluster` / `hsx` SHALL carry the cluster's `--zone` the way they carry its
  `-b`.
- **R7** [seed R6] — Zone names SHALL be stored in a `Zone` table that maps a minted UUID to the
  name, mirroring `Source`. Each task SHALL reference its zone by UUID in a `zone` column on `task`.
- **R8** [seed R7] — Resolving a zone name to its id SHALL be an idempotent get-or-create: every
  reference to the same name, from submit or from a client, SHALL resolve to the one UUID, with one
  row. IF several processes resolve the same new name (or the reserved default zone) at once, THEN
  none SHALL fail because another created the row first. *(The cache that avoids a lookup per
  reference is **deferrable**; idempotence and concurrency safety are not.)*
- **R9** [seed R8] — WHEN a zoned client requests work, the server SHALL serve it only tasks of
  that client's zone. Zone SHALL constrain task selection only, for new tasks and for retries
  alike, and a retry of a task SHALL keep that task's zone.
- **R10** [seed R9] — Zone SHALL NOT affect task-group gating: the current group and its advance
  stay global across zones, and group ordering is exactly as today.
- **R11** [seed R10] — A task submitted without `--zone` SHALL belong to a reserved **default
  zone** with a fixed id, in the spirit of the reserved `Source` ids. A client given no `--zone`
  SHALL run default-zone tasks, and only those.
  WHEN a command opens a 2.8 or 2.9.0a2 database, the in-place upgrade step from
  `schema-upgrade-step` (#69) SHALL bring it current: the `zone` table created and `task.zone`
  added, with no per-column upgrade code and no manual step, forward-only on SQLite. Pre-existing
  tasks SHALL be treated as default-zone tasks without their rows being rewritten.
- **R12** [seed R11] **(deferrable)** — `hs list`, `hs info` and the other places that show a
  task's fields SHALL show its zone by name, resolved from the `Zone` table. A user SHALL NOT need
  the raw UUID.

**Docs and tests**

- **R13** [seed R12, trimmed] — The change SHALL update, in the same commit as each CLI change,
  the affected `docs/_include/*.rst` help snippets and the shell completions under `share/`, for
  `--zone` on submit, client and cluster; the meaning of a client's `-b` as its
  advertised size; and the server cap if it is user-facing. The autoscaler's `{zone}` /
  `HYPERSHELL_ZONE` documentation belongs to Z3.
- **R14** [seed note] — New tests SHALL cover the request path: per-client sizing, the cap,
  partial and empty replies, targeted delivery with no task delivered twice, zone matching,
  default-zone handling, retries keeping their zone, the too-old-client disconnect, and the
  request path re-entering on timeout and halting on shutdown. A server with clients in two zones
  plus unzoned clients on loopback SHALL run a grouped, mixed-zone workload that includes a task
  that fails and is retried, to completion, with every task run only by a client of its zone. The
  existing test suite SHALL pass.

## Non-goals (no-gos)

- **Zone-aware autoscaling** — the seed's R13–R17, cycle Z3: per-zone pressure and fleet
  accounting, zone discovery, the autoscaler appending `--zone` (alignment Q4), and `{zone}` /
  `HYPERSHELL_ZONE` launcher exposure and its docs.
- **Multi-server / one-cluster-per-zone topology.** One server instance.
- **Per-zone differentiated settings,** of the autoscaler or of the server cap.
- **Turnkey resource pinning.** Zone matches a client to tasks; it does not detect topology, set
  `OMP_*` / `taskset` / `CUDA_VISIBLE_DEVICES`, or check that a client fits its tasks.
- **Arbitrary DAGs or per-zone phase progression.** Group stays one global, serial phase pointer.
- **Zone as a dependency mechanism.** Zone is placement only.
- **Serving 2.8 clients.** They are disconnected cleanly (R5); no compatibility shim.
- **Changing the auth/TLS wire model, or replacing `BaseManager`.**
- **Fixing the reserved-`Source` race** (`issues/source-reserved-race.md`). R8 only requires that
  `Zone` not repeat it.
- **Filtering or searching tasks by zone name** (`hs list` / `hs search` / `hs update` /
  `hs wait`) beyond showing it (R12).

## Clarifications

- **2026-11-01 — Zone ships in three cycles:** Z1 = R1 (`zone-queue-primitive`), Z2 = R2–R12
  (this GOAL), Z3 = R13–R17; Z3 may be deferred into `issues/zone-autoscale.md` if Z2 overruns
  (maintainer, alignment memo, 2026-11-01).
- **2026-11-01 — In-process manager** (maintainer, alignment memo, 2026-11-01; Q1): done in Z1, and
  it is what lets the request be a callable served by the manager in the server process.
- **2026-11-01 — Dispatch staging** (maintainer, alignment memo, 2026-11-01; Q0, recorded in the
  seed). Approved direction, input to `hs-plan` rather than a requirement: the server stages ready
  tasks in per-zone pools, each `queuesize ×` the largest advertised bundle size deep; staging
  stamps tasks as `Task.next` does today; per-zone unstaging is by task id; a bundle of the
  client's size is cut from its zone's pool at request time. R1's benchmark bar is at least 95% of
  2.8.3's throughput with no higher footprint. A relay counts as one client advertising a large
  size.
- **2026-11-01 — Advertised size and the cap** (maintainer, alignment memo, 2026-11-01; Q2): the
  client advertises its existing `-b`; the server's `-b` applies to clients that advertise nothing;
  a new server-side cap bounds any single cut, and so also bounds the reply's frame size (R2, R3).
- **2026-11-01 — Transport conditions for the request callable** (maintainer, alignment memo,
  2026-11-01): JSON-only arguments and reply, and a server cap on the advertised size (R3).
- **2026-11-01 — Older clients** (maintainer, alignment memo, 2026-11-01; Q3): a 2.8 client gets
  a clean disconnect and the server logs "client too old" (R5). This is the one deliberate 2.8 wire
  break in the cycle.
- **2026-11-01 — Database compatibility** (maintainer, alignment memo, 2026-11-01): the zone table
  and column go in through the in-place upgrade step from #69: idempotent add-column, forward-only
  on SQLite (R11). No database break without it.
- **2026-11-02 — Seed R12's autoscaler docs move to Z3.** They document R15 (`{zone}` /
  `HYPERSHELL_ZONE`), which ships in Z3; this follows from the three-cycle cut above.
- **2026-11-02 — Target release 2.9.0a3 or a4, within about 3–3.5 days** (foreman, under the
  maintainer's delegation). Drift since the seed was written: 2.9.0a2 was cut on `develop`
  (`d996579`) without Z1, so the seed's "Z1 ships in 2.9.0a2" no longer holds.
- **2026-11-02 — Who advertises no size (R2): any explicitly configured `-b` is advertised**
  (command line, environment, or config file); only the built-in default advertises nothing. This
  keeps R1: a plain `hs client` against `hs server -b 100` behaves as today (foreman, as judge under
  the maintainer's delegation, 2026-11-02).
- **2026-11-02 — A client with no `--zone` serves the default zone only (R11).** In the
  homogeneous, zone-less case every task and client is in the default zone, so behaviour is
  unchanged (foreman, as judge under the maintainer's delegation, 2026-11-02).
- **2026-11-02 — A new client against an older server (2.8.x or 2.9.0a2) exits non-zero** within
  a bounded time, with one clear error naming the version mismatch, mirroring R5; no fallback path
  (R5) (foreman, as judge under the maintainer's delegation, 2026-11-02).
- **2026-11-02 — Sequencing: `writeback-scoped-conditional` lands first.** That cycle (branch
  `fix/writeback-scoped-conditional`, in plan) lands on `develop` before Z2's build; Z2 rebases
  onto it and SHALL preserve its result-column fence and assignment check in `server.py`
  `Receiver` and `data/model.py`. No R-ID change (foreman, as judge under the maintainer's
  delegation, 2026-11-02).
- **Note (for `hs-plan`):** this branch starts at Z1's head (`aa55e86`), which is based on
  `b819de6` and does not contain #69. The upgrade step R11 relies on (`src/hypershell/data/upgrade.py`)
  arrives when the branch is rebased onto `develop` after Z1 lands. Plan against `origin/develop`
  for the data layer.
- **Note (for `hs-plan`):** a zone whose current-group tasks have no client of that zone holds the
  group open; that follows from R10 and is expected, not a defect. It may deserve a log line.
- **Note (for `hs-plan`):** the touched code is the untested concurrency core flagged in
  `AGENTS.md`. Keep the finite-timeout, re-enter discipline on every blocking request, and keep
  shutdown and sentinel ordering (`AGENTS.md` § Invariants is not changed by this cycle).

## Related materials

- Seed: [`issues/zone.md`](../../issues/zone.md) — this cycle carries its R2–R12; the seed stays
  `shaped` for Z3 (R13–R17).
- Previous cycle: [`spec/zone-queue-primitive/`](../zone-queue-primitive/GOAL.md) (Z1, R1).
- Depends on: `fix/writeback-scoped-conditional` (lands on `develop` before Z2's build).
- Upgrade step: `spec/schema-upgrade-step/` and `src/hypershell/data/upgrade.py` on `develop` (#69).
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/core/queue.py`
  (`QueueServer`, the request callable); `src/hypershell/server.py` (`Scheduler`, staging and cut);
  `src/hypershell/client.py` (request, advertise, receive); `src/hypershell/data/model.py`
  (`Source` as the pattern for `Zone`, `Task.zone`, `Task.next` / `select_new` / `select_failed`
  zone filter); `src/hypershell/submit.py` and the `task` CLI; `src/hypershell/cluster/` (`--zone`
  and `-b` propagation).
- Precedent: the `Source` name↔UUID table from `spec/cli-cluster-restart-repeat-update/`;
  `issues/source-reserved-race.md` for the get-or-create race R8 must avoid.
- Invariants: `AGENTS.md`, "Concurrency model" and "Queue transport & security".
