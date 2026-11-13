# PLAN — Zone-aware autoscaling (zone, cycle 3)

> **Status:** Draft for review · **Last updated:** 2026-11-12
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

The autoscaler is split into **demand** and **actuation**. Demand is a pure per-zone function over
group- and zone-scoped readings of the database. Actuation is a launcher that keeps one cached
`ClientLaunch` per zone and a flat list of the clients it started. `AutoScaler` keeps its FSM and
becomes the glue between them. Each check discovers the zones that have current-group work (the
cluster's own zone is always among them), reads each zone's load, and asks the demand for at most one
more client per zone. The actuator then launches those clients, with `{zone}`, `HYPERSHELL_ZONE` and
`--zone` set for each. In a zone-less database every reading equals today's unscoped one and the own
zone's launch is today's launch, so the founding case does not change. The relay turns discovery off
with one keyword. The seam has named inputs and outputs, so `relay-topology` can add a publish actuator
and a relay-side reconciler without reworking Z3.

## 2. Design

### 2.1 The per-zone signal (`src/hypershell/data/model.py`) — R1, R2

Each of these helpers gains `zone: str = None` and applies `Task._filter_zone`. None means every zone,
which is today's behaviour and the default for every existing caller:

- `Task.count_remaining(group=None, zone=None)`: `completion_time IS NULL` [+ group] [+ zone].
- `Task.effective_rate_by_client(zone=None)`, `Task.effective_rate(zone=None)`,
  `Task.avg_duration(zone=None)`: per-zone **rate and duration** (decision 1). A client serves exactly
  one zone, so filtering on the task's zone selects that zone's clients.
- `Task.time_to_completion(group=None, zone=None)`, `Task.task_pressure(factor, group=None, zone=None)`.
  The walrus behaviour is kept: no remaining work gives None, not 0.

Two new read-only classmethods:

- `Task.count_remaining_by_zone(group: int) -> Dict[str, int]`: one `GROUP BY task.zone` over
  `group == g AND completion_time IS NULL`, with the NULL key mapped to `DEFAULT_ZONE_ID`. It has the
  same shape as `Scheduler.report_held`.
- `Task.count_retryable_by_zone(attempts: int, group: int, eager: bool = False) -> Dict[str, int]`:
  `query_retryable(attempts, group=g)` grouped by zone, under `count_retryable`'s rule (`attempts <= 1
  and not eager` gives `{}`). The `exit_status != 0 AND != CANCEL_STATUS` filter comes with
  `query_retryable` (invariants §2). This closes the retry edge (decision 2).

`latest_server()` stays unscoped, since one server owns the database. No schema change and no new
index: `index_tasks_zone (zone, group, …)` serves the zone filters, and `index_tasks_unscheduled`
leads with `group`.

### 2.2 Demand (`src/hypershell/cluster/demand.py`, new) — R1, R2, R4

This module has no launching and no Popen. Its DB reads are confined to one function. It names:

```python
class AutoScalerPolicy(Enum): FIXED = 1; DYNAMIC = 2          # moved here; re-exported by cluster/remote.py

@dataclass(frozen=True)
class ZoneRef:                    # a zone as both sides need it
    id: str                       # zone id; DEFAULT_ZONE_ID for the default zone, never None
    name: str                     # zone name for the launcher ('default' for the default zone)

@dataclass(frozen=True)
class ScalingKnobs:               # the uniform sizing knobs (R4), applied to each zone
    policy: AutoScalerPolicy
    factor: float
    init_size: int
    min_size: int
    max_size: int

@dataclass(frozen=True)
class ZoneLoad:                   # one zone's reading at a check
    zone: ZoneRef
    remaining: int                # incomplete current-group tasks in the zone
    retryable: int                # current-group failures awaiting a retry row (named zones; 0 for default)
    pressure: Optional[float]     # task_pressure(factor, group, zone) under DYNAMIC; None if unknown or FIXED
    @property
    def work(self) -> int: ...    # remaining + retryable

def read_zone_loads(group: int, knobs: ScalingKnobs, *, own: Optional[ZoneRef], discover: bool,
                    attempts: int, eager: bool) -> Dict[str, ZoneLoad]:
    """The only DB reads: the own zone always (if given), plus, with `discover`, every zone with
    current-group work (count_remaining_by_zone ∪ count_retryable_by_zone), named via
    Zone.names_for_ids. A zone id with no Zone row is skipped with one warning."""

def zone_demand(load: ZoneLoad, launched: int, knobs: ScalingKnobs, *, own: bool, initial: bool) -> int:
    """Pure. Desired client count of one zone at one check: never below `launched` (scale-down is
    idle exit, R5) and, once steady, at most `launched + 1`."""

def fleet_demand(loads: Mapping[str, ZoneLoad], launched: Mapping[str, int], knobs: ScalingKnobs, *,
                 own: Optional[str], initial: bool) -> Dict[ZoneRef, int]:
    """Pure. zone_demand for every zone in `loads` (and the own zone when `initial`)."""
```

`zone_demand` is today's `check_fixed`/`check_dynamic` table applied to one zone. With `L` launched in
zone z:

| Case | Desired |
|------|---------|
| `initial` and own zone | `max(L, init_size)` (today's INIT loop; no DB read) |
| `initial`, other zone | `L` (no init launches for it) |
| own zone and `L < min_size` | `L + 1` (checked first, as today) |
| FIXED: `L == 0` and `work > 0` | `1` |
| DYNAMIC, pressure known: `pressure > 1` and `L < max_size` | `L + 1` |
| DYNAMIC, pressure unknown: `L == 0` and `work > 0` | `1` |
| otherwise | `L` |

For the own default zone in a zone-less database: `work == count_remaining(group)` (the default
zone's retryable count is 0 by definition) and `pressure` equals today's. Every row of the table then
reproduces today's decision, which is R4 identity. Per the 2026-11-12 ruling, init-size and min-size
apply only to the own zone. Other zones get only the work-driven rows, so a gated zone (no
current-group work) is never discovered and never provisioned (R2).

### 2.3 Actuation (`src/hypershell/cluster/launch.py`) — R3

**`client_launch(..., expose_zone: bool = False)`.** When set, on the launcher path only, each word of
`launcher` and `launcher_args` has `{zone}` replaced by the zone name. The replacement is `str.replace`
after `shlex.split`, so `${HOME}` and `{}` are untouched and a name with a space stays one word. The
`env` gains `HYPERSHELL_ZONE=<name>`. The default zone's name is `default`. The `-t` template word and
the client options are never substituted. `HYPERSHELL_ZONE` is not added to the Open MPI forward list.
`--zone` still comes from `client_options(zone=…)`. With an SSH host, `expose_zone` raises
`ValueError`: autoscaling never uses SSH, and the flag is meant for launcher modes. The default is off,
so `RemoteCluster`, `SSHCluster` and the relay's `zone_launch` build exactly what they build today.

**`LaunchActuator`** (new, beside `ClientLaunch`):

```python
class LaunchActuator:
    """Starts clients zone by zone and keeps the processes it started. Never terminates one."""
    clients: List[Popen]                        # flat, every zone, assignable (relay and tests rely on it)
    launch: ClientLaunch                        # the own zone's launch
    def __init__(self, launch: ClientLaunch, own: Optional[ZoneRef] = None,
                 launch_for: Optional[Callable[[ZoneRef], ClientLaunch]] = None) -> None: ...
    def launched(self) -> Dict[str, int]:       # per zone id; an untracked entry counts as the own zone
    def launch_one(self, zone: Optional[ZoneRef] = None) -> Popen:   # one client; None → own zone
    def apply(self, desired: Mapping[ZoneRef, int]) -> int:          # launch max(0, desired - launched) per zone
    def clean(self) -> List[Popen]:             # drop exited processes (today's AutoScaler.clean), return them
```

The `ClientLaunch` for a zone is built once by `launch_for(zone)` and cached. Its `describe()` is
logged once, at the zone's first launch ("Autoscale launcher (zone gpu): …"). Zone membership is
tracked beside the list, keyed by `id(proc)`, so plain lists assigned to `clients` keep working.

### 2.4 The autoscaler (`src/hypershell/cluster/remote.py`) — R1, R2, R4, R5

**`AutoScaler`** keeps its states, phases, FSM wiring and public attributes (`policy`, `factor`,
`period`, `init_size`, `min_size`, `max_size`, `launch`, `clients`). It gains these keywords, all
defaulting to today's single-fleet behaviour: `own_zone: Optional[ZoneRef] = None` (None means the
default zone), `launch_for: Optional[Callable[[ZoneRef], ClientLaunch]] = None` (discovery is on only
when this is given), `attempts: int = 1`, `eager: bool = False` and
`registered: Optional[Callable[[str], int]] = None`. It holds a `LaunchActuator`, and `clients` and
`launch` are properties onto it (with a `clients` setter).

- `init()`: `fleet_demand(…, initial=True)` for the own zone; SCALE until it is met; then STEADY.
  Same launches as today, still with no DB read.
- `check()` → `clean()`, then `check_fixed()` / `check_dynamic()`. These stay zero-argument and
  return `AutoScalerState`. Each calls `read_zone_loads(Task.current_group().value, …)` and
  `fleet_demand(…, initial=False)`, logs one line per zone (`zone`, registered/launched, work,
  pressure), stores the zones that need a launch in `self.pending`, and returns SCALE if any zone
  needs one, else WAIT.
- `scale()` → `actuator.apply(self.pending)`, clears it, and returns INIT or WAIT as today. With
  nothing pending (INIT, or a direct call) it launches one own-zone client through `self.launch`. That
  is today's contract (`test_autoscaling_mode_starts_every_client_through_one_launch`).
- It never halts by itself (the relay treats an ended autoscaler as a death) and never terminates a
  client (R5, invariants §11). A zone whose current group is done stops getting launches, and its
  clients exit on idle (`-T`).

**`AutoScalerThread`** passes the new keywords through. Its signature only grows, so `zone_fleet` is
unchanged.

**`AutoScalingCluster(..., discover_zones: bool = True)`.** With discovery on, it builds
`launch_for = lambda ref: client_launch(…same values…, zone=<per §2.5>, expose_zone=True)`. It sets
`self.launch = launch_for(own)` and passes `launch_for`, `own_zone`, the server's `attempts`/`eager`
and `registered=lambda z: self.server.queue.dispatcher.clients(z)` (resolved lazily; logging only).
With discovery off, it builds today's single launch verbatim (no exposure) and no `launch_for`.
`machine.launch is cluster.launch` holds in both cases.

**Relay (`src/hypershell/relay/site.py`).** The only change is that `site_cluster` passes
`discover_zones=False`. The site cluster and the further-zone fleets then behave exactly as today
(ruling 2026-11-12, decision 4).

### 2.5 `--zone` on the launched client — R3

- **The own zone:** today's argv, with `--zone <name>` exactly when the cluster was given `--zone`.
  This pins the founding case (`test_autoscaling_mode_keeps_every_client_option[defaults]`).
- **Any other zone:** `--zone <name>`. A discovered default zone gets `--zone default`, which every
  supported client accepts; the contract test already lists `--zone`.
- `{zone}` and `HYPERSHELL_ZONE` are set for every launch with discovery on, the own zone included.

[NEEDS CLARIFICATION: R3 says the autoscaler SHALL append `--zone`. For a zone-less cluster this
design keeps today's argv (no `--zone`) for the own default zone, because R4 and the Outcome require the
founding case to behave exactly as today and a client without `--zone` serves the default zone. Confirm
this reading, or rule that every launch carries `--zone`, the own default zone included
(`--zone default`). The ruling decides one test assertion and the drive's founding check (PLAN §5 Q1).]

### 2.6 CLI and docs — R3, R2

No new option. `ClusterApp.run_autoscaling` (`cluster/__init__.py`) builds the cluster with discovery
on, which is the default. Help, docs and completions:

- `docs/_include/cluster_help.rst`, together with `APP_HELP` in `cluster/__init__.py`, which must
  match:
  - `--launcher`: under `--autoscaling`, `{zone}` and `HYPERSHELL_ZONE`.
  - `--zone`: under autoscaling this is the cluster's own zone. Other zones with current-group work get
    their own clients.
  - `--autoscaling`: per-zone scaling with uniform knobs, plus a worked launcher-shim example, a `case`
    on `$HYPERSHELL_ZONE` choosing a scheduler partition, written generically.
- `docs/_include/cluster_desc.rst`: one sentence on per-zone launches.
- `docs/_include/config_param_ref.rst`: in `[autoscale] .launcher`, the same substitution.
- `docs/cli/cluster.rst`: `.. versionchanged:: 2.9.0` beside the include.
- `share/zsh/site-functions/_hs`: the `--launcher` and `--zone` descriptions. Bash has no descriptions
  and needs no change.

Man pages are rebuilt at release, so they are not touched here (03 §docs). In-code docstrings
(`AutoScalingCluster`) get the new keyword and the `{zone}` note.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | §2.1 zone-filtered `count_remaining`/`task_pressure` and helpers, with per-zone rate and duration; §2.2 `ZoneLoad`/`read_zone_loads`; §2.3 `LaunchActuator.launched()` per zone; §2.4 registered count per zone from the dispatcher (logging) |
| R2 | §2.1 `count_remaining_by_zone` + `count_retryable_by_zone` (current group only); §2.2 `read_zone_loads(discover=True)`; demand table, where a gated zone is never read and never launched for |
| R3 | §2.3 `client_launch(expose_zone=True)`: `{zone}` in launcher words, `HYPERSHELL_ZONE` in env; §2.5 `--zone`; §2.6 docs/completions with the shim example |
| R4 | §2.2 `ScalingKnobs` applied per zone by `zone_demand`; own zone keeps init/min (ruling 2026-11-12); zone-less identity argued in §2.2 and proven by the founding tests (§6) |
| R5 | §2.3/§2.4: no termination path; demand never below `launched`; idle-exit retires clients of a passed phase, shown by the drive (§6) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research (Step 2: §1, §2, §3, §4, §5, §9, §10, §11 and §12 touched; the intent is sane against all of
them) and again after this design was drafted:

- **§1 Task lifecycle.** The new queries are read-only and reproduce the predicates. "Remaining" is
  `completion_time IS NULL` (unscheduled plus in-flight, as `count_remaining` is today). No task-state
  transition is added, and the new code is `Task` classmethods.
- **§2 `exit_status`.** `count_retryable_by_zone` is built on `query_retryable`, which keeps
  `exit_status != 0 AND exit_status != CANCEL_STATUS`, so cancelled tasks never count as work.
- **§3 Retry model.** The rule is mirrored, not changed: `attempts`/`eager` are the server's own, and
  `count_retryable_by_zone` follows `count_retryable`'s `attempts <= 1 and not eager` short cut. No
  retry row is created by the autoscaler. Group-gating is unchanged, and a zone gated behind the
  current group is never read (R2).
- **§4 Server modes.** `--autoscaling` still refuses `--no-db`. The autoscaler ignores
  `no_confirm`/`in_memory`/`forever`/`restart` as before.
- **§5 Concurrency.** No new FSM state. Every state still returns a next state and has an action. WAIT
  keeps its 1 s sleep (the shutdown-latency bound). `AutoScalerThread.stop()` still calls
  `machine.halt()` first. The dispatcher count is read under the dispatcher's own lock, in logging only.
- **§9 Transport & security.** The key stays in `env` (`HYPERSHELL_SERVER_AUTH`), never on an argv.
  `HYPERSHELL_ZONE` is not secret and is not added to the Open MPI forward list, so the
  key-forwarding contract is unchanged.
- **§10 Configuration.** `HYPERSHELL_ZONE` falls under the `HYPERSHELL` config prefix and so appears as
  config key `zone` in a launched `hs` process. Nothing reads `config.zone`, and the client keeps
  `--zone` authoritative. A test pins that `config.zone` is not consulted for the client's zone.
- **§11 Cluster orchestration.** `client_launch()` stays the one builder; zone exposure is a keyword
  on it. Only contract-listed options are emitted (`--zone` is listed). The autoscaler never terminates
  clients. The relay's fleets are still launched only through `client_launch()`, via the site cluster
  and `zone_launch`, unchanged, with `fence_site` over the same launches. P2 adds one line on
  `expose_zone` and per-zone launches to AGENTS.md's cluster section and to invariants.md §11, in
  lockstep and in the same commit as the code.
- **§12 Conventions.** Docs, help and completions change in the same commit as the CLI-visible change
  (P2, P3). `.. versionchanged:: 2.9.0` goes in the prose. New tests carry `@mark.unit` or
  `@mark.integration`. No version is hard-coded and no dependency floor moves.

### Deviation justifications

None bends an invariant. Two rows add complexity:

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Discovery also counts named zones' retryable failures (`count_retryable_by_zone`, `attempts`/`eager` threaded into `AutoScaler`) | A named zone's retry rows are created only while one of its clients asks; without this, a zone whose only current-group work is a failure awaiting retry is never discovered and holds the group: the stall the GOAL's R2/R4 note says Z3 closes | Counting `count_remaining` alone is one query fewer but leaves that stall; the default zone (always refilled) is unaffected either way |
| One-keyword edit in `relay/site.py` (`discover_zones=False`) | `AutoScalingCluster` discovers by default (R2 for every caller); the relay must not (ruling 2026-11-12) | Default-off (no `relay/` edit) leaves library callers of `AutoScalingCluster` silently without R2 ([02 §2](research/02-relay-and-topology-seam.md)) |

## 4. Rabbit holes (resolved)

- **Global vs per-zone pressure.** Per-zone. A global rate makes a slow zone read as near-zero
  pressure, so it never grows. A zone with no completions yet reads None and takes today's bootstrap
  row ([01 §2](research/01-autoscaler-signal.md)).
- **Retry rows of an unserved named zone.** Counted at discovery (§3 row 1;
  [01 §2](research/01-autoscaler-signal.md)).
- **Relay duplicate fleets.** Discovery in the relay would launch a second fleet, with the wrong
  launcher and nameless zones. Decided: S4 ([02 §2](research/02-relay-and-topology-seam.md)).
- **Where `{zone}` is substituted.** Inside `client_launch`. A built `ClientLaunch` does not know where
  its launcher words end, so a later `for_zone()` could hit `-t`
  ([03](research/03-launcher-docs-verify.md)).
- **Registered counts without a zone column.** Taken from the dispatcher in process, for logging only.
  No schema change ([01 §3](research/01-autoscaler-signal.md)).
- **Mocking seams of `tests/test_autoscale.py`.** Its `patch_queries` helper patches
  `count_remaining`/`task_pressure` without `zone`. The helper is widened (accept `zone=None`, stub
  `count_remaining_by_zone`/`count_retryable_by_zone`, record `zone` outside the asserted tuple). Its 11
  decision assertions stay byte-identical.

## 5. Risks & open questions

- **Q1 (gate): `--zone` for a zone-less cluster's own default zone.** See the marker in §2.5. The plan
  proceeds on "today's argv" because it keeps the founding case exactly as today. The alternative
  changes one assertion (`test_autoscaling_mode_keeps_every_client_option[defaults]`) and one drive
  check, and nothing else.
- **Pacing per zone.** One launch per zone per period means a cluster with k active zones can start up
  to k clients in one check. That is the reading of "uniform values applied independently to each
  active zone" (R4), and it is identical for one zone. Documented in `--autoscaling`.
- **A launcher that ignores `{zone}`.** A launcher with no `{zone}` and a shim that ignores
  `HYPERSHELL_ZONE` still gets correctly zoned clients (`--zone`), all on the same resource. The docs
  say the shim is how to route.
- **Same-host name resolution in the drive.** The cluster advertises the host name, which a sandbox
  may not resolve, so the shim rewrites `-H` to loopback, as the tests' stand-in does.
- **DB cost.** Per check: one GROUP BY, and for named zones one more, plus per-zone pressure (two small
  queries per zone, dynamic only). This is negligible at a period of seconds.
- **`relay-topology` fleet input.** A publishing server has no `Popen`s. Its fleet size per zone must
  come from relay-reported counts, which do not exist yet. That is that cycle's work; Z3 takes
  `launched` as an injected mapping so it can be supplied (§Forward compatibility in TECH.md).

## 6. Verification strategy

**Founding case: existing tests that must stay green**, with assertions unchanged:

- `tests/test_autoscale.py`, all 12. `TestCheckDynamic` (7) and `TestCheckFixed` (4) are unit tests;
  their only edit is the declared `patch_queries` widening (§4). `test_count_remaining_by_group` is an
  integration test.
- `tests/test_cluster_launch.py -k autoscaling` (integration):
  - `test_autoscaling_mode_keeps_key_off_command_lines`, all cases, including `fixed-tls-zone`;
  - `test_autoscaling_mode_logs_never_hold_key`;
  - `test_autoscaling_mode_keeps_every_client_option`;
  - `test_autoscaling_mode_starts_every_client_through_one_launch`;
  - the 2.9.0a4 contract test.
- `tests/test_relay_launch.py`, the whole file. That includes `test_zone_fleet_keeps_a_fixed_size`,
  `test_site_clients_of_every_fleet_are_ended` and the `env == {AUTH_ENV: …}` assertions.
- `tests/test_relay_fsm.py`. Autoscaler parts never end while the site runs.
- `tests/test_zone_model.py`, `tests/test_relay_roster.py`.

**New tests:**

- `tests/test_zone_autoscale.py`:
  - unit: the `zone_demand` table, including a parametrized single-zone equivalence to today's decision
    table, own-only init/min, and gated zones never launched;
  - unit: `LaunchActuator` per-zone accounting, untracked entries counted as own, and caching;
  - unit: `AutoScaler` multi-zone checks with fake launches, one launch per zone per check;
  - integration: real DB, `read_zone_loads` discovery and the retry edge.
- In `tests/test_cluster_launch.py`, `client_launch(expose_zone=True)` cases:
  - `{zone}` in a launcher word and in `--opt={zone}`;
  - `-t '{zone} {}'` untouched;
  - a name with a space stays one word, and `${HOME}` is untouched;
  - `HYPERSHELL_ZONE` in env;
  - Open MPI forwarding unchanged, and SSH plus `expose_zone` refused.
- In `tests/test_zone_model.py`, the zone-filtered helpers, `count_remaining_by_zone` (NULL mapped to
  default) and `count_retryable_by_zone` (cancelled excluded, the attempts rule). Parallel cases go in
  `tests/test_zone_postgres.py`, which is skipped without `HS_TEST_POSTGRES`.

**CLI drive (laptop scale, no cluster).** `spec/zone-autoscale/drive/zone_shim.sh` is a launcher shim
that logs its `{zone}` argument, `$HYPERSHELL_ZONE`, the client's `--zone` and the group-0 remaining
count, rewrites `-H` to loopback, and execs the client. `spec/zone-autoscale/drive/zone_drive.sh MODE`
runs inside `.agents/factory/bin/temp_site.sh`. It submits 3 tasks each to default (group 0), `cpu`
(group 0) and `gpu` (group 1), runs `hs cluster --restart --autoscaling=MODE --launcher="sh …/zone_shim.sh
LOG {zone}" -T 2 -P 1`, polls `hs list` until nothing remains (bounded, since macOS has no `timeout`),
stops the cluster, and asserts on the logs. Modes:

- `founding`: default zone only, no `--zone`. Asserts:
  - every launch is in the default zone with no `--zone` (per Q1);
  - all tasks complete;
  - from P2 on, `{zone}` and the env equal `default`.
- `fixed` and `dynamic`: three zones. Asserts:
  - every launch has `word == env == --zone` (default per Q1);
  - default, `cpu` and `gpu` were each launched;
  - no `gpu` launch while group 0 had remaining work (R2);
  - each task's `HYPERSHELL_ZONE` equals its zone;
  - 3 tasks per zone, none failed;
  - every launched client exited by itself before the drive's end, meaning idle-exit and no
    termination (R5).

Docs gate: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
docs/_build/html`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
