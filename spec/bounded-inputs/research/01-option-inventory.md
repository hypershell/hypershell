# Research 01 — numeric option inventory and argparse/cmdkit mechanics

Worktree `fix/bounded-inputs`; cmdkit 2.8.0, Python 3.14. Paths are under `src/hypershell/` unless shown.

## 1. Inventory (59 declarations, 9 commands)

The backwards-compatible task-command aliases are the same classes as `hs list`, `hs update`, `hs wait` and `hs run`
(`task.py:1258-1265`), so one declaration covers the top-level command and its alias. `hsx` is `hs cluster`
(`__init__.py` `main_x`). `hs info`, `hs config`, `hs initdb` and the submit alias (`TaskSubmitApp`, tags only)
have no numeric option.
"`X or None`" means the default is `config.X or None` at import, so a configured 0 becomes None.

| Command | Flags (as declared) | `type=` | Default (source) | Key | Class |
| --- | --- | --- | --- | --- | --- |
| submit `submit.py:1586-1621` | `-b/--bundlesize` | int | `submit.bundlesize` | same | Count |
| | `-w/--bundlewait` | parse_time | `submit.bundlewait` | same | Duration |
| | `-c/--cores` | int | `task.cores` or None | task.cores | Cores |
| | `-m/--memory` | parse_bytes | `task.memory` or None | task.memory | Size |
| | `-W/--timeout` | parse_time | `task.timeout` or None | task.timeout | Duration |
| | `-g/--group` | group_option (`:1464`) | None | — | Group |
| | `-p/--port` (dest `queue_port`) | int | `server.port` | server.port | Port |
| server `server.py:1837-1875` | `-b/--bundlesize` | int | `server.bundlesize` | same | Count |
| | `-w/--bundlewait` | parse_time | **`submit.bundlewait`** | (none of its own) | Duration |
| | `-c/--task-cores`, `-m/--task-memory`, `-W/--task-timeout` | int, parse_bytes, parse_time | `task.*` or None | task.* | Cores, Size, Duration |
| | `-r/--max-retries` | int | `configured_retries()` = `server.attempts − 1`, **called at import** (`:117`) | — | Attempts (0…32,766) |
| | `-F/--factor` | float | `autoscale.factor` | same | Factor |
| | `-P/--period` | parse_period | `autoscale.period` | same | Period |
| | `-Q/--poll` | parse_time | `server.poll` | same | Duration |
| | `-p/--port` | int | `server.port` | same | Port |
| client `client.py:1655-1708` | `-N/--num-threads/--num-tasks` | int | literal 1 (`DEFAULT_NUM_THREADS`) | — | Threads |
| | `-p/--port` | int | `server.port` | same | Port |
| | `-b/--bundlesize` | int | None; `client.bundlesize` read later by `advertised_size` (`:351`) | client.bundlesize | Count |
| | `-w/--bundlewait` | parse_time | `client.bundlewait` | same | Duration |
| | `-d/--delay-start` | parse_delay | literal 0 (`DEFAULT_DELAY`) | — | Start delay |
| | `-T/--timeout`, `-W/--task-timeout` | parse_time | `client.timeout` / `task.timeout` or None | same | Duration |
| | `-R/--ratelimit` | parse_rate | `client.ratelimit` or None | same | Rate |
| | `-S/--signalwait` | parse_time | `task.signalwait` | same | Duration |
| | `-C/--client-cores`, `-M/--client-memory` | int, parse_bytes | `client.*` or None | same | Cores, Size |
| cluster `cluster/__init__.py:151-273` | `-N/--num-threads/--num-tasks`, `-d/--delay-start` | int, parse_delay | literal 1, 0 | — | Threads, Start delay |
| | `-b`, `-w` | int, parse_time | `server.bundlesize`, **`submit.bundlewait`** | — | Count, Duration |
| | `-r/--max-retries` | int | `configured_retries()` at import | — | Attempts (option) |
| | `-Q/--poll`, `-p/--port` | parse_time, int | `server.poll`, `server.port` | same | Duration, Port |
| | `-c/--cores`, `-m/--memory`, `-W/--task-timeout`, `-S/--signalwait` | int, parse_bytes, parse_time ×2 | `task.*` (or None) | task.* | Cores, Size, Duration ×2 |
| | `-C/--client-cores`, `-M/--client-memory`, `-T/--timeout`, `-R/--ratelimit` | int, parse_bytes, parse_time, parse_rate | `client.*` or None | client.* | Cores, Size, Duration, Rate |
| | `-F/--factor`, `-P/--period` | float, parse_period | `autoscale.*` | same | Factor, Period |
| | `-X/--min-size`, `-Y/--max-size`, `-I/--init-size` | int | `autoscale.size.min/max/init` | same | Count or none |
| relay `relay/__init__.py:1125-1154` | **`-p/--upstream-port`** | int | `relay.upstream.port` (`DEFAULT_UPSTREAM_PORT`, `:1019`) | same | Port |
| | **`-N/--size`** | int | `relay.size` (`DEFAULT_RELAY_SIZE`, `:121`) | same | Count |
| | `--port` (no short flag) | int | `relay.port` | same | Port |
| list `task.py:657-678` | `-l/--limit`, `-g/--group` (dest `group_filter`), `--part` (dest `part_filter`) | int | None | — | Count, Group, Part |
| update `task.py:1024,1039` | `-l/--limit`, `-g/--group` | int | None | — | Count, Group |
| wait, run `task.py:348,433` | `-n/--interval` | parse_time | literal 5 (`DEFAULT_INTERVAL`, `:310`) | — | Duration |

