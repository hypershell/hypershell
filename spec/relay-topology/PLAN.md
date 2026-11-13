# PLAN — Relay site-topology autoscaling: fill and spread across sites

> **Status:** Approved at the plan gate (foreman, 2026-11-12) · **Last updated:** 2026-11-12
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

A new mode, `hs server --autoscaling[=fixed|dynamic]`, runs the true server with a **topology
publisher** in place of a launcher. Each check it reads Z3's per-zone loads unchanged
(`read_zone_loads(own=None, discover=True)`). It then hands the loads, together with every live
relay's advertised per-zone bounds and launched counts, to one pure function, `plan_sites`. That
function applies Z3's `zone_demand` to each zone, capped at the sum of the live sites' `max`. It holds
each site's floors and splits growth by `spread` or `fill`. The result is versioned, and relays fetch
it through a new dispatcher method at heartbeat cadence.

Each relay advertises its site, its zones, each zone's `min`/`max`/`init` and each zone's launched
count on every zone identity's heartbeat. It keeps its per-zone fleets, whose machines become a
`SiteScaler`. With a topology, a `SiteScaler` launches once per new version what the server asked for
beyond what it counted, and it never terminates a client. Without one, it is today's fixed fleet.

`hs cluster`, `cluster/remote.py` and the work-request wire are untouched. Everything new is off
unless `--autoscaling` is given to `hs server`. That fits the big appetite in five phases.

**Foreman readings, confirmed for this plan** (GOAL Clarifications, 2026-11-12):

- **Server knobs under sites.** `[autoscale] policy`, `factor` and `period` keep their Z3 meaning for
  each zone's demand. The server's own `size.init`/`min`/`max` and `launcher` do not apply under sites.
  A zone's total is bounded by the sum of its live sites' advertised `max`.
- **Local fallback with bounds (R6).** With no topology, a relay sizes each zone from that zone's own
  `init`/`min`/`max` through the fixed-policy fleet it runs today. `size` is shorthand for
  `init = min = max = size`, so a relay configured only with `size` behaves exactly as today.
- **Site naming.** `[relay] site` defaults to the host name (`relay/upstream.py:159-161`). Unchanged.

## 2. Design

### 2.1 Entry point (D3)

**`hs server -A/--autoscaling [MODE]`**, with `-F/--factor` and `-P/--period` (`server.py`
`ServerApp`). `MODE` is `fixed` or `dynamic` and defaults to `[autoscale] policy`; `-F` and `-P` default
from `[autoscale]`. `--autoscaling` implies `--forever`. It is refused together with any of these:
`FILE`, `--restart`, `--no-db` and `--no-confirm`. Failover rests on confirmation and on the scheduler's
group (§4, D2). `--forever` given alongside it is accepted and redundant. `run()` imports
`cluster.topology` lazily, because `cluster/remote.py` imports `server.py`.

Why not `hs cluster` (research 02):

- "No `--launcher`" already means a local `hs client` and is documented (`docs/security.rst:203`).
- `AutoScalingCluster` mints a random key on every run and has no `-k`
  (`cluster/remote.py:855`), so relays configured with the operator's key could not join.
- It re-picks a default port (`cluster/__init__.py:563`) and ignores `[server] evict`
  (`remote.py:851`), which R3's liveness rests on.
- About 15 of its client-launch options would need refusing.

`hs server` already resolves the operator's key (`server.py:1666`), keeps its port and honours
`evict`. Without a launcher, "autoscaling" there can mean only one thing.

A Kubernetes deployment is then simply `hs server --autoscaling=dynamic` with
`HYPERSHELL_SERVER_AUTH` and `HYPERSHELL_AUTOSCALE_*` in its environment.

New thread `cluster/topology.py::AutoScalingServer(Thread)` is the analogue of
`AutoScalingCluster`. It holds:

- `ServerThread(forever_mode=True, sites=registry, …)` with the operator's key, address, TLS and `evict`;
- a `TopologyPublisherThread` wired to `server.scheduler.machine.active_group`
  (D2), `attempts = max_retries + 1` and `eager`.

It starts the server, waits for scheduling, then starts the publisher. On stop, it stops the publisher
first, then the server, which keeps the server's own shutdown order (§6).

### 2.2 Configuration (D4, D5)

**Server**, in `core/config.py` `[autoscale]`. Each new key is a single word (research 02: environment
names split on `_`).

| Key | Values | Default | Notes |
|-----|--------|---------|-------|
| `distribution` | `spread` \| `fill` | `spread` | Its own key: `policy` already means fixed/dynamic (D4). An unknown value raises `ConfigurationError` naming the source (`blame()`). |
| `sites` | list of names, or one comma-separated string | unset | Read by `fill` only (R1). The environment form is `HYPERSHELL_AUTOSCALE_SITES=east,west`. |

