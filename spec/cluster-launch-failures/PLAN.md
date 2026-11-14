# PLAN — A cluster whose clients all fail says so and exits; one advertised address for every launch mode

> **Status:** Draft for review · **Last updated:** 2026-11-14
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

`hs cluster` stops waiting on clients it can no longer have. Launcher, `--mpi` and `--ssh` modes
watch every launched process while the server runs, warn at each non-zero exit, and once every one
has exited with at least one failure and the server would otherwise wait forever on work, stop the
server and exit `runtime_error` (5) after one error naming each process, its status and the
address the clients were told. Autoscaling counts failed launches in a row while no client of the
cluster is or was registered, and stops at `autoscale.failed_launches` (default 3, 0 off); relay
fleets keep it at 0 by construction. One helper decides the address every launch mode tells its
clients, overridable by `--advertise` / `server.advertise`. All of it lives in cluster code plus a
small `submit.py` fix; nothing in `server.py`, `core/dispatch.py` or `data/model.py` changes, so it
rebases cleanly over #103. Four phases: the address first (the errors name it, the drives need it),
then R1/R2/R4, then R3/R5, then the full gate.

## 2. Design

### 2.1 The advertised address (R6, R7) — `cluster/launch.py`, `cluster/__init__.py`, `core/config.py`

- **`advertised_host(bind: str, advertise: Optional[str] = None) -> str`** in `cluster/launch.py`
  (exported), beside `client_launch`: `advertise` if given; `HOSTNAME` when `bind` is a wildcard
  (`WILDCARD_BINDS = {'0.0.0.0', ''}`); else `bind`. IPv6 `::` is not a wildcard here (non-goal; the
  listener is IPv4 only and refuses it first). [04 §1]
- **Call sites**, each building its launch with `(advertised_host(bind[0], advertise), bind[1])`:
  `RemoteCluster.__init__` (`remote.py:288`), `AutoScalingCluster.__init__` (`remote.py:881`),
  `SSHCluster.__init__` (`ssh.py:283`), and `relay/site.py::zone_launch` (`:172`) as
  `advertised_host(queue.host)` — no override for the relay (non-goal). A default relay binds
  `0.0.0.0` (`core/config.py:201`) and still advertises `HOSTNAME`; only `--bind ADDR` changes. Each
  cluster keeps `self.advertised: Tuple[str, int]` for the R1/R3 error text.
- **Constructors** (`RemoteCluster`, `AutoScalingCluster`, `SSHCluster`) take `advertise:
  Optional[str] = None` and **never read configuration** (a relay's site cluster is an
  `AutoScalingCluster` and must not pick up `server.advertise`). Docstrings for `bind` say which
  address clients are told; a new `advertise` entry each. `run_cluster`/`run_ssh` forward `**kwargs`
  unchanged.
- **Key:** `'advertise': PARAM_UNSET` in `default['server']` between `host` and `port`; read as unset
  when `None`, `''` or `PARAM_UNSET`. `HYPERSHELL_SERVER_ADVERTISE` maps directly (no underscore in
  the key name).
- **Option:** `ClusterApp` gains `--advertise ADDR` (default `None`, *not* the key, so the option can be
  told from the key). `check_arguments`: in `local` mode, `--advertise` raises
  `ArgumentError('Cannot use --advertise with a local cluster (no launched clients)')` (exit 2); an
  empty value is refused too. `server.advertise` set with a local cluster is ignored silently (R7
  refuses only the option). A cached `ClusterApp.advertise_host` resolves option > key > `None`, and
  `run_launch`/`run_mpi`/`run_ssh`/`run_autoscaling` pass `advertise=` on.
- **TLS:** no behaviour change. Docs say the advertised name must be one the client's certificate
  check accepts: only in system-CA mode is the dialed name checked; with `cafile` plus `servername`,
  the servername is; under `<auto>` none is, and the `<auto>` certificate names the FQDN, short name,
  `localhost`, `127.0.0.1`, `::1`. [04 §3]

### 2.2 Watching launched clients (R1, R2, R4) — new `cluster/watch.py`

