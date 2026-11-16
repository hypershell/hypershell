# Research 02: `HYPERSHELL_*` variables in use

Scope: `src/`, `tests/`, `docs/`, `share/man`, `.github/workflows/`, `.agents/`, `spec/*/drive*`, `Dockerfile`,
`Apptainer`, README, pyproject (`spec/env-config-keys` excluded). Base: `develop` `90ab2fc2`.
Probes ran one child interpreter per case, with `HYPERSHELL_CONFIG_FILE=` and a throwaway `HYPERSHELL_SITE`,
printing `load_env()` and the merged config. "P:" marks probe evidence.

CI sets no `HYPERSHELL_*` variable. `.github/workflows/tests.yml:99-100` exports `HS_TEST_POSTGRES*`, and
`tests/test_timescale.py:114-122` and `tests/test_upgrade_postgres.py:113-121` turn it into `HYPERSHELL_DATABASE_*`.
README and pyproject name none. `issues/` and `spec/*` prose other than drives is history and not listed here
(it includes `HYPERSHELL_SERVER_MAXRETRIES`, `_BUNDLECAP`, `_CLIENT_ZONE`, which were proposals and are not keys).

## 1. Variables that set keys

"Same?" asks whether a schema matcher with per-key types gives the same key and value as today.

| Variable | Where (main) | Key today | Value forms | Same? |
|---|---|---|---|---|
| `SERVER_PORT` | `tests/conftest.py:68`, many tests, `make-legacy-postgres.sh:79` | `server.port` | digits → int | yes (int) |
| `DATABASE_FILE` | `conftest.py:88`, `temp_site.sh:29`, `drive.sh:111`, `bench_queue.py:178`, docs `database.rst:28` | `database.file` | path | yes |
| `LOGGING_LEVEL` | `conftest.py:89`, Dockerfile:28, Apptainer:55, tests, drives | `logging.level` | `DEBUG`, `devel`, `TRACE`, `INFO`, `WARNING` | yes |
| `LOGGING_STYLE` | Dockerfile:29, Apptainer:56, drives | `logging.style` | `SYSTEM`, `system` | yes |
| `LOGGING_FILE` | `test_logging.py:136,443`, `test_key_option.py:206` | `logging.file` | `enabled` | yes when set alone; **conflict** (see F3) |
| `LOGGING_FILE_ROTATE` / `_LEVEL` | `test_logging.py:485`, `test_size_duration.py:200`, `test_key_option.py:206` | `logging.file.rotate` / `.level` | `@daily`, `30 2 * * *`, `2G`, `2GB`; `devel` | yes |
| `DATABASE_PROVIDER/HOST/USER/DATABASE/SCHEMA` | test_timescale, test_upgrade_postgres, `test_server_initdb.py:480`, contended-writes drive | `database.*` | `postgres`, `timescale`, host, role, name | yes (text) |
| `DATABASE_PORT` | same | `database.port` | digits → int | yes if typed int or TOML-scalar |
| `DATABASE_PASSWORD` | same, `:481` | `database.password` | hex; **`''`** when the URL has none | **no** for `''` (F4) |
| `SERVER_AUTH` | conftest-based tests, `drive.sh:97`, `bench_queue.py:179`, launch handoff | `server.auth` | `token_hex(16/64)`, test constants | yes; text (R4) |
| `RELAY_UPSTREAM_AUTH` | relay tests and drives | `relay.upstream.auth` | `token_hex(16)` | yes; text |
| `SERVER_TLS_ENABLED`, `RELAY_UPSTREAM_TLS_ENABLED` | `test_relay_retire.py:439`, `test_signal_wake.py:281`, docs `security.rst:325,933` | `…tls.enabled` | `false`, `true` | yes (bool) |
| `SERVER_TLS_CERT/KEY/CAFILE/FINGERPRINT/SERVERNAME`, `RELAY_UPSTREAM_TLS_CAFILE` | docs `security.rst:326-329,631,909-924`; `spec/relay/drive/relay_probe.py:609` | `…tls.*` | path, `SHA256:…`, host | yes |
| `SERVER_TLS_MIN_VERSION` | docs `security.rst:826` (k8s example) | **`server.tls.min.version`** | `TLSv1.3` | **no**: R1 change; the doc example is a no-op today |
| `SERVER_TLS_HANDSHAKE` | `test_queue_tls.py:922` | `server.tls.handshake` | `1m` | yes (text, parsed at reader) |
| `SERVER_EVICT` | `test_relay_workload.py:438,848`, `test_relay_retire.py:447,503`, `test_relay_topology.py:935`, drives | `server.evict` | `10`, `30`, `120` → int | yes; R6 must keep int |
| `SERVER_QUEUESIZE`, `SERVER_BUNDLEMAX`, `SUBMIT_BUNDLESIZE` | `test_auth_variants.py:336`, `test_dispatch_*`, `drive.sh:153` | as named | digits → int | yes |
| `SERVER_ADVERTISE` | `test_cluster_advertise.py:232`, cluster-launch-failures drive | `server.advertise` | `no-such-name.invalid`, `localhost` | yes |
| `AUTOSCALE_PERIOD` | `test_duration_options.py:413`, `test_relay_retire.py:440`, `test_relay_topology.py:1016`, drives | `autoscale.period` | `1`, `5m`, **`5MB` (must fail late, F5)** | yes |
| `SUBMIT_BUNDLEWAIT`, `TASK_SIGNALWAIT`, `SERVER_POLL` | `test_duration_options.py:295,319,335` (f-string `HYPERSHELL_{SECTION}_{KEY}`) | as named | `30s`, `5s`, `1m`, `7`→int, `0.5`→float, `5MB` | yes; number type must hold (`:327`) |
| `TASK_TIMEOUT` | `test_size_duration.py:343` | `task.timeout` | `1h30m` | yes |
| `AUTOSCALE_DISTRIBUTION`, `AUTOSCALE_SITES` | `test_topology_server.py:351`, docs `server.rst:105-106`, `config_param_ref.rst:644` | as named | `even` (refused, blame checked `:361`), `fill`; `east,west` | yes |
| `AUTOSCALE_FAILED_LAUNCHES` | `test_autoscale_failed_launches.py:279-301`, cluster-launch-failures drive | **`autoscale.failed.launches`** (P) | `5`, `0`, `-1`, `true`, `abc`, `1.5` | **no**: R1 moves it (F1) |
| `RELAY_DATABASE` | relay tests; set by `relay/__init__.py:1317` | `relay.database` | path | yes |
| `RELAY_BUNDLEWAIT`, `RELAY_UPSTREAM_HEARTRATE/DEADLINE/BACKOFF` | `test_relay_outage.py:160-165`, `test_relay_workload.py:715,813`, `test_relay_retire.py:551` | as named | `1`, `2`, `3`, `5` → int | yes; R6 keeps number |
| `RELAY_UPSTREAM_HOST/PORT` | `test_relay_db.py:168`, `test_signal_wake.py:278`, `test_relay_topology.py:951` | as named | `127.0.0.1` (text: float() fails), digits | yes |
| `RELAY_SIZE`, `RELAY_PORT`, `RELAY_BUNDLESIZE` | `test_relay_db.py:172,408`, `test_relay_launch.py:68`, `test_relay_workload.py:904` | as named | digits → int | yes |
| `RELAY_LAUNCHER`, `RELAY_TEMPLATE`, `RELAY_SITE` | `test_relay_db.py:172` (a path, since `true` "reads as a boolean", `:51`), `test_relay_launch.py:503`, `test_relay_workload.py:242,1119` | as named | path, `echo … {}`, `site-z` | yes |
| `SSH_ARGS` | `test_cluster_launch.py:834` | `ssh.args` | `-n -o BatchMode=yes` | yes |
| `EXPORT_FENCE_PROBE`, `EXPORT_FOO`, `EXPORT_HYPERSHELL_SERVER_AUTH` | `test_relay_launch.py:532`; docs `config_task_env.rst:80`, `security.rst:259` | `export.fence.probe`, `export.foo`, `export.hypershell.server.auth` | `probe:<key>`, `1`, a key | task name same (`FENCE_PROBE`, `FOO`, `HYPERSHELL_SERVER_AUTH`); values same unless coercible (R7) |
| `PROBE_A`, `PROBE_Z` | `test_cluster_launch.py:1444-1446` | `probe.a`, `probe.z` (P) | awkward text, `''` | unread; becomes `probe_a` (change 9) |
| `RELAY_ZONE_GPU_LAUNCHER` | `spec/relay/REVIEW.md:970` (a probe) | `relay.zone.gpu.launcher` | `false` → **False** | **no**: becomes text (intended, R4) |
| `CLIENT_TIMEOUT` | docs `manual.rst:279` (the naming rule) | `client.timeout` | none | yes |

