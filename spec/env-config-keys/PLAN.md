# PLAN — Environment variables reach every configuration key with the value as written; the remaining duration keys take units

> **Status:** Draft for review · **Last updated:** 2026-11-15
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

Replace cmdkit's `Environ(prefix='HYPERSHELL').expand()` with a matcher over one declared table of
the documented keys and open tables (`src/hypershell/core/config_schema.py`). Each `HYPERSHELL_*` name
resolves to a key by exact match first, then by the longest table prefix, and each value is read by
the key's declared kind: text as written, whole numbers, numbers and booleans converted or refused
at load naming the variable, durations and sizes passed through for their readers. The loader keeps
the variable that set each key, so `blame`, `hs config which` and completion name keys and variables
by the same rule. The ten duration keys of R6 are converted at the start of the command that reads
them, through one helper, before anything binds or launches. Seven phases: tests first, then the
matcher and typing (the core), `[export]` and the key variable, `blame`/completion, durations, docs,
and a regression sweep.

## 2. Design

### 2.1 The key table (`src/hypershell/core/config_schema.py`, new) — R1, R2, R5, R8

A module importing only the standard library and `core/types.py` (whose imports are standard-library
only), so `core/config.py` imports it with no cycle ([01 §2](research/01-schema-table.md)). It holds:

- `KEYS: dict[str, Kind]` — every documented key by dotted path, about 115: the reference's 101
  definition-list entries, the six `database.timescale.*` keys and `logging.file.format` the code
  reads, the scalar forms `logging.file`, `ssh.nodelist`, `relay.zone` and the undocumented scalar
  `database` (a SQLite path, `data/core.py:195`, which `HYPERSHELL_DATABASE` sets today).
- `Kind`: `text`, `bool`, `int`, `number`, `duration`, `size`, `int_or_none` (`relay.min`/`max`/`init`:
  a whole number or exactly `<none>`), `logging_file` (§2.3). Declared per key, starting from the
  default, overriding it where the GOAL's Clarifications found it wrong ([04](research/04-typed-readers.md)):
  `relay.min`/`max`/`init` → `int_or_none`; `task.memory`/`client.memory` → `size`; every #106 duration
  key and R6's ten → `duration`; `logging.file` → `logging_file`; `database.port` → `int`,
  `database.echo` and `database.timescale.columnstore`/`policy` → `bool` (no default);
  `client.ratelimit` and `autoscale.factor` → `number` (env `2.5` works today); `logging.file.rotate`
  → `text` (a size, a cron expression or `never`; `parse_bytes` reads text).
- `TABLES: dict[str, TableKind]`: `closed` (the fixed sections and sub-tables), `open`
  (`database.connect_args`, `database.pragmas`, `database` itself for provider arguments, `ssh.nodelist`
  groups), `zone` (`relay.zone`), `export`. The root counts as a closed table.
- `ZONE_KEYS` (`launcher`, `launcher_args` text; `size`, `min`, `max`, `init` int), equal to
  `relay.ZONE_OPTIONS`; the timescale keys equal `data.timescale.SETTINGS_KEYS` (the sync test checks
  both, or those modules import the names from here).