No numeric option uses `nargs` or a custom `action`. Non-numeric `nargs`/`action` options near them: `-t/--tag
nargs='+'` (submit, run, alias submit; R9's tags), `-A/--autoscaling nargs='?'` (policy text), relay `--zone
action='append'` (zone names only, `zone_options` `relay/__init__.py:301`), `refuse_key_option` (`core/config.py:595`).

**Against the GOAL.**
- **Tally confirmed.** `KEYS` has 46 numeric keys (21 `INT`, 3 `INT_OR_NONE`, 18 `DURATION`, 2 `SIZE`, 2 `NUMBER`),
  plus 4 `INT` zone keys = 50. Every one appears once in the GOAL table. Seven option-only settings: threads,
  retries, group, part, limit, start delay, interval. No other numeric option lacks a key.
- **Relay names.** On `hs relay`, `-p` is `--upstream-port`, and `relay.port` is `--port` alone. `-N` is `--size`
  (Count) there but `--num-threads` (Threads) on client and cluster. Key the option table by (command, dest), not
  by flag, and take `<name>` from the declaration.
- **`--num-tasks`.** `-N` is declared with three strings. argparse names it `-N/--num-threads/--num-tasks`. To
  print the GOAL's `-N/--num-threads`, leave the alias out of the name (see §3).
- **"Count or none".** Only `relay.min/max/init` are `INT_OR_NONE`. `logging.file.keep`, `failed_launches`,
  `autoscale.size.*` and the zone `min/max/init` are plain `INT` with a floor of 0. They take no `<none>`.
- **`-T`/`-W` of 0 on `hs client`.** Refused today with exit 2 (`client.py:1786-1789`). On `hs cluster` they are
  accepted and mean none, since the client reads 0 as none (`client.py:222`). The table's 0 floor would accept
  `hs client -T 0` as "never", which widens today's behaviour. Decide this and record it in TECH.md, or keep a
  floor of 1 for the two client options.
- **`-n 0`** on `hs wait`/`run` makes `time.sleep(0)` re-query the database in a tight loop (`task.py:391-396`). R1(c)
  allows a floor above 0 here.
- **Accepted today, outside the table:** `-R -5` (`parse_rate` compares `abs`, `core/types.py:194`); `-F nan/inf/-1`
  (plain `float`); `-l -5` (on SQLite a negative LIMIT is no limit). `-l 0` lists nothing, but `hs update -l 0`
  takes the unlimited path at `if self.limit:` (`task.py:1138`).
- `database.port` has no built-in default. The R1 test that checks each default against its range must skip it.
  Every other numeric default is inside its range.

## 2. How an option error reaches the user

- argparse 3.14 `_get_value` (`argparse.py:2646-2668`): `ArgumentTypeError` → its own text. `TypeError`/`ValueError` →
  `invalid <type.__name__> value: 'x'`. Both become `argparse.ArgumentError`, printed as
  `argument <'/'.join(option_strings)>: <msg>` (`_get_action_name`, `:777-781`). `parse_known_args` catches that
  (`:2042-2046`) and calls `self.error(str(err))`.
- cmdkit `Interface.error`/`exit` raise `cmdkit.cli.ArgumentError(message)` (`cli.py:95-100`). `Application.main`
  catches it, calls `log_critical` and returns `bad_argument` = 2 (`app.py:175-183`). HyperShell sets
  `Application.log_critical = log.critical` (`__init__.py:72`).
- Probed: `hs client -N x` → `CRITICAL [hypershell] argument -N/--num-threads/--num-tasks: invalid int value: 'x'`,
  exit 2. `hs submit -g 99999999999` → `argument -g/--group: task group must be …`, exit 2. `hs relay -N 0` →
  `Site size must be at least 1 …`, exit 2, raised by `check_args`. `UnitError` is both a ValueError and an
  ArgumentTypeError, and ArgumentTypeError is caught first, so its text is printed as written.
- **Any other exception type from a `type=` callable is not caught by argparse.** A `cmdkit.cli.ArgumentError`
  raised there reaches `Application.main` and is printed exactly as raised, exit 2. A prototype printed
  `-N/--num-threads: expected a whole number from 1 to 4,194,304, given 0`, exit 2, for `-N 0`,
  `-N abc` and `--num-tasks 0` alike.
- **Hazard: argparse parses string defaults again** (`argparse.py:2331-2338`). When an option is not given and its
  default is a `str`, argparse passes the default through `type=`. Configuration text therefore fails as if it were
  the option. A file with `[server] port = "x"` makes `hs submit -q` fail with
  `argument -p/--port: invalid int value: 'x'`, exit 2. `HYPERSHELL_SUBMIT_BUNDLEWAIT=zz` fails `-w/--bundlewait`
  the same way, exit 2. Duration and size values are left as text by `read_value`. R4 requires exit 3 with
  `(from: …)` for these, so R5's load check must run before the class bodies and give them parsed numbers, not text.

## 3. Recommended mechanism

- **Route B: the error escapes argparse.** A helper `ranged_option(interface, *flags, setting='threads'|'<key>',
  hidden=('--num-tasks',), **kwargs)`:
  - It builds a `RangedOption` callable from the table entry and the display name, which is
    `'/'.join(flags minus hidden)`.
  - It calls `interface.add_argument(*flags, type=that, **kwargs)`.
  - On a bad value, the callable raises `cmdkit.cli.ArgumentError(f'{name}: expected {form} from {A} to {B},
    given {V}')`.
  - It must not subclass ValueError, TypeError or ArgumentTypeError. That gives the exact text and exit 2, with
    no change to cmdkit and no change to other argparse messages.
  - Give it a `__repr__`. The callable's `__name__` matters only on argparse's ValueError path, which this route
    never takes.
- **Route A, rejected:** an ArgumentTypeError plus a HyperShell `Interface` subclass whose `error()` strips
  `argument ` and the alias. It touches about 15 interfaces, rewrites every other argparse message unless it
  matches narrowly, and depends on parsing text.
- **R1 test hook.** The helper records (program, flags, setting) in a registry. A test walks each app's
  `interface._actions` and fails on any action whose `type` is `int`, `float` or a `parse_*` function rather than a
  `RangedOption`. Option-only settings need table names, for example `option.threads`, `option.retries`,
  `option.group`, `option.part`, `option.limit`, `option.delay`, `option.interval`.
- **Import cost.** Every `Interface` is built in a class body. `hypershell/__init__.py` imports all the apps, so
  each command builds every interface at start-up. The factory must be a dictionary lookup that builds a small
  object. The table should live beside `KEYS` in `core/config_schema.py`, which imports only the standard library.
  It should own the bound constants (`GROUP_MIN/MAX`, `RATE_MINIMUM`, the evict floor), and `data/model.py` and
  `core/types.py` should import them from it.
- **Defaults.** Class defaults should come from the checked, parsed configuration, never text (the §2 hazard).
  `configured_retries()` and the `or None` defaults run at import. With `attempts = "3"` they raise `TypeError`
  there today. Accept None and already-parsed numbers in the callable without parsing them again.
- **`<V>`.** For an option, cut the argv text, which is already a string. For a configuration integer, guard
  `str()`: from Python 3.11, converting an integer of more than 4,300 digits to text raises `ValueError`.

## 4. Conversions after parse today (to move into the table and parsers)

- `server.py:1974-1977` and `cluster/__init__.py:386-390`: `parse_period` again on a configured number, then
  `ArgumentError('autoscale.period: …')`, exit 2.
- `server.py:1994` and `cluster/__init__.py:391`: `check_evict(config_seconds('server','evict'))`, a
  `RuntimeError` (exit 5), at `server.py:826`.
- `cluster/__init__.py:392-400`: `client.ratelimit` finiteness and floor for a configured float, exit 2.
- `cluster/__init__.py:349` → `cluster/remote.py:372-378` `configured_failed_launches`, `ConfigurationError` (exit 3).
- `server.py:117` `configured_retries()`, called in class bodies at `server.py:1850` and
  `cluster/__init__.py:170`.
- `client.py:1786-1790`: `-T`/`-W` ≤ 0 refused, and `config_seconds('client','heartrate')`.
- `client.py:351` `advertised_size` reads `client.bundlesize` from configuration (`ConfigurationError`).
- relay (`relay/__init__.py`):
  - `:1252` size < 1
  - `:1317` `site_period` (`parse_period`)
  - `:1329` `durations` (`config_seconds` over `RELAY_DURATIONS`, `:130`)
  - `:1339` `relay_bounds`, and `:1389` `zone_settings` (zone `size` ≥ 1, `min/max/init` ≥ 0), all
    `ArgumentError`, exit 2
  - `check_handshake` → `core/tls.py:160` `handshake_seconds`
- `submit.py:1464` `group_option` → `data/model.py:104-109` `parse_group`, already a `type=`.
- `task.py:556-559`: `-g`/`--part` are formatted into where-clause text (`group == N`). `task.py:498`
  `query.limit(self.limit)`, plus `:729` and `:1133-1158` (`if self.limit:`).
- `core/config.py:345` `config_seconds`, the shared duration reader for configuration.
