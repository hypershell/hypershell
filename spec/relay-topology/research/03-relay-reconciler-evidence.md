# Research 03 — relay reconciler, per-zone counts, evidence harness

## Findings (with citations)

- **Relay fleets today.** First zone: `site_cluster` builds an `AutoScalingCluster` with
  `policy='fixed'`, `init=min=max=size`, `discover_zones=False` (`relay/site.py:105-111`), so its
  launch is `build(zone=zone)` with no `expose_zone` (`cluster/remote.py:891-893`). Further zones:
  `zone_fleet` = an `AutoScalerThread` with the same fixed sizes over `zone_launch`
  (`relay/site.py:122-153`). `zone_launch` calls `client_launch(...)` without `expose_zone`
  (`site.py:140-144`).
- **What a fixed fleet does per period** (`remote.py:505-556`, `demand.py:141-155`): `check()`
  first calls `clean()` (drops exited processes, `launch.py:309-324`). It then runs
  `read_zone_loads` on the relay-local DB and `fleet_demand`. For the own zone, `zone_demand` gives
  `init` at start, then `+1` while `launched < min`, and under the fixed policy
  `1 if launched==0 and work>0 else launched` (`demand.py:150-155`). `check_zones` caps each zone
  at `have+1` per check (`remote.py:551`), and `scale()` calls `actuator.apply(pending)`
  (`remote.py:581-591`). Fixed policy ignores `max_size` entirely. Replacement is therefore one
  client per zone per `period`.
- **The site's autoscaler cannot be told to stop launching cleanly.** `AutoScalerThread.stop()`
  halts the FSM (`remote.py:633-637`, `core/fsm.py:46-52,80-82`) and the thread ends. That trips
  `check_site`, which treats any autoscaler that ends before `stopping` as `RelayPartEnded`
  (`relay/__init__.py:639-649`). Setting the knobs to 0 doesn't stop it either: the fixed branch
  still launches 1 when the zone has work (`demand.py:155`). Nothing sets
  `AutoScalerPhase.STOP`. `AutoScalingCluster.run_with_exceptions` starts and joins whatever
  `self.autoscaler` is, then `server.join()` (`remote.py:906-912`), so the thread can be swapped
  before `start()`.
- **Lifecycle hooks that read the fleets.** `fence_site` checks `site_launch(cluster)` plus the
  `launches` it is given (`site.py:263-284`; the call site passes `fleet.machine.launch` for each
  fleet, `relay/__init__.py:501-502`). `end_site_clients` reads `autoscaler.machine.clients` for
  the cluster and each fleet (`site.py:287-294`). `stop_site` stops `cluster.autoscaler` and every
  fleet (`__init__.py:603-605`). `abort` does the same (`:699-702`). `check_site` requires all of
  them alive until `stopping` (`:639-649`). There is a precedent for adjusting a machine after
  construction: `cluster.server.confirm.machine.strict = False` (`site.py:112-113`).
- **`expose_zone`** (`launch.py:184-190,216-219`): it replaces `{zone}` in launcher words only
  and adds `HYPERSHELL_ZONE=<name|default>` to the env. Client options and `--zone` are unchanged,
  and SSH refuses it. Invariant §11 pins "relay fleets launched only through `client_launch()` …
  every launch passes `fence_site`" and "the relay's site cluster uses `discover_zones=False`"
  (`.agents/factory/invariants.md` §11).
- **Counts at the relay.** `store.roster` and `store.count_connected` read the relay-local `client`
  table, which has no zone column (`relay/store.py:267-286`; `spec/relay/TECH.md:1614-1619`).
  `RosterEntry` is `uuid`/`host`/`state` (`core/heartbeat.py:50-55`). The only per-zone source is
  the relay-local `Dispatcher.demand[client_id] = (zone_id, size)`. A client is entered there at its
  first request and removed on `forget`, which runs at finish or eviction
  (`core/dispatch.py:211,259-266,412-415,433-443`). `RelayThread.zone_clients` reads it
  (`__init__.py:546-548`). The launched count per zone is `AutoScaler.launched()` →
  `LaunchActuator.launched()` (`remote.py:453-455`, `launch.py:268-274`). Every zone identity
  already beats with `site` (`relay/upstream.py`, `UpstreamHeartbeat.submit`, lines ~753-760). The
  identity is `uuid5(instance, zone)` (`upstream.py:127-134`), which the server cannot invert, so
  the zone has to be named in the beat or the advertisement.

## Reconciler options compared

