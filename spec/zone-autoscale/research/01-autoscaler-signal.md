# Research 01 — autoscaler internals, the data-model signal, founding-case tests

Against `develop` `8a85d46` (worktree `zone-autoscale`). Paths are under `src/hypershell/` unless noted.

## 1. The AutoScaler FSM as it is

- States `AutoScalerState` (`cluster/remote.py:342-353`): START, INIT, WAIT, CHECK, CHECK_FIXED,
  CHECK_DYNAMIC, SCALE, CLEAN, FINAL, HALT. CLEAN is mapped (`:427`) but never returned; `check()`
  calls `clean()` directly (`:460`). Phases `AutoScalerPhase` (`:370-374`): INIT, STEADY, STOP —
  **STOP is never assigned anywhere** (grep: no reference outside the enum).
- Flow: START → INIT (`:431-435`). INIT: `len(self.clients) < init_size` → SCALE, else phase=STEADY →
  WAIT (`:437-443`). SCALE launches one `Popen` via `self.launch.start(...)`, appends it, returns INIT
  while in phase INIT else WAIT (`:524-532`). WAIT in STEADY sleeps 1 s until `period` has passed since
  `last_check` (set in `__init__` `:413`, so the first check is one period after construction), then
  CHECK; outside STEADY it returns FINAL (`:445-456`, unreachable in practice). CHECK: `clean()`,
  reset `last_check`, dispatch on policy (`:458-465`).
- **At most one launch per period in STEADY** (SCALE → WAIT, and CHECK reset the clock). A per-zone
  design that can scale several zones in one check needs a SCALE that drains a list of pending
  (zone) launches, or accepts one launch per period overall — a decision for the plan.
- `check_fixed` (`:467-485`), in order: `launched < min_size` → SCALE; `launched == 0 and
  group_remaining == 0` → WAIT; `launched == 0 and group_remaining > 0` → SCALE; else WAIT. Fixed never
  uses max-size (min-size is the fleet size; relay sets init=min=max).
- `check_dynamic` (`:487-522`): `launched < min_size` → SCALE; pressure known: `> 1` → SCALE unless
  `launched >= max_size` (WAIT), else WAIT; pressure None: `launched == 0 and group_remaining == 0` →
  WAIT, `launched == 0 and group_remaining > 0` → SCALE, else WAIT.
