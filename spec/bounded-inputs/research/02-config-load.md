# Research 02 — configuration load, start-up check, `hs config set`, R8 backstop

Probes ran in throwaway sites (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`, `HYPERSHELL_CONFIG_FILE`
under `$TMPDIR`) against this worktree's editable install. Line numbers are on `fix/bounded-inputs`.

## 1. How the configuration is built and read

- **Build.** `core/config.py:642-654` runs at import: `reload_local(HYPERSHELL_CONFIG_FILE)` (only the
  `local` layer, `path.local.config` rewritten so `blame` names that file) or `load()`; then
  `check_auth_forms`. A `ConfigurationError` prints one line via `display_critical` and exits 3; any
  other exception goes to `write_traceback` (a log file plus two lines), also exit 3.
  `build_configuration` (`:621-623`) loads twice: once bare, to derive the `preload` layer
  (logging style, default database file), then with it. Layers, low to high: `default`, `preload`,
  `system`, `user`, `local`, `env` (`partial_load`, `:293-304`). Files go through `reload_file`
  (`:252-259`, cmdkit `Namespace.from_toml` = `tomllib`), and any parse error becomes
  `ConfigurationError('(from file: …) …')`. The env layer is typed by `read_environment`
  (`config_schema.py:344-391`), which already refuses a kind error with exit 3 for every command,
  `hs config` included.
- **Merge.** cmdkit `Configuration.extend` applies each layer depth-first (`cmdkit/config.py:173-191`,
  `_apply_layer :64-67`); a layer that sets `name_env`/`name_eval` displaces the other variants
  below it (`:51-61`). `which(*path)` (`:247-291`) returns the highest layer label holding that exact
  path (`'default'`, `'preload'`, a file label, `'env'`, `'_'` for in-place updates), and raises
  `KeyError` when asked for a displaced variant; so callers ask for the form actually held
  (`_given`, `core/config.py:388-400`). `blame` (`:332-342`) turns that into `from: <path>` or
  `from: HYPERSHELL_…` (via `ENV_VARIABLES`).
- **Resolution.** `_env`/`_eval` resolve only on attribute access, `NSCoreMixin.__getattr__`
  (`cmdkit/namespace.py:80-103`): `os.getenv` (unset gives `None`) or `subprocess.check_output`
  (always text). Item access (`table['port']`) does not resolve.
- **Reads happen at import.** `hypershell/__init__.py:26-33` imports every app, and the class
  bodies read `config.…` by attribute: `server.py:1836-1874`, `client.py:1661-1714`,
  `submit.py:1585-1628`, `cluster/__init__.py:157-279`, `relay/__init__.py:1153`, plus
  module constants (`core/config.py:667` `DEFAULT_RELAY_PORT`, `relay/__init__.py:115,121,1019`,
  `relay/upstream.py:87`). These all sit outside the guarded `try`. Probes:
  - `HYPERSHELL_SERVER_PORT_EVAL='…; echo 50001'` runs **4 times** per process, for `hs list` and
    `hs config get` alike.
  - `HYPERSHELL_SERVER_PORT_EVAL=false hs list` gives a raw `CalledProcessError` traceback, exit 1.
  - The values become argparse defaults (`server.py:1837` `default=bundlesize`). **argparse runs a
    string default through `type=`** (`_get_values`), so a file's text `bundlewait = "5s"` or
    `period = "1h"` is parsed by the *option's* type and is refused as an ArgumentError naming the
    option, exit 2. That is the mechanism behind `hs cluster`'s exit 2 for `autoscale.period`.
- **Knowing the subcommand at import.** `_relay_requested(argv)` (`:690-696`) sniffs `sys.argv`
  (`hs`/`hyper-shell`, `argv[1] == 'relay'`) and is the precedent. It is defined *after* the load,
  so a config sniff must move above `:642`. Limits: `hsx` is always `cluster`; there is no
  `__main__.py`; an in-process `main([...])` sees the process's own argv, as the relay sniff does.

## 2. `attempts = "3"` (issues/config-text-attempts-crashes.md)

Reproduced (`[server] attempts = "3"`, `hs list -l 1`). It exits **1** with a raw Python traceback:
`hypershell/__init__.py:27` → `server.py:1850` (class body `max_retries: int = configured_retries()`)
→ `server.py:119` `config.server.attempts - 1` → `TypeError: unsupported operand type(s) for -:
'str' and 'int'`. It is the same for `attempts = "abc"`, for `HYPERSHELL_SERVER_ATTEMPTS_EVAL="echo 3"`
(an `_eval` is always text), and for `hs config get`. So today a bad file value **also blocks
`hs config`**, which C2 says must still run.

## 3. `hs config set` (R7)

`ConfigSetApp.run` (`src/hypershell/config.py:305-336`): `read_value(match_path(varpath), value)`
(`config_schema.py:219-242`, `:310-329`) before `update()` (`core/config.py:750-773`); a
`ValueError` becomes `ArgumentError` → exit 2, nothing written. Gaps found by probing:
- Typed int keys convert with no range: `relay.zone.a.size 0` is written (exit 0).
- DURATION/SIZE keep any text (`read_number` or the text as given), unchecked.
- UNTYPED keys: `read_untyped` (`:208-216`) passes a TOML int of any width. `database.connect_args.x
  99999999999999999999` is written. Above 4,300 digits `tomllib` raises a plain `ValueError`
  (not `TOMLDecodeError`), which escapes `read_untyped` and reaches the `except ValueError` by luck:
  exit 2, but with Python's text as the "expected" form. The same text appears for a typed key
  (`server.evict <5000 digits>`) via `int()` in `read_number`/`read_value`.
- `given {self.value!r}` echoes the full value (5,000 characters).

**Fit.** Route the numeric kinds of `read_value` through the table's parser for `match_path`'s key
(zone keys included) and raise in the R4 form without `(from: …)`. For UNTYPED, after `read_untyped`,
refuse an `int` outside −2⁶³…2⁶³−1. A digit string too long for `int()` is out of range by length,
so check the length before calling `int()`. Keep writing the value **as given** (text durations stay
text, R2). The refusal stays before `update()`, so nothing is written. The message uses the short form
of `<V>` (§6).

## 4. Existing helpers under "one parser reads every key"

| Helper | Call sites | Becomes |
| --- | --- | --- |
| `config_seconds` (`core/config.py:345-375`) | `server.py:1994`, `cluster/__init__.py:391` (evict); `client.py:1790` (heartrate); `relay/__init__.py:1335` (`RELAY_DURATIONS`, `whole` for `relay.timeout`) | The DURATION case of one accessor `setting(*path)` (below); `whole` stays a caller option. Its form-finding, `_env`-unset and eval-failure code is the accessor's core. |
| `_server_value`/`client_host` (`:403-443`) | `client.py:1792`, `submit.py:1663`, `core/queue.py:1369` | Text keys, out of scope. `_server_value` should reuse the accessor's resolve step so there is one resolver. |
| `configured_retries` (`server.py:117-119`) | class bodies `server.py:1850`, `cluster/__init__.py:170`; API `server.py:1274`, `cluster/remote.py:998`, `cluster/topology.py:601` | `setting('server','attempts') - 1` (range 1…32,767). This is the R10 path for API defaults. |
| `configured_eager` (`server.py:125-…`) | `server.py:1995`, `cluster/__init__.py:392` | Boolean, a non-goal; unchanged. |
| `check_evict` (`server.py:826-834`) | start `server.py:1994`, `cluster/__init__.py:391`; API `HeartMonitor.__init__` `:889` | The start sites become the Evict entry (10…2³¹−1, ConfigurationError exit 3, C1). `:889` checks an explicit argument: C3 says "as today", but its 86.4e12 cap and `given {seconds}` (fails over 4,300 digits) need a plan ruling. |
| `configured_failed_launches` (`cluster/remote.py:372-378`) | `cluster/__init__.py:350` | The Count-or-none entry via `setting`. |
| `check_arguments` period/rate (`cluster/__init__.py:384-400`) | `ClusterApp.__enter__ :650` | Deleted. The class-body defaults (`:247`, `:263`) come from `setting()` already parsed, so argparse never re-parses a string default. The relay's `parse_period(config.autoscale.period)` (`relay/__init__.py:1324`) moves to `setting` too. `hs client`'s `ratelimit` (`client.py:1684`) gets the floor this way (N1). |

## 5. R8 backstop: where an escaped error goes today

`Application.main` (`cmdkit/app.py:160-194`) calls `from_cmdline` (argparse plus `__init__`) inside
`with`, then `run()`. `ArgumentError` → exit 2. Any other exception is matched in insertion order
against `cls.exceptions`; every app spreads `get_shared_exception_mapping`
(`core/exceptions.py:129-143`), whose last entry is `Exception → write_traceback`, exit 5, with a log
file. argparse turns a `ValueError` from a `type=` into an ArgumentError, but **not an
`OverflowError`** (`_get_value` catches only `ArgumentTypeError`, `TypeError`, `ValueError`). An
OverflowError from a type therefore takes the traceback path. At import, `core/config.py:652`
writes a traceback file, and a class-body error (§1) prints a raw traceback. Putting `ValueError`
into the mapping would also catch real work's errors, so it must not be done.

Start checks are not uniform. They live in `run()` for server (`:1921`), client (`:1743`), relay
(`:1191`) and submit (`:1673`), and in `__enter__` for cluster (`:650`). Work starts right after them:
`check_database`, `bind_database`, `initdb`.

**Design.** Add one context manager, `reading(stage)`, in `core/exceptions.py`. It re-raises
`ArgumentError` and `ConfigurationError` untouched. It converts an escaping `ValueError` or
`OverflowError` into `ArgumentError(f'{type}: {short(msg)}')` when the stage is `'argument'`, or
`ConfigurationError(…)` otherwise, `from None`. Apply it in four places:
1. Every app's `from_cmdline`, through a small mixin classmethod that wraps `super().from_cmdline`:
   stage `argument`, exit 2.
2. Each start-check call site, up to the first line of work (`resolve_auth`, `check_args`,
   `relay.check_args`/`durations`, `check_arguments`): stage `configuration`, exit 3.
3. Import: split `core/config.py:652` so that `ValueError`/`OverflowError` get `display_critical`
   only (no traceback file).
4. Import: guard `hypershell/__init__.py:26-33` around the app imports. On `ConfigurationError`,
   `ValueError` or `OverflowError`, call `display_critical` and exit 3. Importing any submodule runs
   the package `__init__` first, so this covers the API too.

Everything after the checks is outside the manager, so later `ValueError`s keep today's path.

## Recommended design (R3, R5, R6, C2)

1. **Load check, once, at import.** Inside the `try` at `core/config.py:642-648`, after
   `check_auth_forms`, call `check_settings(config, files=not CONFIG_COMMAND)`. It walks every numeric
   path in `KEYS` and every `relay.zone.<name>.<zone key>` held **literally** in the merged view,
   skips paths decided by `default`/`preload` (R1's test covers those), and parses each one by its
   table entry. A refusal raises `ConfigurationError` in the R4 form with
   ` (from: {blame(...)})`. The existing handler prints one line and exits 3, with no traceback, before
   `data/core.py` builds an engine. Run it before the `RELAY_PROCESS` swap (`:706`), so the whole
   merged configuration is checked (C2). `CONFIG_COMMAND` is an argv sniff like `_relay_requested`,
   moved above the `try`. Under `hs config`, only values decided by a file layer are skipped; the env
   layer is still refused (C2: "file values only").
2. **One accessor, `setting(*path, base=config)`.** It finds the form held (`name`, `name_env` or
   `name_eval`) and resolves it by attribute, so an unset `_env` or a failed `_eval` gives a
   ConfigurationError naming only the error's type, as `config_seconds` does today. It then parses by
   the kind with the range and blames the form. This is how `_env`/`_eval` keys are "checked when they
   resolve": every numeric read (class bodies, module constants, start checks, API defaults, relay and
   autoscaler reads) goes through it (R6). A class-body failure surfaces at import through guard 4.
   Under `CONFIG_COMMAND`, a refused value decided by a file falls back to the built-in default, so
   the other apps' class bodies still import and `hs config get/set/edit/which` run on the bad file.
   They read the raw layers, so `hs config get` still shows the bad value. Caching the resolved value
   per path would also cut the import-time `_eval` runs from 4 to 1. That would be a side benefit,
   and the plan should rule on it.
3. **Env layer.** `read_environment` keeps typing values, but through the same parsers, so its
   refusal has the R4 form (it already appends `(from: HYPERSHELL_…)`).
4. **Message prefix.** `display_critical` and `handle_exception` prefix `ConfigurationError: `,
   while an ArgumentError prints bare. The plan should decide whether "one message form" drops that
   prefix.

A lazy-only design (a check at each command's start, with no import check) does not work while class
bodies read configuration at import: `attempts = "3"` dies before any command starts.

## 6. Large integers and the short form of `<V>`

Python 3.14 refuses to convert between `int` and decimal text past 4,300 digits
(`sys.get_int_max_str_digits()`). Probes:
- `tomllib` reads 4,300 digits; `repr`/f-string print them. At 4,301 or more, `tomllib` raises
  `ValueError`, so a file with such an int is refused whole at load: `(from file: …) ValueError:
  Exceeds the limit…`, exit 3, every command including `hs config`. `hs config edit` cannot run
  either; this is the same as any TOML syntax error. `tomlkit` reads a 4,301-digit int as a *Float*.
- `INT_FORM` matches 5,000 digits, and `int(text)` then raises `ValueError`. The env layer prints
  `server.evict: expected Exceeds the limit (4300 digits)…, given '<all 5000 digits>'`, exit 3.
- A product can pass the limit even though its text did not (#117 N2: `1` followed by 4,298 zeros
  and `d`). Today a 4,300-digit `server.evict` exits 5 with a 4,300-digit message (`check_evict`).
- `hex()` and `decimal.Decimal(v)` have no limit. `format(Decimal(10**6000), '.4e')` takes 1 ms
  (0.2 s at 10⁵ digits); `float(v)` overflows.

**Cutter.** `<V>` is always the value **as given** (option text, variable text, `_eval` output, or
the TOML scalar), never a computed number such as seconds from days. That leaves only TOML ints
(at most 4,300 digits) to turn into text. `short(v, width=32)`: text → `repr`; bool, float → `repr`;
int → `repr` inside `try`, falling back to `format(Decimal(v), '.4e')` on `ValueError`. Then, past
`width`, keep the head and tail around an ellipsis and add the length, e.g.
`'99999999999…99999' (5000 characters)`. In the parser, refuse a whole-number text by its digit
count (leading zeros and `_` stripped) before calling `int()`, so the limit never fires.
