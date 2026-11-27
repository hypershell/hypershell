# Research 01 — detecting "given", and where the client-host rule lives

Probes: `/tmp/claude-501/venv314-host/bin/python` (editable install of this worktree), run under
`.agents/factory/bin/temp_site.sh`, with `Configuration` objects built directly from `default`, a
`preload` layer, TOML text per file layer, and `read_environment(...)` for the env layer. cmdkit
source: `cmdkit/config.py` in that venv.

## Findings

1. **`which()` names the deciding layer for a plain key, even when the value equals the default.**
   With nothing given, `which('server','host'|'advertise'|'bind')` is `default`. With
   `HYPERSHELL_SERVER_HOST=localhost` it is `env` and the value is still `localhost`. A preload
   layer (`build_preloads`, `core/config.py:538`) carries only `logging` and `database`, so it never
   decides a `[server]` key. This is the same test as `core/logging.py:1017`.

2. **A variant form hides the plain key from `which()`.** A layer that gives `host_env` or `host_eval`
   (file, or `HYPERSHELL_SERVER_HOST_ENV`, which `match_name` maps to `('server','host_env')`)
   displaces `host` in the merged view (`_drop_displaced_variants`, cmdkit `config.py:51`). Then
   `which('server','host')` **raises `KeyError`** (`_displaced`, cmdkit `config.py:99`, raised at
   `:279`), and `config.server.get('host')` returns nothing. Attribute access still resolves it
   (`config.server.host` gives the variable's value or the command's output; cmdkit
   `namespace.py:80-103`). `which('server','host_env')` names the layer. The same holds for
   `advertise_*` and `bind_*`. Across layers the highest form wins: user `host_env` and local `host`
   leaves only `host` (local); user `host` and local `host_env` leaves only `host_env`.
   `config_seconds` (`core/config.py:357`) already finds the form with
   `next(f for f in (name, name+'_env', name+'_eval') if f in table)` and blames that form.

3. **`host_env` naming an unset variable** resolves to `None` by attribute, not an error. Today
   `hs submit --queue`'s default (`submit.py:1614`) and `QueueConfig.load()` (`core/queue.py:1107`)
   would pass `None` through. `config_seconds` and `resolve_auth` raise `ConfigurationError` for
   this case (`core/config.py:364`, `:456`).

4. **Empty values.** `server.bind` and `server.advertise` are in `EMPTY_UNSET`
   (`core/config_schema.py:149-154`). An empty `HYPERSHELL_SERVER_ADVERTISE` or `HYPERSHELL_SERVER_BIND`
   sets nothing: `which` stays `default`. A file's `advertise = ""` or `advertise = "<none>"` *is*
   given (`which` = `user`), with the value `''` or `'<none>'`. So a layer test alone would dial
   `''` or `'<none>'`; the value must also be checked. An empty `HYPERSHELL_SERVER_HOST` is held:
   `which` = `env`, value `''`. Under R3 the rule then returns `''`, which the socket layer treats
   as the local host. That is the GOAL's non-goal, recorded here, not handled.

5. **`ClusterApp.advertise_host`** (`cluster/__init__.py:353-360`) reads
   `config.server.get('advertise', None)` and treats `None`, `''` and `PARAM_UNSET` as unset.
   `.get` is `dict.get`. It does **not** resolve variants: a file `advertise_env = "X"` displaces
   `advertise`, so `.get` returns `None` and the cluster falls back to the bind rule. This is a
   latent cluster bug, outside this fix (R6 leaves the cluster unchanged). The new helper should
   read by attribute so the client honours `advertise_env` and `advertise_eval`. Then the client and
   `hs cluster` would differ only in this case, which nobody uses today.

6. **The `_` layer and preloads.** `Configuration.update()` (an in-place change) writes to `local`,
   and `which` then returns `'_'`, which is not `default`, so it counts as given. Correct.
   `config['database'] = relay_database_section(config)` (relay process, `core/config.py:639`) is
   plain `__setitem__`. It touches only `[database]`, and `which` for `[server]` keys is unchanged
   (probed). Nothing in `[server]` is affected. **One trap:** nested item assignment
   (`config['server']['host'] = ...`) changes the merged value, but `which` still says `default`, so
   the helper ignores it. Tests must set keys through layers (environment, files, or a constructed
   `Configuration`), not by mutating `config`. Existing tests also swap in a plain `Namespace` for
   the config (`tests/test_cluster_advertise.py:128-133`), and a `Namespace` has no `.which`, so the
   helper must take a real `Configuration`.

7. **Import graph.** `core/queue.py:40` imports `default, config as _config` from
   `hypershell.core.config`. `core/config.py` imports only `core.platform`, `core.exceptions`,
   `core.sys`, `core.types` and `core.config_schema`. `cluster/launch.py:37-41` imports `core.config`,
   `core.logging` and `client`, so `core/queue.py` cannot import from `cluster/` without a cycle
   (`cluster/launch.py` → `client` → `core/queue`). `WILDCARD_BINDS` (`cluster/launch.py:85`) is
   listed in `cluster/launch.py`'s `__all__` (`:45`) and imported by `tests/test_cluster_advertise.py:32`,
   which asserts its value (`:75`). Nothing else uses it.

8. **Import-time evaluation.** `ClientApp.host` (`client.py:1657`) and `SubmitApp.queue_server`
   (`submit.py:1614`) are class attributes, evaluated when the module is imported. `hypershell/__init__.py:26-28`
   imports both for every `hs` command. Today `submit.py:1614` already runs a `host_eval` command at
   every `hs` start. If the helper is called there, `client.py` will too, and a helper that raises
   would break every `hs` command (even `hs list`) through an import-time `ConfigurationError`. The
   `--help` text shows `{DEFAULT_HOST}` (`client.py:1625`, `submit.py:1543`), not the live default,
   so R8 has to change the wording either way.