- Inputs per check: `len(self.clients)`; `Client.count_connected()` (**log only**, `:470`, `:490`);
  `Task.count_remaining()` (all groups, log only); `Task.current_group()` (DB-derived from the most
  recently scheduled row, `data/model.py:641-669`, not the scheduler's in-memory group);
  `Task.count_remaining(group=g)`; dynamic also `Task.task_pressure(self.factor, group=g)`.
- `self.clients: List[Popen]` (`:388`, `:415`): only `len()` (checks, init), append (scale), and
  `poll()` filtering (`clean`, `:534-546`). No zone recorded anywhere; one `ClientLaunch` serves all
  (`:816-823`).
- **Stopping:** the machine never halts on its own. Only `AutoScalerThread.stop()` → `machine.halt()`
  (`:576-580`; `core/fsm.py:47-52,80-82` returns HALT on the next step, so ≤1 s latency from WAIT).
  `AutoScalingCluster.run_with_exceptions` joins the autoscaler then the server (`:828-834`); the server
  is `forever_mode=True` (`:805`), so the cluster runs until `stop()` (signal/caller). The test harness
  says so explicitly (`tests/test_cluster_launch.py:208-209`). The relay treats an autoscaler that
  ends while the site runs as a death (`relay/__init__.py:639-649`), so **a per-zone autoscaler must
  still never end by itself**.
- External readers (src + tests):
  - `relay/site.py:104-112` builds `AutoScalingCluster(policy=fixed, init=min=max=size, zone=zone)`;
    `:145-150` `zone_fleet` = `AutoScalerThread(launch, fixed, init=min=max=size)` per further zone;
    `:292` `end_site_clients` iterates `list(autoscaler.machine.clients)` over cluster + fleets
    (needs a flat iterable of `Popen`).
  - `relay/__init__.py:502` reads `fleet.machine.launch`; `:603`, `:639-649`, `:699` stop/is_alive the
    cluster's autoscaler thread.
  - `tests/test_relay_launch.py:326`, `:581-584` (policy, period, init/min/max sizes), `:623-624`
    **assign** `machine.clients = [...]` lists, `:637-644` (`machine.min_size`, `machine.launch.argv/env`).
  - `tests/test_cluster_launch.py:637` (`cluster.autoscaler.machine.launch is cluster.launch`),
    `:649-660` (`AutoScaler(launch, policy='fixed', max_size=2)`, `scaler.clients = []`, `scale()` twice,
    `scaler.launch`, `len(scaler.clients) == 2`, iterates `client.wait/args`).
  - `tests/test_autoscale.py:21-32` (`AutoScaler`, `AutoScalerState`, `scaler.clients = [None]*n`).

## 2. The pressure signal (`data/model.py`)

- `count_remaining(group=None)` `:841-846`: `completion_time IS NULL` [+ `group == g`]. Counts
  unscheduled **and** in-flight rows (lifecycle §1). Failed rows awaiting retry have `completion_time`
  set, so they are not counted until the scheduler creates the retry row (`:818-838`).
- `latest_server()` `:1118-1129`: server_id of the most recently scheduled row (group_by server_id).
- `effective_rate_by_client()` `:1132-1152`: per connected client of the latest server,
  `n / (max(completion) - min(start))`, joined to `client`, completed rows only. `effective_rate()`
  `:1155-1160` = sum. `avg_duration()` `:1163-1178`: `avg(duration)` over the same population. **Neither
  is group- nor zone-scoped.**
- `time_to_completion(group)` `:1181-1186` = `count_remaining(group)/effective_rate()`; docstring says
  "unscheduled" but it counts incomplete. `task_pressure(factor, group)` `:1189-1197` =
  `ttc / (factor * avg_duration)`. Note the walrus: a `ttc` of 0 (no remaining) yields **None**, not 0,
  so "no work" lands in the pressure-unknown branch of `check_dynamic` (→ WAIT), keep that.
- `_filter_zone(query, zone)` `:727-733`: None = every zone, `DEFAULT_ZONE_ID` → `zone IS NULL`, else
  equality. `Task.zone` is `UUID` = Text on SQLite, native `uuid(as_uuid=False)` on PostgreSQL
  (`:146`, `:376`), so values come back as `str` on both.
- `index_tasks_zone (zone, group, schedule_time, submit_time)` `:1242`; `index_tasks_unscheduled
  (group, schedule_time)` `:1227`.

**Proposed minimal signatures** (default `zone=None` = today, every zone; all apply `cls._filter_zone`):

```python
count_remaining(cls, group: int = None, zone: str = None) -> int
effective_rate_by_client(cls, zone: str = None) -> Optional[Dict[str, float]]
effective_rate(cls, zone: str = None) -> Optional[float]
avg_duration(cls, zone: str = None) -> Optional[float]
time_to_completion(cls, group: int = None, zone: str = None) -> Optional[float]
task_pressure(cls, factor: float, group: int = None, zone: str = None) -> Optional[float]
count_remaining_by_zone(cls, group: int) -> Dict[str, int]   # new: discovery + per-zone counts
```

`count_remaining_by_zone` mirrors the existing `report_held` query (`server.py:365-370`) with the
lifecycle predicate changed to incomplete:
`Task.query(Task.zone, func.count(Task.id)).filter(Task.group == g).filter(Task.completion_time.is_(None)).group_by(Task.zone)`,
mapping the NULL key to `DEFAULT_ZONE_ID`. One round trip gives both the active-zone set (R2) and each
zone's remaining count. Dialects: `GROUP BY`/`DISTINCT` collapse NULLs into one group on both SQLite and
PostgreSQL; grouping a native uuid is fine; avoid `min`/`max` over the uuid column on PostgreSQL (no
uuid `min`, see `:697-699`, which casts to Text). `latest_server()` stays unscoped (one server owns the
DB, GOAL non-goal). Always pass `DEFAULT_ZONE_ID` (never None) for the default zone in multi-zone use,
or its pressure silently spans every zone.

**Retry edge (flag for the plan):** the dispatcher always keeps a default-zone pool
(`core/dispatch.py:210`) but refills a named zone only while a client of it is demanding
(`:362`), so retry rows for a named zone with no client are never created. A non-own zone whose
only current-group work is retryable failures shows 0 remaining, is never discovered, and holds the
group (`increment_group` counts retryables, `:690-692`). Today's `--zone blue` autoscaler has the
same latent edge. Fix option: discovery also counts `query_retryable(attempts, group=g, zone=z)`
under the `count_retryable` rule (`:764-773`; filters §2-correct), which needs `max_retries`/`eager`
passed into the AutoScaler. Without it, the single-zone decision table is unchanged.

**Per-zone vs global rate/duration.** Global: a GPU zone with 20 long tasks divided by a CPU fleet's
huge rate reads near-zero pressure and never grows past its bootstrap client; dividing by the global
mean duration also distorts it. Per-zone (`_filter_zone` on the `Task` rows; a client serves exactly one
zone, so task-zone ≈ client-zone): correct for heterogeneous campaigns; a zone with no completions yet
returns None, which falls into today's pressure-unknown bootstrap branch (launch one if none launched,
else wait) — exactly the R2/R4 behaviour wanted. In a single-zone run every latest-server row is in that
zone, so filtered == unfiltered (R4 identity). Cost: one or two more small queries per zone per period.
**Recommend per-zone rate and per-zone avg_duration.**