No new server CLI options for these. They are documented in the configuration reference and the
`hs server --autoscaling` help (R7).

**Relay**, in `[relay]` and `[relay.zone.<name>]`. The keys are `min`, `max` and `init`, flat beside
`size`; `ZONE_OPTIONS` becomes `launcher, launcher_args, size, min, max, init`. A zone's value wins over
the relay-wide value, as `launcher` does today. The relay-wide values are the relay's own settings for
a relay that names no zone table (R1).

Resolution, per zone (`relay/__init__.py::zone_bounds`, pure, table-tested):

| | min | init | max |
|---|---|---|---|
| **Advertised** (the server plans with these, R1/R4) | `min` key, else **0** (R1) | `init` key, else `min` | `max` key, else `size` |
| **Fallback fleet** (no topology, R6) | `max(1, min key else size)` | `init` key else `size`, held within [min, max] | `max` key, else `size` |

- With only `size` set, the fallback is `init = min = max = size`, which is today's fixed fleet
  exactly (foreman reading).
- With nothing set, the fallback is `size`'s default of 1, also exactly today.
- The fallback floor of 1 keeps D5's rule that a zone with no site client never pulls.
- Under a topology, a zone whose `min` is 0 gets nothing while gated (Zone floor ruling).
- `size` caps a zone unless `max` is set. The docs say so (risk §5).

