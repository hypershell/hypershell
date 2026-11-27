# 01 — Where the authkey is read, and what cmdkit 2.8.0 hands back

Scope: the GOAL leaves the HyperShell-side design to `/hs-plan`, and the fix touches `server.py` and
`client.py` (high blast radius). This brief pins every read of the two keys and the exact cmdkit
2.8.0 behavior the guard sits on. Codebase reads at `15587cf`; cmdkit at `a3cb454` (2.8.0).

## Read sites of `server.auth`

| Site | When it runs | Note |
|------|--------------|------|
| `server.py:1622` `ServerApp.auth: str = config.server.auth` | **import** (class body) | default of `-k`; `check_args` refuses `DEFAULT_AUTH`, short keys, bad chars |
| `submit.py:1304` `SubmitApp.queue_auth: str = config.server.auth` | **import** (class body) | default of `-k`; used only with `-q` |
| `client.py:1666` `self.auth = config.server.auth` | run, only without `-k` | already the lazy pattern (`auth: Optional[str] = None`) |
| `core/queue.py:923` `QueueConfig.load` | run | only caller is `QueueInterface.new()`, which no command calls |
| `core/queue.py:905`, `server.py:931`, `client.py:1140`, `submit.py:1199` | import | read `default.server.auth` (the built-in layer), never the merged view: safe |

`hs` imports every app module at start, so the two class-body reads are the "first read at import":
an `AttributeError` or `CalledProcessError` there escapes before any `Application.exceptions`
mapping exists, hence the raw traceback and exit 1 for every command. `cluster/*` mints its own key
(`secrets.token_hex(64)`) and never reads `server.auth`.

## Read sites of `relay.upstream.auth` (2.9 only)

`relay/upstream.py:151` `upstream_key(base=None)` already walks `base.namespaces` from the top,
refuses two forms in the deciding layer, wraps any resolution error and a `None` in
`ConfigurationError`, and falls back to `DEFAULT_AUTH`. Callers: `upstream_config` (run),
`RelayApp.check_args` (run), `relay/site.py` `fence_site`. `RelayApp.exceptions` includes the shared
mapping, so a `ConfigurationError` exits `bad_config` with one line. Tests:
`tests/test_relay_launch.py:201` and `:215` (`layered()` helper). What is missing for R4 is
`hs config get relay.upstream.auth` (R2) and one shared message format.

## `hs config get` (`src/hypershell/config.py`, identical on `2.8.6`)

Merged view, no `-x`: `variable in config_section` → print, else `"server.auth" not found` (exit 3).
Under cmdkit 2.8.0 the merged `server` section holds only `auth_env`, so `auth` is "not found".
With `-x` it calls `getattr` and maps `AttributeError` to an empty `ConfigurationError('')`; a
`CalledProcessError` would fall to the generic `Exception` handler (traceback file).

## cmdkit 2.8.0, probed (`uv run --no-project --with 'cmdkit[toml]==2.8.0'`, default layer `auth`)

| User/local layers | merged `server` | `getattr(server, 'auth')` | `which('server','auth_env')` |
|---|---|---|---|
| user `auth_env=PK` (set) | `{auth_env}` | `'from-env'` | `user` |
| user `auth_env=NOPE` (unset) | `{auth_env}` | `None` | `user` |
| user `auth` + `auth_env` | `{auth, auth_env}` | `AttributeError … more than one variant` | `user` |
| user `auth` + `auth_env`, local `auth=z` | `{auth}` | `'z'` (the bad layer is hidden) | `KeyError` |
| user `auth_eval='exit 4'` | `{auth_eval}` | `CalledProcessError … status 4` | `KeyError` |
| user `auth_env`, local `auth=lit` | `{auth}` | `'lit'` | `KeyError` |

So precedence (R3) is cmdkit's; the guard only has to turn `None`, `CalledProcessError` and the
two-forms case into `ConfigurationError`. A lower layer with two forms is invisible in the merged
view, so R6 needs a per-layer scan of `base.namespaces`.

## Recommendation

One resolver in `core/config.py` used by every command and by `hs config get`; class-body defaults
become `None` and resolve at run (the client's existing pattern); a structural two-forms scan at
import (cheap, no side effects) so R6 holds for every command. Leave `QueueConfig.load` alone (no
command uses it, and its import line is pick-hostile; see 02).
