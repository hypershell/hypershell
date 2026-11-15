# 01 — The key/type table: what the reference documents, where the table lives, what collides

Worktree at `94319cdb` (src identical to `develop` `90ab2fc2`). Probes: `$TMPDIR/eck/probe.py` (parse
the reference, diff against `default`, collision sweep), `probe2.py` (today's env layer, one child
per case, empty `HYPERSHELL_CONFIG_FILE`, throwaway `HYPERSHELL_SITE`), `probe3.py` (prototype
matcher, no hypershell import). No service started.

## 1. Documented keys and their types

`docs/_include/config_param_ref.rst` marks every entry as an RST definition-list term alone on its
line: top sections ``` ``[logging]`` ``` at column 0, then ``` ``.key`` ```, ``` ``[table]`` ``` or
``` ``*`` ``` indented 4 spaces per level, body +4. The regex
``^(?P<ind>(?: {4})*)``(\[[a-z_]+\]|\.[a-z_]+|\*)``\s*$`` parses it cleanly: **101 leaf entries,
20 tables** (probe.py), and skips the two example terms (``` ``.mycluster = [...]`` ``` :548,
``` ``foo = 1`` ``` :925). Types below are what the entry text, `default` (`core/config.py:96-241`)
and the reader say. `—` = no default in `default`.

| Section | Keys → type |
|---|---|
| `logging` :1 | `level`, `datefmt`, `format`, `style` text (level/style enums) · `color` bool · **`file`** special: `true`/`"enabled"`/a path, or the table (:52-54; reader `core/logging.py:1024`) |
| `logging.file` :48 | `path`, `level`, `style`, `compress` text · `rotate` text (size or cron, "bare number is bytes" :69-74) · `keep` whole · prose-only `format` text (:66-67, read at `core/logging.py:1050`, —) |
| `database` :86 | `provider` text (only default, :99) · `url`, `file`, `database`, `schema`, `host`, `user`, `password` text — · `port` whole — · `echo` bool — · `password_env`/`_eval` (:137) — |
| `database.*` | `[connect_args]` :142, `[pragmas]` :146 open · `*` :163 open (provider args) · `[timescale]` :150 closed, keys **in prose only** (:152-155): `chunk_interval`, `segmentby`, `orderby`, `compress_after` text, `columnstore`, `policy` bool (all —; `data/timescale.py:29-34`, `SETTINGS_KEYS` :79) |
| `server` :167 | `bind`, `host`, `advertise` text · `port`, `queuesize`, `bundlesize`, `bundlemax`, `attempts` whole · `eager` bool · `poll` duration · `evict` duration (R6) · `auth` text + `_env`/`_eval` |
| `server.tls` :308 | `enabled`, `insecure` bool · `cert`, `key`, `cafile`, `fingerprint`, `ciphers`, `servername` text · `min_version` text (enum) · `handshake` duration |
| `client` :381 | `bundlesize`, `cores`, `ratelimit` whole (`-R type=int`, `client.py:1617`) · `bundlewait`, `timeout` duration · `heartrate` duration (R6) · `memory` size |
| `submit` :444 | `bundlesize` whole · `bundlewait` duration |
| `task` :483 | `cwd` text · `cores` whole · `memory` size · `timeout`, `signalwait` duration |
| `ssh` :530 | `config` text · `args` text — · **`nodelist`** special: one host list/pattern (text) or open table of groups (:542; `cluster/ssh.py:366-380`) |
| `autoscale` :550 | `policy`, `launcher`, `distribution` text · `factor` number (float, `server.py:1778`) · `period` duration · `sites` comma list (:640-644) · `failed_launches` whole · `size.init`/`min`/`max` whole |
| `relay` :700 | `database`, `bind`, `site`, `launcher`, `launcher_args`, `template` text · `port`, `bundlesize`, `size`, `num_threads` whole · `min`/`max`/`init` whole (default `'<none>'`) · `bundlewait`, `poll` (fractional), `retention`, `timeout` duration (R6) · **`zone`** special: name, list, or zone tables (:748-754) |
| `relay.zone.<z>` | prose-only zone keys (:751-752; `ZONE_OPTIONS` `relay/__init__.py:238`): `launcher`, `launcher_args` text, `size`, `min`, `max`, `init` whole (checked at `relay/__init__.py:1279-1293`) |
| `relay.upstream` :838 | `host` text · `port`, `bundlesize` whole · `auth` text + `_env`/`_eval` (:855) · `heartrate`, `deadline`, `backoff`, `evict_hint` duration (R6; last three float) |
| `relay.upstream.tls` :895 | `enabled` bool · `cert`, `cafile`, `fingerprint` text |
| `console` :911 | `theme` text |
| `export` :920 | open; names and values as written (R7) |

