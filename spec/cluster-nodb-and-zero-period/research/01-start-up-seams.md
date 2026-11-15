# 01 — Start-up seams for `--no-db` and the period floor

Scoped brief (hs-plan Step 3, high-blast-radius exception: the design edits `server.py`). Done
sequentially by the planner; every claim below was probed on the base tree (`2f4df3e3`, whose
`src/` equals `90ab2fc2`) on 2026-11-15 in a throwaway site.

## 1. What opens the database under `hs cluster --no-db`

- The only start-up call is `ClusterApp.__enter__` (`cluster/__init__.py:596-603`):
  `initdb()` when `DATABASE_DIALECT == 'sqlite'` or `--initdb`, else `checkdb()` unless `--no-db`.
  `--initdb` and `--no-db` are one mutually exclusive group (`:167-171`), so `--no-db --initdb`
  never parses.
- Probe: the same `ClusterApp.main` with `hypershell.cluster.initdb`/`checkdb` replaced by
  recorders, `--no-db`, local mode and `--launcher env -H 127.0.0.1 --remote-exe <venv hs>`, with
  `HYPERSHELL_DATABASE_FILE` both a fresh path and a path under a missing directory: every run
  exits 0, both tasks run, and the file (and the missing directory) never appears. So nothing past
  the start-up call opens the database: the engine is lazy (`data/core.py` connects on first use),
  source gating and `Zone.ensure` are already skipped under `--no-db`
  (`prepare_source`/`prepare_json_source`, `check_arguments` refuses `--zone`), and the server
  threads guard every write with `in_memory` (invariants §4). Launched clients never open it.
- `--ssh` is refused nothing by `check_arguments` and builds its node list only in `run_ssh`, so
  an in-process `__enter__` covers it without a host.
- With no database configured (`DATABASE_ENABLED` false: SQLite `:memory:`), `initdb()` without
  `--no-db` runs against the in-memory engine; the server then forces `in_memory`. Unchanged by a
  guard that skips only under `--no-db`.

## 2. How a period reaches `hs cluster` and `hs server`

- `-P` is `type=parse_time, default=config.autoscale.period` on both apps (`cluster/__init__.py:263`,
  `server.py:1783`). argparse converts a **string** default through `type=` only when the option is
  not given, and reports a failure as `argument -P/--period: …` (exit 2 through cmdkit's
  `ArgumentError`). A **number** default is never converted.
- The loader types the key (probe, `HYPERSHELL_AUTOSCALE_PERIOD=v`): `0` → `0`, `-5` → `-5`,
  `0.5` → `0.5`, `nan` → `nan` (floats), `0s` → `'0s'`; a TOML `period = 0.5` → `0.5`. So the
  four routes are: option text (string), key string (string default), key number and environment
  number (number default, unconverted).
- Probe (serve functions replaced by recorders): `hs server --no-tls -P 0 FILE --no-db`,
  `hs server --no-tls --autoscaling fixed -P 0s` (period `0` reaches `serve_with_sites`),
  `hs cluster FILE --no-db --no-tls -P 0`, `HYPERSHELL_AUTOSCALE_PERIOD=-5 hs cluster …`,
  `HYPERSHELL_AUTOSCALE_PERIOD=0s hs server …` all exit 0 today; `=5MB` exits 2 at parse.
- Order before anything listens: `ClusterApp.__enter__` runs `check_arguments()` first, then TLS,
  the database and the port; `ServerApp.run` runs `resolve_auth`, `check_args()`, `enable_tls()`,
  `check_database()`, then serves. Both checks are the first point after parsing.
- `tests/test_duration_options.py::test_seconds_option_parsed` asserts `-P 0` → `0` for both apps
  (case `('0', 0)` over `SECONDS_OPTIONS`); it must change with the floor.

## 3. `hs relay`

- `RelayApp.site_period` (`relay/__init__.py:1207-1219`): a non-string is returned as is, a string
  goes through `parse_time`, a `UnitError` becomes `ArgumentError('autoscale.period: …')`.
  `check_args()` reads it, and `run()` calls `check_args()` before it binds or locks its database,
  builds `RelayThread`, connects upstream or launches a site client. Probe: `0` and `-5` pass today.
- `relay/site.py::DEFAULT_SITE_PERIOD` is the built-in default and never reads configuration; the
  relay passes `site_period` to `RelayThread` and on to `SiteScaler`/`AutoScalingCluster`.

## 4. Autoscaler waits (unchanged by the design)

`AutoScaler.wait` (`cluster/remote.py:563-574`) and `TopologyPublisher.wait`
(`cluster/topology.py:401-408`) leave their wait once `now - last_check > timedelta(seconds=period)`
and otherwise sleep one second. A period above zero therefore waits at least one second between
checks; zero or less never sleeps. Refusing at the commands removes every route a command offers,
so the loops need no change.
