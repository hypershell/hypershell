# 05 — Auth, `_ENV`/`_EVAL`, `[export]`, blame and completion

Read-only brief for `env-config-keys` (R3, R4 key part, R7, R8). Source as of `develop` `90ab2fc2`.
Probes: child interpreter per case, `env -i`, `HYPERSHELL_CONFIG_FILE=` (empty), throwaway
`HYPERSHELL_SITE`. "Fake env layer" = `cmdkit.namespace.Environ.expand` patched before import to
return a given Namespace, so the env layer looks exactly like a file layer.

## 1. R3 — an env layer shaped like a file layer already works downstream

Fake env layer `{'server': {'auth_env': 'MYKEY'}}`:

| Case | Result |
|---|---|
| `MYKEY=0012345678901234`, `hs config get server.auth -r` | `0012345678901234`, rc 0 |
| `MYKEY` unset | rc 3, `server.auth_env names MYKEY, which is not set (from: HYPERSHELL_SERVER_AUTH_ENV)` |
| layer `{'auth_env':'MYKEY','auth':'abc'}` | rc 3 at import, `More than one form of server.auth (auth, auth_env) in one configuration layer (env)` (`check_auth_forms`, core/config.py:346-359, :491) |
| `resolve_auth('relay','upstream')` with `{'relay':{'upstream':{'auth_eval':'echo …'}}}` | the command's output |
| `blame(c,'server','auth_env')` / `…'relay','upstream','auth_eval'` | `from: HYPERSHELL_SERVER_AUTH_ENV` / `from: HYPERSHELL_RELAY_UPSTREAM_AUTH_EVAL` |
| user file `auth = …` under env `auth_env` (hand-built `Configuration`) | merged `server` holds `auth_env` only; `resolve_auth` gives the env key (displacement, cmdkit config.py:51-67) |
| `{'database':{'provider':'postgres',…,'password_env':'PGPW'}}` | `DatabaseURL.password == 's3cret'` |
| `hs config which server.auth_env` | `MYKEY (env: HYPERSHELL_SERVER_AUTH_ENV \| default: null)` |
| `hs config which server.auth` | `"server.auth" not found`, rc 0 — same for the file form (pre-existing, server-auth-variant-collision REVIEW.md:115-117) |

Today, real env: `HYPERSHELL_SERVER_AUTH_ENV=MYKEY` → `hs config get server.auth` prints
`Namespace({'env': 'MYKEY'})`; `HYPERSHELL_DATABASE_PASSWORD_ENV=PGPW` → `password` is
`Namespace({'env': 'PGPW'})`.

**So R3 needs only the loader to emit `<key>_env` / `<key>_eval` at the key's table.** Two conditions:

- **Suffix must be lower case.** cmdkit matches variants by `key.endswith('_env')` (config.py:44-47)
  and `NSCoreMixin.__getattr__` builds `f'{item}_env'` (namespace.py:85-87). Probe: env layer
  `{'server':{'AUTH_ENV':'K'}}` merges beside `auth` with no displacement. The matcher must write the
  schema's own (lower-case) name plus `_env`/`_eval`, whatever case the variable used.
- **A variant always yields text** (`os.getenv`, `check_output(...).decode().strip()`,
  namespace.py:97-103). Probe: `server.evict_env=EV`, `EV=30` → `config.server.evict == '30'`. For R5/R6
  typed keys the typed read must happen after variant expansion (at the reader, or in a resolver
  like `resolve_auth`), or R3 must be limited to text keys. Same gap exists for a file today.

**Variant-aware readers (attribute access → `__getattr__`)**: `resolve_auth` `getattr(table,'auth')`
(core/config.py:384); `DatabaseURL.from_namespace` strips `_env`/`_eval` and `getattr`s every
`[database]` URL field (data/core.py:165-170: `password`, `user`, `host`, `port`, `database`, `file`…);
plain chains like `config.server.evict`, `config.client.heartrate`, `config.relay.launcher`,
`config.relay.upstream.deadline`; `QueueConfig.load` `_config.server.auth` (core/queue.py:1109; only via
`QueueInterface.new()`, no caller); `hs config get -x` (config.py:210-217).

**Not variant-aware (item / `.get`)**: TLS `from_namespace` `ns.get('min_version'|'ciphers'|
'fingerprint'|'insecure'|'servername'|'handshake')` (core/tls.py:505-510); `server.tls.get('enabled'|
'handshake')` (server.py:1940-1942, cluster/__init__.py:461-463, relay/__init__.py:1201-1203);
`config.relay.get(key)` for `min`/`max`/`init` (relay/__init__.py:1226), `'zone'` (:1254), `'site'`
(:1150); `config.server.get('advertise')` (cluster/__init__.py:347); `config.task.get('ratelimit')`
(cluster/__init__.py:240); `config.database.get('file'|'host')` (data/__init__.py:69-72,126,
submit.py:1480, task.py:1096); `[database]` `pop('schema'|'echo'|'connect_args'|'pragmas'|'timescale')`
(data/core.py:201-205); `hs config get` without `-x` (config.py:219). Probe: `tls.min_version_env=MV`
→ `.get('min_version')` is `None`, TLS config gets `TLSv1.2`; `relay.launcher_eval` → `.get` `None`,
attribute `'srun'`; `hs config get server.evict` → rc 3 "not found", `-x` → `30`.

