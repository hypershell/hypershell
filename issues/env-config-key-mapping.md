---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Environment variables cannot reach every configuration key, or set it to a literal string

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The configuration guide says that "every option can also be set by an environment variable, where
the name aligns to the path to that option, delimited by underscores"
(`docs/_include/config_intro.rst:25-27`), and the parameter reference says every `[server.tls]` key
"is also exposed as an environment variable" (`docs/_include/config_param_ref.rst:257-259`). Two
properties of the mapping make both claims untrue.

Every `HYPERSHELL_*` variable goes through cmdkit's `Environ(prefix='HYPERSHELL').expand()`
(`src/hypershell/core/config.py:209-211`), on `origin/develop` (`2d0d74e`, 2.9.0a5) with cmdkit
2.7.7, the floor `pyproject.toml:31` pins.

- **Every underscore is a level of nesting.** `expand` splits the name after the prefix on `_`
  and lower-cases each part (`cmdkit/namespace.py:445-448`). A key whose own name has an underscore
  cannot be named: `HYPERSHELL_SERVER_TLS_MIN_VERSION` sets `server.tls.min.version`, and
  `config.server.tls.min_version` keeps its default. The `_env` / `_eval` suffixes nest the same
  way, so `HYPERSHELL_SERVER_AUTH_ENV` sets `server.auth` to the table `{env = ...}`.
- **Every value is coerced.** `expand` passes each value through `_coerced`
  (`cmdkit/namespace.py:316-331`): `''` and `null` become `None`, `true` and `false` become
  booleans, and anything `int()` or `float()` accepts becomes a number. A string-valued key cannot
  be given any of those strings. `HYPERSHELL_AUTOSCALE_LAUNCHER=false` gives `False`, not the
  command `false`, and an all-digit `HYPERSHELL_SERVER_AUTH` becomes an `int`.

Documented keys that no environment variable can reach today:

- `server.tls.min_version` (read at `src/hypershell/core/tls.py:465`).
- `database.timescale.chunk_interval` and `database.timescale.compress_after`. The variable makes
  a `chunk` or `compress` key instead, which `src/hypershell/data/timescale.py:110-112` refuses as
  an unknown setting.
- Anything under `database.connect_args`, and any `database.pragmas` name with an underscore, such
  as the reference's own `journal_mode` (`docs/_include/config_param_ref.rst:136-142`).
- Every `_env` / `_eval` variant: `server.auth_env`, `server.auth_eval`, `database.password_env`,
  `database.password_eval`.
- An `[ssh.nodelist]` group whose name has an underscore.

