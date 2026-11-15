# 04 — Typed readers: what each non-text key's reader accepts today

Source identical to `develop` `90ab2fc2`. Probes: a child interpreter per case,
`HYPERSHELL_CONFIG_FILE=` (empty), a throwaway `HYPERSHELL_SITE`, printing `hypershell.core.config.config`.
No services started.

## Two mechanisms that set what a reader sees

1. **argparse converts string defaults.** Most numeric keys reach their app as
   `add_argument(..., type=int|float|parse_bytes|parse_time, default=config.<key>)`. argparse runs
   `type` on a default only when the default is a `str`. So text `'4'` and int `4` both arrive as `4`.
   A non-str value (`1.5`, `True`, `None`) is passed through unchecked. Evidence: `server.py:1753-1792`,
   `client.py:1588-1654`, `cluster/__init__.py:150-272`, `submit.py:1352-1366`, `relay/__init__.py:1070-1093`.
2. **Many keys bypass argparse.** These are read directly and need the real type:
   `server.attempts - 1` in a class body (`server.py:1767`; text would raise TypeError at import);
   `config.<k> or None` (`server.py:1759-1761`, `client.py:1612-1636`; text `'0'` is truthy);
   `base.submit.bundlesize` advertised raw (`client.py:332`); `server.queuesize` → `Queue(maxsize=)`
   (`core/queue.py:1110,1218`); relay values passed straight to `RelayThread` (`relay/__init__.py:1143-1166`).

**Dead config reads (not in scope, but they affect the probes):** `server.eager` never reaches the app. The class
attribute is overwritten by `--eager`'s `store_true` default of False. Probe: `HYPERSHELL_SERVER_EAGER=true`
gives `ServerApp.from_cmdline([]).eager_mode == False`, and the same for `ClusterApp`
(`server.py:1766-1769`, `cluster/__init__.py:162-165`). `client.bundlesize` and `client.bundlewait` are
read only from `default` (`client.py:315,318`). `hs cluster -R` reads `task.ratelimit`, which is not
documented, instead of `client.ratelimit` (`cluster/__init__.py:240`).

## Probes of today's environment behaviour

| Variable | Value today |
|---|---|
| `SERVER_EAGER=no` / `SERVER_TLS_ENABLED=off` / `DATABASE_ECHO=yes` | `'no'` / `'off'` / `'yes'` (str, all truthy) |
| `SERVER_TLS_ENABLED=` | `None`, which turns TLS off (`tls.py:498`) |
| `SERVER_TLS_ENABLED=0` | `0` (int) |
| `RELAY_MAX=4` / `RELAY_MIN=1.0` | `4` int / `1.0` float (the relay refuses the float, `relay/__init__.py:1229`) |
| `TASK_MEMORY=16G` / `=1e3` / `CLIENT_MEMORY=0` | `'16G'` / `1000.0` float / `0` |
| `CLIENT_RATELIMIT=2.5` / `AUTOSCALE_FACTOR=inf` / `LOGGING_FILE_KEEP=nan` | `2.5` / `inf` / `nan` (floats) |
| `SERVER_PORT=+5_0001` | `50001` (Python `int()` accepts the sign and `_`) |
| `LOGGING_LEVEL=10` | int; exits 3 with "Expected string for 'logging.level'" (`logging.py:555-561`) |
| `LOGGING_FILE=true` / `TRUE` | `True`; logs to the default file |
| `LOGGING_FILE=Enabled` | `'Enabled'`, used as a **path** named `Enabled` (the match is case-sensitive, `logging.py:1024`) |
| `LOGGING_FILE=1` | refused: "expected path-like or Namespace, given int:1" |
| `LOGGING_FILE=false` | `False`, then a **TypeError traceback** in `posixpath.dirname` |
| `LOGGING_FILE=/tmp/x.log` | `'/tmp/x.log'` |
| `AUTOSCALE_SITES=a,b` / `RELAY_ZONE=gpu` / `SSH_NODELIST=a,b` | text |
| `RELAY_ZONE_GPU_SIZE=2` | `relay.zone = {gpu: {size: 2}}` |
| `DATABASE_CONNECT_ARGS_TIMEOUT=5` | `database.connect.args.timeout = 5` (R1 bug; the value is int) |
| `DATABASE_TIMESCALE_POLICY=no` | `'no'`, which `_check_setting` refuses (it takes only true/false, `timescale.py:146-151`) |
| `SERVER_TLS_FINGERPRINT=` | `None`, read as unset (`tls.py:475`) |
| `EXPORT_RUN=007` | `export.run = 7` |