**Keys whose own name ends in `_env`/`_eval`**: none among the defaults (core/config.py:96-241) or the
reference (`grep _env docs/_include/config_param_ref.rst` hits only notes, :137, :855). Open-table
entries can (`[export] CONDA_ENV`, a nodelist group `gpu_env`). Probe (plain cmdkit): file
`[export] conda` + env `[export] conda_env` → merged `export` = `{'conda_env': …}`, file entry
dropped; upper-case `CONDA_ENV` beside `CONDA` → both kept. Recommend: `_ENV`/`_EVAL` splits only
after a documented key's path, never inside an open table (R2 keeps the entry name whole).

**Should `_ENV`/`_EVAL` cover every documented key?** The guide says "any option … that ends with
`_env` or `_eval`" (docs/_include/config_intro.rst:61-64), so the file form claims it for all keys,
but only attribute readers honour it. Giving the env form to every documented key matches R3's text
("for a documented key"); whether each reader honours it is the file form's existing gap, not this
cycle's, except where the plan adds typed readers (R5/R6), which should read via attribute.

## 2. R4 — every key path goes through `resolve_auth`

- `hs server`: `self.auth = resolve_auth('server')` in `run` (server.py:1834); checks length/charset
  after (:1896-1900).
- `hs client`: `resolve_auth('server')` (client.py:1665). `hs submit --queue`: (submit.py:1437).
- `hs relay` upstream: `upstream_key` → `resolve_auth('relay','upstream')` (relay/upstream.py:241).
- `hs config get server.auth`: `resolve_auth` (config.py:189-191).
- `hs cluster` does not read the key: it mints `secrets.token_hex(64)` (cluster/remote.py:273, :969),
  hands it to its server directly (:287, :974) and to each client in `HYPERSHELL_SERVER_AUTH`
  (cluster/launch.py:243); the launched client reads it back through `resolve_auth`. A hex token is
  coerced only if all digits or digits plus one `e` (vanishingly rare), fixed by R4 anyway. The relay's
  site key is likewise minted, read from `self.server.queue.config.auth` (relay/__init__.py:568).
- Only non-`resolve_auth` read: `QueueConfig.load` (core/queue.py:1109), dead path.

Probe today: `HYPERSHELL_SERVER_AUTH=0012345678901234` → `config.server.auth == 12345678901234`
(int), `resolve_auth` `'12345678901234'`. With text kept, `str(value)` (core/config.py:394) is a
no-op; nothing else needs changing for R4.

