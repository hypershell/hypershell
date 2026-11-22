# Research 03 — `server.evict` under `hs cluster` (R7)

Source: worktree `fix/config-set-and-unread`, src = develop `d9901cf0`. Probes in throwaway sites
(`temp_site.sh`), bounded with `drive/bound.sh`, 127.0.0.1, key from `HYPERSHELL_SERVER_AUTH`.

## 1. What `hs server` does today (the behaviour R7 mirrors)

- Read: `ServerApp.check_args` `server.py:1913`, `self.evict_after = config_seconds('server', 'evict')`
  (exact value, not rounded; `core/config.py:343`). A non-duration raises `ConfigurationError` → exit 3.
- Floor: **not in `check_args`.** Only `HeartMonitor.__init__` `server.py:797-801`
  (`if evict_after >= 10 … else raise RuntimeError(...)`), built inside `ServerThread.__init__`
  (`server.py:1215`). That is still before bind: `QueueServer` binds in `start()`/`__enter__`
  (`core/queue.py:1210,1249`), entered only in `ServerThread.run_with_exceptions`. Exit 5 via the
  shared mapping (`core/exceptions.py:137`). The check is `>= 10`, so 10 is accepted despite the text.

Probe (`printf 'echo hi\n' | hs server --no-db -H 127.0.0.1 -p 50731 --no-tls`, env `HYPERSHELL_SERVER_EVICT`):

| value | stderr (CRITICAL line) | exit |
|---|---|---|
| `abc` | `[hypershell.server] ConfigurationError: server.evict: Duration 'abc' is not valid: expected a number of seconds, units d, h, m and s … (e.g., 90:00, 1:30:00, 1-12) (from: HYPERSHELL_SERVER_EVICT)` | 3 |
| `5` | `[hypershell.server] RuntimeError: Evict period must be greater than 10 seconds: given 5` (after the `--no-tls` WARNING) | 5 |
| `9.5` / `9.5s` | same, `given 9.5` | 5 |
| TOML `nan` | same, `given nan` | 5 |
| TOML `inf` | `OverflowError: cannot convert float infinity to integer` + traceback file (pre-existing; out of scope) | 5 |
| `10m`, TOML `10` | `Server listening on 127.0.0.1:…` (ran until the bound) | — |

`hs cluster` today (`printf 'echo hi\n' | hs cluster --no-db -H 127.0.0.1 -p 50733 --no-tls`): with
`=abc` and `=5` it listens, runs the task, exits **0**. With `--launcher=true` and `=5` it launches
and waits (clients exit 0; killed at the bound).

## 2. Where `ClusterApp` reads the period and refuses — recommendation

`ClusterApp` has no `check_args`; its start check is `check_arguments` (`cluster/__init__.py:374`),
called first in `__enter__` (`:599-610`), which then runs `enable_tls`, `initdb`/`checkdb`, and
`find_available_ports` (a probe **bind**, `core/config.py:661-671`, when `-p` is the default). Only
then does `run` (`:282`) build a `*Cluster`, whose `ServerThread` precedes any client launch.

Plumbing alone would refuse `5` from `HeartMonitor` with the same text and exit 5, but **after**
`initdb` and the port-probe bind, so R7's "before it binds a port" needs the floor in `check_arguments`.

**Recommendation:** one helper in `server.py` beside `DEFAULT_EVICT` (`:741`), e.g.
`EVICT_MINIMUM = 10` and `check_evict(seconds)` raising
`RuntimeError(f'Evict period must be greater than 10 seconds: given {seconds}')` when
`not seconds >= EVICT_MINIMUM` (keeps `nan` refused, `10` accepted). `HeartMonitor.__init__` calls it
(library callers unchanged); `ClusterApp.check_arguments` does
`self.evict_after = config_seconds('server', 'evict'); check_evict(self.evict_after)` right after the
`autoscale.period` block (`:376-380`), with a class attribute `evict_after: int | float = DEFAULT_EVICT`
as `ServerApp` has (`server.py:1829-1830`). Same text, same status (3 / 5) via the same
`get_shared_exception_mapping`; only the log tag differs (`[hypershell.cluster]`). Optional: call
`check_evict` in `ServerApp.check_args` after `:1913` too (refuses before the TLS warning; text and
status unchanged). The relay (`relay/__init__.py:128-133,1228-1235`) reads `server.evict` through
`config_seconds` in `RelayApp.durations` but has **no** floor helper (floor only in `HeartMonitor`,
after its DB lock); the new helper is reusable there later, out of scope.

## 3. Plumbing per mode — recommendation: one kwarg in the shared call, three constructors

`ClusterApp.run` (`cluster/__init__.py:282-295`) passes common options to every mode's launcher;
add `evict_after=self.evict_after` there. `run_local`/`run_cluster`/`run_ssh` forward `**kwargs`
(`local.py:30`, `remote.py:61`, `ssh.py:45`): no change. Constructors (take the period, never read
config — the same rule §11 states for `advertise=`):