Relay (#82, 2.9.0a6) adds more: `relay.launcher_args`, `relay.num_threads`,
`relay.upstream.evict_hint`, `relay.upstream.auth_env`, a zone's `launcher_args`, and any zone whose
name has an underscore (`src/hypershell/core/config.py:192-224` on `origin/develop`, `b4b37cd`). The
coercion was first noticed there: `HYPERSHELL_RELAY_ZONE_GPU_LAUNCHER=false` reached the relay as the
boolean `False`, and the relay refused it at start.

`[export]` names survive, because `load_task_env` joins them back with `_`
(`src/hypershell/core/config.py:415-418`), but they are upper-cased and their values are coerced and
re-stringified: `HYPERSHELL_EXPORT_RUN_ID=007` gives a task `RUN_ID=7`, `1e3` gives `1000.0`, `True`
gives `true`, an empty value gives `null`, and `HYPERSHELL_EXPORT_MyVar` gives `MYVAR`. The guide
says the variable is injected "sans prefix" (`docs/_include/config_task_env.rst:76-78`).

Two helpers repeat the same mapping. `blame` names `HYPERSHELL_` plus the path joined by `_` as the
source of a value (`src/hypershell/core/config.py:248-257`), which for `min_version` is a variable
that could not have set it. `ACTIVE_CONFIG_VARS`, the list behind `hs config` completion, turns every
underscore not followed by `env` or `eval` into a dot (`src/hypershell/core/config.py:328-331`), so it
offers `server.tls.min.version`.

**Reproduced** on `origin/develop` with a pure-Python probe that starts no server (a child
interpreter per case, an empty `HYPERSHELL_CONFIG_FILE`, a throwaway site):

```
HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3
  config.server.tls.min_version -> 'TLSv1.2'; env layer -> {'tls': {'min': {'version': 'TLSv1.3'}}}
HYPERSHELL_DATABASE_TIMESCALE_CHUNK_INTERVAL='1 day'
  Settings.from_config(...) -> ConfigurationError: Unknown setting database.timescale.chunk
HYPERSHELL_SERVER_AUTH_ENV=MYKEY
  env layer -> {'auth': {'env': 'MYKEY'}}; ServerApp.check_args() ->
  ArgumentError: Authentication key must be at least 16 characters
HYPERSHELL_SERVER_AUTH=12345678901234567890
  config.server.auth -> int; ServerApp.check_args() -> TypeError: object of type 'int' has no len()
HYPERSHELL_AUTOSCALE_LAUNCHER=false     -> config.autoscale.launcher is False
HYPERSHELL_LOGGING_DATEFMT=null         -> config.logging.datefmt is None
HYPERSHELL_EXPORT_RUN_ID=007 / _FLAG=True / _EMPTY= / _SCALE=1e3
  load_task_env() -> {'RUN_ID': '7', 'FLAG': 'true', 'EMPTY': 'null', 'SCALE': '1000.0'}
```

The all-digit key is the worst of these: a key that passes the documented character check
(`src/hypershell/core/config.py:83`) crashes `hs server` with a raw traceback.

## Why it was deferred

Found out of cycle (2026-11-06), while setting relay zone options from the environment. No pass had
the configuration loader in scope. **Pre-existing:** `reload_env` is the same on `origin/master`
(2.8.5) and in 2.7.0 and 2.8.0, and the behaviour is cmdkit's.

## Outcome / vision

Every documented key can be set from the environment, and a string-valued key takes the string it
is given. The rule for spelling a key with an underscore, and for which values are coerced, is
written once in the configuration guide, and `blame` and completion use the same rule as the loader.
An `[export]` variable reaches a task with the name and value the user wrote.

## Sketch of the acceptance criteria

- **R1** — WHEN a `HYPERSHELL_*` variable names a documented key whose name has an underscore
  (e.g. `server.tls.min_version`), the configuration SHALL hold the value at that key.
- **R2** — WHEN a `HYPERSHELL_*` variable sets a key whose default is a string, the configuration
  SHALL hold the variable's text unchanged, including `false`, `true`, `null`, an empty value and
  digits.
- **R3** — WHEN a `HYPERSHELL_*` variable sets a key whose default is a number or boolean, the
  configuration SHALL hold the converted value, and IF the text does not convert THEN the command
  SHALL exit with a configuration error naming the variable.
- **R4** — WHEN `HYPERSHELL_<PATH>_ENV` or `HYPERSHELL_<PATH>_EVAL` is set for a key, the key SHALL
  resolve as its `_env` / `_eval` variant does in a file.
- **R5** — WHEN `HYPERSHELL_EXPORT_<NAME>` is set, a task's environment SHALL hold `<NAME>` with
  the variable's value, both unchanged.
- **R6** — `blame` and `hs config` completion SHALL name, for every key, the variable that sets it.
- **R7** — The configuration guide SHALL state the mapping rule, and SHALL NOT claim a key is
  reachable from the environment when it is not.

## Open questions for shaping

- **How to spell a nested underscore.** A double underscore as the level separator
  (`HYPERSHELL_SERVER__TLS__MIN_VERSION`) is unambiguous but breaks every existing variable. Matching
  names against the known schema (defaults plus the documented open tables) keeps today's names and
  leaves only open tables — `connect_args`, `pragmas`, `export`, `[ssh.nodelist]`, relay zones —
  ambiguous. Shaping picks one, and whether the old form stays as a fallback.
- **Typing without a schema.** Keys with no default (`database.password`, `connect_args` entries)
  have no type to convert to. Leave them strings, or keep coercion only there?
- **Fix in cmdkit or here.** `expand` takes a `converter`, which covers R2-R3 but not R1. A
  HyperShell-side loader avoids a cmdkit release; changing cmdkit fixes its other users too.
- **Is the all-digit key a separate small fix?** Coercing `server.auth` back to `str` at the three
  reads (`src/hypershell/server.py:1549`, `src/hypershell/client.py:1666`,
  `src/hypershell/submit.py:1304`) would ship ahead of the rest.

## Notes

- Related: [`server-auth-variant-collision.md`](server-auth-variant-collision.md) — the file form of
  `auth_env` / `auth_eval` breaks every command. That seed lists the environment form as untested;
  it does not collide, it nests (above), so R4 here and R1 there should be shaped together.
  [`templating-and-remote-env-contract.md`](templating-and-remote-env-contract.md) R3 quotes the
  `HYPERSHELL_*` values that `--ssh --env` forwards; on the remote host they go through this same
  mapping. Relay's `[relay.zone.*]` tables (#82) add the per-zone tables
  named above; zone-aware autoscaling (`spec/zone-autoscale/`, #99) kept the sizing knobs uniform
  and added none.
- Found by: out-of-cycle review, 2026-11-06; reproduced on 2.9.0a5.