Validation (`ArgumentError`, as today's `size`):

- whole numbers only;
- `0 ≤ min ≤ init ≤ max`;
- `max ≥ 1`;
- `size ≥ 1`, still refused below that for `-N` and for the zone `size`.

`init` and `max` are checked after defaults are applied, so `max = 2` with the default `size` of 1 is
valid.

### 2.3 Advertisement and per-zone counts (R4, D6)

`core/heartbeat.py::Heartbeat` gains three optional fields. Like `site` and `roster`, they are packed
only when set and read with `.get`:

- `zone`: the zone name;
- `bounds`: `{"min", "max", "init"}`;
- `clients`: an int, that zone's launched site clients.

`UpstreamHeartbeat` (`relay/upstream.py`) puts them on **every** beat of each zone identity. It takes
the identity → zone map, each zone's bounds, and a `launched(zone) -> int` callable. That callable reads
the zone fleet machine's `launched()`, which counts processes started and not yet exited, pending
scheduler steps included (Z3 parity, research 02/03).

Sending on every beat satisfies "on first connect and on every later reconnect". The first beat after
a reconnect is the link's `beat` stage. It is idempotent, and it also re-teaches a restarted server. An
old server ignores the keys. A new server treats a site beat without `zone` (an older relay) as a
non-participant: it gets no entries, and pulls, roster and failover work as today.

**Where the server records it.** The record lives in a new `core/topology.py::SiteRegistry`, which is
in-memory and lock-guarded. The HeartMonitor writes to it, and the publisher reads a snapshot.

`HeartMonitor.update_client` (`server.py:761`) records each beat that has `site` and `zone` as
`registry.record(identity, site=, zone=, bounds=, clients=, host=)`, keyed by **identity**. Each beat
overwrites the previous record in place: "where it recorded the previous one, overwriting".

- A new identity is stamped `registered` from a monotonic counter on the server (research 01: there is
  no registration time today).
- A relay restart makes new identities, which register as newer. Its old identities leave at finish or
  eviction.
- `check_clients` drops the record on eviction (`server.py:865`), next to `end_roster`/`forget`, and
  the FINISHED branch drops it on a clean finish.
- A record holds `init_held`. The record starts true and turns false once the reported `clients` has
  reached `init`, so `init` is a floor "at start" only.

The registry is passed as `ServerThread(sites=…)` → `HeartMonitor(sites=…)`. When it is None, which is
every mode but `--autoscaling`, nothing new runs.

Presentation: site clients are already attributed to their site upstream (relay cycle, `client.site`).
Each published version is logged at INFO, one line per site and zone with its target and launched
count. Duplicates and dropped sites are logged at WARNING.

### 2.4 Topology: shape, versioning, fetch, and the pure planner (R1, R2, R3)

**Shape** (`core/topology.py`):

- `Bounds(min, max, init)`.
- `Entry(zone: str, target: int, seen: int)`. `target` is how many clients this identity's zone
  should have. `seen` is the count the server planned from: this identity's own last reported
  `clients`.
- `Topology(version: str, entries: Dict[identity, Entry])`.

`version` is `f'{server INSTANCE}:{n}'`. `n` increases each time the entries change, and only then.
A restarted server therefore never repeats a version a relay has already applied.

**Fetch** (`core/dispatch.py`, `core/queue.py`):

- Add `Dispatcher.topology(text) -> text` to `DISPATCH_EXPOSED`.
- The request is `{"protocol", "site", "identities": [...]}`.
- The reply is one of:
  - `{"protocol", "version", "zones": {zone: {"target", "seen"}}}` for the identities given;
  - `{"protocol", "version": null}` while the dispatcher has nothing published, which is every server
    not autoscaling with sites.
- A topology that has no entry for an identity answers that zone with `target: 0`. This is how the
  older relay of a duplicated site is told zero (Q2).
- `Dispatcher.publish(topology)` sets it under the dispatcher lock. Nothing else on the dispatcher
  changes, and work requests and their replies keep their exact shape (`test_dispatch.py`).
- The JSON goes through `text`, as a work request does (§6, §9).

On the relay side, after each successful beat, `UpstreamHeartbeat` calls
`link.call('heartbeat', lambda c: c.dispatcher.topology(text))`, guarded by
`hasattr(c.dispatcher, 'topology')`, and stores the reply in a lock-guarded `relay/site.py::TopologyView`:

| Server reply | View | Relay does |
|---|---|---|
| Method missing (old server) | `None` | Local fleets (R6). Logged once at INFO. |
| `version: null` | `None` | Local fleets (R6). |
| Link down | Last view kept | An outage never flips modes. |

`RelayThread.run` does one synchronous probe fetch after `wait_link()` and before `cluster.start()`.
A relay facing a publishing server therefore never makes its fallback's initial launches first. The
same view stays current at heartbeat cadence, so a server that starts or stops publishing mid-run is
followed at the next beat.

**Pure planner** (`cluster/topology.py`, no I/O, table-tested on its own):

```python
class Distribution(Enum): SPREAD = 1; FILL = 2

@dataclass(frozen=True)
class SiteZone:
    zone: ZoneRef
    bounds: Bounds
    running: int      # launched clients of this zone at this site, over its live identities
    starting: bool    # the holder's init is still a floor (init_held)

@dataclass(frozen=True)
class SiteView:
    name: str
    rank: int         # first-registration order of the site name on this server
    zones: Mapping[str, SiteZone]   # by zone id; only zones the site serves

def plan_sites(loads: Mapping[str, ZoneLoad], knobs: ScalingKnobs, sites: Sequence[SiteView], *,
               distribution: Distribution = Distribution.SPREAD,
               order: Sequence[str] = ()) -> Dict[str, Dict[str, int]]:
    """Site name -> zone id -> client target, for every zone some site serves."""
```

For each zone `z` that some live site serves (zones with work but no serving site are logged and left
out; a zone with no live client holds its group, Z2 R10):

1. Compute each site's **floor**: `floor_s = min_s`, or `max(min_s, init_s)` while `starting`.
   Compute each site's **base**: `base_s = min(max_s, max(running_s, floor_s))`.
   - A site never drops below what it runs, except where `max_s` is under it. There the base is `max_s`,
     so no launch (R5).
2. Compute the zone's **demand**:
   `D = zone_demand(load_z or empty, launched=Σrunning, replace(knobs, init_size=0, min_size=0,
   max_size=Σmax), own=False, initial=False)`. That is Z3's own rule, unchanged: under `fixed`, one
   client when none runs and the zone has work; under `dynamic`, +1 while pressure > 1, up to the cap.
3. Compute the **growth** still owed:
   `extra = max(0, (D − Σrunning) − Σ max(0, base_s − running_s))`. A floor that already adds a client
   counts toward the zone's growth, so a check never grows a zone by more than Z3 would.
4. **Place** `extra` one client at a time among the sites with `base_s < max_s`:
   - `spread` picks the smallest `base_s`;
   - `fill` picks the first site in preference order.

   Preference order is the listed sites in `order`, then unlisted sites by `rank`. Ties under `spread`
   go by the same order. If no site has room, the rest is dropped (the cap).
5. The **target** for each site is its `base_s`.

Companions in the same module, also pure and table-tested:

- `site_views(records, order) -> (List[SiteView], List[Duplicate])` groups live records by site.
  - **The newest registered identity of a site holds** each zone and supplies its bounds.
  - `running` sums the site's identities in that zone.
  - `Duplicate(site, holder, older)` feeds the warning that names both, with identity and host (Q2).
- `entries(targets, records) -> Dict[identity, Entry]`.
  - The holder gets `target_s − (the older identities' running)`, floored at 0, and `seen` = its own
    `clients`.
  - An older identity gets `Entry(zone, 0, its clients)`.

**Publisher** (`cluster/topology.py::TopologyPublisher(StateMachine)` + `TopologyPublisherThread`).
States: START → WAIT → CHECK → PUBLISH → WAIT, plus FINAL/HALT. Every wait is finite (1 s steps, as
`AutoScaler.wait` does) and re-enters (§5).

CHECK does the following, in order:

1. `group = scheduler_group(reported, attempts, eager)` (a new helper in `cluster/demand.py`: the live
   `Scheduler.active_group`, else `Task.starting_group(Task.current_group())`, D2).
2. `read_zone_loads(group, knobs, own=None, discover=True, attempts, eager)`.
3. `registry.snapshot()` → `site_views` → `plan_sites` → `entries`.
4. Log each zone:
   `Autoscale check (zone: Z, sites: N, clients: R, tasks: W in group G[, pressure: P])`.

PUBLISH calls `dispatcher.publish(Topology(...))` only when the entries differ from the last ones.

The publisher never launches anything and never ends by itself. Like `AutoScaler`, it ignores
`forever`/`restart`.

### 2.5 Relay reconciler (R2, R5, R6, D7)

`relay/site.py::SiteScaler(AutoScaler)` is the machine of every site fleet. It is assigned after
construction to `cluster.autoscaler.machine` and to each `zone_fleet`'s thread, a pattern this file
already uses (`strict = False`, `site.py:112`). Its fields are those of the fleet it replaces, so
`machine.launch`, `.clients`, `.min_size` and the like keep meaning what the tests read. It adds `view`
and `zone`, and it overrides `init` and `check_zones` only.

**`view.assignment(zone)` is `None`.** The parent's code runs unchanged: today's fixed fleet, with the
fallback bounds of §2.2 (R6).

**It is an `Entry(target, seen)` under version `v`.**

- If `v` has already been applied, the relay launches nothing.
- Otherwise it launches `max(0, target − max(seen, launched))` through `actuator.apply` (the whole
  delta), then records `v` as applied.

The relay never replaces an exit by itself. It launches only what the server asks for beyond what it
counted. Z3's own actuator behaves the same way: an exited client is replaced only if the next demand
asks for one. Research 03's "top up to the target every check" would relaunch every idle exit, because
Z3's demand never falls below `launched`, so a fleet with no work would never retire (digest §6).

- A lowered target (the max below running, or zero for an older duplicate) launches nothing, and the
  surplus retires through the site clients' idle timeout (R5).
- `apply` never terminates (`launch.py:299-307`).
- The floor (the explicit `min`) is held by the server's targets, including while the zone is gated,
  and by no relay-side override. A server target of 0 means 0 (Q2).

**Zone exposure (D7).**

- `zone_launch` passes `expose_zone=True`.
- `site_cluster` rebuilds the first zone's launch as `zone_launch(cluster, zone, …)` with the cluster's
  key and port. It sets it as `cluster.launch` and as the `SiteScaler`'s launch, so `machine.launch is
  cluster.launch` still holds.
- Every launch, on both paths, is built at construction and passed to `fence_site` (§11).
- `discover_zones=False` stays.
- Exposure is the same in both modes, so a `{zone}` launcher works whether or not a topology arrives.
  That is deviation row 3 and question **Q1**.

`RelayThread` passes the bounds of §2.2:

- `site_cluster` and `zone_fleet` gain `init`, `min` and `max` in place of the single `size`, which
  stays as a keyword for callers;
- the heartbeat gets the zone map, the advertised bounds and `launched`;
- the probe fetch runs before `cluster.start()`.

`check_site`, `stop_site`, `abort`, `end_site_clients`, the puller and the returner are unchanged.
At zero clients the relay stays connected and heartbeating, and its puller never asks (`bound == 0`).
Growth comes from the server's demand (D5, R5 "ready to regrow").

### 2.6 Liveness and failover (R3)

A site-zone is live exactly while its relay identity is in `HeartMonitor.beats`, the existing liveness
authority. The registry mirrors it: a record is dropped at eviction (`evict`, on the relay clock as
today) and at FINISHED.

The next CHECK plans over the survivors only. The dead site's launched count leaves `Σrunning`. Under
`dynamic`, while pressure stays above 1, `zone_demand` re-grows the zone one client per check at the
survivors, up to their `max` sum. Under `fixed`, the zone is re-floored, and it gets one client if none
runs. That is Z3's fixed meaning, documented.

The dead site's unreturned tasks revert through the existing `Task.revert_orphaned` and `forget`
(`server.py:865-882`). This cycle adds nothing to the task path.

**Duplicated site name (Q2).** `site_views` makes the newest registration the holder. The older
identity gets `target 0`, so its clients retire through idle exit and are never terminated. The
publisher logs, once per pair, `Site S advertised by two relays (older: <id> on <host>, newer: <id> on
<host>) - the newer holds its entries`.

### 2.7 The founding case (R6 and "exactly as today")

- `hs server` without `--autoscaling` passes `sites=None`, so the HeartMonitor records nothing. The
  dispatcher's `topology` answers `version: null`.
- Plain clients' beats pack exactly as before, because the new keys are packed only when set.
- `hs cluster` (every mode) is untouched: no edit to `cluster/__init__.py`, `cluster/remote.py` or
  `cluster/launch.py`. Z3's seam (`demand.py`'s three functions) is unchanged, and `scheduler_group` is
  additive.
- A relay against a server that publishes nothing runs today's fleets, through `SiteScaler`'s
  unchanged parent path, with the fallback bounds. Those equal today's with only `size` set. The one
  change on that path is zone exposure (Q1).

Guarding tests, kept green with their assertions unchanged, except the declared amendments in §3:

- `tests/test_autoscale.py`, `tests/test_zone_autoscale.py`,
  `tests/test_cluster_launch.py -k autoscaling`, `tests/test_cluster.py`;
- `tests/test_server.py`, `tests/test_server_fsm.py`;
- `tests/test_dispatch.py` (exact reply shapes), `tests/test_dispatch_version.py`;
- `tests/test_relay_{connector,db,fsm,launch,link,outage,return,roster,workload,judge}.py`;
- `tests/test_completions.py`;
- the drives: `spec/zone-autoscale/drive/zone_drive.sh` in its four modes (`founding`, `fixed`,
  `dynamic`, `restart`), the byte-identical guard for `hs cluster` and Z3; and
  `spec/relay/drive/relay_smoke.sh`, the relay cycle's loopback R21 rehearsal against a non-publishing
  `hs server`, the R6 end-to-end guard.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `[autoscale] distribution`/`sites` (§2.2); `Distribution`, `plan_sites` order rule (listed, then by `rank`); `[relay]`/`[relay.zone.<name>]` `min`/`max`/`init` and `zone_bounds` (`min` defaults to 0); server plans with the advertised bounds; floors `min`, plus `init` while `init_held`, held regardless of gating; growth from `zone_demand` (§2.4) |
| R2 | `hs server --autoscaling` → `AutoScalingServer` with no launch path; `TopologyPublisher` reading `read_zone_loads(own=None, discover=True)` from the scheduler's group; versioned `Topology` via `Dispatcher.topology`; `SiteScaler` reconciles only its own identities' entries with its own launchers (§2.1, §2.4, §2.5) |
| R3 | Registry entries only for identities in `beats`; dropped at eviction and finish; the next CHECK re-plans over survivors; orphan recovery unchanged (§2.6) |
| R4 | `zone`/`bounds`/`clients` on every zone identity's beat; `SiteRegistry.record` overwrite per identity; per-site, per-zone running and liveness; zones assigned only to sites that serve them; site defaults to the host name; newest registration holds, older gets 0, warning names both (§2.3, §2.6) |
| R5 | `base_s ≤ max_s`, never below running except under `max`; `SiteScaler` applies each version's delta once, never terminates, never replaces exits on its own; relays stay connected at zero (§2.4, §2.5) |
| R6 | `version: null` / missing method → `TopologyView` None → stock fixed fleets with fallback bounds; relay test suite and `relay_smoke.sh` (§2.5, §2.7) |
| R7 | `docs/_include/server_{usage,help,desc}.rst`, `docs/cli/server.rst`, `relay_{help,desc}.rst`, `docs/cli/relay.rst`, `config_param_ref.rst` (`[autoscale] distribution`/`sites`, `[relay] min`/`max`/`init` and the zone tables), `share/` bash/zsh completions and man page, each in the phase that adds the option (P2 server, P3 relay) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) **before**
research (gate #1: the intent touches §4, §5, §6, §9, §10, §11, §12; nothing in §1–§3, §7 or §8, since no
task-row write, exit status, retry, resource or signal path changes) and **again** on this design
(gate #2).

- **§1–§3 (task lifecycle, exit status, retries).** No new query or write on `task`. The planner reads
  through `read_zone_loads`, whose counts already apply `CANCEL_STATUS` and the retry rule. Failover
  is the existing `revert_orphaned`.
- **§4 (server modes).** `--autoscaling` is refused with `--no-db` and `--no-confirm`, because orphan
  recovery and the group read need both. The registry is in memory and writes no DB row.
- **§5 (FSM and Thread).** `TopologyPublisher` has a HALT state and an action for every non-HALT
  state. Its waits are bounded at 1 s and re-enter. `TopologyPublisherThread.stop()` calls
  `machine.halt()` before `super().stop()`. `SiteScaler` inherits `AutoScaler`'s states and overrides
  two actions only. The topology fetch in `UpstreamHeartbeat` goes through the link with its deadline,
  and a failure leaves the view as it was.
- **§6 (shutdown, payloads).** `AutoScalingServer.stop` stops the publisher before the server, and the
  server's own ordering is untouched. Topology request and reply are JSON text through the dispatcher.
  Heartbeat fields are plain JSON values, packed by `Heartbeat.pack`. No live object goes on a queue.
- **§9 (auth, TLS).** `hs server --autoscaling` keeps every key check `hs server` has (no default key,
  length, characters). The topology carries no secret: zone names, counts and identities only. TLS is
  as configured.
- **§10 (configuration).** The new keys are read once at import (`config` singleton). Bounds change
  only by restarting the relay, which is also why the lowered-`max` evidence is a Q2 handoff. Single-word
  keys keep the environment forms working.
- **§11 (cluster orchestration).**
  - The autoscaler never terminates clients: the publisher launches none, and `SiteScaler` only
    `apply`s.
  - The group-read rule is kept through `scheduler_group`, pinned equal to `AutoScaler.current_group`.
  - Relay fleets are launched only through `client_launch()` (`zone_launch`). Every launch is built
    eagerly and passed to `fence_site`, and `discover_zones=False` stays.
  - The AGENTS.md/§11 line on autoscaled launches gains "relay launches expose their zone", and the
    publisher gets one line ("a server autoscaling with sites launches nothing"), in lockstep.
- **§12 (conventions).** Docs, help snippets, completions and man page go in the same commit as each
  option (P2, P3). `.. versionadded:: 2.9.0` marks `hs server --autoscaling` and the relay bounds. New
  tests carry `@mark.unit`/`@mark.integration`. Exit codes use `exit_status`.

### Deviation justifications

None of these changes what an R-ID SHALL do. Each adds surface or complexity, and each test edit
listed is a declared amendment, not drift. All five rows were accepted as written at the plan gate
(foreman, 2026-11-12). Row 4 stays honest only through its equivalence test, which runs in P1's verify.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| 1. Dispatcher remote surface gains `topology`; `test_dispatcher_remote_surface_is_pinned` amended to the new tuple | A relay with no clients must learn its targets, and a heartbeat has no reply (research 01) | Fields in the work-request reply never reach a zero-client relay, and a probe request can take tasks |
| 2. Relay beats gain `zone`/`bounds`/`clients`; the exact-key asserts in `test_beat_with_site_and_roster_round_trips` and `test_relay_beat_carries_site_and_roster` amended to include them | R4's advertisement and D6's counts need a carrier on every connect, and the beat is the one the reconnect path already sends first | A dedicated advertisement message would need a new queue and its own reconnect ordering, while old servers already ignore unknown beat keys |
| 3. Relay launches expose their zone in fallback too: the environment gains `HYPERSHELL_ZONE`, and a literal `{zone}` in a launcher word is replaced; the env asserts at `test_relay_launch.py:334` and `:572` amended. **Decided (Q1):** always expose, with or without a topology (GOAL Clarifications, "Ruled at the plan gate", 2026-11-12 — Q1) | One launch per zone, built and fenced at construction; a `{zone}` launcher must work before the relay knows the mode | Two launch sets per zone, chosen after the probe, double the fenced launches and make the fallback ignore `{zone}`. R6's fleet size is unchanged either way. |
| 4. The group-read rule (D2) appears twice: `AutoScaler.current_group` and the new `demand.scheduler_group` | Keeps `cluster/remote.py` (high blast radius) untouched this cycle | Refactoring `AutoScaler` to call the helper is a behaviour-preserving edit to a high-blast file for five lines. An equivalence test pins the two instead, and a follow-up may fold them. |
| 5. `SiteScaler` replaces the fleets' machines after construction | Reuses `AutoScaler`'s lifecycle and `LaunchActuator` with zone exposure (D7), with no `remote.py` edit, so the relay's supervision (`check_site`, `stop_site`, `abort`, `end_site_clients`) needs no change | A `target` hook on `AutoScaler` touches a high-blast file. A new relay-level reconciler breaks the fleet-shape tests and needs every launch prebuilt (research 03). |

## 4. Rabbit holes (resolved)

- **Which command starts a server with no launcher?** → `hs server --autoscaling`. `hs cluster`'s
  minted key, port re-pick and its documented "no launcher = local client" rule it out
  ([02](research/02-server-entry-autoscaler.md)).
- **How does a relay with no clients learn its targets?** → A new dispatcher method, fetched after each
  beat. On an old server it fails locally with `AttributeError` and the link stays up
  ([01](research/01-upstream-surfaces.md)).
- **Where do per-site, per-zone counts come from?** → `clients` on each zone identity's beat, counting
  launched clients rather than connected ones, so pending scheduler steps count
  ([01](research/01-upstream-surfaces.md), [03](research/03-relay-reconciler-evidence.md)).
- **Replacement churn.** A relay that tops up to a target relaunches every idle exit, because Z3's demand
  never falls below launched. → Apply each version once, as `target − max(seen, launched)`
  (digest §6).
- **Fixed policy under sites.** `zone_demand(own=False)` gives at most one client per zone. → Sizing
  under `fixed` comes from the relays' `min`/`init`, and growth past that needs `dynamic`. This is Z3's
  own meaning, and the docs state it. The drives run `dynamic` for regrowth.
- **`min = 0` without a topology** strands a zone, because the relay never pulls (D5). → The fallback
  floor is `max(min, 1)`, and `size < 1` stays refused.
- **Lowering `max` on a live relay** is impossible in-process (§10), and a restart ends its own clients.
  → The evidence is the Q2 handoff ([03](research/03-relay-reconciler-evidence.md)).
- **Registration order** is not recorded today. → The registry stamps a monotonic `registered` value
  per identity, and a first-seen `rank` per site name ([01](research/01-upstream-surfaces.md)).

## 5. Risks & open questions

- **Q1, decided: zone exposure in fallback.** Relay launches always expose `{zone}` and
  `HYPERSHELL_ZONE`, with or without a topology (GOAL Clarifications, "Ruled at the plan gate", 2026-11-12 — Q1; deviation 3). That is one launch path, as
  Z3 exposes on every launch. Fleet sizes and argv are unchanged, and the only addition to a site
  client's environment is that variable.
- **Defaults cap small.** With nothing configured, a relay advertises `min 0, init 0, max = size = 1`
  for each zone, so each site contributes at most one client per zone. That is safe, but operators must
  set `max` (or `size`). The relay docs and the `hs server --autoscaling` help say so.
- **Count lag.** Launched counts reach the server at heartbeat cadence, so growth may wait one check.
  The `seen` rule prevents both double launches and the replacement of fresh exits. The worst case is
  one extra client per race, bounded by `max`.
- **The server restarts mid-run.** The registry is in memory. Every beat carries the advertisement and
  the version carries the server instance, so the first beats after a reconnect rebuild it. During the
  first period after a restart, the publisher may see fewer sites than are live (no entries yet), which
  can delay growth but never shrinks a running fleet.
- **The mode changes mid-run** (a server is restarted with or without `--autoscaling`). The view follows
  at the next beat. Clients already launched keep running. A relay going from fallback to topology
  reports them as `clients`, so the server counts them.
- **Unrelayed zones.** A zone with work but no live site serving it holds its group (Z2 R10), as in Z3.
  The publisher logs it at each check.
- **Multi-node run.** The batch script is the foreman's to submit. It uses `srun --overlap` steps, as
  the relay drive did, and P5's gate waits on it.

## 6. Verification strategy

- **Pure (P1).** `tests/test_topology_plan.py` holds table tests for `plan_sites`, `site_views`,
  `entries` and `zone_bounds`:
  - `spread` evens out, with ties broken by order;
  - `fill` saturates in order: listed sites first, then unlisted ones by `rank`;
  - a zone goes only to the sites that serve it;
  - the `min` floor holds with no load (gated);
  - `init` applies while starting and lapses after;
  - `max` below running gives a target equal to `max`, so nothing launches;
  - the cap is Σmax;
  - `fixed` gives one client when none runs;
  - a floor lift counts as growth;
  - a dead site's absence regrows the survivors under `dynamic`;
  - a duplicate site gives holder/older entries.

  It also covers the wire round-trips (`Heartbeat` fields, legacy beats, topology request/reply).
- **Server (P2).** `tests/test_topology_server.py` (integration, real queue server):
  - beats with an advertisement populate the registry;
  - eviction and finish drop it;
  - `dispatcher.topology` replies (null; entries; zero for an older duplicate);
  - a publisher check over a seeded DB publishes once per change;
  - the duplicate warning;
  - an old relay's beat is ignored.

  `tests/test_server.py` gains the new refusals. The founding drive is `zone_drive.sh founding`.
- **Relay (P3).** `tests/test_relay_topology.py`:
  - the `SiteScaler` delta table, with each version applied once;
  - None falls back to the parent;
  - a missing entry gives 0;
  - the probe against a server with no `topology` method (a stub proxy) stays silent;
  - `version: null`;
  - bounds advertised on every beat;
  - the zone is exposed on every launch.

  The R6 guard is the whole relay suite plus `relay_smoke.sh`.
- **Laptop drive (P4).** `spec/relay-topology/drive/topology_drive.sh MODE` runs under `temp_site.sh`.
  It starts `hs server --autoscaling=dynamic` with `evict` 10 s and a 1 s period, plus two relays as
  separate processes on loopback:
  - relay A serves `cpu` and `highmem`;
  - relay B serves `cpu` and `gpu`.

  The relays use heartrate 1 and site-client timeout 2 s. Their shim launcher records `site`, the
  `{zone}` word, `HYPERSHELL_ZONE` and `--zone`. The modes and their checks:
  - `spread`: `cpu` at both sites, `highmem` only at A, `gpu` only at B, word = env = `--zone`, no
    site-zone over its max.
  - `fill` (`sites = B`): B's `cpu` reaches its max before A's first `cpu` launch.
  - `floor`: `gpu` work in group 1 with B `gpu.min = 1` gets a client while group 0 runs; `highmem`
    (`min = 0`, also group 1) gets none before group 0's last completion.
  - `idle`: after the work, every client exits by itself, no launch follows, and both relays stay
    connected with live identities.
  - `kill`: SIGKILL relay B mid-run; after its eviction and orphan revert, A's `cpu` launches rise
    above their pre-kill count, and every task completes once.
  - `lower`: start A2 (`site = A`, `cpu.max = 1`) while A runs with `cpu.max = 3`. The warning names
    both; A launches nothing after that; A's clients end by idle exit (`Autoscale client
    disconnected`), never by `Terminating site client`; A2 never exceeds 1.

  `TD_EVIDENCE=<dir>` writes scrubbed `MODE.txt` files to `spec/relay-topology/evidence/laptop/`.
- **Multi-node (P5, split gate).** `spec/relay-topology/drive/topology_cluster.sh INSTALL SCRATCH` is a
  self-contained batch script with one job of at most 4 nodes and at most 1 hour (`#SBATCH --nodes=4
  --time=01:00:00`). It names no account, partition or QOS; the foreman adds those at submit time.
  `INSTALL` is the prefix whose `bin/hs` and `bin/python` are under test, and `SCRATCH` is a shared
  directory.
  - **Node layout.** Node 0 runs `hs server --autoscaling=dynamic --forever` with no launcher. Nodes 1
    and 2 each run one relay: A serves `cpu` and `highmem`, B serves `cpu` and `gpu`.
  - **Launcher.** Each relay's launcher is the scheduler's own
    `srun --overlap --nodes=1 --ntasks=1 --exclude=<node 0> --export=ALL sh <recorder> <log> {zone}`, so
    clients land on the allocation's nodes. The recorder logs node, `{zone}` word, `HYPERSHELL_ZONE`,
    `--zone`, pid and whether the upstream key is present, then `exec`s the client.
  - **Runs.** Two runs in the hour: `r1-spread-kill` (kill relay B once a third of the tasks are
    complete) and `r2-fill` (`sites = B,A`).
  - **Logs.** Each run writes `SCRATCH/<run>/{server,relay-A,relay-B}.log`, `launch-{A,B}.log`, and an
    `hs list` export of task counts per group and per executing site. A collector writes
    `SCRATCH/results/<run>.json`, counts and booleans only.
  - **Judge.** `topology_judge.py` checks:
    - every task completed exactly once, in group order;
    - zone placement per site;
    - word = env = `--zone`;
    - peak launched per site-zone ≤ advertised max;
    - the gated-zone floor and min-0 rules;
    - the fill order (r2);
    - B's identities evicted and orphans reverted, then A's `cpu` launched rising (r1);
    - no relay `Terminating site client` before the drain, with idle exits seen;
    - the upstream key present in no site client;
    - the records scrubbed of host, user and path.

  **Run A** builds the script, the judge, the judge's unit tests and a local rehearsal
  (`TC_MODE=local`). **Run B** judges the collected `results/*.json` copied into
  `spec/relay-topology/evidence/cluster/`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md) and briefs 01–03.*
