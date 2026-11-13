# Research 02 — The server's entry point and the publishing autoscaler

Scope: D3 (entry point) and D4 (config keys). Citations are against `feature/relay-topology`.

## Findings (with citations)

**`hs cluster --autoscaling` today.**
- `-A/--autoscaling` takes an optional MODE (`nargs='?'`, `const=config.autoscale.policy`); `-F`, `-P`,
  `-X`, `-Y` and `-I` default from `[autoscale]` (`cluster/__init__.py:248-260`). `mode` is `autoscaling`
  whenever a policy is set, ahead of ssh/mpi/launch (`:339-348`).
- **No launcher is required.** `run_autoscaling` passes `launcher=(self.launch_mode or '')` (`:322`).
  An empty launcher builds a bare `hs client …` argv (`launch.py:225`). Running with no launcher is
  documented behaviour: `docs/security.rst:203` says "`--autoscaling` with no launcher". The help text
  says "Used with --launcher" (`cluster/__init__.py:114`), but nothing enforces it. The CLI also never
  reads `[autoscale] launcher`. The only place it is used is the `AutoScalingCluster` default
  (`remote.py:340`, `:824`), which comes from the *default* layer.
- Validation: `--ssh`, `--mpi` and `--no-db` are refused with `--autoscaling` (`:394-399`). `--restart`,
  `--forever` and a FILE only warn (`:400-406`). Further warnings cover `--timeout` with `--min-size>0`
  and dynamic without `--timeout` (`:407-422`). With no FILE the source is stdin (`:357-358`).
  `AutoScalingCluster` silently ignores `in_memory`, `no_confirm`, `forever_mode` and `restart_mode`
  (`remote.py:827-830`) and always runs the server with `forever_mode=True` (`:870`).
- **Key and port.** Every cluster mints a fresh `secrets.token_hex(64)` (`remote.py:855`), and `hs cluster`
  has no `-k`. A default port is swapped for the first free one (`cluster/__init__.py:563-564`). `evict_after`
  is never passed, so it is `DEFAULT_EVICT` from the default layer and `[server] evict` is ignored
  (`remote.py:851`).
- **Construction** (`remote.py:854-904`). It builds a `ServerThread` and a `partial(client_launch, …)`, with
  `own = ZoneRef.from_name(zone)` and `launch_for` (only with `discover_zones`). `registered` is
  `server.queue.dispatcher.clients(zone_id)`, which counts relay *identities* (D6). `scheduler_group` is
  `scheduler.machine.active_group` (`:898-899`, D2). All of it goes into an `AutoScalerThread`.
  `run_with_exceptions` starts the server, then `wait_scheduling()`, then the autoscaler, then joins both
  (`:906-912`).

**`AutoScaler` FSM** (`remote.py:344-601`). The states are START→INIT⇄SCALE, then WAIT→CHECK→CHECK_FIXED or
CHECK_DYNAMIC→SCALE or WAIT, plus CLEAN and FINAL. Both `check_fixed` and `check_dynamic` simply call
`check_zones` (`:514-520`). `check_zones` (`:531-556`) runs `current_group()` → `read_zone_loads(own=own,
discover=…, own_idle=…)` → `fleet_demand(launched=actuator.launched())`. It then caps each zone at +1 per
check, logs, and fills `pending`. `scale` calls `actuator.apply(pending)`, or `actuator.launch_one()` when
nothing is pending (the INIT path, `:581-591`). `current_group` falls back to `Task.starting_group`
(`:522-529`).

**How far `AutoScaler` depends on `LaunchActuator`.** The surface is narrow, but the actuator is
constructed inside `__init__` (`:428`) and cannot be injected. The autoscaler touches `launch` (property,
`:433-436`), `clients` (property and setter, `:438-446`), `launched()` (`:453-455`), `launch_for`
(through `discover`, `:457-460`), `describe()` (`:479`), `apply()`/`launch_one()` (`:584-587`) and
`clean()` (`:593-595`). To run with a `PublishActuator`:
1. Inject it. Add `actuator=` (with `launch` optional when it is given), and keep `AutoScaler(launch, …)`
   positional, because the relay (`relay/site.py:105`, `:150`) and the tests construct it that way.
2. Allow **no own zone**. Today `own_zone=None` means the default zone (`:423`), but the server needs
   `own=None`. Guard the uses that assume an own zone: `own_idle` (`:543-546`, `own.id`) and
   `init`'s `fleet_demand(own=…)` (`:485`). With `own=None` the initial demand is `{}`, so INIT goes
   straight to STEADY and the `launch_one` fallback is never reached.
