# Research 03 — R3's failed-launch counter under autoscaling, and R5's relay/server guarantee

Read-only pass on `9d1e1c5` (= `develop`). Line numbers are this worktree's.

## 1. The registration signal

**Recommendation: count `Client` rows stamped with this process's `INSTANCE`.** Two numbers per
check: `ever` = rows with `server_id == INSTANCE` (monotonic) and `now` = those with
`disconnected_at IS NULL`. The run resets when `now > 0` **or** `ever` rose since the last check.

- The server stamps `INSTANCE` on every registration. `HeartMonitor.update_client` adds
  `Client.from_heartbeat(hb)` (`server.py:794-801`) → `Client.new(server_id=INSTANCE)`
  (`data/model.py:1411-1421`). A re-registration goes through `Client.reconnect`, which stamps
  `INSTANCE` again (`model.py:1434-1436`). Relay roster rows use `Client.new` too (`server.py:880`).
  `INSTANCE` is one per process (`core/logging.py:63`), so the cluster and its in-process server
  share it.
- No rows are deleted, so `ever` is monotonic. It catches a client that registers and exits between
  two checks. `now` catches a client that registered long ago and still runs, so a zone whose
  launches fail while another zone's client is registered keeps relaunching, as the non-goal
  requires.
- Stale rows from earlier runs carry another `server_id` and are never counted.
- **Probe** (scratch script on a temp site, autoscaler period 1 s, one healthy client with
  `client_timeout=2`, plus a planted "connected" row from a fake earlier server). By second:
  t=0 `ever 0 now 0`; t=1–3 `ever 1 now 1`, dispatcher demand 1, `beats` 1; from t=4 on
  `ever 1 now 0`, demand 0, `beats` 0. The planted row was never counted. Every live source drops
  to 0 after the exit, and only the row count remembers it.
- **Rejected:**
  - `Client.count_connected()` (`model.py:1424-1431`). It filters on `Task.latest_server()`
    (`model.py:1232-1243`): the server that last set `schedule_time`. A run that has scheduled
    nothing yet (a `--min-size` fleet with no work, say) reads an earlier server's rows. A
    crashed earlier run's never-disconnected rows would then keep R3 from ever firing. It also
    misses a register-and-exit.
  - `Dispatcher.clients(zone)` and `demand` (`core/dispatch.py:219, 422-425`). These are live, not
    monotonic: `forget` drops a client (`:490`), and a client enters only at its first request.
  - `HeartMonitor.beats` (`server.py:738, 802, 811`). Also live, and it is #103's territory.
- **No edit to `server.py`, `core/dispatch.py` or `data/model.py` is needed.** The query is two
  lines over the public `Entity.query()` (`model.py:212`), so put a module function in
  `cluster/remote.py`, e.g. `client_registrations() -> Tuple[int, int]`. If the plan prefers a
  `Client.count_registered(server_id=INSTANCE)` classmethod, it would sit beside `count_connected`
  in `Client` (`model.py:1424`), away from `Task`. That is still a high-blast-radius file, though.
- Ordering: call `actuator.clean()` first, then read registrations. A heartbeat sent before the
  client exited is then usually already written. A client that registers and dies within
  milliseconds of a check can still count once before its registration is seen at the next check.
  That is acceptable (it resets then), and it can only matter on the last failure of the run.

## 2. Where the counter lives and how the run stops

- **In `AutoScaler.check`** (`remote.py:505-511`), around `self.clean()`. `AutoScaler.clean`
  (`:593-595`) discards what `LaunchActuator.clean` returns. That method returns the exited
  `Popen`s in launch order and warns once per non-zero exit (`launch.py:310-325`). Make
  `AutoScaler.clean` return the list. The actuator is not the place for the counter: it is shared
  by every fleet and knows nothing about registrations.
- The rule at each check, when `failed_launches > 0`:
  1. Run `exited = clean()`, then `ever, now = registrations()`.
  2. If `now` or `ever > seen`: set `seen = ever`, clear the run, and count none of this check's
     exits.
  3. Otherwise walk `exited` in order: status 0 clears the run, and a non-zero status appends
     `(pid, zone, status)`.
  4. If `len(run) >= failed_launches`: set `self.exhausted = True` and return `FINAL`, before
     `CHECK_*` sets `pending`, so nothing more launches. `finalize` (`:597-601`) then returns
     `HALT`.

  With the limit at 0, return before calling `registrations()`, so a fleet with the limit off runs
  no new query at all.