**The key variable's spellings.** Once R1 documents that a key's variable may be written in any
letter case, every place that handles the key variable by name (`inherited_task_env`, client.py;
`ssh_env_exports` and `assigns_key`, cluster/launch.py; the client's no-key hint) should recognise
the same set of names the matcher maps to `server.auth`, through one predicate from the matcher. The
relay's site fence matches the key by value and needs no change (relay/site.py).

## 3. R7 — `[export]`

`load_task_env` (core/config.py:639-642): `__collapse_lists(config.export)` joins lists with
`PATH_SEP` (:623-636), then `Namespace.to_env().flatten()`: cmdkit `_flatten` upper-cases every key and
joins nested tables with `_`, values via `_de_coerced` (`None`→`null`, `True`→`true`, else `str`)
(namespace.py:334-358). Consumers: `task_env` (client.py:507), launch env (cluster/launch.py:134,
:531), relay fence `check_exports` (relay/site.py:530). Only `test_cluster_launch.py:1167`
monkeypatches it.

Today "env overrides file" is an accident of key collision after upper-casing, not a merge. Probe
(file `[export] foo=1, run_id="file", lst=["a","b"], Mixed="m", [export.tbl] x=1`):

| Env | Merged `config.export` | Task env |
|---|---|---|
| `_EXPORT_FOO=2` | `foo: 2` | `FOO=2` |
| `_EXPORT_foo=2` | `foo: 2` | `FOO=2` |
| `_EXPORT_RUN_ID=env` | `run_id: 'file'`, `run: {id: 'env'}` | `RUN_ID=env` (later key wins in `_flatten`) |
| `_EXPORT_MIXED=e` | `Mixed: 'm'`, `mixed: 'e'` | `MIXED=e` |
| `_EXPORT_MyVar=x _E= _N=007` | `myvar`, `e: None`, `n: 7` | `MYVAR=x`, `E=null`, `N=7` |

File-only results: `LST=a:b`, `TBL_X=1` (behaviour to keep).

**Design.** Keep the env entries apart from the file ones; don't rely on the merged table:

```python
file_export = Namespace()
for name, layer in config.namespaces.items():
    if name != 'env':
        file_export.update(layer.get('export', {}))   # depth-first, as the merge does
task_env = {**__collapse_lists(file_export).to_env().flatten(),       # upper-cased, as today
            **dict(config.namespaces.get('env', {}).get('export', {}))}  # names/values as written
```

`config.namespaces.env` is a plain `Namespace` copy of the layer (cmdkit config.py:188), so reading it
is safe. The merged `config.export` still shows both for `hs config get export`. Alternative: the
loader returns env exports in a side table; more plumbing, same result.

**"Same task variable"** should mean the same final name, case-sensitive (POSIX environments are).
Then env `HYPERSHELL_EXPORT_FOO` beats file `foo` (both `FOO`, as today), but env
`HYPERSHELL_EXPORT_foo=2` with file `foo=1` gives **two** variables, `foo=2` and `FOO=1`; today it gives
`FOO=2`. That follows R7's letter (name as written; file "as today") but is a user-visible change the
release note should name. The other reading (case-insensitive: env `foo` suppresses file `FOO`) would
need a rule for which spelling survives; not recommended. On Windows `os.environ` upper-cases names,
so the env form arrives upper-case there anyway.

## 4. R8 — blame, `hs config which`, completion

- `blame` (core/config.py:299-309) uses `Configuration.which` (cmdkit config.py:247-291: top-most layer
  holding the path), then builds `HYPERSHELL_` + upper-cased path. `which` picks `env` correctly once
  the env layer holds the right path; only the **name** is wrong. Probe today:
  `blame(c,'export','myvar')` → `HYPERSHELL_EXPORT_MYVAR` for a variable `HYPERSHELL_EXPORT_MyVar`.
- `hs config which` (config.py:350-390) builds the name itself (:383), and for a table value searches
  `full_config.namespaces.env.to_env().flatten(prefix='HYPERSHELL')` by prefix (:384-387). Probes with
  `HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3`: `which server.tls.min.version` →
  `TLSv1.3 (env: HYPERSHELL_SERVER_TLS_MIN_VERSION …)`; `which server.tls.min_version` →
  `TLSv1.2 (default)`; `which server.tls.min` → `[...] (env: …)`.
- **Need:** the loader records a reverse map, key path tuple → variable name as set (case as
  written), e.g. `ENV_SOURCES[('server','auth_env')] = 'HYPERSHELL_SERVER_AUTH_ENV'`. `blame` and
  `which` both look it up (for a table, the first entry under that path). It cannot ride on the layer:
  `Configuration.extend` re-wraps each layer as `Namespace(mapping)` (cmdkit config.py:188), dropping
  any attribute. Keep it module-level and rebuilt by `reload_env` (and `load_env`'s cache), or return
  it beside the layer.
- `ACTIVE_CONFIG_VARS` (core/config.py:553-556): flatten the merged config to upper `A_B_C` names, then
  every `_` not before `env`/`eval` becomes `.`. Consumers: only `hs config get --list-available`
  (config.py:150), used by bash (share/bash_completion.d/hs:155, :170, :228) and zsh
  (share/zsh/site-functions/_hs:47-50). `tests/test_completions.py` does not read it. Probe with
  `HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3`: 93 words on one line, including
  `server.tls.min.version`, `autoscale.failed.launches`, `relay.launcher.args`, `config.file`, `site`;
  no `server.tls.min_version`.
- **Should become:** the dotted paths of the merged config's leaves, names kept (walk the tree; no
  flatten/regex), minus the four process variables (which the new loader drops anyway, R8). Open-table
  entries appear as their own leaves.
- **Side finding:** zsh's `${(f)…}` splits on newlines, but `--list-available` prints one
  space-joined line, so zsh offers the whole list as one candidate. cmdkit raises the version string
  raw (cmdkit/cli.py:44-47), so `'\n'.join` would fix zsh and still word-split in bash.

## 5. `load_env` / `reload_env` return type

Consumers: `partial_load`/`partial_reload` (core/config.py:281, :295) and
`tests/test_autoscale_failed_launches.py:43,262` (`Configuration(..., env=reload_env())`). Every
other use sees the layer after `Configuration.extend` has made it a `Namespace` (cmdkit config.py:188),
including `hs config which`'s `.to_env().flatten(prefix='HYPERSHELL')` (config.py:385), which is a
`Namespace` method (namespace.py:271-273). Nothing uses `Environ`'s `_prefix`, `expand` or `export`.
**A plain `Namespace` is the simpler return:** change the two annotations, `__all__` is unaffected,
the test keeps working. The `which` fallback at :384-387 goes away once the reverse map exists.
Keeping `Environ` buys nothing and keeps `expand`'s coercion in reach.