## Recommendation

**Move `WILDCARD_BINDS` to `core/config.py`** (add it to `__all__`). In `cluster/launch.py`, import
it from there and keep the name in its `__all__`, so `from hypershell.cluster.launch import WILDCARD_BINDS`
still works and `advertised_host` is unchanged.

**Helper in `core/config.py`:**

```python
def client_host(base: Configuration = None) -> str:
    """The address a client dials with no -H: server.host, else server.advertise,
    else server.bind when it is one address, else 'localhost' (R3)."""
```

Default `base=None` → module `config`, as `config_seconds` and `resolve_auth` do. Tests pass a
constructed `Configuration` and never reload the singleton (§10). Detection, in pseudocode:

```
FORMS(name) = (name, name+'_env', name+'_eval')

def given(base, name):                       # -> the deciding form, or None
    table = base['server']
    form  = first f in FORMS(name) with f in table, else None   # merged view holds one form (F2)
    if form is None or base.which('server', form) == 'default':
        return None
    return form

def value(base, name, form):
    v = getattr(base.server, name)           # attribute: resolves _env/_eval
    if v is None and form endswith '_env':
        raise ConfigurationError(f'server.{form} names {table[form]}, which is not set ({blame(base,"server",form)})')
    return v

client_host(base):
    if form := given(base, 'host'):      return str(value(base, 'host', form))       # even 'localhost', even ''
    if form := given(base, 'advertise'):
        v = value(base, 'advertise', form)
        if v not in (None, '', PARAM_UNSET): return str(v)                           # F4: file "" / "<none>"
    b = base.server.bind                     # attribute; bind_env/bind_eval resolve
    if b is not None and str(b) not in WILDCARD_BINDS: return str(b)
    return 'localhost'
```

**PARAM_UNSET guarantee.** `'<none>'` can come only from `advertise` (the default, or a file that
writes it), and that branch returns only a value outside `(None, '', PARAM_UNSET)`. `host` and
`bind` have no `'<none>'` default. A file could write `host = "<none>"` literally. Then R3's
"given by its layer" would dial it. To keep R3's "never dialled" airtight, also skip `PARAM_UNSET`
for `host` and `bind`. This is a one-line guard, and the plan should pick it. The final `'localhost'`
is a literal.

**Call sites.** `QueueConfig.load()` uses `'host': client_host(_config)`. For `ClientApp.host` and
`SubmitApp.queue_server`, the choice is between (a) calling `client_host()` in the class body, as
today, which keeps "option default" tests straightforward but raises at import for an unset
`host_env`, and (b) an argparse default of `None` resolved by `client_host()` when the app starts.
Recommend **(b)**, or (a) with the `_env`-unset case returning `'localhost'` and not raising. The
plan must choose. Either way, R7's tests can call `client_host(Configuration(...))` directly, plus
one end-to-end case per command through environment variables in `temp_site`.

## Edge cases (config given → address dialled, as prototyped)

| Given | Dialled |
|---|---|
| nothing | `localhost` |
| file `bind="0.0.0.0"`, `host="login00.example.edu"` (seed) | `login00.example.edu` |
| env `HYPERSHELL_SERVER_BIND=10.0.0.5` | `10.0.0.5` |
| env `HYPERSHELL_SERVER_HOST=localhost`, file `bind="10.0.0.5"`, `advertise="a.example"` | `localhost` |
| file `host="h.example"`, `advertise="a.example"` | `h.example` |
| file `advertise="a.example"`, `bind="10.0.0.5"` | `a.example` |
| file `bind="0.0.0.0"` / `bind=""` / env BIND empty | `localhost` |
| file `advertise=""`, `bind="10.0.0.5"` | `10.0.0.5` |
| file `advertise="<none>"`, `bind="0.0.0.0"` | `localhost` |
| env ADVERTISE empty, file `bind="0.0.0.0"` | `localhost` |
| env `HYPERSHELL_SERVER_HOST=""` | `''` (non-goal; held as given) |
| file `host_env="VAR"` (set), `bind="0.0.0.0"` / env `HYPERSHELL_SERVER_HOST_ENV` | value of `VAR` |
| file `host_eval="echo evalhost"` | `evalhost` |
| file `advertise_env="VAR"`, `bind="0.0.0.0"` | value of `VAR` (cluster ignores this form, F5) |
| file `bind_env="VAR"` | value of `VAR` (unless wildcard) |
| file `host_env="UNSET"` | `ConfigurationError` naming `server.host_env` and its file |
| in-place `config.update({'server':{'host':...}})` (`_` layer) | that host |

## Open risks

- **Unset `host_env` at import (F8).** If the helper raises inside a class body, every `hs` command
  fails. Choose call-site option (b), or make the import-time path not raise.
- **`host_eval` runs a command at import.** This already happens through `submit.py`. Calling the
  helper in `client.py`'s class body adds a second run, and a third in `QueueConfig.load()`.
  Option (b) removes the import-time runs.
- **The cluster's `advertise_host` ignores `advertise_env` and `advertise_eval`** (F5). This is a
  separate seed, not part of this fix.
- **`hs config get server.host` does not print the effective address.** R8's docs carry the rule.
- **Tests must use layers** (F6). Item assignment or a `Namespace` stand-in bypasses `which`.