- `PROCESS_VARIABLES = ('SITE', 'CONFIG_FILE', 'PYTHONPATH', 'ZONE')` and `PROSE_KEYS` (keys the
  reference names in an entry's prose rather than as entries).
- The matcher (§2.2) and the kind readers (§2.3).

**Sync test** (`tests/test_config_schema.py`, `@mark.unit`, repository path as
`tests/test_key_option.py:261` does): every leaf of `default` is in `KEYS` (or is an empty open table)
and its kind accepts the default (or the default is `<none>`/`<auto>`); the reference's entries,
parsed by the regex in [01 §1](research/01-schema-table.md) with an indentation stack, equal `KEYS`
minus `PROSE_KEYS` (and each prose key appears in its parent entry's text); the zone and timescale
keys equal their code constants; no two keys share a variable name; no zone key is a `_`-suffix of
another; no `KEY_ENV`/`KEY_EVAL` equals another key's name; and, from P5, each R6 key's reference
entry gives the duration forms (R9). A new documented key therefore cannot land without its kind, and the reference
cannot name a key the loader does not know.

### 2.2 The matcher — R1, R2, R3, R8

`read_environment(environ) -> (Namespace, dict[tuple[str, ...], str])` returns the environment layer
and the variable that set each key path. For each name that starts with `HYPERSHELL_` (case-sensitive,
as cmdkit's filter was; `HYPERSHELLX` and `HYPERSHELL_` alone are not keys, [02 F6](research/02-variables-in-use.md)),
the rest is compared upper-cased:

1. **Process variables** `SITE`, `CONFIG_FILE`, `PYTHONPATH`, `ZONE` are dropped (R8). Their readers
   already use `os.getenv`; nothing reads `config.site`/`zone`/`config.file`/`pythonpath` (02 §4).
2. **Exact fixed key**: the name equals a `KEYS` path with `.` → `_`. Then **a fixed key plus
   `_ENV`/`_EVAL`**: stored as the key's own lower-case name plus `_env`/`_eval` (cmdkit matches
   variants case-sensitively, [05 §1](research/05-auth-export-blame.md)), value text (R3).
   Exact-first settles the only prefix pairs among fixed keys (`RELAY_LAUNCHER` /
   `RELAY_LAUNCHER_ARGS`; the auth and password variants), [01 §3](research/01-schema-table.md).
3. **Longest table prefix** (`TABLE_` + rest), by the table's kind:
   - `open`: the rest is one entry name, lower-cased, underscores kept
     (`DATABASE_PRAGMAS_JOURNAL_MODE` → `database.pragmas.journal_mode`; `SSH_NODELIST_MY_GROUP` →
     group `my_group`; `DATABASE_SSL_MODE` → provider argument `database.ssl_mode`). `database`'s fixed
     keys and sub-tables win over its provider-argument entries, which is the corner the GOAL's
     Non-goals leave unreachable (`connect_args_…`, `pragmas_…`, `timescale_…`). `_ENV`/`_EVAL` is not
     split inside a table.
   - `export`: the rest is the entry name **as written** (`EXPORT_MyVar` → `MyVar`, R7).
   - `zone`: the name must end in `_` + a zone key, longest first, with a non-empty zone name before it
     (`RELAY_ZONE_GPU_A100_LAUNCHER_ARGS` → zone `gpu_a100`, key `launcher_args`; no zone key is a
     suffix of another, so the answer is unique). A name that ends in no zone key is refused (R2).
   - `closed`: the rest is held as one unknown key at that table, underscores kept
     (`SERVER_PORT_X` → `server.port_x`, leaving `server.port` intact; `FOO_BAR` → `foo_bar`), change 9.
4. **After matching**, across all names:
   - `HYPERSHELL_RELAY_ZONE` together with any zone-table variable → refused, naming both (R2).
   - Two names that resolve to the same key (`HYPERSHELL_SERVER_PORT` and `HYPERSHELL_server_port`,
     or `LOGGING_FILE=<path>` with `LOGGING_FILE_PATH`) → refused, naming both (decision S10).
   - `logging.file` scalar with `LOGGING_FILE_*` entries → one table: a path scalar becomes
     `logging.file.path`, `true`/`enabled` adds nothing (a table already enables file logging). This
     keeps `tests/test_key_option.py:206` (`LOGGING_FILE=enabled` + `LOGGING_FILE_LEVEL=devel`) as it
     is, and no longer loses a path (decision S8).
   - `ssh.nodelist` or `database` scalar with entries of its own table → the table wins, as today; the
     reference says so (decision S8).
   - A name equal to a table's own path (`SERVER_TLS`, `AUTOSCALE_SIZE`, `DATABASE_CONNECT_ARGS`, …)
     gets no special case: step 3 holds it at the parent table, so it sets that path to its text and
     replaces the table, as it does today. R1 keeps every key a variable sets today; the guide warns
     against it (decision S9).

Zone and open-table entry names are lower-cased like every other path (only `export` keeps case).

### 2.3 Kind readers (environment text → value) — R4, R5

Each reads the text exactly as the variable holds it; typed kinds strip surrounding whitespace first
and match ASCII only (`re.ASCII`; Python's `int()` accepts other digits) ([04](research/04-typed-readers.md)).

| Kind | Accepts | Gives | Refused |
|---|---|---|---|
| `text` | anything | the text unchanged (`false`, `null`, `''`, `007`, `1e5` stay text) | never |
| `bool` | `true`/`yes`/`on`/`1`, `false`/`no`/`off`/`0`, any case | `True`/`False` | anything else, `''` included |
| `int` | `[+-]?\d+(_\d+)*` (leading zeros allowed) | `int` | `1.0`, `1e3`, hex, `''` |
| `number` | the `int` form, or decimal/exponent (`2.5`, `1e3`) | `int`/`float` | `nan`, `inf`, non-finite, `''` |
| `int_or_none` | the `int` form, or exactly `<none>` | `int` / `'<none>'` | anything else |
| `duration`, `size` | anything | an `int`/`float` when the text is a number (`7`, `0.5`, `1e3`), else the text | never at load (§2.6) |
| `logging_file` | `true`/`yes`/`on`/`1`/`enabled` any case; any other text | `True`; the text as a path | `false`/`no`/`off`/`0` (no "disabled" form is documented; today `false` crashes with a traceback) |
| untyped (open entries except `export`/`ssh.nodelist`, provider arguments, unknown held keys) | anything | a TOML integer, float or boolean when the text is exactly one (TOML's grammar: lower-case `true`/`false` only; `007`, `True`, `null`, `''` are not), else the text | never |
| `export`, `ssh.nodelist` groups, zone `launcher`/`launcher_args` | anything | the text | never |
| zone `size`/`min`/`max`/`init` | `int` | `int` | as `int` |

A refusal raises `ConfigurationError('<key>: expected <form>, given <value!r> (from: <VARIABLE>)')`,
the `(from: …)` suffix being `blame`'s own convention. `reload_env` runs at import, so the import
handler in `core/config.py` prints it and exits `exit_status.bad_config` (3) for every command, without
a traceback (R5: "the command SHALL exit with a configuration error naming the variable and the form
expected"). A refused value is echoed only for typed kinds, never for text (so never for a key). The
untyped rule is implemented as a strict match on TOML's scalar grammar, or with `tomllib` only for
text that is a single token (no whitespace, `#`, `=`, quote or newline); the test list fixes the
outcomes (`5`→5, `1_000`→1000, `0x1F`→31, `1e3`→1000.0, `true`→True; `007`, `True`, `null`, `''`,
`5 # c` stay text).

### 2.4 The environment layer and the variable map — R1, R8

`reload_env()` and `load_env()` return a plain `Namespace` (nothing uses `Environ`; `Configuration`
re-wraps every layer, [02 §3](research/02-variables-in-use.md), [05 §5](research/05-auth-export-blame.md)),
and store the variable map module-wide (`ENV_VARIABLES`, rebuilt by every `reload_env`; `load_env`'s
cache keeps the first). `hs config which`'s prefix scan over `namespaces.env.to_env().flatten()` goes.
Precedence is unchanged (CLI > env > local > user > system > preload > defaults); the layer is still
built once at import (invariant §10). `tests/test_autoscale_failed_launches.py` builds
`Configuration(..., env=reload_env())` and keeps working.

### 2.5 `_ENV`/`_EVAL` and the key — R3, R4

Because the env layer now holds `server.auth_env` exactly as a file does, everything downstream works
unchanged ([05 §1](research/05-auth-export-blame.md), probed with an env layer built like a file
layer): cmdkit's variant displacement across layers, `check_auth_forms` (two forms in the env layer
→ exit 3 at import), `resolve_auth` for `server` and `relay.upstream`, `DatabaseURL`'s variant-aware
fields (`database.password_env`), `hs config get server.auth`. R4 needs nothing past the text kind:
every command reads the key through `resolve_auth` ([05 §2](research/05-auth-export-blame.md)), whose
`str(value)` becomes a no-op.

**The key variable by the matcher (decision S6).** R1 documents that a variable may be written in any
letter case. Every place that handles the key variable by name (the client's task environment,
`client.py` `inherited_task_env`, and its no-key hint; `--ssh --env` exports and `assigns_key`,
`cluster/launch.py`) recognises the same names the matcher maps to `server.auth`: one predicate,
`is_key_variable(name)` in `core/config.py`, replaces the exact-name tests. Key variables and relay
variables are recognised by the matcher wherever HyperShell handles them by name (Review-cycle-1
rulings, below).

### 2.6 Duration keys — R6

One helper in `core/config.py`, `config_seconds(*varpath, base=config, whole=False)`: read the key
**by attribute** (so `_env`/`_eval` resolve, and a variant's text is parsed too); a number (not a
bool) passes as given; text goes through `core/types.py`'s `_match_seconds` (`parse_time` when
`whole`, rounding up); a refusal raises
`ConfigurationError(f"{key}: Duration {value!r} is not valid: expected {TIME_FORMS} ({blame(...)})")`,
exit 3, no traceback ([03 §4](research/03-duration-keys.md); #106's message plus the source). A
whole result is an `int`, else a `float`; overflow is refused as `parse_delay` does.

Called from each command's start check, before TLS material, a database lock, a bind or a launch:

- `ServerApp.check_args` (`server.py`, run before `enable_tls`): `server.evict`, passed to
  `ServerThread` and the autoscaling server as a number. Its `>= 10` refusal stays in `HeartMonitor`
  (exit 5 as today); it is **not** rounded, so `"9.5s"` fails it as `9.5` does.
- `ClientApp.check_args` (`client.py`): `client.heartrate`.
- `RelayApp.check_args` (`relay/__init__.py`, beside `site_period`, before `bind_database`): `server.evict`
  (the site server) and `relay.bundlewait`, `relay.poll`, `relay.retention`, `relay.timeout` (`whole=True`,
  rounded up: it becomes each site client's `-T`, which rounds up and reads 0 as never),
  `relay.upstream.heartrate`, `.evict_hint`, `.deadline`, `.backoff`; `RelayApp.run` passes the
  converted values. `relay.bundlewait` and `relay.timeout` reach site clients as `-w`/`-T` through
  `plain_seconds` (`cluster/launch.py:468-479`), so a fraction is never written in exponent form.
- The relay's import-time defaults (`relay/__init__.py:98-116`, `relay/upstream.py:90,105,108`,
  `relay/site.py:92`) read `default.`, not user text, as #106 did for `DEFAULT_SITE_PERIOD`.

Not at load: a load-time refusal would stop `hs list` and the `hs config` commands needed to fix a
file, every #106 duration key must reach argparse as text (its `type=parse_time` and the sibling
`cluster-nodb-and-zero-period`'s `autoscale.period > 0` check both rely on it), and `hs config get`
keeps showing what was written ([03 §4](research/03-duration-keys.md), [02 F5](research/02-variables-in-use.md)).
The #106 keys keep their exit 2. `hs cluster` does not read `server.evict` (it never did; filed in
`issues/config-keys-unread.md`).

### 2.7 `[export]` — R7

`load_task_env` builds the task variables per layer ([05 §3](research/05-auth-export-blame.md)):
the file layers' `export` tables merged depth-first, list-joined and upper-cased exactly as today; then
the environment layer's entries laid over them, name and value as written (`RUN_ID=007`, `MyVar=x`,
an empty value empty). "The same task variable" is the same name, case-sensitively, so
`HYPERSHELL_EXPORT_FOO` still beats a file's `foo` (both `FOO`), while `HYPERSHELL_EXPORT_foo` beside a
file's `foo` gives `foo` and `FOO` (decision S7). Consumers (`client.py` task env, `cluster/launch.py`,
the relay fence) see the same `str → str` mapping.

### 2.8 `blame`, `hs config which`, completion — R8

- `blame` (`core/config.py`): for the `env` layer, the variable from `ENV_VARIABLES` (for a table path,
  the first variable under it), never one built from the path.
- `hs config which` (`src/hypershell/config.py:383`): the same lookup (through `blame` or the map), so
  `HYPERSHELL_server_tls_min_version` and `HYPERSHELL_EXPORT_MyVar` are named as set.
- `ACTIVE_CONFIG_VARS`: the documented keys' dotted paths plus the merged configuration's leaves
  (walked, never flattened and re-split), sorted, without the process variables. `--list-available`
  prints one per line; bash's `compgen -W` splits on newlines too, and zsh's `${(f)…}` then offers each
  key instead of one long candidate ([05 §4](research/05-auth-export-blame.md)). `share/` completions
  change only if a test shows they must; `tests/test_completions.py` gains the check.

### 2.9 Empty text where `None` meant unset — R4

R4 keeps `''` in the configuration; the readers that took the `None` cmdkit gave it as "unset" treat
`''` the same ([04](research/04-typed-readers.md), [02 F4](research/02-variables-in-use.md)):
`core/tls.py` (`cert`, `key`, `cafile`, `fingerprint`, `ciphers`, `servername`: `in (PARAM_NONE, None, '')`),
`core/logging.py` (`logging.file.path`, `compress`), `data/core.py` (`database.user`/`password` before
`_validate_user_and_password`, so `HYPERSHELL_DATABASE_PASSWORD=` is still refused and never connects
as the OS user), and `task.cwd`. `server.advertise` and `relay.zone` already do.

### 2.10 `autoscale.failed_launches` — R1

`configured_failed_launches` (`cluster/remote.py:360-380`) reads its own key and blames
`('autoscale', 'failed_launches')`; the nested-path workaround and its test docstring go.
`tests/test_autoscale_failed_launches.py::test_refused_from_environment` changes shape: `true`, `abc`
and `1.5` are now refused by the loader (exit 3, naming the variable), `-1` still by the reader.

### 2.11 Documentation — R9

- **The rule, once:** a new section in `docs/_include/config_intro.rst` (anchor `config_env`)
  replacing "every option can also be set by an environment variable … delimited by underscores": the
  rule (prefix, `.` as `_`, any letter case after the prefix), underscore keys, open tables, zones
  (`HYPERSHELL_RELAY_ZONE_<ZONE>_<KEY>` and the two refusals), `_ENV`/`_EVAL`, the typing rule (text as
  written; whole numbers, numbers and booleans checked, with the words; durations and sizes in their
  forms; entries with no type of their own as TOML numbers/booleans, else text), the process variables
  (`HYPERSHELL_SITE`, `_CONFIG_FILE`, `_PYTHONPATH`, `_ZONE`), unknown names held, a scalar beside its
  table, two variables for one key, a name that equals a table, and what stays unreachable (provider arguments beginning `connect_args_`/`pragmas_`/
  `timescale_`; names with a character a variable cannot hold, e.g. a zone `gpu-a100`). `.. versionchanged:: 2.9.0`.
  The man-page copies (`config_intro_alt.rst`, `manual.rst` § Environment Variables) say the same in
  short and point to it; neither claims more.
- **Reference** (`config_param_ref.rst`): R6's ten keys give the duration forms (adapting
  `server.tls.handshake`'s sentence; "rounded up" for `relay.timeout`, "fractions are kept" for the
  rest), each with `versionchanged:: 2.9.0`; `logging.file` lists its words; the timescale and zone keys
  stay as prose or become entries (the sync test follows either).