- **Cluster side.** `AutoScalingCluster.run_with_exceptions` (`remote.py:906-912`) today does
  `autoscaler.join(); server.join()`, and the server runs forever (`:870`). After
  `autoscaler.join()`, if `self.autoscaler.machine.exhausted`: call `self.server.stop(wait=True)`,
  then raise a `RuntimeError` subclass. Its message names the count, the statuses, and the
  advertised `host:port` the cluster built the launch with (`:881`; R6/R7 change that value).
  - **Probe:** with nothing ever registered, `server.stop(wait=True)` took 3.85 s and left the
    server not alive. `hs relay` already relies on this path when no site client ever connected
    (`relay/__init__.py:716-718`).
  - The exception is captured by `core/thread.py` `Thread.run` and re-raised by `join`
    (`thread.py:40-44, 58-62`). `run_cluster`'s `join_waking` re-raises it, and its
    `except: thread.stop(); raise` (`remote.py:84-88`) stops the server a second time; guard that
    (skip the server if it is no longer alive) so its "Stopping" warnings don't repeat.
  - `ClusterApp.exceptions` (`cluster/__init__.py:272-274`) maps `RuntimeError` to
    `handle_exception` → one `CRITICAL <Class>: message` and `exit_status.runtime_error`
    (`core/exceptions.py:91-94, 137`). Share one exception class with R1's `RemoteCluster`/`SSHCluster`
    path.
- **Rejected: raising from `AutoScalerThread.run_with_exceptions`.** The relay's
  `zone_fleet` threads are `AutoScalerThread`s too (`relay/site.py:192`). Raising there would change
  what the relay sees when it joins a fleet. A flag read after `join()` leaves the thread contract
  unchanged. The relay's `stop_site` halts the autoscaler (`relay/__init__.py:710`): `exhausted`
  stays False, and `server.join()` runs as today.
- **Timing (probe):** with `-P 2s` a check runs every 2.0 s (log stamps 2.00–2.02 s apart). Three
  failures are detected about 6 s after "listening", plus about 4 s to stop the server, so the exit
  comes about 10 s after listening. The GOAL drive's bound should allow at least 15 s.

## 3. R5: relay site fleets and `hs server --autoscaling`

- **Off by construction, with no `relay/site.py` edit.** `AutoScaler` and `AutoScalerThread` take
  `failed_launches: int = 0` (and `registrations=None`). Only `AutoScalingCluster` defaults to
  `DEFAULT_AUTOSCALE_FAILED_LAUNCHES` (3) and passes it on. Each relay fleet ends up at 0:
  - `site_cluster` swaps the machine for `site_scaler(...)` (`site.py:145`), and `site_scaler` copies
    an explicit list of settings (`:392-397`) that does not include the new ones, so its `SiteScaler`
    has 0.
  - `zone_fleet` builds `AutoScalerThread` without the setting (`:192-197`), so it also has 0.
- So R5 needs no `relay/site.py` change beyond R6's address line (`:172`).
  - Rejected: `SiteScaler.__init__` forcing 0, or `site_cluster` passing `failed_launches=0`. Both
    are relay edits beyond the GOAL's scope, and both are redundant with the default.
  - Risk: someone later adds `failed_launches=machine.failed_launches` to `site_scaler`. The tests
    below pin against it.
- `hs server --autoscaling` (`server.py:1746-1749` → `cluster/topology.py`) imports only
  `cluster.demand` (`topology.py:40`). Its `TopologyPublisher` launches nothing and has no
  `AutoScaler` or actuator. It is untouched as long as `demand.py` is.

## 4. Config plumbing for `autoscale.failed_launches`

- **Default.** Add `'failed_launches': 3` to `autoscale` in `core/config.py:179-191`, then
  `DEFAULT_AUTOSCALE_FAILED_LAUNCHES = default.autoscale.failed_launches` beside the other defaults
  (`remote.py:321-340`). `ClusterApp` reads the live `config` and passes it from
  `run_autoscaling` (`cluster/__init__.py:319-326`), as it does for the other knobs (`:248-253`).
- **The environment variable does NOT map by itself.** cmdkit's `Environ.expand` splits on every
  underscore. Probe: `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=5` produces `autoscale.failed.launches = 5`,
  and `hs config get autoscale` shows a separate `[autoscale.failed] launches = 5` table while
  `failed_launches` keeps the default. Every underscored key has the same problem (e.g.
  `relay.launcher_args`, `relay.upstream.evict_hint`); that is pre-existing and worth a seed.