## 3. Registered-client counts per zone

`Client.count_connected()` (`:1313-1320`) has no zone and the `client` table no zone column; it is used
**only in log lines** today (`cluster/remote.py:470-475`, `:490-498`). Decisions use the launched count
only, so per-zone **launched** count is what R1 needs to drive decisions; registered per zone is for
logging. Without a schema change, `Dispatcher.clients(zone)` (`core/dispatch.py:412-415`, lock-guarded,
in-process) counts clients that have sent a work request and not been `forget`-ten
(`server.py:788`, `:877`). It is reachable from the cluster: `AutoScalingCluster.server.queue.dispatcher`
(`ServerThread.queue = QueueServer` `server.py:1058`; `QueueServer.dispatcher` `core/queue.py:1016-1022`).
It lags registration (a client appears on its first request). Recommend injecting an optional
`registered: Callable[[str], int]` into `AutoScaler` (the cluster passes `server.queue.dispatcher.clients`),
falling back to `Client.count_connected()` for the log line when absent (standalone scalers in tests,
relay fleets).

## 4. Tests that exercise the autoscaler

Markers: only `unit`/`integration` (`pyproject.toml:142-147`, `--strict-markers`).
`tests/test_cluster_launch.py` is module-level `pytestmark = mark.integration` (`:52`).
Run: `uv run pytest -m unit tests/test_autoscale.py tests/test_relay_launch.py -k "Check or fleet"`
(17 pass today, 0.65 s); integration: `uv run pytest -m integration tests/test_cluster_launch.py -k autoscaling`.

`tests/test_autoscale.py` — **founding cases; assertions must stay unchanged**:
- `TestCheckDynamic` (unit): `test_pressure_computed_for_active_group` (pressure asked as `(factor, group)`
  = `(2, 4)`, high → SCALE); `test_low_pressure_waits_despite_backlog`; `test_high_pressure_capped_by_max_size`;
  `test_min_size_scales` (empty DB, min-size 1 → SCALE); `test_unknown_pressure_starved_group_waits`
  (later-group backlog, 0 in group → WAIT); `test_unknown_pressure_bootstraps_active_group`;
  `test_unknown_pressure_with_clients_waits`.
- `TestCheckFixed` (unit): `test_min_size_scales`, `test_steady_state_waits`, `test_starved_group_waits`,
  `test_bootstraps_active_group`.
- `test_count_remaining_by_group` (integration, real submit; `count_remaining()`/`(group=)` values).