A new module keeps `remote.py`/`ssh.py` edits small and holds the one rule both modes share.

- **`ClusterLaunchError(RuntimeError)`** — the one exception R1 and R3 raise. `ClusterApp`'s shared
  mapping logs it once at CRITICAL (`ClusterLaunchError: …`) and exits `exit_status.runtime_error`.
- **`exit_text(code) -> str`:** `status N` for `N >= 0`; `signal N (SIGNAME)` for `-N`
  (`signal.Signals`; an unknown number prints the number alone).
- **`watch_launched(server, launched, advertised, interval=1.0) -> None`**, `launched` a list of
  `(name, Popen)`: the name is `ClientLaunch.describe()` in launcher/`--mpi` mode and the host in
  `--ssh` mode; statuses are kept by index (two entries may share a name). While `server.is_alive()`:
  1. Poll every process; at the first non-zero exit of each, `log.warning(f'Client {name} exited with
     {exit_text(code)}')` — at once, while the others run (R2, within ~1 s).
  2. If every process has exited and at least one failed, and `work_remains(server)` (2.3): call
     `server.stop(wait=True)` and raise `ClusterLaunchError('every launched client has exited with
     tasks remaining: <name> exited with status 1[; …]; clients were told to dial HOST:PORT')`.
     All-zero exits never reach this (the GOAL-gate ruling): the cluster waits as today.
  3. `server.join(timeout=interval)`.
  When the server ends on its own, remaining processes are waited on as today and any non-zero exit
  is only warned about (R4). The predicate is re-read every few seconds for as long as the server
  lives, so `--forever`/`--restart` fire on work submitted after the clients left, and a `--forever`
  cluster with nothing pending keeps waiting.
- **`RemoteCluster.run_with_exceptions`** becomes: start server, `wait_scheduling`, start the launch,
  `watch_launched(self.server, [(self.launch.describe(), self.clients)], self.advertised)`,
  `self.server.join()`. **`SSHCluster`** the same with `[(host, proc) …]`. Their `stop()` skips
  `server.stop` when the server is no longer alive, so `run_cluster`'s `except: thread.stop()` repeats
  nothing.