## Per-key table

Variable names omit the `HYPERSHELL_` prefix. "int" means a whole number under the rule below.
`⚠default` marks a key where typing from the default alone would be wrong.

| Key(s) | Declared type | Env text accepted | Converted value | Reader evidence |
|---|---|---|---|---|
| `logging.color`, `server.eager`, `server.tls.enabled`, `server.tls.insecure`, `relay.upstream.tls.enabled` | bool | boolean words | `True`/`False` | truthiness: `logging.py:634`; `tls.py:498,506`; `relay/__init__.py:1183`; `server.py:1940` |
| `database.echo` ⚠default (no default) | bool | boolean words | bool | `if engine_echo:` `data/core.py:202,259` |
| `database.timescale.columnstore`, `.policy` | bool | boolean words | bool | takes a bool, or the text `true`/`false` only (`timescale.py:146-151`) |
| `server.port`, `relay.port`, `relay.upstream.port` | int | int | int | `type=int` (`server.py:1792`, `relay/__init__.py:1094`); `int(port or section.port)` `relay/upstream.py:166`; read raw for `submit --queue` (`core/queue.py:1108`) |
| `database.port` ⚠default (no default) | int | int | int | f-string into the URL (`data/core.py:134-139`); any type works, int is the documented meaning |
| `server.bundlesize`, `submit.bundlesize`, `client.bundlesize`, `server.queuesize`, `server.bundlemax`, `server.attempts` | int | int | int | `type=int`, plus the raw reads above; `int(bundlemax)` `dispatch.py:210` |
| `task.cores`, `client.cores` | int | int | int | `or None`, then `type=int` |
| `client.ratelimit` | number (int or float) | int or float | int/float | executor does `1/self.ratelimit` (`client.py:949`). The CLI `-R` is `type=int`, but env `2.5` works today as a float. Keep floats (R1: every value that works today keeps working) |
| `autoscale.factor` | float | int or float | float | `type=float` (`server.py:1782`, `cluster/__init__.py:262`) |
| `autoscale.size.init`/`min`/`max`, `autoscale.failed_launches` | int | int | int | `type=int` (`cluster/__init__.py:264-266`); `failed_launches` checks a non-bool int ≥0 (`cluster/remote.py:376`) |
| `relay.size`, `relay.bundlesize`, `relay.num_threads`, `relay.upstream.bundlesize` | int | int | int | `-N type=int`; `int()` in `site_bundlesize` (`relay/__init__.py:318`); `upstream_bundlesize` raw |
| `relay.min`/`max`/`init` ⚠default (`'<none>'`) | int or the sentinel | int, or exactly `<none>` | int / `'<none>'` | needs a non-bool int ≥0, or None/`'<none>'` (`relay/__init__.py:1226-1232`, `:265`) |
| `relay.zone.<z>.size`, `.min`/`.max`/`.init` | int | int | int | non-bool int (size ≥1; bounds ≥0). `'<none>'` is **refused** in a zone table (`relay/__init__.py:1277-1289`) |
| `relay.zone.<z>.launcher`/`launcher_args` | text | any | str | must be `str` (`relay/__init__.py:1291-1293`) |
| `logging.file.keep` | int | int | int | `int(count)`, ≥0 (`logging.py:331-336`) |
| `task.memory`, `client.memory` ⚠default (`0`) | size | a whole number, or `parse_bytes` forms (`16G`, `1.5GiB`, `512MB`) | see note S | `or None`, then `type=parse_bytes` (`server.py:1763`, `client.py:1638`, `cluster/__init__.py:233,238`, `submit.py:1364`); `parse_bytes` takes str or int (`types.py:111-117`) |
| `logging.file.rotate` | text | any (a size, a cron expression, `never`) | str | `parse_bytes(str)`, else cron (`logging.py:1044-1074`). Keep `'1024'` as text: `parse_bytes('1024')` works, and a cron expression can start with a digit |
| `logging.file` (scalar) ⚠default (a table) | special | `true`/`enabled` in any case → enabled; any other text → path | `True` or path str | `isinstance(str,bool)`, then `in [True,'enabled']` (`logging.py:1024-1026`). See note L |
| `logging.level`, `logging.file.level` | text | names | str | needs `str`, upper-cased (`logging.py:555-561`); `'10'` stays an error |
| `server.tls.min_version` | text | `TLSv1.2`/`TLSv1.3` | str | `or DEFAULT` (`tls.py:507`), then a mapping lookup (`tls.py:154`) |
| `database.timescale.chunk_interval`, `compress_after`, `segmentby`, `orderby` | text | any | str | needs `str` (`timescale.py:152`). `segmentby=""` means none, but env `''` gives None today, which is refused. The text reader fixes this |
| `autoscale.sites` | text | comma string | str | `parse_sites` splits a str (`topology.py:79-95`) |
| `relay.zone` (scalar) | text | a name (`''` = default zone) | str | `zone_options`: `''`/None/`<none>` → default; else `zone_name(str(v))` (`relay/__init__.py:276-310`, `model.py:110`). A comma is part of the name, so env cannot give a list |
| `ssh.nodelist` (scalar) and **groups** | text (groups too) | host pattern | str | needs `str` or list, else refused (`cluster/ssh.py:367-385`). ⚠ Under the TOML rule, a group `=123` would become an int and be refused: declare the groups text |
| `database.connect_args.*` | TOML scalar else text | any | int/float/bool/str | `create_engine(connect_args=)` → `sqlite3.connect(**kw)` (`data/core.py:262`): `timeout` float, `check_same_thread`/`uri` bool, `detect_types` int; psycopg takes any. Today `''` gives None (`isolation_level=None`); the TOML rule gives `''` |
| `database.pragmas.*` | TOML scalar else text | any | any | `f'PRAGMA {name}={value}'` (`data/core.py:294`): any type works (`True` gives `TRUE`, valid in SQLite) |
| `[database]` `*` passthrough (e.g. `DATABASE_SSL_MODE`) | TOML scalar else text | any | any | goes to `DatabaseURL.parameters`, then `urlencode` (`data/core.py:121,58`): always `str()`'d, so the type does not matter |
| `export.*` (env form) | text, verbatim | any | str | `to_env().flatten()` re-stringifies (`core/config.py:640-642`); R7 |
| Duration keys (other brief) | — | — | — | `parse_time` via argparse takes int/float/str: `server.poll`, `autoscale.period`, `submit.bundlewait`, `task.timeout`, `task.signalwait`, `client.timeout`. `handshake_seconds` takes int/float/str (`tls.py:160`). `site_period` takes a number or a str (`relay/__init__.py:1208-1219`). R6's ten take numbers only today; `relay.timeout` is forwarded as `-T` text |
| All other documented keys (`bind`, `host`, `auth*`, `datefmt`, `format`, `style`, `provider`, `url`, `file`, `user`, `password`, `schema`, `tls.cert`/`key`/`cafile`/`fingerprint`/`ciphers`/`servername`, `advertise`, `launcher*`, `template`, `site`, `database` (relay), `policy`, `distribution`, `ssh.config`/`args`, `task.cwd`, `console.theme`, `logging.file.path`/`style`/`compress`) | text | any | str | — |