| | (a) keep one `AutoScaler` per zone and add a target source | (b) one relay-level reconciler with a single `LaunchActuator` |
|---|---|---|
| Topology appears or vanishes mid-run (older server, restart) | Natural: each check reads the target, and `None` means today's fixed logic | It must run fallback too (the relay can't know the mode at start), so it replaces the fixed fleets in both modes |
| `check_site` / `stop_site` / `abort` / `end_site_clients` | Unchanged | Work only if it is assigned as `cluster.autoscaler` before `start()` and `fleets=[]` |
| `fence_site` | Unchanged: every launch is built at construction | `launch_for` builds lazily (`launch.py:276-280`), so every served zone's launch has to be prebuilt and passed in |
| R6 tests | Green, except two env-equality asserts (below) | Breaks `test_relay_launch.py:577-645` (fleet shape, `min_size` per fleet) and changes the meaning of `test_relay_fsm.py:779` |
| Per-zone `min`/`max`/`init` | Already per fleet (`init_size`/`min_size`/`max_size`) | A new knob table |
| Matches the Z3 forward-compat note (`spec/zone-autoscale/TECH.md:177-183`) | In substance: each fleet already holds a `LaunchActuator` and calls `apply` | Literally |

## Recommendation

**(a): one `AutoScaler` per served zone, each with an injectable target.** Give `AutoScaler` an
optional `target: Callable[[], Optional[int]]`, default `None`, which leaves `hs cluster`
unchanged. The relay sets it on `cluster.autoscaler.machine` and on each `zone_fleet`, following
the `strict=False` precedent.

- When `target()` returns an int, `init()` and `check_zones()` skip `read_zone_loads` for the own
  zone. They set `pending={own: T}` if `T > launched`, else wait. A lowered `T` stops replacement
  only. Nothing is terminated, because `apply` only adds (`launch.py:299-307`).
- `None` means the current fixed logic (R6).
- A shared, lock-guarded topology view, fed by the topology fetch, answers
  `target(zone) -> Optional[int]`. It returns `None` when no topology has ever been published, and
  `0` when a topology exists but has no entry for this site-zone (for example, the Q2 older
  duplicate). The last version is kept through a link outage, so an outage never flips the relay
  into fallback.
- The relay doesn't apply its own `min` over a topology entry. The server already keeps each entry
  ≥ `min` (R1), and the Q2 "assign zero" ruling needs zero to mean zero.
- **Open for the plan:** whether to keep the `have+1` per-check cap or apply the whole shortfall.
  The server already grows each zone by at most one per check, so applying the whole shortfall
  regrows faster after a site loss.
- **Alternative if `cluster/remote.py` (high-blast) must stay untouched:** a `SiteScaler(AutoScaler)`
  subclass in `relay/site.py` that overrides `init`/`check_zones`, swapped into
  `thread.machine` while keeping the same `launch` object (`test_relay_launch.py:326` asserts
  `cluster.autoscaler.machine.launch is launch`). It works, but it's clumsier.
- **Fallback with bounds.** The fixed policy never reads `max`. With `min=0` and no topology, the
  R8 pull gate never pulls, so the zone stays dead (D5). Two choices for the plan: keep refusing
  `size < 1`, which `test_relay_fsm.py:916-924` pins, and in fallback floor at `max(min, 1)`; or
  refuse `min=0` without a topology. Decide at the plan gate.

**`expose_zone` (D7), minimal change.**

- In `zone_launch` (`site.py:140-144`), add `expose_zone=True` to `client_launch(...)`.
- For the first zone, add `expose_zone: bool = False` to `AutoScalingCluster`:
  `self.launch = build(zone=zone, expose_zone=expose_zone)` when `launch_for is None`
  (`remote.py:893`). `site_cluster` passes `True`, and `discover_zones=False` stays.
- Expose always, not only under a topology. A launcher written as `--partition={zone}` has to work
  in both modes, and the relay can't know the mode at configuration time.
- Effect on no-topology launches:
  - argv is unchanged unless a launcher word holds a literal `{zone}`;
  - `--zone` is unchanged, and the default zone still has none;
  - the env gains `HYPERSHELL_ZONE`;
  - `fence_site` still sees every launch, because all of them are built eagerly.
- Add one line to §11: "relay launches expose their zone".

## Per-zone counts

Use per-identity counts on each zone identity's beat, not a `zone` field on `RosterEntry`.