Nothing executable relies on nested splitting except `AUTOSCALE_FAILED_LAUNCHES`. `HYPERSHELL_RELAY_NUM_THREADS`
shows up only in a drive comment noting the defect (`spec/relay/drive/relay_probe.py:589`).

### Findings

- **F1. `failed_launches`.** `remote.py:368-373` reads `which('autoscale','failed','launches')`, and
  `test_autoscale_failed_launches.py:278` documents the split ("arrives split at every underscore"). Its unit
  tests build `Configuration(default=…, **sources, env=reload_env())` (`:262`), so `reload_env()` must still
  return a mapping. `test_refused_from_environment` (`:283-288`) expects the `ConfigurationError` from
  `configured_failed_launches()`. If `reload_env` types the value (`true`, `abc`, `1.5`), the error moves into
  `with_config`, outside the `pytest.raises` block, so the test has to change shape. `-1` still reaches the reader.
- **F2. Blame text is pinned in three places:** `test_initdb.py:25-27`
  (`(env: HYPERSHELL_DATABASE_FILE | default: null)`), `test_topology_server.py:361`, and
  `test_autoscale_failed_launches.py:287,304`.
- **F3. `LOGGING_FILE` with a `LOGGING_FILE_*` sub-key** (`test_key_option.py:206`). Today the scalar is lost
  in either order. P: `{'logging': {'file': {'level': 'devel'}}}`, and `which('logging','file') == 'env'`, so file
  logging is on at the default path through the Namespace branch (`core/logging.py:1017-1039`). A schema matcher
  sees two documented keys, one a scalar and one inside its table, and needs a rule for the pair. The test
  requires that a `.log` file exists (`:216-217`).