3. Make `discover` an attribute instead of `launch_for is not None`.
4. **Semantic gap.** `ScalingKnobs` are uniform (`demand.py:68-76`), and with `own=None` `zone_demand`
   gives no zone an init or min (`demand.py:146-155`). Under **fixed**, a zone with work gets exactly
   **one** client in total (`:155`). Under dynamic it grows +1 per check up to `knobs.max_size` (`:154`).
   The relay floors (`min`, and `init` at start, R1) and the cap (the sum of the live sites' advertised
   `max`) therefore cannot come from `fleet_demand`. The publishing glue should either call `zone_demand`
   per zone with per-zone knobs (max = Σ live-site `max`), or clamp in the split. In both cases it adds
   the per-site floors in the split. That is allowed: the seam takes knobs and fleet sizes as inputs and
   stays unchanged.
5. `launched()` for the publisher must count what it has *asked for* per zone. That is the published
   entries reconciled with relay-reported counts (the accounting topic), not only the clients that have
   registered. Otherwise pending launches at the sites (batch queue wait) would make it over-publish.

**`hs server`** (`server.py:1528-1769`). Its options are FILE, `--forever`, `--restart`, `-b -w -c -m -W
-r --eager -Q -H -p -k --no-db --initdb --no-confirm --print -f`, the TLS options and a hidden
`--available-ports`. `-A`, `-F` and `-P` are free. The key comes from `-k`, else `resolve_auth('server')`
(`:1666-1667`). Relays reach the same key through `resolve_auth('relay','upstream')`
(`relay/upstream.py:156`). `--forever` runs `serve_forever` (`:1671-1682`, `:1446-1526`), which honours
`[server] evict` (`:1681`). `check_args` refuses FILE together with `--forever`, and `--forever` together
with `--restart` (`:1702-1711`). `ServerThread` exposes `queue` (with `.dispatcher`), `scheduler`
(`SchedulerThread.machine.active_group`, `:343-351`, `:406-423`; None with `--no-db`), `heartmonitor`
(`HeartMonitorThread.machine` with `beats`/`rosters`, `:686-728`, `:900-912`), `start()` and
`wait_scheduling()` (`:1101-1140`). The relay tests already run the upstream as `hs server --restart …`
with a configured key. `cluster/remote.py:31` imports `server.py`, so `ServerApp` must import any
cluster-side class lazily.

## Entry-point options compared

| | (A) `hs cluster --autoscaling` + `--sites` (or "no `--launcher`") | (B) `hs server --autoscaling [MODE]` |
|---|---|---|
| Existing behaviour | "No launcher" already means local `hs client` (documented), so it **cannot** mean sites; a new flag is needed | New option only; without it `run()` is unchanged |
| Key | Mints a random key, has no `-k`, so relays cannot join; it would have to break the "fresh key per cluster" invariant for this mode | Already uses the operator key relays are configured with |
| Port / evict | Port auto-reselected; `[server] evict` ignored (hurts R3 liveness) | Fixed port; honours `evict` |
| Irrelevant options | About 15 client-launch options plus `-I/-X/-Y` become meaningless and need refusals | Only `-F/-P` (and a policy) are added |
| Kubernetes, no launcher | Wrong shape (a "cluster" launches clients); stdin is the source by default | Exactly the shape: a long-running server |
| Wiring cost | `AutoScalingCluster` already wires `scheduler_group` | A small thread class: build `ServerThread(forever_mode=True)` plus the publishing autoscaler, wired like `remote.py:896-903` |

## Recommendation

**(B): `hs server --autoscaling[=MODE] [-F VALUE] [-P SEC]`**, with `--autoscaling` implying `--forever`.
Refuse it with `--no-db` (as `cluster/__init__.py:398`), with FILE (as `--forever` already is), and with
`--restart` (or warn it is redundant). Warn or refuse `--no-confirm`, since `check_relay` already logs an
error for a relay under it (`server.py:800-805`). Because `hs server` has no launcher, `--autoscaling`
there unambiguously means "with sites", so no `--sites` flag is needed. Under this mode the server's
`[autoscale] size.*` and `launcher` keys do not apply, consistent with the foreman reading. Put a new
`AutoScalingServer`-style thread in `cluster/` (or a new `cluster/topology.py`). It should construct
`AutoScalerThread(actuator=PublishActuator(…), own_zone=None, discover=True, attempts=max_retries+1, eager,
scheduler_group=…)` and import lazily from `ServerApp.run`. `hs cluster` stays byte-identical. Also
document in R7 that `hs cluster --autoscaling` remains the launching form.

## Config keys proposal

```toml
[autoscale]
distribution = "spread"   # spread | fill (new; `policy` stays fixed|dynamic, D4)
sites = ["east", "west"]  # optional fill order; unlisted live sites follow in first-registration order
```

- **Single-word keys are required.** `Environ.expand` splits environment names on `_`
  (`cmdkit/namespace.py` `expand`), so a key such as `site_order` would become `autoscale.site.order`.