- Each beat would carry its zone name and that zone's `launched` count, plus optionally
  `connected` from `Dispatcher.clients(zone_id)`. Pack them only when set, as `site` and `roster`
  are (`heartbeat.py:95-104`), so old beats and old servers stay compatible (`test_relay_roster.py:125-150`).
- Planning should use `launched`, which is the relay's own `LaunchActuator` count, pending scheduler
  steps included. That mirrors Z3, where demand is computed from `launched` and `registered` only
  feeds the logs and `own_idle` (`remote.py:534-547`).
- Counting connected clients would make a server re-grow a target every check while `srun` steps
  sit pending.
- A roster `zone` field would need a new accessor on the relay-local dispatcher (`demand` is
  private), and it would only count clients after their first request. It would also need a zone on
  the upstream `client` rows (`data/model.py:1286-1302`, high-blast), or in-memory aggregation.
- The relay supplies `zone_launched(zone)` from `cluster.autoscaler.machine.launched()` for the
  first zone and `fleet.machine.launched()` for the others. It is read from the heartbeat thread;
  the list reads are safe under the GIL.

## Tests to keep green (R6)

`tests/test_relay_*.py`:

- `connector`: the pull gate and identity.
- `db`: the private SQLite database. It drains relays and runs them as processes.
- `fsm`:
  - the puller, returner and heartbeat machines;
  - `check_site` needing the autoscaler and every fleet (`:769-800`);
  - the relay registering and pulling nothing without site clients against an in-process server
    (`:871`), so a topology fetch must stay silent against it;
  - an old server giving exactly one CRITICAL (`:896`);
  - `-N 0` refused (`:916`).
- `judge`: the drive judge.
- `launch`:
  - the fence;
  - the argv carrying no key;
  - `zone_launch` equal to the cluster's launch (`:563-573`);
  - `zone_fleet` fixed sizes (`:577-585`);
  - every zone's launch fenced (`:588`);
  - `end_site_clients` over the fleets (`:618`);
  - the fleet and identity shape (`:630-645`);
  - the zone tables (`:659-690`).
- `link`, `outage`, `return`: unaffected.
- `roster`: beat round-trips and roster attribution.
- `workload`: end to end on loopback, including the multi-zone tests (`:1052`, `:1111`).

**Exact env asserts that `expose_zone` breaks:**

- `test_relay_launch.py:334`: `launch.env == {AUTH_ENV: site_key}` for `zone='gpu'`.
- `test_relay_launch.py:572`: `other.env == {AUTH_ENV: …}`.

Both need `ZONE_ENV` added; that's a deliberate amendment, not drift. `:568`
(`same.env == site_launch(cluster).env`) stays green as long as both launches expose.
`test_cluster_launch.py:652-655` stays green if `AutoScalingCluster` defaults to `expose_zone=False`.

The argv asserts (`test_relay_launch.py:327-328,348,570-571,637-641`) use no `{zone}`, so they are
unchanged.

## Drive design

**What exists.**

- `zone_drive.sh` + `zone_shim.sh`: one shell script run under `temp_site.sh`, which provides a
  throwaway `HYPERSHELL_SITE`/DB, sets cwd to the site, and pins `UV_PROJECT`.
  - The shim appends `launch pid word env client g0` and rewrites `-H` to 127.0.0.1.
  - It `exec`s the client, so the logged pid is the client's pid.
  - Idle exit is judged by `kill -0` on the logged pids while the cluster runs.
  - The autoscaler log lines are grepped, and the judging is done in awk.
  - With `ZD_EVIDENCE`, a scrubbed `MODE.txt` is written.
- `relay_drive.sh` / `relay_probe.py` / `relay_judge.py`:
  - roles run as processes, one per node: `upstream`, `relay SITE`, `watch`;
  - the roles meet through flag files in `HSD_RUN`;
  - each relay gets its own `HYPERSHELL_SITE`, `HYPERSHELL_RELAY_DATABASE`,
    `HYPERSHELL_RELAY_UPSTREAM_AUTH`, `HYPERSHELL_RELAY_SITE` and heartrate;
  - the upstream server is an `hs server` subprocess with a low `HYPERSHELL_SERVER_EVICT`;
  - a relay is killed by SIGSTOP, then SIGKILL, after which its children are collected and ended
    (`relay_probe.py:667-698`);
  - the records hold counts and booleans only, and the judge checks `records.scrubbed`.