- **F4. `DATABASE_PASSWORD=''`** (`url.password or ''`, test_timescale:119, test_upgrade_postgres:118).
  P: today it gives `None`, and import fails with `Must provide 'password' if 'user' provided`
  (`data/core.py:88-90`). As text (R4) `''` passes validation, and `_format_user_and_password` (`:145-149`) then
  leaves the user and the password out of the URL, so the connection is made as the OS user. CI's URL has a
  password, so CI is unaffected; a passwordless local URL changes from refused to connecting as another role.
- **F5. Durations must stay lazy.** `test_relay_refuses_bad_period` (`test_duration_options.py:411-420`) sets
  `AUTOSCALE_PERIOD=5MB` and requires `hs list --count` to succeed. `test_seconds_key_refused` (`:333-338`) wants
  `exit_status.bad_argument` from the option parse, not `bad_config`. Load-time typing of duration keys would
  break both.
- **F6. Prefix edge.** cmdkit matches `startswith('HYPERSHELL')` (`cmdkit/namespace.py:418-420`).
  P: `HYPERSHELLX=1` and `HYPERSHELL_=1` both make the key `''`. The private markers `_HYPERSHELL_RELAY` and
  `_HYPERSHELL_RELAY_REEXEC` stay outside the prefix only because of their leading `_`
  (`core/config.py:500-505`). The matcher should require `HYPERSHELL_`.

## 2. Variables the code sets for child processes