**Sentinels.** Keys with the default `'<none>'` are `logging.file.path`, `logging.file.compress`, `server.advertise`,
`server.tls.fingerprint`/`ciphers`/`servername`, `autoscale.sites`, `relay.database`/`zone`/`site`/`min`/`max`/`init`
and `relay.upstream.tls.fingerprint`. Keys with the default `'<auto>'` are `server.tls.cert`/`key`/`cafile` and
`relay.upstream.tls.cert`/`cafile`. All are text except `relay.min`/`max`/`init`. Those three must also accept the
literal `<none>`.

**Note S (sizes).** Convert whole-number text to `int` (`0` must stay falsy for `or None`). Other text: check it
with `parse_bytes` at load, so a bad value is a configuration error naming the variable, and keep the text
(`hs config get` shows `16G`). Readers already parse it. `1e3` is rejected (today it is the float `1000.0`).

**Note L (`logging.file`).** Map `true`/`enabled` in any case to `True`. Map any other text to a path, which keeps
`/tmp/x.log`. Recommend that the false words (`false`/`no`/`off`/`0`) be a configuration error. Today `false`
crashes with a traceback, and the reference documents no "disabled" form. The table subkeys come through
`HYPERSHELL_LOGGING_FILE_<SUB>` as usual.

## Readers that would change or break under the converted value