- `relay_drive.sh submit` is already a site-neutral batch script: `sbatch` with account and
  partition from the environment, `srun --overlap` job steps, and `relay_launch.sh` round-robining
  site-client steps over the site's nodes under a `mkdir` lock. `relay_smoke.sh` is its loopback
  rehearsal (`HSD_MODE=local`). Evidence lives in `spec/relay/evidence/cluster/`. No other spec has
  a batch script.

**`spec/relay-topology/drive/topology_drive.sh MODE`, run under `temp_site.sh`.**

- **Server.** One upstream server with sites and no launcher. The entry point is the plan's call
  (D3).
  - It uses `HYPERSHELL_SERVER_EVICT=10` and the fixed policy with a short period.
  - For `fill`, it gets a site list.
- **Relays.**
  - Relay A serves `cpu` and `highmem`; relay B serves `cpu` and `gpu`.
  - Each relay has its own home and DB, `HYPERSHELL_RELAY_SITE=A|B`, heartrate 1 and
    `[relay] timeout = 2` for idle exit.
  - The per-zone bounds go in a TOML with `[relay.zone.<z>]` tables, written into
    `HYPERSHELL_CONFIG_FILE`.
- **Shim.** `topology_shim.sh LOG SITE {zone}` extends `zone_shim.sh`. It logs:
  `launch t=EPOCH site=SITE pid word={zone} env=$HYPERSHELL_ZONE client=--zone`.
  - It drops the `hs list` call: the shim runs under the relay's environment, not upstream's.
  - Gating is judged after the run, by comparing launch times with the upstream DB's group-0
    `completion_time`.
- **Run sequence.** Run each relay as its own job (`set -m`), poll `hs list --remaining --count`
  upstream, then judge in awk.
- **Modes.**
  - **spread**: cpu work exceeds one site's max. Checks:
    - cpu launches at both A and B;
    - highmem only at A, gpu only at B;
    - word = env = `--zone` on every launch;
    - no site-zone over its advertised max.
  - **fill**: list `B` first; cpu demand is over B's max. B's cpu launches reach B's max before
    A's first cpu launch.
  - **floor**: cpu work is in group 0, gpu and highmem in group 1. B has `gpu.min=1`, A has
    `highmem.min=0`. Checks: a gpu launch exists while group 0 has work, and no highmem launch
    happens before the last group-0 completion.
  - **idle**: after the work is done, every logged pid exits on its own while both relays run,
    and no launch follows. Both relays stay connected at zero clients: the process is alive and the
    upstream `client` rows of its identities are not disconnected. Then SIGUSR1 drains each relay.
  - **kill**:
    - `kill -KILL` relay B mid-run, then end B's logged pids;
    - after eviction (`Evicting client` and `Reverting orphaned tasks` in the server log), A's cpu
      launches rise above their pre-kill count, up to A's max;
    - all tasks complete exactly once.
  - **lower**: start A2 with `site=A` and `cpu.max=1` while A runs with `cpu.max=3` (Q2). Checks:
    - the server warns naming both;
    - A launches nothing after A2 registers;
    - A's clients exit by idle timeout, logged by A as `Autoscale client disconnected`, not
      terminated;
    - A2 never has more than 1 cpu client.
  - **Open for the plan:** a lower `max` on a single relay takes effect only on reconnect, and
    config is read at start, so that case is a restart. A restart ends its own site clients
    (`reap_site_clients`). The plan should say whether `lower` is the Q2 handoff or a restart.
- `TD_EVIDENCE` writes a scrubbed `MODE.txt` to `spec/relay-topology/evidence/laptop/`.

**Cluster batch script.** Mirror `relay_drive.sh`, as `topology_drive.sh submit` or a sibling
`topology_cluster.sh`.

- **Layout:** node 0 runs the server; nodes 1 and 2 each run one relay; an optional node 3 is a
  shared leaf pool.
- **Limits:** at most 4 nodes and at most 1 hour.
- **Arguments:** the install location (`HS`, whose directory must hold `python` with psutil) and a
  scratch dir (`HSD_WORK`/`HSD_RESULTS`). There are no site names; account and partition come only
  from the environment.
- **Launches:** relays launch through a `relay_launch.sh`-style wrapper that `srun --overlap`s one
  step per client. It records the `{zone}` word and `HYPERSHELL_ZONE` before the `exec`.
- **Judging:** reuse the probe-plus-JSON-record pattern and the `records.scrubbed` discipline.
  Per site-zone, record launches, the peak launched count and the peak beside its max, plus
  evictions and reverts from the server-log regexes (`relay_probe.py:52-58`). Write a small
  `topology_judge.py` alongside `relay_judge.py`.
