# Research 02 — relay consumers of the autoscaler, and the relay-topology seam

Read against the `zone-autoscale` worktree (2026-11-12). Paths are repo-relative.

## 1. What `relay/` and its tests rely on

**Source.**
- `relay/site.py:104-110` `site_cluster` builds `AutoScalingCluster(source=None, max_retries=0,
  policy='fixed', init_size=size, min_size=size, max_size=size, zone=zone, launcher=launcher or '', …)`;
  `:111-112` reaches `cluster.server.confirm.machine.strict`.
- `site.py:116-118` `site_launch(cluster)` **returns `cluster.launch`**: a single `ClientLaunch`.
- `site.py:121-143` `zone_launch` calls `client_launch(..., zone=zone)` with the cluster's
  `server.queue.config` port and auth: a further zone's launch is its own `ClientLaunch` object.
- `site.py:146-151` `zone_fleet` = `AutoScalerThread(launch, policy='fixed', period, init=min=max=size)`
  and then **sets `fleet.name = 'hypershell-autoscaler-<zone>'`** after construction.
- `site.py:248-251` `check_invocation` reads `launch.argv` and `launch.env`; `:275` fences
  `site_launch(cluster)` plus each `launches`.
- `site.py:292` `end_site_clients` flattens with
  `for proc in list(autoscaler.machine.clients)` and calls `poll/wait/terminate/kill`. So **`machine.clients`
  must iterate as `Popen`s**.
- `relay/__init__.py:458-468` builds the cluster and `fleets`; `:502` reads `fleet.machine.launch`;
  `:527-528` starts fleets after the site is ready; `:603-605`, `:698-701` stop `cluster.autoscaler` and
  each fleet (`.stop()`); `:639-648` `check_site` uses each autoscaler's `ident/is_alive/join/name`;
  `:684`, `:702` reap through `end_site_clients`. The site size is validated as at least 1 (`:941`, `:984`).

**Contract relied on:** `AutoScalingCluster.launch` is one `ClientLaunch` and is the same object as
`cluster.autoscaler.machine.launch`; `AutoScaler.launch` is the `ClientLaunch` passed in;
`AutoScaler.clients` is a flat, assignable list of `Popen`; `AutoScaler` exposes `policy`, `period`,
`init_size/min_size/max_size`; `AutoScalerThread(launch, policy=…, period=…, init_size=…, min_size=…,
max_size=…)` keeps that signature, and its `name` stays assignable; its `stop/start/join/is_alive` are a
`Thread`'s.

**Tests that would break.**
- *If `clients` became a dict:* `tests/test_relay_launch.py:620-626` assigns
  `cluster.autoscaler.machine.clients = [...]` and `fleet.machine.clients = [...]`, then `end_site_clients`
  iterates them (`list(dict)` would yield zone keys, and `.poll()` would fail). Cluster-side tests break too:
  `tests/test_autoscale.py:31` sets `scaler.clients = [None] * n`, and only `len()` is read.
- *If `AutoScalingCluster.launch` became per-zone (a dict or factory):* `test_relay_launch.py:326` checks
  `cluster.autoscaler.machine.launch is launch`; `:327-336` and `:346-351` read `site_launch(cluster).argv/.env`;
  `:373` reads `relay.cluster.launch.argv`; `:568-570`, `:637` call `site_launch(...).argv`; and every
  `fence_site` test (`:338`, `:352`, `:384`, `:401`, `:593-594`) goes through `check_invocation`. On the
  cluster side, `tests/test_cluster_launch.py:633-638` and `:649-655` break the same way.
- *If R3 put `HYPERSHELL_ZONE` into `client_launch`'s env whenever `zone` is set:* these tests break, because
  each asserts `env` equality on a zoned launch: `test_relay_launch.py:335`
  (`launch.env == {AUTH_ENV: site_key}`, zone `gpu`) and `:575` (`other.env == {AUTH_ENV: …}`, zone `gpu`).
  If `{zone}` were substituted inside `client_launch`, relay launchers would change too. So R3's exposure
  must not live unconditionally in `client_launch`.
- `tests/test_relay_fsm.py:713-730` stubs `cluster` as `SimpleNamespace(autoscaler=Part)`; `:779-800`
  checks fleet thread names. These are safe while `cluster.autoscaler` stays one thread.
- `test_relay_launch.py:580-585` checks `machine.policy`, `machine.period`, `init_size == min_size == max_size`
  and the thread name. `:637-644` checks `machine.min_size` and `machine.launch.argv/env` on each fleet.
