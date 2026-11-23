# 05 — R8: one `max_retries` default (research brief)

Tree: `fix/update-client-retry` at `eb2a4487` (develop with #117 merged). Read-only; nothing edited.

## 1. Every signature taking `max_retries`

| Entry point | file:line | Default today | Where it flows |
|---|---|---|---|
| `ServerThread.__init__` | `server.py:1258` | `DEFAULT_ATTEMPTS - 1` (0) | `:1273` warning; `:1291` `SchedulerThread(attempts=max_retries + 1)` |
| `serve_from` | `server.py:1471` | `DEFAULT_ATTEMPTS - 1` | → `ServerThread` (`:1562`) |
| `serve_file` | `server.py:1585` | `DEFAULT_ATTEMPTS - 1` | → `serve_from` (`:1676`) |
| `serve_forever` | `server.py:1689` | `DEFAULT_ATTEMPTS - 1` | → `ServerThread` (`:1754`) |
| `LocalCluster.__init__` | `cluster/local.py:205` | `DEFAULT_ATTEMPTS` (1) | → `ServerThread` (`:237`) |
| `RemoteCluster.__init__` | `cluster/remote.py:265` | `DEFAULT_ATTEMPTS` | → `ServerThread` (`:296`) |
| `AutoScalingCluster.__init__` | `cluster/remote.py:969` | `DEFAULT_ATTEMPTS` | → `ServerThread` (`:1008`) **and** `AutoScalerThread(attempts=max_retries + 1)` (`:1043`) |
| `SSHCluster.__init__` | `cluster/ssh.py:255` | `DEFAULT_ATTEMPTS` | → `ServerThread` (`:288`) |
| `run_local` / `run_cluster` / `run_ssh` | `local.py:31`, `remote.py:61`, `ssh.py:46` | none: `*args, **kwargs` to `.new` | inherit the class default |
| `AutoScalingServer.__init__` (not in R8's list) | `cluster/topology.py:590` | `DEFAULT_ATTEMPTS - 1` | `ServerThread` (`:605`), publisher `attempts=max_retries + 1` (`:614`); only caller `serve_with_sites` from `ServerApp.run` (`server.py:1936`), always explicit |
| relay site cluster | `relay/site.py:140` | passes `max_retries=0` explicitly | unaffected (the upstream retries) |

`AutoScaler`/`AutoScalerThread` (`remote.py:477`, `:739`) and `TopologyPublisher` (`topology.py:353`) take `attempts`, not `max_retries`, default 1; always given by their builders. `hsx` is `main_x` → `ClusterApp`, no separate signature. §3's `attempts == max_retries + 1` is preserved everywhere: the `+ 1` lives only in `ServerThread:1291`, `AutoScalingCluster:1043`, `topology.py:614`.

The `in_memory` warning ("Retries disabled when database disabled", `server.py:1273-1274`) fires on `self.in_memory and max_retries > 0`. Today every cluster class with `in_memory=True` logs it unasked (probed below; the `run_local` docstring example itself, `local.py:41`, triggers it). With the R8 default it fires only when the caller passes `max_retries > 0` or the configuration has `attempts > 1`, the same rule #117 gave `hs cluster --no-db` (#117 PLAN residual risk).

## 2. How the apps compute it; design

`ServerApp.max_retries: int = config.server.attempts - 1` (`server.py:1855`) and `ClusterApp.max_retries` (`cluster/__init__.py:166`, #117 R6), class-body attributes: evaluated **at import** of the module, from the singleton that is itself built at import (§10). `-r` help prints `DEFAULT_ATTEMPTS - 1` by convention (help shows `default.*`).

**Recommend a sentinel plus one call-time helper**, in `server.py` beside `DEFAULT_ATTEMPTS`:

```python
def configured_retries() -> int:
    """Retries `server.attempts` allows: the default for every `max_retries` not given."""
    return config.server.attempts - 1
```

- Signatures become `max_retries: Optional[int] = None`. Resolve in **`ServerThread.__init__`** first thing (before the warning), so `serve_*`, `LocalCluster`, `RemoteCluster`, `SSHCluster` just pass `None` through. **`AutoScalingCluster`** must resolve itself before `:1043` (it computes `max_retries + 1` for the autoscaler) and pass the int on; same for `AutoScalingServer` if included (recommend it: one line, and R8 says "every Python API entry point").
- Apps: `max_retries: int = configured_retries()` in both class bodies, so one expression exists (still import-time; identical value).
- Why not a module constant (`DEFAULT_MAX_RETRIES = config.server.attempts - 1`): it freezes at import so in-process tests cannot monkeypatch it, and it breaks the `DEFAULT_* = default.*` convention the help text relies on.
- Precedent for library code reading configuration at call time, with a `None` sentinel: `SSHCluster` reads `config.ssh.args` when `launcher_args is None` (`ssh.py:300`); `ServerThread` reads `config.server.queuesize` (`server.py:1275`). `configured_eager` is **not** a library precedent: it runs only in the apps' start checks; library `eager` defaults to the built-in `DEFAULT_EAGER_MODE`/`False`. So after R8, a library caller with `server.attempts = 3, server.eager = true` gets retries but not eager. Outside R8; name it as a residual (or a seed).
- **Invariant §11**, "Constructors take `advertise=` and never read configuration; only `ClusterApp`/`RelayApp` read the keys": it sits in the advertise paragraph and #117 PLAN §2.4 extended it to `evict_after=` in prose only. Its letter is already bent by `SSHCluster`/`ServerThread` above. R8 bends it for `max_retries`: the plan should declare the bend and amend §11 (scope the sentence to `advertise=`/`evict_after=`; add "`max_retries=None` resolves `server.attempts` through `configured_retries()`"), and add `server.attempts` to §10's list of call-time reads.
- **Validation:** `server.attempts` is kind `I` in `config_schema.KEYS` (`:79`): a non-integer env value exits 3 at import. A file text `"3"` crashes at import (`ServerApp` class body; seed `config-text-attempts-crashes`). No `>= 1` check exists; `0`/negative are absurd input, ranged by `issues/bounded-inputs.md` (counts 1..2³¹−1). Per the rubric's absurd-input rule, the helper adds no handler.

## 3. Tests

**No test relies on the cluster classes' one retry.** Sites building them without `max_retries` either run succeeding `echo` tasks or launchers that never run a task, and none asserts the retry warning: `test_cluster_advertise.py:90,99,109,141-145` (argv/advertise only), `test_autoscale_failed_launches.py:240,243,334` (failed-launch counts; warnings filtered on "exited with status"), `test_cluster_exit.py:79` script (warnings filtered on `Client `), `test_cluster_launch.py:181,201,226` (echo; `WARNING` filters on `'-n'`), `test_finish_wake.py:255,259` (`ServerThread`, attempts unasserted). Explicit `max_retries=0`: `test_finish_wake.py:298`, `test_stream_input.py:303,358,454`, `test_client_return.py:177`; `max_retries=2`: `test_zone_autoscale.py:633`. Grep finds no `"Retries disabled"` in tests.

**Pinning tests (one per entry point, no cluster run):** parametrize over attempts `default` (1 → 0 retries) and `3` (→ 2), setting it by `monkeypatch.setitem(config.server, 'attempts', 3)` (works only with the call-time helper), plus one child-interpreter case with `HYPERSHELL_SERVER_ATTEMPTS=3` (the `config_repr` helper in `tests/test_config_unread.py`) proving the real layer.
- `ServerThread`: `monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)` then `ServerThread(address=('127.0.0.1', 0), auth=…).scheduler.machine.attempts` (pattern `test_finish_wake.py:258-260`). With `in_memory=True`, assert the warning via `caplog` is absent at default, present at 3.
- `AutoScalingCluster(launcher='srun', bind=('127.0.0.1', 0), tls=None)`: `.autoscaler.machine.attempts` and `.server.scheduler.machine.attempts` (pattern `test_zone_autoscale.py:633`).
- `LocalCluster`, `RemoteCluster`, `SSHCluster`: same `.server.scheduler.machine.attempts` with `DATABASE_ENABLED` patched; or patch the module's `ServerThread` with a recorder and assert the `max_retries` it receives resolves (if passing `None` through, assert on the built scheduler, not the kwarg).
- `serve_from`/`serve_file`/`serve_forever` and `run_local`/`run_cluster` (both branches)/`run_ssh`: patch `hypershell.server.ServerThread.new` (resp. the class `.new`) to build without starting and record, or patch `join_waking` to a no-op and read the built thread's scheduler; then `thread.stop()` is not needed since nothing started.
- `AutoScalingServer` if included: `.server.scheduler.machine.attempts` and its publisher's `attempts`.

**Probe on this tree** (`.agents/factory/bin/temp_site.sh uv run python probe.py`, wrapped in `perl -e 'alarm 90; exec …'`: there is no `timeout`/`gtimeout` on this host). Default config and `HYPERSHELL_SERVER_ATTEMPTS=3` give the same library values:

```
config.server.attempts = 1 | 3
ServerThread/serve_from/serve_file/serve_forever  max_retries default = 0
LocalCluster/RemoteCluster/SSHCluster/AutoScalingCluster  max_retries default = 1
AutoScalingServer  max_retries default = 0
ServerApp.max_retries = 0 | 2   ClusterApp.max_retries = 0 | 2
ServerThread().scheduler attempts = 1      (DATABASE_ENABLED patched)
AutoScalingCluster() autoscaler attempts = 2
LocalCluster(in_memory=True) warnings = ['Retries disabled when database disabled']
```

## 4. Docs to change (`.. versionchanged:: 2.9.0`)

- **`docs/api/server.rst:14-15`, `docs/api/cluster.rst:18-19`:** "Configuration files have no impact when using the library; i.e., default values are not overridden by configuration." R8 makes `server.attempts` the exception: amend both, with a versionchanged (cluster page already groups 2.9.0 notes at `:54`; server page at `:52`). `docs/api/client.rst:14` unaffected.
- **Docstrings** "Number of allowed task retries. See `DEFAULT_ATTEMPTS`": `server.py:1200-1201, 1523-1524, 1633-1634, 1714-1715`; `local.py:117-119`; `remote.py:175-177, 840-842`; `ssh.py:161-163` (and `topology.py` if included). Say "Default: `server.attempts - 1`". `DEFAULT_ATTEMPTS`' docstring (`server.py:113`, autodata `docs/api/server.rst:81`) can stay.
- **`docs/_include/config_param_ref.rst:265-280`** (`[server].attempts`, "Read by `hs server` and `hs cluster`"): add the Python API entry points; R9 edits this entry anyway (terminal statuses).
- `server_desc.rst`, `server_help.rst`, `cluster_help.rst`, `docs/cli/cluster.rst:65`: CLI-only, no library default stated; `-r` help keeps `DEFAULT_ATTEMPTS - 1`. No R8 change.

## 5. Overlap with #118 (`fix/submit-input-checks`, merge base `6a0c28ea`)

`git diff origin/develop...fix/submit-input-checks` on `cluster/` and `server.py`: `server.py` moves `is_file_input` out (`-1396`), adds imports, and adds `ServerApp.prepare_source` (called in `run`, before the `serve_*` dispatch); `cluster/__init__.py` changes imports and `ClusterApp`'s source-gate methods. **No hunk touches any `max_retries` signature, the apps' `max_retries` attributes, or the `run` launcher call.** Textual neighbours only: `ServerApp.run` (`+self.prepare_source()` three lines above the `serve_with_sites(... max_retries=...)` block) and the import lines of both files. Not R8, but note for R7's researcher: #118's tip `c7790dca` touches the retry row's construction in `data/model.py` ("a retry runs in its task's group column").