**Mocking seams (will break under a naive refactor):** `make_scaler` (`:26-32`) builds a real
`client_launch` and sets `scaler.clients = [None] * n` ("only len() is consulted"). `patch_queries`
(`:35-53`) monkeypatches `Task.current_group` (`lambda:`), `Task.count_remaining` (`lambda group=None:`),
`Task.task_pressure(factor, group=None)` and `Client.count_connected` (`lambda:`); `calls['task_pressure']`
must equal `(factor, group)`. Consequences: (a) passing `zone=` to these breaks with TypeError;
(b) any new unmocked query (e.g. `count_remaining_by_zone`) runs against whatever engine `Session` is
bound to; (c) `check_fixed()`/`check_dynamic()` are called with no arguments and must return an
`AutoScalerState`; (d) `clients` items may be `None`. Either keep these call shapes on the default/own-zone
path, or make a declared harness-only edit to `patch_queries` (accept `zone=None`, record it outside the
asserted tuple, stub the discovery query to `{}`/own zone) with assertions untouched — the plan gate
should approve which.

Other autoscaler tests that must stay green: `test_cluster_launch.py` —
`test_autoscaling_mode_keeps_key_off_command_lines` (5 cases incl. `fixed-tls-zone` with `zone='blue'`,
end-to-end, all tasks complete), `test_autoscaling_mode_logs_never_hold_key` (fixed/dynamic; expects a
DEBUG "Autoscale launcher" record, `:615`), `test_autoscaling_mode_keeps_every_client_option` (`launch.argv`
exact, `zone='blue'`), `test_autoscaling_mode_starts_every_client_through_one_launch` (`scale()` with no
args appends to flat `clients`). `test_relay_launch.py` (unit) — `test_zone_fleet_keeps_a_fixed_size`,
`test_site_clients_of_every_fleet_are_ended` (assigns flat lists to `machine.clients`),
`test_relay_serving_several_zones_has_one_fleet_and_one_identity_per_zone`; `test_relay_fsm.py:759-798`
(autoscaler/fleet parts must not end while the site runs). Model-level: `test_zone_model.py:259-280`
(`count_remaining(group=0)` across zones), `test_relay_roster.py:264-274` (`count_connected`). New model
queries can reuse `test_zone_model.py`'s `task_db`/`add_tasks`/`finish` (`:49-73`); PostgreSQL coverage
goes in `test_zone_postgres.py` (skipped without `HS_TEST_POSTGRES`).

## Recommendations

1. Add `zone: str = None` to `count_remaining`, `effective_rate_by_client`, `effective_rate`,
   `avg_duration`, `time_to_completion`, `task_pressure` via `_filter_zone`; add
   `count_remaining_by_zone(group) -> Dict[zone_id, int]` (NULL → `DEFAULT_ZONE_ID`). Use per-zone rate
   and duration. Pass `DEFAULT_ZONE_ID`, never None, for the default zone.
2. Factor a pure per-zone demand function (inputs: zone, is-own-zone, launched, remaining, pressure,
   knobs → SCALE/WAIT or desired count) that reproduces today's tables exactly; own zone keeps
   init-size and min-size, other zones get only the work-driven rules (bootstrap, pressure, max-size).
   Keep `check_fixed()`/`check_dynamic()` as zero-argument FSM actions returning `AutoScalerState`.
3. Keep `machine.clients` a flat `List[Popen]` (readable and assignable: relay and tests rely on it);
   track per-zone fleets alongside (e.g. `Dict[zone_id, List[Popen]]` with `clients` as a flattening
   property whose setter assigns to the own zone), and make `clean()` per-zone.
4. Give the AutoScaler an explicit zone mode: own zone (name → `zone_id`) plus `discover: bool`; relay
   site clusters and `zone_fleet` keep `discover=False`, so a relay never opens a second fleet for zones
   it already serves (fixed policy would otherwise launch one client per discovered zone).
5. Decide the SCALE cadence (one launch per zone per period vs one overall); a pending-launch list keeps
   actuation separate from demand, which is the seam relay-topology will reuse.
6. Inject registered counts from `server.queue.dispatcher.clients` (log only); launched counts drive
   decisions. Map ids → names with `Zone.names_for_ids` for the launch (`--zone`, `{zone}`), falling
   back sensibly if a row is missing.
7. Decide at the gate whether discovery counts retryable failures (§2 retry edge); it needs
   `max_retries`/`eager` threaded into the AutoScaler.