- The `test_relay_workload.py:1051-1097` integration test (a relay with two zones, one site client per
  zone) is the behavioural guard for item 2.

## 2. Zone discovery in the relay's site cluster, unchanged

The relay database copies each pulled task's upstream `zone` column verbatim (`relay/store.py:179-198`).
Zone ids are `uuid5` of the name (`data/model.py:122-126`), so the ids match upstream. But **no `Zone` rows
exist** in the relay DB for pulled zones: only the default zone is ensured (`server.py:214`), and
`insert_received` never calls `Zone.ensure`. If discovery ran in the site cluster of a relay serving
`cpu, gpu, default`:
- **Both** the site cluster's autoscaler (own zone `cpu`) and each `zone_fleet` autoscaler would see the
  other served zones' current-group work and launch for them. Under the fixed site policy, each discovered
  zone gets at least one client (the rule "launched 0 and tasks remain → launch", or min-size). Every zone
  would end up with duplicate fleets.
- Those clients would launch with the **wrong launcher**. The site cluster's `ClientLaunch` carries the
  first zone's launcher, while `[relay.zone.<name>]` launchers (for example `mpirun` for `gpu`,
  `test_relay_launch.py:632-639`) live only on that zone's fleet.
- A discovered id with no `Zone` row has no name to put after `--zone`. A non-default zone would therefore
  be dropped or launched wrongly, but the default zone (nameable) would get a second default fleet.

Under the fixed policy with `size ≥ 1`, the min-size branch fires first
(`cluster/remote.py:476`), so a **zone-filtered** `group_task_count` for the site cluster's own zone changes
only debug logs. Per-zone *counting* is invisible to the relay; per-zone *launching* is the hazard.

**Candidate seams.** In every option, `AutoScaler(launch: ClientLaunch, …)` and
`AutoScalerThread(launch, …)` keep the meaning they have today: one launch, used verbatim, for one zone,
with no discovery and no zone exposure. That alone makes `zone_fleet` safe with no edit.

| Seam | `relay/` lines | Exact? | Notes |
|---|---|---|---|
| **S1** Explicit zone set on `AutoScalingCluster` (`zones=None` means discover); `site_cluster` passes `zones=[zone]` | 1 (`site.py:104-110`) | Yes, if the explicit set also means "one launch, verbatim, no R3 exposure" | Couples two meanings to one argument. Exposure for an explicitly zoned `hs cluster` would need its own switch. |
| **S2** Discover only under the dynamic policy | 0 | Yes for the relay | **Breaks R2/R4** for `--autoscaling=fixed`: a fixed zoned cluster keeps the Z2 R10 stall. Reject. |
| **S3** Opt-in flag `discover: bool = False` on `AutoScalingCluster`, with R3 exposure tied to it; `ClusterApp.run_autoscaling` (`cluster/__init__.py:319-322`, the only CLI construction) passes `discover=True` | **0** | Yes, structurally: the relay passes nothing new and gets today's single-launch path | The library default differs from the CLI. A `AutoScalingCluster.new(...)` caller must opt in to get R2. |
| **S4** Discovery default on, `site_cluster` passes `discover=False` | 1 | Yes | The same mechanism as S3 with the default flipped. R2 becomes the library default too. |
| **S5** Rely on missing `Zone` rows in the relay DB | 0 | **No**: the default zone is still discovered | Accidental. Reject. |

A zero-edit option exists (**S3**), and it is clean as long as one flag gates both discovery and R3
exposure.

## 3. What relay-topology needs from Z3