**Diff, `default` leaves vs reference entries** (probe.py): in `default` but not an entry: only
`export` and `ssh.nodelist` (empty dicts that are tables in the reference). Entries with no
default: `database.{url,file,database,schema,host,port,user,password,echo,*}`, `ssh.args`. Keys the
code reads that the reference gives **only in prose**: the six `database.timescale.*`, the six zone
keys, `*_env`/`*_eval`, `logging.file` scalar form, `logging.file.format`. **Undocumented scalar
form:** `database` itself as a string (a SQLite path, `data/core.py:195-196`);
`HYPERSHELL_DATABASE=my.db` sets it today, so R1's "every variable that sets a key today" keeps it.

## 2. Where the table lives, and the sync test

Precedent for a test reading repo docs: `tests/test_key_option.py:261-310` (`REPO =
Path(__file__).resolve().parent.parent`, sweeps `docs/`, `@mark.unit`); `tests/test_completions.py:28-29`
reads `share/zsh`. No test touches `config_param_ref.rst` today.

Place the table in a new module, e.g. `src/hypershell/core/config_schema.py` (not bare `schema`:
`data/core.py:201` already uses `schema` for the database schema). It may import only stdlib and
`core/types.py` (whose imports are stdlib-only, `types.py:8-17`: `parse_time`, `parse_bytes`,
`TIME_FORMS`), so `core/config.py` imports it with no cycle. Shape: `KEYS: dict[str, Type]` (dotted
path → `text|whole|number|bool|duration|size|comma|special`), `TABLES: dict[str, kind]` with kind
`closed|open|zone|export`, `ZONE_KEYS`, `PROCESS_VARS`.

A unit test `tests/test_config_schema.py`:
- **(a) vs `default`:** walk `default` leaves (empty dict = table); every leaf is in `KEYS` or
  `TABLES`, and its declared type accepts the default (or the default is `PARAM_UNSET`/`'<auto>'`).
- **(b) vs the reference:** parse with the regex above, keeping the indentation stack; the set of
  entries (minus `database.*`) equals `KEYS` minus a declared `PROSE_KEYS` set, each `[table]` is in
  `TABLES`, and each prose key appears as ``` ``name`` ``` in its parent entry's body. Cross-check
  `ZONE_KEYS == relay.ZONE_OPTIONS` and the timescale keys `== data.timescale.SETTINGS_KEYS` (or let
  those modules import them from the table). Alternative: R9 promotes the timescale and zone keys to
  real nested entries, shrinking `PROSE_KEYS`.
- **(c) R1:** the collision sweep of §3 as an assertion.

## 3. Collisions (probe.py over 115 fixed keys: 100 entries, timescale keys, `_env`/`_eval`, `logging.file`/`ssh.nodelist`/`database.timescale` as keys)