| Mode | Constructor | Today | Edit |
|---|---|---|---|
| local | `LocalCluster.__init__` `cluster/local.py:184-214` | no param; `ServerThread(...)` `:220-238` no `evict_after` | add `evict_after: int = DEFAULT_EVICT`, pass at `:238`; import `DEFAULT_EVICT` `:20`; docstring Args after `zone` `:163` |
| `--launcher`, `--mpi` | `RemoteCluster.__init__` `cluster/remote.py:242-274` | no param; `ServerThread` `:280-298` | add param, pass at `:298` (`DEFAULT_EVICT` already imported `:34-35`); Args after `advertise` `:221` |
| `--ssh` | `SSHCluster.__init__` `cluster/ssh.py:229-263` | no param; `ServerThread` `:271-289` | add param, pass at `:289`; import `DEFAULT_EVICT` `:29`; Args `:202-206` |
| autoscaling | `AutoScalingCluster.__init__` `cluster/remote.py:945-987` | `evict_after: int = DEFAULT_EVICT` `:983`, passed `:1008` | none (now receives it from `run`) |

`cluster/topology.py:593,606` (`AutoScalingServer`, `hs server --autoscaling`) already takes and
passes it; not an `hs cluster` path. `remote.py`/`ssh.py` are high-blast-radius, but the edit is one
defaulted keyword each, passed straight to `ServerThread`; nothing in `client_launch` changes.

Per-mode probe (child interpreter patching `hypershell.server.ServerThread.__init__` to record
`evict_after` and raise; `HYPERSHELL_SERVER_EVICT=30`; `hs cluster t.in --no-tls -H 127.0.0.1 -p 50741 …`):

| args | recorded |
|---|---|
| `--no-db` | `[600]` |
| `--no-db --launcher=true` | `[600]` |
| `--no-db --mpi` | `[600]` |
| `--no-db --ssh localhost` | `[600]` |
| `--repeat --autoscaling fixed --launcher=true` | `[600]` |
| `hs server t.in --no-db …` (control) | `[30]` |

Each child ~1 s. Note: in a reused site a taskfile is gated as already submitted (exit 2) unless
`--no-db`/`--repeat`; use a fresh site per test.

## 4. Proposed tests — new `tests/test_cluster_evict.py`

Module level imports only existing names (`tests.conftest.free_port`, `tests.test_env_config`
`child_env`, `run_bounded`, `KEY`; `tests.create_taskfile`). Model: `RUN_RECORDED` in
`tests/test_period_floor.py:126-147` and `test_relay_passes_duration_seconds`
(`tests/test_env_duration.py:198`). One child program `EVICT_RECORDED`: wraps
`hypershell.cluster.initdb`, `checkdb`, `find_available_ports` with recorders that call the
originals, replaces `hypershell.server.ServerThread.__init__` with a recorder of `evict_after`
(default from `inspect.signature`) that raises `RuntimeError('recorded')`, runs `hypershell.main`,
prints JSON `{status, steps, evict}`. Nothing binds or launches in either outcome.
`MODES` = local `[-H 127.0.0.1, --no-db]`, launcher `[--launcher=true, -H 127.0.0.1, --no-db]`,
mpi `[--mpi, -H 127.0.0.1, --no-db]`, ssh `[--ssh, localhost, -H 127.0.0.1, --no-db]`,
autoscaling `[--autoscaling, fixed, --launcher=true, -H 127.0.0.1]`; `-p free_port()` except where noted.

1. `test_cluster_evict_reaches_server[env-local|…|file-autoscaling]` (@mark.unit; `env`
   `HYPERSHELL_SERVER_EVICT=1m30s`, `file` `[server]\nevict = "1m30s"`) → `evict == [90]`. **Red** ×10 (600).
2. `test_cluster_evict_exact[10|10.5]` (local) → `[10]`, `[10.5]` float kept (floor inclusive, not
   rounded, per env-config-keys D5). **Red** (600).
3. `test_cluster_evict_default[mode]` — variable unset (`child_env(..., HYPERSHELL_SERVER_EVICT=None)`)
   → `[600]`. **Guard.**
4. `test_cluster_evict_refused[abc|5|9.5s|file-5][mode]` (no `-p`, so the port probe would show) →
   status and the CRITICAL text after `] ` equal those of a real bounded `hs server --no-db -H
   127.0.0.1 -p free_port() --no-tls <taskfile>` with the same setting (3 / 5), `steps == []` (no
   initdb, no port probe), `evict == []` (no ServerThread), no traceback. **Red**: today status 5
   `recorded` (≠3 for `abc`; message absent for `5`), steps non-empty.
5. `test_cluster_evict_floor_cli` — real `hs cluster <taskfile> --no-db -H 127.0.0.1 -p free_port()
   --no-tls`, `HYPERSHELL_SERVER_EVICT=5`, via `run_bounded` → exit 5, `Evict period must be greater
   than 10 seconds: given 5` in stderr, `Server listening` absent. **Red** (exit 0, listens).
6. `test_server_evict_floor` — `hs server` with `=5` exits 5 with that message (no test pins it
   today; `grep 'Evict period' tests` is empty). **Guard** for the shared helper.

Existing guards to keep green: `tests/test_env_duration.py::test_duration_refused_at_start[server.evict]`,
`::test_config_seconds*`, `::test_server_evict_units`, `::test_relay_passes_duration_seconds`.