- Since R3 requires the variable, add one reader, `configured_failed_launches()`, modelled on
  `topology.configured_distribution` (`topology.py:538-543`):
  - Prefer the env path `('autoscale', 'failed', 'launches')` when
    `config.which(...) == 'env'`. `which` raises `KeyError` when the key is absent; guard it.
  - Otherwise read `('autoscale', 'failed_launches')`.
  - Refuse `bool`, a non-`int` or a negative value with `ConfigurationError` (→ `bad_config`),
    naming the source with `blame(config, *path)`. Probe: on the env path that yields
    `from: HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES`.
  - Env values arrive coerced: `7` → 7, `-1` → -1, `abc` → `'abc'`, `true` → `True`.
  - Validate only in autoscaling mode (`check_arguments`, `cluster/__init__.py:350`).
- **Docs.** `docs/_include/config_param_ref.rst`'s `[autoscale]` section runs from 512 to 639.
  Insert `.failed_launches` after `.sites` (602-614), before `[size]` (616). That keeps it clear of
  `[relay]` (640) and #103's hunks (`server.evict` near 277, relay sections from 640 on). The
  entry needs its `.. versionadded:: 2.9.0` and the env form.

## 5. Probe evidence (abbreviated, temp site, `--no-tls`)

- **Baseline.** `seq 4 > t.in; hs cluster t.in --no-tls --autoscaling fixed -P 2s --launcher false`:
  "Server listening", then every 2.0 s `WARNING Autoscale client (1) exited with status 1` and
  `INFO Autoscale adding client (<pid>, zone: default)`. That gave 5 failures in 12 s, still
  running, stopped by SIGTERM (exit 143). The warning names the list index `(1)`, not the pid; R2
  asks for the process to be named.
- **Inputs.** `-P 2s` is accepted, and `parse_time` takes `1s` and `0` but refuses `-1`. `-P 0`
  looks like a check loop with no sleep (`wait`, `remote.py:492-503`). That is out of scope.
- **Probe side effect.** A real client launched with no `-H` override could not resolve this host's
  own name inside the sandbox (`HostAddressInfo`). That is R6's defect in miniature, so the
  registration probe patched `remote.HOSTNAME` to `127.0.0.1`.
- **Stopping probes.** A probe started through `uv run` and stopped with SIGKILL left its python
  child listening. Stop the PID that `.venv/bin/hs` itself runs as.

## 6. Tests

These follow the patterns already in use: `FakeProc`/`FakeLaunch`/`zoned_scaler`/`run_checks`/
`FleetDB` (`tests/test_zone_autoscale.py:320-343, 483-505, 411+`), `FailingLaunch`
(`tests/test_relay_topology.py:328-334`), and `scaler()` (`:154`). `run_checks` needs a tweak so it
treats `FINAL` as an end.

1. **Three failures stop.** Use `failed_launches=3` and `registrations=lambda: (0, 0)`, with work
   in the own zone. Each started proc gets `status=1`. By the third failing check, `check()`
   returns `FINAL` and `exhausted` is set, the run holds `[1, 1, 1]`, and no fourth launch
   happens.
2. **An exit of 0 resets.** fail, fail, 0, fail, fail → no stop; one more failure → stop.
3. **A registration resets.**
   - `ever` rises between checks (register-and-exit) → no stop, and the count restarts.
   - `now == 1` with `ever` unchanged (a long-lived client in another zone) → never stops.
4. **0 never stops.** `failed_launches=0`, 10 failing checks: states stay within `{SCALE, WAIT}`,
   and a counting `registrations` stub is never called. The existing
   `test_never_ends_and_never_terminates` (`test_zone_autoscale.py:605`) stays green unchanged.
5. **A `SiteScaler` never stops.**
   - `site_cluster(...).autoscaler.machine.failed_launches == 0` and
     `zone_fleet(...).machine.failed_launches == 0`, beside `test_zone_fleet_keeps_a_fixed_size`
     (`tests/test_relay_launch.py:577`).
   - A fixed-fleet `SiteScaler` (no view) on `FailingLaunch` over 10 checks never returns `FINAL`.
   - `test_site_scaler_relaunches_a_failing_launcher_only_while_asked`
     (`test_relay_topology.py:486`) already pins the topology path.
6. **Defaults and plumbing.**
   - Extend `test_cluster_discovers_zones_by_default` (`test_zone_autoscale.py:624`):
     `AutoScalingCluster(...).autoscaler.machine.failed_launches == 3`.
   - Unit-test `configured_failed_launches` (env 5 → 5; −1, `true`, `abc` → `ConfigurationError`
     naming the variable).
7. **Cluster-level.** On a temp site: `AutoScalingCluster(launcher='false', period=1, bind=loopback)`
   joined → raises within about 10 s, the message names "3", the statuses and `host:port`, and the
   server is not alive.