- **`[export]`** (`config_task_env.rst`, `_alt`): name and value as written from the environment; a
  file's table upper-cased; the same-name rule.
- **AGENTS.md** § Configuration and `invariants.md` §10 (lockstep): the environment layer is read
  through `core/config_schema.py`; a new documented key gets its kind there and its reference entry in
  the same commit (the sync test enforces it).
- The release note (the PR's squash body, drafted by `hs-publish`) lists the GOAL's nine changes,
  with S4 under change 4 and S7 under change 5.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | §2.1 `KEYS`; §2.2 steps 2–3 (exact-first, closed-table hold); §2.10 `failed_launches` reads its own key; sync test (no shared names) |
| R2 | §2.2 step 3 `open`/`zone` kinds and step 4 zone refusals; §2.3 untyped rule for entries |
| R3 | §2.2 step 2 variants (lower-case suffix); §2.5 (cmdkit displacement, `check_auth_forms`, `resolve_auth`, `hs config get`) |
| R4 | §2.3 `text` kind; §2.5 `resolve_auth` and `is_key_variable`; §2.9 empty text |
| R5 | §2.1 declared kinds; §2.3 readers and refusals (exit 3 naming variable and form); `size`/`logging_file`/`int_or_none`/zone kinds keep the documented forms |
| R6 | §2.6 `config_seconds` at each command's start check; rounding rule; relay defaults; `plain_seconds` |
| R7 | §2.2 `export` kind; §2.7 per-layer `load_task_env` |
| R8 | §2.2 step 1 process variables; §2.4 variable map; §2.8 `blame`, `which`, completion |
| R9 | §2.11 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(§9, §10, §11, §12 in scope; §5 for `HeartMonitor`'s input) and again on this design.

- **§10 Configuration** — still built once at import, an immutable per-process singleton; precedence
  unchanged; `hs config set` untouched (filed as `issues/config-set-coerces-text.md`). Sentinels: every
  `<none>`/`<auto>` default is a `text` kind, and `relay.min`/`max`/`init` take `<none>` exactly. The
  `X or None` idiom: `0` from the environment is the integer `0` for every typed, duration and size key,
  so it stays falsy. `data/core.py`'s import-time engine and its exit 3 are unchanged; the relay's
  `[database]` swap at import happens after the env layer is read, as today.
- **§9 Queue transport & security** — `AUTH_MINIMUM_LENGTH` and `AUTH_ALLOWED_CHARS` are untouched; a
  key is text, so its characters reach the check unchanged (an all-digit key is no longer shortened).
  No message echoes a key (text kinds are never echoed). TLS stays on by default; an empty
  `HYPERSHELL_SERVER_TLS_ENABLED` becomes a refusal instead of silently turning TLS off.
- **§11 Cluster orchestration** — the key is still never in a launched argv; launched clients still get
  it as `HYPERSHELL_SERVER_AUTH`; `ssh_env_exports` still drops it by name before quoting, now through
  `is_key_variable`, and the client still strips it from every task's environment, now in any letter
  case. Launched-client arguments are unchanged (`-w`/`-T` values only change spelling through
  `plain_seconds`; the 2.9.0a4 contract test still applies). `autoscale.failed_launches` keeps its
  default 3 and 0 for relay fleets. The relay's site fence recognises relay variables by the matcher
  (Review-cycle-1 rulings).
- **§5 Concurrency** — no FSM or thread change; `HeartMonitor`, the client heartbeat and the relay's
  upstream FSMs receive numbers converted before they are constructed.
- **§12 Conventions** — docs in the same commit as each behaviour change, `versionchanged:: 2.9.0`; the
  `--list-available` output format is a completion-facing change made with its `share/` check; tests
  tagged `unit`/`integration`; `exit_status.bad_config` reused; no new dependency (`tomllib` is
  standard on 3.11+), no cmdkit floor change.
- **High-blast-radius files touched:** `server.py`, `client.py` (start checks; task-environment key
  strip), `cluster/remote.py` (`configured_failed_launches`), `core/tls.py` (empty text as unset). Each
  change is local to configuration reading; `hs-review` will gate them.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A new module with a ~115-entry key table and a sync test | R1/R2 need the schema to match names; R5 needs a kind per key; R8 needs one rule for loader, `blame` and completion | Typing from the default is wrong for at least eleven keys (GOAL Clarifications); cmdkit's `converter` sees only values (GOAL Non-goals) |
| Two typing moments: scalars at load, durations at the command's start | R5 asks the configuration to hold converted values; R6 asks the reading command to refuse at start | All at load would stop `hs list`/`hs config` on a bad file value and turn #106's text defaults into numbers before argparse sees them (breaks #106 tests and the sibling cycle) |
| R6 refusals exit 3 while #106's duration keys exit 2 | R6 says "a configuration error"; `bad_config` is today's status for those | Raising `ArgumentError` (2) would call a configuration value an argument; #106's keys are left as they are because the sibling pins `hs relay`'s status |
| Empty text read as unset in `core/tls.py`, logging, `data/core.py`, `task.cwd` | R4 keeps `''`; without this, an empty TLS path or fingerprint, or an empty password, changes meaning | Mapping `''` to `None` in the env layer contradicts R4 ("the configuration SHALL hold … an empty value") |
| `is_key_variable` in `client.py`/`cluster/launch.py` | R1's "any letter case" makes a second spelling of the key variable documented; without it that spelling reaches tasks | Refusing other spellings of the key variable adds a new error and still leaves whatever spelling is accepted to strip |
| `logging.file` scalar and entries merged in the env layer | Keeps today's `LOGGING_FILE=enabled` + `LOGGING_FILE_LEVEL` outcome and stops losing a path | "Table wins" (today) silently drops a path; refusing breaks a pinned test and a working combination |

## 4. Rabbit holes (resolved)

- **Does any name collide, or prefix another, under exact-first matching?** No duplicates among about
  115 fixed keys; two prefix pairs, both settled by exact-first; the `[database]` provider table shadows
  its fixed keys, so fixed keys win; zone keys suffix-unique
  ([`research/01-schema-table.md`](research/01-schema-table.md) §3).
- **Which variables in tests, docs, CI and drives depend on today's splitting or coercion?** Only
  `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES` (and one docs example that is a no-op today); CI sets none;
  durations must stay lazy for `test_duration_options.py`; `DATABASE_PASSWORD=''` would change meaning
  without §2.9 ([`research/02-variables-in-use.md`](research/02-variables-in-use.md)).
- **Where do the duration keys convert, and do any need whole seconds?** At the reader, as #106 did;
  every R6 consumer accepts a float; only `relay.timeout` rounds up
  ([`research/03-duration-keys.md`](research/03-duration-keys.md)).
- **Does any reader break on a converted value, and which keys does the default mistype?** No reader
  breaks; eleven-plus keys need a declared kind; empty text is the one regression risk
  ([`research/04-typed-readers.md`](research/04-typed-readers.md)).
- **Does an env layer shaped like a file layer make `_ENV`/`_EVAL`, the key and `blame` work?** Yes,
  probed end to end, given lower-case suffixes and a variable map; `Environ` is not needed
  ([`research/05-auth-export-blame.md`](research/05-auth-export-blame.md)).

## 5. Risks & open questions

**Decisions for the foreman's sign-off** (each within the GOAL's text, but a reader could take it
otherwise):

- **S1** Duration keys convert at the reading command's start, exit 3 (§2.6); #106's keys keep exit 2.
- **S2** Rounding: `relay.timeout` up; the other nine keep fractions; `server.evict` not rounded.
- **S3** A typed variable that does not convert stops **every** command at import, `hs config`
  included (R5's "the command"); a duration or size variable never does.
- **S4** Empty text on a `bool`/`int`/`number` key is refused (`HYPERSHELL_SERVER_TLS_ENABLED=` no
  longer turns TLS off; release-note under change 4).
- **S5** Empty text on an optional text key reads as unset in its reader (§2.9).
- **S6** The key variable is recognised in any letter case for stripping (§2.5).
- **S7** `HYPERSHELL_EXPORT_foo` beside a file's `foo` gives two task variables, `foo` and `FOO`
  (release-note under change 5).
- **S8** `logging.file` scalar and entries merge; for `ssh.nodelist` and `database` the table wins as
  today and the guide says so; `relay.zone` is refused (R2).
- **S9** A name equal to a table's own path (`HYPERSHELL_SERVER_TLS`) is held as today and replaces
  the table (R1 keeps every key a variable sets today); the guide warns against it. Dropping it was
  considered and rejected as a breach of R1's letter.
- **S10** Two variables naming one key (letter case, or a path scalar with `LOGGING_FILE_PATH`) are
  refused, naming both. Today one wins by environment order. The alternative is a fixed order (the
  sorted-first name wins, silently); refusal was preferred as R2 refuses its own two-form case.
- **S11** Unknown held keys follow the untyped (TOML) rule; `ssh.nodelist` groups and zone launchers
  are text, zone sizes and bounds whole numbers (they have types of their own, so the GOAL's untyped
  rule does not apply to them).
- **S12** `logging.file` words: true words and `enabled` → enabled; false words refused; other text a
  path.
- **S13** `client.ratelimit` is a `number` (env `2.5` works today), though `-R` takes whole numbers.

**Residual risks.**
- A typed key given through `_env`/`_eval` still yields text at its reader (cmdkit resolves variants to
  text); this is the file form's existing gap and is not widened. Duration keys handle it (§2.6).
- Import-time refusals (S3) are new failure modes for scripts that set a malformed typed variable they
  never used. The release note says so.
- The full suite runs against the new loader for every test (conftest sets variables at import); a
  mistyped kind shows up widely and early, which is the point of P2's broad verify.
- The shared uv cache under `$TMPDIR/uv-cache` had a broken `trove-classifiers` entry while planning;
  the docs and 3.11 environments were built with `UV_CACHE_DIR=$TMPDIR/uv-cache-eck`.

## 6. Verification strategy

- **Tests first** (P1): about sixty-two tests across `tests/test_env_config.py`, `test_env_duration.py`,
  `test_env_export.py`, `test_env_blame.py`, each driving a child interpreter or the `hs` CLI with
  variables set (the configuration is built at import), so each is red on `90ab2fc2` by an assertion
  about the defect. The list is [`red.txt`](red.txt); `spec/relay-hardening/drive/red_check.sh` proves
  it red against the base `src`, and `spec/contended-writes/drive/rest_green.sh` that every other test in
  the touched files still passes.
- **Expected red on 90ab2fc2** (62 nodes): R1 underscore keys ×7, letter case, `relay.launcher` kept
  beside `launcher_args`, `hs config get`, an unknown name held whole; R2 three open-table entries, a
  provider argument, a zone key, two zone refusals; R3 `auth_env`, `auth_eval`, `password_env`, two
  forms refused; R4 six text cases plus an all-digit key through `hs server`+`hs client` and
  `hs submit --queue`; R5 four boolean words, five refusals, two `logging.file` cases, the TOML rule;
  R6 `server.evict = "10m"` from a file and the environment, five refusals at start (`server.evict`,
  `client.heartrate`, `relay.upstream.deadline`, `.evict_hint` from a file, `relay.poll`, which on the
  base keeps running and is stopped by the test); R7 four values/names as written plus a real task, and the key variable spelled `HYPERSHELL_Server_Auth`
  kept out of a task's inherited environment and out of `--ssh --env` exports;
  R8 `blame`, two `which` cases, completion paths, one per line, process variables.
- **Must keep working** (green before and after, P1 adds them as unit tests): the variables of
  [02 § Must keep working](research/02-variables-in-use.md): `SERVER_PORT`, `DATABASE_*` as the
  PostgreSQL tests set them, `LOGGING_LEVEL`/`STYLE`/`FILE`/`FILE_*`, `SERVER_TLS_ENABLED=false`,
  `SERVER_EVICT=10`, the #106 duration keys with units, numbers and `5MB` (refused only by the command
  that reads it, exit 2), `RELAY_ZONE=gpu` with `RELAY_ZONE_GPU_SIZE`, `EXPORT_FOO`, `TASK_MEMORY=16G`.
- **Sync test** (P2): table ↔ `default` ↔ reference (§2.1).
- **CLI drive** [`drive/env_drive.sh`](drive/env_drive.sh) in a throwaway site, modes `keys`, `typed`,
  `durations`, `serve` (an all-digit key, `evict = "10m"`, `heartrate=5s`, a real server and client),
  `export` (a real task's environment), `which`. Each mode was dry-run on the base and fails at its
  first new-behaviour check, as expected.
- **Sweep** (P7): the full suite (`-n auto`), every red-list node green, Python 3.11 on the new test
  files, every drive mode, the red check again, the docs build with `-W`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-15)

Foreman, gate judge under the maintainer's mandate. Approved as planned; S1–S13 (§5) are accepted as
proposed.

- **S1–S5:** accepted. The ten duration keys are converted at the start of the command that reads
  them and refused with exit 3; #106's keys keep exit 2. Only `relay.timeout` rounds up. A typed
  variable that does not convert stops every command at import, naming the variable. Empty text on
  a boolean or number key is refused (release note, change 4); on an optional text key it reads as
  unset.
- **S6:** accepted, with the maintainer's sign-off in session (2026-11-15): the key variable is
  recognised by the matcher wherever HyperShell handles it by name. Public text states the outcome
  only.
- **S7–S13:** accepted. `S7` and `S4` go into the release note under changes 5 and 4.
- **Sibling cycle:** `cluster-nodb-and-zero-period` refuses an `autoscale.period` not greater than
  zero through `parse_period`, which leaves non-numeric values to the command. Keep the key's text
  reaching argparse as planned; whichever cycle lands second rebases onto the other.
- **High-blast files** (`server.py`, `client.py`, `cluster/remote.py`, `core/tls.py`): the review's
  human gate applies as the rubric says.
- **Toolchain:** the shared uv cache under `/tmp/claude-501/uv-cache` is corrupt; use a fresh
  `UV_CACHE_DIR` as TECH says.

## Review-cycle-1 rulings (2026-11-16)

The review's human gate was triggered: F1 concerns how the queue key reaches launched clients
(invariants §11). The maintainer ruled in session (2026-11-16) on every finding, and on the review's
private notes, which the foreman hands to the builder directly.

- **F1.** Every launch path drops every form of the key variable the matcher recognises (`_ENV` and
  `_EVAL` included) from a launched client's environment before it sets the run key, so a key given
  by any form never makes a client refuse two forms. Tests cover launcher, `--mpi` and autoscaling
  launches with the key given by `_ENV`, `_EVAL` and a non-canonical spelling, and `--ssh` and relay
  site launches by their command construction. They are red on `f495fa20`.
- **Key handling by the matcher.** Wherever HyperShell handles key variables by name (a task's
  environment, `--ssh --env` exports, the relay's site fence), it recognises them by the matcher:
  every form of the key, and every name that maps under `relay`. Public text states this outcome
  only.
- **F2.** Empty text on an optional text key means "not given", and the key's default applies:
  `logging.file`, `database.host` and the other optional text keys, and `server.bind` too (an empty
  bind is the default bind). The guide says so. Tests cover `HYPERSHELL_LOGGING_FILE=`,
  `HYPERSHELL_DATABASE_HOST=` on SQLite, and an empty bind.
- **F3.** The file layer keeps base behaviour for an empty `database.user` or `database.password`;
  "empty means unset" applies to the environment layer only. A test pins the file case.
- **F4.** The two new seeds cite line numbers as of this branch.
- **Wording.** Docstrings, `AGENTS.md`, `invariants.md` and test names describe the outcome
  ("recognised by the matcher") and do not single out letter case. Rename
  `test_key_variable_any_case_not_inherited` and `test_key_variable_any_case_not_exported` to
  `test_key_variable_forms_not_inherited` and `test_key_variable_forms_not_exported`, and update
  `red.txt` to match.
- **Sibling #112** (`cluster-nodb-and-zero-period`) lands first. Rebase onto it. Eight
  `tests/test_period_floor.py::test_period_key_refused` cases (`env-nan` and `env-neginf`) expect the
  "greater than zero" message, but the environment's `nan` and `-inf` now arrive as text and are
  refused as not a duration. Update those expectations; the behaviour stays a refusal with exit 2.
- **GOAL Non-goals amended by these rulings** (foreman, 2026-11-16). GOAL § Non-goals says the
  relay's site fence and `--ssh --env`'s handling of key variables are unchanged. The key-handling
  ruling above supersedes that: both now recognise variables by the matcher. Everything else in that
  Non-goal stands. GOAL.md stays locked as written; review cycle 2 grades against this amendment.

## Review-cycle-2 rulings (2026-11-21)

The review's human gate was triggered by N2, whose locus is `client.py`. The maintainer ruled in
session (2026-11-21): "Base behaviour + fixes". Review cycle 3 is the last.

- **N2.** The client's `task.cwd` reader is as on base, so a file's `cwd = ""` behaves as before. An
  empty `HYPERSHELL_TASK_CWD=` from the environment means "not given" (`task.cwd` joins
  `EMPTY_UNSET`), matching the F3 ruling's environment-only rule. A test pins both layers.
- **N1.** A key with a documented default keeps empty text from the environment, as in a file:
  `database.timescale.segmentby`, `orderby`, `chunk_interval` and `compress_after` leave
  `EMPTY_UNSET`. `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=` turns segmenting off as `segmentby = ""`
  does. The test that decides "optional" uses the documented defaults, not only the defaults table.
- **N3.** `AGENTS.md` states the empty-value rule alongside `invariants.md`.
- **Wording.** Current spec prose states outcomes only: the PLAN deviation-table row on the key
  variable's spelling, the old test names in the phase notes, and `research/05`'s framing. The same
  row's stale claim about `tls.py`, logging and `data/core.py` is corrected to the remediation as
  built.
