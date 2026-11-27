# 02 — Every launch path, the key, and the fence

Scope: R2 ("each zone's fleet and each topology-driven launch alike"), R3 (the key read before
`fence_site`), R4 (the Python API constants). Read on `develop` `11faef5e`.

## Launch paths

Every site client is launched from a `ClientLaunch` that `relay/site.py::zone_launch` builds
(`client_launch(DEFAULT_REMOTE_EXE, …)`, `:181`):

1. **The first zone.** `site_cluster` (`:99`) builds an `AutoScalingCluster`, whose own
   `self.launch` and autoscaler are built with `remote_exe=DEFAULT_REMOTE_EXE` (`remote.py:1023`),
   then replaces `cluster.launch` with `zone_launch(...)` (`site.py:150`) and the autoscaler's machine
   with `site_scaler(..., cluster.launch, view)` (`:153`). The `AutoScalingCluster`'s own launch is
   dead after that, but it is still built with the default; passing `remote_exe` to the
   `AutoScalingCluster` too leaves no launch anywhere built with another program.
2. **Further zones.** `RelayThread.__init__` (`relay/__init__.py:551`) builds
   `zone_fleet(zone_launch(self.cluster, name, ...), ...)` per zone.
3. **Topology-driven launches.** `SiteScaler` (`site.py`) launches with the `ClientLaunch` it was
   given (`site_scaler` → `SiteScaler(launch, ...)`); a fixed fleet and a topology-driven one use the
   same object. No other code path builds a site launch.

So one value threaded `RelayApp.run` → `RelayThread(remote_exe=…)` → `site_cluster(remote_exe=…)`
(→ `AutoScalingCluster(remote_exe=…)` and `zone_launch(…, remote_exe=…)`) and → each further
`zone_launch(…, remote_exe=…)` reaches every launch. `zone_launch`'s docstring already says every
value but the zone must be the one `site_cluster` was given.

## The key and the fence

`config` is built once at import (`invariants.md` §10), from files and the `HYPERSHELL_*` layer
(`config_schema.KEYS`). `relay.remote_exe` is therefore read when the module is imported, long
before `RelayThread.run_with_exceptions` calls `fence_site` (`relay/__init__.py:591`), which scrubs
every `HYPERSHELL_RELAY_*` variable from `os.environ` and hands the site a config copy without
`[relay]`. `RelayApp` resolves the value in `run()` before it constructs `RelayThread`, as it does
`advertise_host` (`:1238`), and passes it as a constructor argument: nothing reads the environment
after the fence. A re-executed relay reads the key again from the same environment (the `execve`
passes `os.environ`). `fence_site` also runs `check_invocation` on every launch: a program path
holding the upstream key is refused like any other leak, unchanged.

`relay.remote_exe` joins `KEYS` as `T` and `EMPTY_UNSET` (Q6), and `default['relay']` gains
`'remote_exe': PARAM_UNSET` (as `advertise`). The empty-variable rule is generic
(`read_environment`); `tests/test_env_config.py::EMPTY_NOT_GIVEN` pins it per key.

## The Python API constants

`cluster/ssh.py:42` and `cluster/remote.py:54` both read `'hyper-shell'` under the comment
"retain old name for remote executable (for now)". `hypershell.cluster` re-exports the `ssh` one
(`cluster/__init__.py:41`, `:52`); `docs/api/cluster.rst:130` autodocs the `remote` one. Users:
`SSHCluster` (`ssh.py:248`), `RemoteCluster` (`remote.py:259`), `AutoScalingCluster`
(`remote.py:964`), and now `site_cluster`/`zone_launch`/`RelayThread`. `hs cluster` always passes
`--remote-exe` (default `sys.argv[0].rstrip('x')`), so its launches do not change (non-goal).

Tests that see the constant: `tests/test_cluster_exit.py:332` asserts
`f'{launcher} hyper-shell client '` in a `RemoteCluster` built without `remote_exe`; it becomes
`hs client`. `tests/test_cluster_launch.py:963` compares against the imported constant and follows
it. `tests/test_relay_workload.py:552-578` uses `['hyper-shell', 'client']` as stand-in argv for fake
site processes; it never launches them and needs no change.

## A pre-existing failure outside the scope

With no `--launcher`, a site client program that does not exist raises `FileNotFoundError` from
`Popen` inside `SiteScaler`, which the FSM logs as CRITICAL, and the relay exits; reproduced on `develop` (the
`hyper-shell` default) and on the prototype (`relay.remote_exe=/nonexistent/…`). The GOAL's
non-goal says "the relay's existing relaunch warnings cover that"; they do for a launcher that
exits non-zero, not for a direct launch. Out of scope here (non-goal); a follow-up seed.