**Demand on the server (topology R2).** "Derive total demand from the existing pressure signal (group- and
zone-scoped, per Z3) and publish … per-site (and, where zones are active, per-zone) client counts"
(`issues/relay-topology.md:62-66`). Per-site `min/max/init` and the `fill`/`spread` split belong to
relay-topology (R1). So Z3 should output a **per-zone desired total**, before any split across sites.
- **Inputs:** current group; for each zone, the current-group remaining count and pressure (or `None`); the
  uniform knobs (policy, factor, init/min/max); the own zone (for the GOAL's init/min edge rule); the
  phase (INIT or STEADY); and **a fleet size per zone**.
- **Fleet size on a publishing server:** no `Popen` exists. *Last published desired* is wrong. Today's
  logic only grows or holds relative to `launched`, and only exits shrink `launched` (`remote.py:534-546`).
  A count that never shrinks would never let a reduced entry (topology R5) happen. The input must be live:
  per zone, the **launched counts relays report** (a heartbeat field relay-topology's R4 adds), or the
  **registered site clients per zone**. Today neither exists upstream. Roster entries carry no zone
  (`relay/store.py:276-286`). `Client` has `site`/`relay` but no zone (`data/model.py:1258-1261`).
  `Dispatcher.clients(zone)` counts relay *identities* (one per zone), not site clients
  (`core/dispatch.py:412-415`). Z3 should therefore take fleet size as an **injected input**, not read
  `len(self.clients)` inside the decision.
- **Stepping:** today one client is launched per zone per check in STEADY (`remote.py:529-532`). INIT loops
  without waiting until `init_size` (`:437-443`). Keep the step in the demand function's output (a target of
  at most `launched + 1` in STEADY), so the local actuator converges by launching `target - launched`
  and behaviour stays exact.

**Relay-side reconciliation (topology R2, R5, R6).** A reconciler that converges "desired N for zone z" is
today's fixed fleet with a target that can be changed. It needs: a per-zone launch built *by the relay*
(the existing `zone_launch` objects, each with its own launcher, so it is a mapping or callable, not "rewrite
`--zone` on one launch"); a per-zone fleet record (`clients`, `clean()`, `launch_one()`); never-kill
semantics (scale-down stays idle-exit); and a flat view of every `Popen` for `end_site_clients`.

## Recommendations

1. **Use seam S3:** `discover: bool = False` on `AutoScalingCluster` and `AutoScaler`, passed `True` by
   `ClusterApp.run_autoscaling`. It makes no `relay/` edit. With the flag off, `AutoScalingCluster` stays
   exactly today's: one `ClientLaunch`, verbatim, with no `HYPERSHELL_ZONE` and no `{zone}`. If the plan gate
   wants R2 as the library default, use S4 instead (one line in `site.py:104-110`, `discover=False`).
2. **Keep the relay contract:** `AutoScalingCluster.launch` stays the own-zone `ClientLaunch`, and
   `autoscaler.machine.launch is cluster.launch`. `AutoScaler.clients` stays a flat, assignable
   `List[Popen]` across all zones. Track zones beside it (`zone_of: Dict[int, str]` keyed by `id(proc)`;
   an untracked entry counts as the own zone, so the `[None] * n` and `Proc` stand-ins still work).
   `AutoScalerThread`'s signature and assignable `name` stay as they are.
3. **Put R3 exposure on the discovering launch path,** not in `client_launch` unconditionally. For example,
   use `client_launch(..., expose_zone=False)` defaulting off, or a `zone_launch_for(base, name)` helper
   that `AutoScalingCluster` uses only when `discover=True`.
4. **Seam shapes** (in `cluster/remote.py` or a new `cluster/autoscale.py`; the plan's call):
   ```python
   @dataclass(frozen=True)
   class ScalingKnobs: policy: AutoScalerPolicy; factor: float; init_size: int; min_size: int; max_size: int

   @dataclass(frozen=True)
   class ZoneLoad: zone: str; name: Optional[str]; group_tasks: int; pressure: Optional[float]

   def zone_loads(group: int, zones: Optional[Collection[str]] = None) -> Dict[str, ZoneLoad]:
       """DB reads only; zones=None discovers current-group zones (ids resolved to names via Zone)."""

   def zone_demand(load: ZoneLoad, launched: int, knobs: ScalingKnobs, *, own: bool, initial: bool) -> int:
       """Pure: today's check_fixed/check_dynamic as a target count for one zone (≤ launched+1 when steady)."""

   class ScaleActuator(Protocol):
       def launched(self, zone: str) -> int: ...
       def apply(self, desired: Mapping[str, int]) -> None: ...   # never terminates clients
       def clean(self) -> None: ...

   class LaunchActuator:  # Z3's local launch; relay-topology reuses it as the relay reconciler
       def __init__(self, launch_for: Callable[[Optional[str]], ClientLaunch], own: Optional[str]) -> None: ...
       clients: List[Popen]   # flat, every zone
       def launch(self, name: Optional[str]) -> Popen: ...
   ```
   `AutoScaler.check` becomes `clean → zone_loads → zone_demand per zone → actuator.apply`.
   relay-topology then adds a `PublishActuator` (where `launched` comes from relay-reported or roster
   counts and `apply` publishes the versioned topology) and a relay reconciler, which is a `LaunchActuator`
   over `{zone: zone_launch(...)}` driven by the fetched entry, or by the local size when there is no
   topology (R6). Z3 needs none of these.
5. **Discovery must skip, with a warning, a zone id that has no `Zone` row,** rather than launch a nameless
   `--zone`.