- Environment forms: `HYPERSHELL_AUTOSCALE_DISTRIBUTION=fill` and `HYPERSHELL_AUTOSCALE_SITES=east,west`.
  The environment yields a **string** (verified: `config.autoscale.sites == 'east,west'`), and values are
  coerced to int, bool or None (`_coerced`). The parser must accept a list or a comma-separated string,
  and `str()` each name. Precedents: `ssh.nodelist` str|list (`cluster/ssh.py:351-366`) and `relay.zone`
  str|list|table (`relay/__init__.py:232-264`). TOML lists load as-is.
- Defaults go in `core/config.py:176-186` (`'distribution': 'spread'`, and `'sites': PARAM_UNSET`, as
  `relay.zone` does at `:202`). A new default key shows up automatically in `ACTIVE_CONFIG_VARS`
  (`core/config.py`, used by `hs config get --list-available`). An unknown distribution raises
  `ConfigurationError` with `blame()` (pattern `get_logging_style`, `:301-311`).
- Docs: `docs/_include/config_param_ref.rst:487-561` (`[autoscale]`; add `.distribution` and `.sites`,
  and note that `.size.*`/`.launcher` do not apply to `hs server --autoscaling`). Also
  `docs/_include/server_usage.rst`, `server_help.rst` and `server_desc.rst`.
- Completions: bash `share/bash_completion.d/hs:192` and zsh `share/zsh/site-functions/_hs:274` (add
  `autoscale.distribution) spread fill`), plus the `hs server` option specs in both files. The man pages
  under `share/man/man1/` also carry `[autoscale]` (`hs.1:2809`).
- Relay-side note for the sibling topic: use flat `init`/`min`/`max` beside `size` in `[relay]` and in
  `ZONE_OPTIONS` (`relay/__init__.py:228`). A nested `size.*` would collide with the scalar `size`.

## Founding-case tests

- `tests/test_autoscale.py`: `TestCheckDynamic` (pressure read for the active group, low pressure waits,
  high pressure capped by max, min-size scales, unknown pressure starved/bootstraps/with clients) and
  `TestCheckFixed` (min-size, steady state, starved group, bootstrap), plus `test_count_remaining_by_group`.
  `make_scaler` builds `AutoScaler(launch=…)` and assigns `scaler.clients` (`:26-31`).
- `tests/test_zone_autoscale.py`: `TestZoneDemand`/`TestFleetDemand` (the pure seam, including
  `test_without_own_zone` `:138`), `test_autoscaler_decides_through_demand` (bit-for-bit with the old
  single fleet), `test_autoscaler_own_named_zone`, `TestReadZoneLoads`, `TestLaunchActuator`,
  `test_autoscaler_scales_through_its_actuator` (`launch`/`clients` are the actuator's, `:411-428`),
  `TestZonedAutoScaler` (gating, next group, retry edge, max per zone, one launch per zone per check,
  registered per zone, never ends or terminates), `test_cluster_discovers_zones_by_default`,
  `TestSchedulerGroup`, `TestCheckLog` and `test_relay_fleet_names_its_zone`.
- `tests/test_cluster_launch.py -k autoscaling`: key off command lines (`:590`), logs never hold the key
  (`:606`), every client option kept and `machine.launch is cluster.launch` (`:626`), zone named to the
  launcher (`:642`), one launch per scale (`:658`, `AutoScaler(launch, …)` positional).
- `tests/test_cluster.py`: usage and help equal `ClusterApp.interface` text, mixed modes, missing option
  values for `-F -P -I -X -Y --zone` (`:99-138`).
- `tests/test_server.py`: usage and help equal `ServerApp.interface` text (`:24`, `:34`), mixed modes,
  bad options, missing values. These move automatically with (B), but add cases for the new refusals.
- `tests/test_completions.py`: zsh bracket lint (`:61-89`), which also guards any new `hs server` specs.
- `tests/test_relay_launch.py` (pins `AutoScaler`/`AutoScalingCluster` use by the relay):
  `machine.launch` (`:326`), `machine.clients` assignment (`:623-624`), `machine.min_size` (`:637`), and
  the zone-fleet tests.
- **Drive.** `spec/zone-autoscale/drive/zone_drive.sh MODE`, run from the repository root as
  `.agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" MODE`. Its modes are
  `founding` (default zone, fixed, no `--zone`), `fixed` (default and cpu in group 0, gpu in group 1),
  `dynamic` (max 3, pressure growth) and `restart` (a late lower group, init 0). Each mode runs
  `hs cluster --restart --autoscaling=$policy --launcher="sh zone_shim.sh $log {zone}" -T 2 -P 1 -I -X 0 -Y`.
  It checks that every task completes, that clients idle-exit, the zone naming in `{zone}`,
  `HYPERSHELL_ZONE` and `--zone`, and gating. Settings are `ZD_DEADLINE` and `ZD_EVIDENCE`. Under (B)
  all four modes must still pass unchanged, as the byte-identical guard for `hs cluster`.