- **`''` on an optional text key.** R4 keeps the empty text, but these readers took the `None` that `''` gave
  them as "unset":
  - TLS fields: `tls.py:472-477` tests `in (PARAM_NONE, None)`. `''` now goes on as a path, fingerprint or
    cipher and fails.
  - `logging.file.compress`: `set_compression('')` gives "Unsupported" (`logging.py:1046`).
  - `database.user`/`password`: `is None` tests (`data/core.py:90-94`), so `''` counts as given.
  - `task.cwd=''`: subprocess `cwd=''`.

  `server.advertise` already treats `''` as unset (`cluster/__init__.py:349`). Planner: either add `''` to
  those unset tests, or document the change.
- **`''` on a typed key** is an error under R5. Today it gives None: TLS off for `SERVER_TLS_ENABLED=`, and
  "unconstrained" for `TASK_MEMORY=`. Release-note it.
- **No reader breaks on converted ints, floats or bools.** Every typed reader accepts the converted type.
  The only intentional flips are `no`/`off` → False (change 4). `timescale.policy`/`columnstore` gain
  yes/no/on/off for free.

## Recommended parse rules

Strip surrounding whitespace first. Match with `re.ASCII`, because Python's `int()`/`float()` accept non-ASCII
digits.

- **Boolean.** Lower-case the text. `true|yes|on|1` → True; `false|no|off|0` → False. Anything else is an
  error, including `''`.
- **Integer.** `[+-]?[0-9]+(_[0-9]+)*`, then `int()`. Accept leading zeros: the key is typed, so `007` cannot be
  mistaken for text, and Python reads it as decimal. Accept `_` and a sign: today's `int()` already takes them,
  and range checks stay with the readers (bounds ≥0, port). Reject `1.0`, `1e3`, hex, `''`, `nan`.
  Rejecting `1.0` matches the relay's own refusal of floats.
- **Float.** The integer form, or `[+-]?([0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)([eE][+-]?[0-9]+)?` → `float()`.
  Reject `nan`/`inf`/`infinity`: today they pass and are meaningless. `factor=inf` sets the pressure to 0,
  and `nan` fails every comparison. Also reject a result that is not finite (overflow, e.g. `1e999`).
- **Untyped entries (the TOML rule).** Parse with `tomllib.loads('v = ' + text)` only when the text has no
  newline and no `#`: otherwise `5\nx = 1` would add a key and `5 # c` would parse as a comment. Accept the
  result only if it is int, float or bool, else keep the text. Probed with `tomllib`: `007`, `True`, `null`
  and `''` stay text. `1e3` → `1000.0`, `+5` → 5, `1_000` → 1000, `0x1F` → 31, `true` → True.
  `inf`/`nan` → float, which is fine for pass-through entries.
  A regex for TOML's decimal/float/bool grammar is an equivalent and safer alternative.