- **Duplicate variable names: none.** R1 holds by construction today.
- **Prefix pairs among fixed keys (A_ prefixes B, B not A's child):** only `RELAY_LAUNCHER` →
  `RELAY_LAUNCHER_ARGS` and `{SERVER_AUTH, RELAY_UPSTREAM_AUTH, DATABASE_PASSWORD}` → `_ENV`/`_EVAL`.
  Exact match first resolves both. No key name ends in `env`/`eval` except those six; `K_ENV` never
  equals another key's name.
- **None of the asked-about pairs collide:** no `relay.upstream_*` or `server_tls` keys exist;
  `autoscale.size.min` / `relay.min` / zone `min` sit under different prefixes.
- **The `[database]` open table `*` shadows every `database.*` fixed key** (19 names, incl.
  `DATABASE_TIMESCALE_*` → would be provider arg `timescale_chunk_interval`). Fixed key and
  sub-table must win; the path is the same either way, but the **type** differs
  (`DATABASE_PASSWORD=12345` text vs TOML int; `DATABASE_ECHO=yes` bool vs text). The other open
  tables have no fixed keys beneath them.
- **Zone suffix:** no zone key is a `_`-suffix of another, so "longest zone key the name ends in"
  is unique (test it). A zone name may end in a key word: `RELAY_ZONE_GPU_MAX_SIZE` → zone
  `gpu_max`, key `size`. `RELAY_ZONE_LAUNCHER_ARGS`, `RELAY_ZONE_SIZE`, `RELAY_ZONE_GPU` have an
  empty or no key → R2 error.
- **Inside open tables `_ENV`/`_EVAL` is not special:** `HYPERSHELL_EXPORT_X_ENV` gives task
  variable `X_ENV` (R7).
- **Scalar + table both set** (probe2.py, today): `LOGGING_FILE=true` + `LOGGING_FILE_PATH`,
  `SSH_NODELIST=a,b` + `SSH_NODELIST_G`, `DATABASE=my.db` + `DATABASE_FILE`, `RELAY_ZONE=cpu` +
  `RELAY_ZONE_GPU_SIZE`, `RELAY_LAUNCHER` + `RELAY_LAUNCHER_ARGS`: in every case the table silently
  wins and the scalar is lost. R2 makes `relay.zone` an error; the other three scalar/table keys
  are unruled. Plan to decide; the same refusal is the consistent choice.
- **A name equal to a closed table** (`SERVER_TLS`, `AUTOSCALE_SIZE`, `RELAY_UPSTREAM`,
  `DATABASE_CONNECT_ARGS`, `DATABASE_PRAGMAS`): change 9 yields a scalar at the table's own path
  (prototype: `held unknown server.tls`; `DATABASE_CONNECT_ARGS` falls to the `*` table as entry
  `connect_args`), which replaces the table and breaks its readers (today it does the same). Plan to
  decide: refuse, or drop with the unmatched rule.

## 4. Precedence a matcher needs

Prototype output (probe3.py), in order:
1. Strip `HYPERSHELL_` and compare the rest upper-cased. Drop `SITE`, `CONFIG_FILE`, `PYTHONPATH`,
   `ZONE` (exact names only). Note cmdkit's `Environ` selects by `startswith('HYPERSHELL')` with
   no `_` (`cmdkit/namespace.py:420`).
2. **Exact fixed key** (incl. `_ENV`/`_EVAL` variants and the scalar forms `logging.file`,
   `relay.zone`, `ssh.nodelist`, `database`): `SERVER_TLS_MIN_VERSION`, `server_tls_min_version`
   → `server.tls.min_version`; `RELAY_LAUNCHER_ARGS` → `relay.launcher_args`;
   `DATABASE_TIMESCALE_CHUNK_INTERVAL` → the timescale key.
3. **Else the longest table whose name plus `_` is a prefix** (the root counts as a table):
   - open (`connect_args`, `pragmas`, `database` `*`, `ssh.nodelist`): the rest is one entry,
     lower-cased, TOML scalar (`DATABASE_SSL_MODE` → `database.ssl_mode`);
   - `export`: the rest is one entry, **case kept**, value as written (`EXPORT_MyVar` → `MyVar`);
   - `relay.zone`: suffix rule of §3;
   - closed: held as one unknown key, underscores kept (`SERVER_PORT_X` → `server.port_x`, not
     today's `server.port = {x: 1}`, which destroys `server.port`; `DATABASE_TIMESCALE_FOO` →
     `database.timescale.foo`, which `data/timescale.py:111` refuses; `FOO_BAR` → `foo_bar`).
4. Open question for the plan: `HYPERSHELL_LOGGING_FILE_FORMAT` falls to rule 3 (held unknown)
   though the reader uses it. Add `logging.file.format` to `KEYS` as text. Unmatched held keys
   have no type of their own: text, or the TOML rule? GOAL leaves this open. Also two variables
   that differ only in case and name one key (`HYPERSHELL_SERVER_PORT`, `HYPERSHELL_server_port`)
   need a rule.
5. `ssh.nodelist` group entries follow the TOML rule, so `SSH_NODELIST_G=true` would be a boolean.
   Consider typing them as text (host list), as R5 calls the scalar form.

## Recommendation

- **Table:** `src/hypershell/core/config_schema.py` (stdlib and `core/types.py` only): `KEYS`
  (≈115 dotted paths → type), `TABLES` (path → closed/open/zone/export), `ZONE_KEYS`,
  `PROCESS_VARS`, `PROSE_KEYS`. `core/config.py`, `blame`, `hs config which` and
  `ACTIVE_CONFIG_VARS` all read it (R8).
- **Matching order:** process variables dropped → exact fixed key (with `_ENV`/`_EVAL`) → longest
  table prefix → by table kind (open entry / export as written / zone suffix / closed: held unknown,
  underscores kept). Refuse scalar + table both set for `relay.zone` (R2). Decide the same for
  `logging.file`, `ssh.nodelist` and `database`, and for a name equal to a closed table.
- **Sync test** (`tests/test_config_schema.py`, `@mark.unit`, `REPO` path as in
  `test_key_option.py:261`): `default` leaves ⊆ `KEYS ∪ TABLES` with type-accepts-default; the
  reference's parsed entries == `KEYS − PROSE_KEYS`, each prose key named in its parent's body;
  zone and timescale keys == code constants; no duplicate variable names; no zone key a suffix of
  another; no `K_ENV`/`K_EVAL` equal to another key.