- Rejected: a thread per process or sequential `wait(timeout)` (breaks R2 under `--ssh`);
  `SIGCHLD`/`waitpid(-1)` (main thread only; could reap the autoscaler's children); a server-side
  "no clients" stop (`server.py`/`HeartMonitor`, #103). [01]

### 2.3 "Tasks remain" — `cluster/watch.py::work_remains(server)`

"Tasks remain" means the server would otherwise wait forever: the key is minted per run, so once its
processes are gone no client can ever connect. [02]

1. **Barrier** (both modes): if `server.receiver.is_alive()`, put `serialize_tasks([])` on
   `server.queue.completed` (put with a timeout) and wait, bounded (~3 s), until the queue is empty
   and `server.receiver.machine.held` is empty. The single FIFO Receiver has then written every result
   the exited clients posted. (`task_done()` precedes the write, so queue counters are no barrier.)
2. **With a database** (`server.scheduler` is not `None`): if the scheduler thread has ended, return
   False (it finished, failed its group, or took a signal: today's path). Else read, in the
   scheduler's own order, `Task.count_remaining() > 0`, else
   `Task.count_retryable(m.attempts, group=m.current_group, eager=m.eager) > 0` with
   `m = server.scheduler.machine`; True only if it holds on two reads ~2 s apart. Counts every zone and
   source, as the scheduler's stop rule does (`server.py:262-266`); in-flight rows of a dead client and
   staged rows are counted (they carry `schedule_time`, no `completion_time`).
3. **Without a database:** True if the server is still alive ~3 s after the barrier and its
   `LiveSubmitThread` is alive or has submitted any task. ("Alive" alone is wrong: an in-memory server
   with an empty source and no client never ends today.)

The predicate composes existing `Task` classmethods and reads `scheduler.machine`/`receiver.machine`
attributes (precedent: the autoscaler, `ZoneLoad.work`); it writes nothing. Known gap, accepted: in
`--no-db`, a client that returned every result and died in the milliseconds before its FINISHED
heartbeat, exiting non-zero, now ends the run with R1 instead of exiting 0 after the 600 s eviction
(exact fix: a result counter in the Receiver, `server.py`; deferred — sign-off item).

### 2.4 `LiveSubmitThread` stops without blocking — `submit.py`

In `--no-db`, `LiveSubmitThread.stop()` and its run path block forever on `self.local.put(None)`
(`submit.py:909`, `:881`) while no client drains the queue (queue size 1, bundle size 1), so stopping
the server for R1 would hang, as would today's `run_cluster` error path. Fix: `stop()` halts the
loader and committer first; the sentinel is offered with a timeout, retried only while the committer
lives (one `put_end()` used by both paths). Unit test: stop a `ServerThread(in_memory=True)` holding
more tasks than the queue takes, bounded. [01 hazard 1]

### 2.5 Failed launches under autoscaling (R2, R3, R5) — `cluster/remote.py`, `cluster/launch.py`

- **`autoscale.failed_launches`:** `'failed_launches': 3` in `default['autoscale']`;
  `DEFAULT_AUTOSCALE_FAILED_LAUNCHES = default.autoscale.failed_launches`. Because cmdkit splits every
  underscore, `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES` lands at `autoscale.failed.launches`; a reader
  `configured_failed_launches()` (in `cluster/remote.py`, modelled on
  `topology.configured_distribution`) prefers that path when `config.which` says it came from the
  environment (guarding `KeyError`), else `autoscale.failed_launches`, and refuses a bool, non-integer
  or negative value with `ConfigurationError` naming its source (`blame`). `ClusterApp` validates it in
  autoscaling mode and passes `failed_launches=` from `run_autoscaling`. [03 §4]
- **Registration signal:** `client_registrations() -> Tuple[int, int]` in `cluster/remote.py` over
  `Client.query()`: `ever` = rows with `server_id == INSTANCE` (monotonic; the server stamps
  `INSTANCE` at every registration and re-registration, `server.py:794-801`, `model.py:1411-1436`;
  cluster and server share it), `now` = those with `disconnected_at IS NULL`. Earlier runs' rows carry
  another server id and never count. [03 §1]
- **Counter:** `AutoScaler` and `AutoScalerThread` take `failed_launches: int = 0` and
  `registrations: Optional[Callable[[], Tuple[int, int]]] = None`; `AutoScalingCluster` defaults to
  `DEFAULT_AUTOSCALE_FAILED_LAUNCHES` and passes `client_registrations`. `AutoScaler.clean()` returns
  `actuator.clean()`'s exited list. In `check()`, when `failed_launches > 0`: `exited = clean()`; read
  `(ever, now)`; if `now > 0` or `ever > seen`, set `seen = ever` and clear the run, counting none of
  this check's exits; else in launch order exit 0 clears the run and a non-zero exit appends
  `(pid, zone, status)`. At `len(run) >= failed_launches`: `exhausted = True`, return `FINAL` (nothing
  more is launched). With the limit at 0 nothing is read or counted (today's loop exactly).
- **Stop:** `AutoScalingCluster.run_with_exceptions`: after `autoscaler.join()`, if
  `autoscaler.machine.exhausted`, `server.stop(wait=True)` and raise `ClusterLaunchError(f'{n}
  launched clients in a row exited non-zero with no client registered ({statuses}); clients were told
  to dial HOST:PORT')`; else `server.join()` as today. Its `stop()` skips a server no longer alive.
  The flag is read after `join`, so `AutoScalerThread` never raises (the relay's fleets are that
  class). [03 §2]
- **R2 under autoscaling:** `LaunchActuator.clean` names the process by pid and zone, `Autoscale client
  (<pid>, zone: <name>) exited with status N` (it named a list index; "as today" otherwise: one warning
  per non-zero exit, at the check).
- **R5:** relay fleets are at 0 with no relay edit: `site_scaler` copies an explicit settings list
  without `failed_launches` (`relay/site.py:392-397`) and `zone_fleet` never passes it; `hs server
  --autoscaling` (`cluster/topology.py`) has no `AutoScaler`. Tests pin both.

### 2.6 Docs and agent docs (R8)

- `cluster/__init__.py` `APP_USAGE` (`[-H ADDR] [--advertise ADDR]`) and `APP_HELP` (the `-H` line
  states the rule; a `--advertise ADDR` line after it); `docs/_include/cluster_usage.rst`,
  `cluster_help.rst` (`-H` body per R6, `--advertise` entry with the TLS sentence),
  `cluster_desc.rst` (the address rule; that a cluster whose clients are all gone with work remaining
  exits non-zero, R1 and R3) — hand-maintained, never hand-marked.
- `docs/cli/cluster.rst`: `--advertise` in its `versionadded:: 2.9.0` list; a `versionchanged::
  2.9.0` for the address rule and the non-zero exit.
- `docs/_include/config_param_ref.rst`: `server.advertise` after `.host`; `.bind`'s text names the
  rule; `autoscale.failed_launches` after `.sites`, before `[size]`, with its environment form; each
  `.. only:: html` + `.. versionadded:: 2.9.0`. Outside #103's `.evict` and `[relay]` blocks.
- `docs/security.rst` (Peer Verification Modes): the advertised-name sentence.
- `docs/_include/relay_help.rst:42`: "connect back to the relay by its host name" → its bind address,
  or its host name when it binds every interface (not in #103's diff). `relay/site.py`'s module and
  `site_cluster` docstrings likewise.
- Completions: `share/bash_completion.d/hs` and `hsx` (identical copies: `all_opts`, no value
  completion) and `share/zsh/site-functions/_hs` (`'--advertise[…]:address:'` after `-H`).
- Man pages (`share/man/man1/*.1`) are generated from `docs/manual.rst`, which includes the cluster
  snippets and the parameter reference, and are rebuilt only by `hs-release`; this cycle does not
  edit them (precedent: every prior cycle). Stated here for `hs-review`.
- `AGENTS.md:415-416` and `.agents/factory/invariants.md:185` (and the footgun line `AGENTS.md:574`):
  "the advertised `HOSTNAME` must be routable" → the `advertised_host` rule, which must be routable
  from clients; plus one line each on R1/R3 (a cluster with every client gone and work left exits
  5; the autoscaler's failed-launch limit, off for relay fleets).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `cluster/watch.py` `watch_launched` + `work_remains` (barrier; DB scheduler rule on two reads; in-memory server-alive + submitter); `server.stop(wait=True)`; `ClusterLaunchError` → exit 5; `submit.py` non-blocking `LiveSubmitThread` stop; `RemoteCluster`/`SSHCluster.run_with_exceptions` |
| R2 | `watch_launched` per-exit warning (launcher text or host); `LaunchActuator.clean` names pid and zone |
| R3 | `autoscale.failed_launches` + `configured_failed_launches()`; `client_registrations()`; `AutoScaler.check` counter and `exhausted`; `AutoScalingCluster` stop and raise |
| R4 | All-zero exits never fire; the predicate defers to a scheduler that ended; barrier before counting; warnings only once the server ends on its own; `--forever` with nothing pending waits |
| R5 | `failed_launches=0` defaults in `AutoScaler`/`AutoScalerThread`; `site_scaler`/`zone_fleet` leave it unset; tests pin `site_cluster`/`zone_fleet` at 0 and a failing `SiteScaler` that never ends |
| R6 | `advertised_host` at four call sites incl. `relay/site.py::zone_launch`; `ClusterApp` widening unchanged |
| R7 | `--advertise` (default `None`), `server.advertise` (`PARAM_UNSET`), option > key in `ClusterApp`, local refusal; constructors take `advertise=` |
| R8 | §2.6 inventory |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research (sections §1,
§2, §4, §5, §6, §8, §9, §10, §11, §12 touched; GOAL sane against all) and again on this design.

- **§1 Task lifecycle** — the predicate reads `Task.count_remaining`/`count_retryable` (classmethods,
  their own predicates); no task write. Rows of dead clients stay in flight; `--restart` reverts
  them, as the GOAL says.
- **§2/§3 exit_status and retries** — no new sentinel; `count_retryable` keeps its `CANCEL_STATUS`
  filter and is called with the scheduler's group, attempts and eager. The cluster's exit status is
  `exit_status.runtime_error`, not a task status.
- **§4 Server modes** — in-memory (`scheduler is None`) has its own branch and never queries the
  database; `no_confirm` untouched.
- **§5 Concurrency** — no FSM state added to `Scheduler`/`HeartMonitor`; the `AutoScaler` returns
  `FINAL` (existing state) and `AutoScalerThread.stop` still halts first. Every wait in the watch and
  the barrier is bounded; the server is stopped with `stop(wait=True)` so `join` re-raises.
- **§6 Shutdown ordering** — unchanged: `ServerThread.stop` and its own `wait_*` sequence, staged
  revert included, run as today; the barrier is an extra empty bundle on `completed`, a JSON payload
  (`serialize_tasks([])`), never a live object or literal `None`. `LiveSubmitThread` still puts its
  sentinel after `loader`/`committer` end; only the unbounded put becomes a bounded retry.
- **§8 Signals** — none captured; `check_signal` untouched.
- **§9 Transport and security** — no TLS change, key handling unchanged, nothing forwarded.
- **§10 Configuration** — two declared keys; `server.advertise` uses `PARAM_UNSET`; the
  failed-launches reader exists because of cmdkit's underscore split, read once per run.
- **§11 Cluster orchestration** — every argv still built by `client_launch()`; only the `-H` value
  changes; no new client option (contract test). The autoscaler still never terminates a client and
  ignores `no_confirm`/`in_memory`/`forever`/`restart`. Relay launches still go through `zone_launch`
  with `expose_zone=True` and `fence_site`. The "advertised `HOSTNAME`" line is rewritten (R8).
- **§12 Conventions** — help snippets and completions with the CLI change, prose page with
  `versionadded`/`versionchanged:: 2.9.0`, `exit_status` constants, tests marked `unit`/`integration`,
  no spec ids in source.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `submit.py` (`LiveSubmitThread`) changes, a file the GOAL does not list | Without it `server.stop` hangs in `--no-db` with tasks still queued, so R1's first drive cannot exit | Leaving `--no-db` out of R1 contradicts R1 and the evidence plan; stopping only the scheduler/heartmonitor reaches into `ServerThread` internals and `--no-db` has no scheduler |
| Cluster code reads `server.receiver.machine.held`, `server.scheduler.machine.*` and `Client.query()` | Exact, race-free reads with no edit to `server.py`/`data/model.py` (#103) | A `Task.count_pending`/`Client.count_registered` classmethod is cleaner but edits `data/model.py`; offered as the fallback (sign-off) |
| A dedicated reader for `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES` | cmdkit maps the variable to `autoscale.failed.launches` | Renaming the key would break the GOAL's locked name; fixing cmdkit's mapping for every key is out of scope (seed) |

## 4. Rabbit holes (resolved)

- How to tell a finishing run from a dead one without racing the last results → an empty-bundle
  barrier through the single Receiver, then the scheduler's own rule twice
  ([02](research/02-tasks-remain.md)).
- `--no-db` has no counts and its server never ends without a client → server-alive after a settle
  plus submitter state ([02](research/02-tasks-remain.md)).
- Stopping the server for R1 hangs in `--no-db` → `LiveSubmitThread` bounded sentinel
  ([01](research/01-exit-watch-and-stop.md)).
- A registration signal immune to stale rows and to clients that come and go between checks →
  `INSTANCE`-stamped `Client` rows, `ever`/`now` ([03](research/03-autoscale-failed-launches.md)).
- Keeping relay fleets out without touching the relay → defaults of 0, not copied by `site_scaler`
  ([03](research/03-autoscale-failed-launches.md)).
- The env variable for an underscored key → dedicated reader ([03](research/03-autoscale-failed-launches.md)).
- Does R6 change a default relay? No: `relay.bind` defaults to `0.0.0.0`
  ([04](research/04-advertised-address.md)).
- TLS names → no behaviour change; one documented sentence ([04](research/04-advertised-address.md)).

## 5. Risks & open questions

- **Overlap with #103 (relay-hardening).** No edit to `relay/__init__.py`, `relay/upstream.py`,
  `core/reconnect.py`, `core/dispatch.py`, `data/model.py` or `server.py`. Text overlap only:
  `docs/_include/config_param_ref.rst` (new entries after `server.host` and `autoscale.sites`; #103
  edits `server.evict` and `[relay]` — separate hunks), `AGENTS.md` and `invariants.md` (§11 lines;
  #103 edits §1, §6 and the data-layer paragraph — separate hunks). Reads of `server.receiver.machine`
  and `server.scheduler.machine` are stable across #103 (it changes `HeartMonitor`/`ServerThread`
  wait order, adds `revert_handed_back`). Rebase expected clean; P4 re-runs the suite after it.
- **Timing without a database** is the tightest: ~1 s tick + ~1.5 s barrier + 3 s settle + 4.9 s stop
  ≈ 10 s from the last exit. P2 adds a test that times exit → raise ≤ 10 s; if it misses, trim the
  settle (≥ 2 s) or the submitter's stop wait.
- **`--no-db` FINISHED gap** (2.3): accepted unless the foreman wants the Receiver counter in
  `server.py`.
- **A launcher that returns non-zero after handing its clients to a scheduler** (e.g. a bare `sbatch`
  that fails one submission) now ends the run with R1 if no client has connected and work remains;
  that is R1 as ruled.
- **Existing tests that pin the old rule** change with R6: `test_autoscaling_mode_keeps_every_client_option`
  (`tests/test_cluster_launch.py:636`) and `test_ssh_mode_launches_each_host_with_every_client_option`
  (`:955`) bind loopback and expect `HOSTNAME`; the suite's stand-in ssh (`:134`) expects
  `-H HOSTNAME` in the remote command. `test_launcher_mode_without_environment_names_the_cause` and
  `test_ssh_mode_without_key_names_the_cause` end today's hang with `until=`; after R1 they assert
  exit status 5.
- **Pre-existing, out of scope, worth seeds:** cmdkit's underscore split breaks every underscored
  key's environment form (`relay.launcher_args`, …); `-P 0` makes the autoscaler spin.

## 6. Verification strategy

- **Unit (new):** `tests/test_cluster_advertise.py` (helper table; each mode's `-H` for wildcard bind,
  specific bind, option, key, option over key; `zone_launch` for a relay bound to one address; local
  refusal; key ignored locally), `tests/test_cluster_exit.py` (`exit_text`; `watch_launched` with
  `sh -c 'exit N'` processes and a stand-in server: all fail → stop + one error naming every process,
  status and address; all zero → waits; partial → one warning while the other runs; server ends → no
  error; a signal-killed process; R1 under a `--forever`-style predicate; timing ≤ 10 s; the
  `LiveSubmitThread` stop), `tests/test_autoscale_failed_launches.py` (three failures stop; exit 0
  resets; `ever` rising and `now > 0` reset; 0 never stops or reads registrations; `SiteScaler`,
  `site_cluster`, `zone_fleet` at 0 and never stop; the reader's env and refusals; the cluster-level
  raise naming 3, the statuses and the address).
- **Drives:** [`drive/launch_drive.sh`](drive/launch_drive.sh), in a throwaway site, nine modes
  (`fail`, `fail-db`, `zero`, `healthy`, `ssh-fail`, `ssh-partial`, `autoscale-fail`,
  `autoscale-off`, `advertise`) covering every bullet of the GOAL's evidence plan with fake launchers,
  a recording launcher and a stub `ssh` (`--ssh hostA,hostB`). Dry-run on `develop`: `zero`,
  `healthy` and `autoscale-off` pass; `fail`, `fail-db`, `ssh-fail`, `ssh-partial`,
  `autoscale-fail` and `advertise` fail as the defects predict (hang past the bound; the host name
  advertised under `--ssh`/autoscaling; `--advertise` unknown), and each check was exercised.
- **Gate:** the suite under Python 3.14 (`.venv`) and 3.11 (`UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311`),
  the contract test, and the Sphinx `-W` build (sandbox off).