| Setter | Variables | Child reads as |
|---|---|---|
| `cluster/launch.py:243-247` `client_launch` | `HYPERSHELL_SERVER_AUTH` (`token_hex(64)`, `cluster/remote.py:273`, `ssh.py:267`, `local.py:216`), `HYPERSHELL_ZONE` | `server.auth` via `resolve_auth('server')` (`client.py:1665`); `ZONE` is read by no hypershell code, only by user launchers (`test_relay_topology.py:899`), and leaks into the client's layer as key `zone` (P) |
| `cluster/launch.py:464` SSH remote script | `read -r HYPERSHELL_SERVER_AUTH; export` | same |
| `cluster/launch.py:385-403` `ssh_env_exports` ← `ssh.py:291,430` `Namespace.from_env('HYPERSHELL')` | every raw `HYPERSHELL_*` name and string (not expanded), the key excepted | the far side's own loader, so the new mapping applies there, unchanged by this fix |
| `cluster/launch.py:134,531` | `{**os.environ, **load_task_env(), **self.env}` | plain dict merge |
| `relay/site.py:407,476-490` `scrub_environment` | deletes every name starting `HYPERSHELL_RELAY_` and every name or value holding the key; rewrites `HYPERSHELL_CONFIG_FILE` to a site copy | It matches names by prefix on the raw environment, so the matcher does not affect it. Every `relay.*` key, including `relay.zone.*`, has a `HYPERSHELL_RELAY_` name, and no key outside `[relay]` does |
| `relay/__init__.py:1317,1326` `bind_database` | `HYPERSHELL_RELAY_DATABASE=<abs path>`, `_HYPERSHELL_RELAY=1`, `_HYPERSHELL_RELAY_REEXEC=1` (`execve`) | `relay.database` as text; the markers are popped by `os.environ.pop` (`core/config.py:535`, `relay/__init__.py:1312`) |
| `client.py:483-508` task env | `os.environ` minus `AUTH_ENV`, plus `load_task_env()`, plus `TASK_*` | the task |

`core/sys.py` sets no variable; it reads `HYPERSHELL_PYTHONPATH` (`:37`).

## 3. Readers of the env layer and `Environ`

- `partial_load` and `partial_reload` (`core/config.py:276-296`) pass `load_env()` into `Configuration`.
  `Configuration.extend` re-wraps every layer as `Namespace(mapping)` (`cmdkit/config.py:186-189`).
  P: `type(config.namespaces.env) == Namespace`. **The `Environ` type never reaches a consumer**: any
  nested `dict`/`Namespace` works.
- `check_auth_forms` (`:354`) iterates `base.namespaces.items()` and reads nested dicts.
- `blame` (`:299-309`) depends on `which()` returning `'env'` and builds the name from the path.
  Callers: `core/config.py:315,382`; `core/logging.py:557,568,603,612,1084,1096,1101`;
  `cluster/topology.py:543,551`; `cluster/__init__.py:447`; `cluster/ssh.py:363`; `cluster/remote.py:379`.
- `hs config which` (`config.py:353,382-387`) uses `which()`, then
  `namespaces.env.to_env().flatten(prefix='HYPERSHELL')`, which needs a `Namespace` method, not `Environ`.
- `which()` is also called directly at `client.py:332` (`submit.bundlesize`), `core/logging.py:1017`
  (`logging.file` == `'default'`) and `remote.py:370`.
- `ACTIVE_CONFIG_VARS` (`:553-556`) is built from the merged config. P: it offers `site`, `config.file`,
  `zone`, `autoscale.failed.launches`, `relay.zone.gpu.size` and `export.run.id`.
- `load_task_env` (`:640-642`) returns an `Environ` of str→str. Consumers use it as a mapping
  (`launch.py:134,531`, `client.py:507`, `relay/site.py:530`), and `test_cluster_launch.py:1167` monkeypatches
  it with a plain dict. P: `{'RUN_ID': '7'}` from `007`.
- Tests that call `reload_env()` directly: only `test_autoscale_failed_launches.py:43,262`.

So `load_env` can return a plain `Namespace` (or keep `Environ`); nothing checks the type.

## 4. Process variables, not keys

| Variable | Read by | As config today (P) | Any `config.<x>` reader? |
|---|---|---|---|
| `HYPERSHELL_SITE` | `os.getenv`, `core/platform.py:34-37,109` | key `site` | none (`relay.site` is a separate key) |
| `HYPERSHELL_CONFIG_FILE` | `os.getenv`, `core/config.py:486` (`''` means no file: `reload_local('')`) | `config.file = None` | none |
| `HYPERSHELL_PYTHONPATH` | `os.getenv`, `core/sys.py:37` | key `pythonpath` | none |
| `HYPERSHELL_ZONE` | no hypershell reader; client `--zone` defaults to `None` (`client.py:1610`) | key `zone` | none |

There is no special `HYPERSHELL_LOGGING_*` path: logging reads `config.logging` only. `AUTH_ENV`
(`core/config.py:397`) is checked by name only in `client.py:483,1667` and `launch.py:397,412,533`; its value
always comes through `server.auth`. A grep of `config.site`, `config.zone`, `config.config` and
`config.pythonpath` finds nothing in `src/`.

## Must keep working (regression tests)

- `SERVER_PORT=<digits>` → int; `DATABASE_FILE=<path>`; `LOGGING_LEVEL=DEBUG|devel|TRACE`;
  `LOGGING_STYLE=SYSTEM|system`; `CONFIG_FILE=''` (no file); `SITE=<dir>`.
- `SERVER_AUTH` and `RELAY_UPSTREAM_AUTH` = hex keys (text, exact); `EXPORT_HYPERSHELL_SERVER_AUTH`
  → task `HYPERSHELL_SERVER_AUTH`.
- `SERVER_TLS_ENABLED`, `RELAY_UPSTREAM_TLS_ENABLED` = `false`/`true` → bool.
- `DATABASE_PROVIDER/HOST/PORT/USER/PASSWORD(hex)/DATABASE/SCHEMA` as in the PG tests; `PORT` stays a number.
- `SERVER_EVICT=10`, `RELAY_UPSTREAM_HEARTRATE/DEADLINE/BACKOFF=1..5`, `RELAY_BUNDLEWAIT=1` → int.
  `SUBMIT_BUNDLEWAIT`, `TASK_SIGNALWAIT`, `SERVER_POLL`, `AUTOSCALE_PERIOD` = `7` → int, `0.5` → float, units → seconds,
  `5MB` → `bad_argument` only in commands that read it.
- `TASK_TIMEOUT=1h30m`, `SERVER_TLS_HANDSHAKE=1m`, `LOGGING_FILE_ROTATE=@daily|30 2 * * *|2G`,
  `LOGGING_FILE=enabled`, `LOGGING_FILE_LEVEL=devel`.
- `SERVER_QUEUESIZE`, `SERVER_BUNDLEMAX`, `SUBMIT_BUNDLESIZE`, `RELAY_SIZE`, `RELAY_PORT`, `RELAY_BUNDLESIZE` = digits → int.
- `RELAY_DATABASE`, `RELAY_LAUNCHER`, `RELAY_TEMPLATE`, `RELAY_SITE`, `RELAY_UPSTREAM_HOST=127.0.0.1`,
  `SERVER_ADVERTISE`, `SSH_ARGS='-n -o BatchMode=yes'`, `AUTOSCALE_DISTRIBUTION`, `AUTOSCALE_SITES=east,west` → text.
- `RELAY_ZONE=gpu`, `RELAY_ZONE_GPU_SIZE=2` → int.
- `EXPORT_FOO=1` → `FOO=1`; `EXPORT_FENCE_PROBE` → `FENCE_PROBE`.
- Blame and `which` strings for `DATABASE_FILE`, `AUTOSCALE_DISTRIBUTION`, `AUTOSCALE_FAILED_LAUNCHES`.
- `scrub_environment` removes `HYPERSHELL_RELAY_*`, including `RELAY_ZONE_*`; `--ssh --env` forwards raw strings.

## Would change

- `AUTOSCALE_FAILED_LAUNCHES` moves to `autoscale.failed_launches` (remove the `remote.py:368-373` workaround
  and its test docstring); `SERVER_TLS_MIN_VERSION` (doc example) starts working.
- `RELAY_ZONE_<Z>_LAUNCHER=false` and `RELAY_LAUNCHER=true` become text.
- `DATABASE_PASSWORD=''` goes from a refusal to a URL with no user or password (F4).
- `LOGGING_FILE` + `LOGGING_FILE_LEVEL` together: the outcome depends on the plan's conflict rule (F3).
- `SITE`, `CONFIG_FILE`, `PYTHONPATH`, `ZONE` leave the layer and completion; `PROBE_A` becomes `probe_a`;
  `HYPERSHELLX`/`HYPERSHELL_` stop making a `''` key.
- Load-time typing errors for `FAILED_LAUNCHES` move the raise out of `configured_failed_launches` (F1);
  duration keys must not be typed at load (F5).
