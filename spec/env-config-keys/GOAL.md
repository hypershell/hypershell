# GOAL — Environment variables reach every configuration key with the value as written; the remaining duration keys take units

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** env-config-keys
- **kind:** fix
- **appetite:** big  ·  *Both seeds say `small`; neither is. The fix replaces how the environment
  layer is read (a name-to-key matcher over the documented keys and open tables, and a typed reader
  per key), moves `blame`, completion and `[export]` onto it, adds the duration reads in the server,
  client and relay, and rewrites the configuration guide's rule. `core/config.py` is imported by
  every command, so a wrong mapping breaks all of them.*

## Problem

The configuration guide says every option "can also be set by an environment variable, where the
name aligns to the path to that option, delimited by underscores" (`docs/_include/config_intro.rst:25-27`),
and the parameter reference says every `[server.tls]` key "is also exposed as an environment
variable" (`docs/_include/config_param_ref.rst:310`). Neither holds. Every `HYPERSHELL_*` variable
goes through cmdkit's `Environ(prefix='HYPERSHELL').expand()` (`src/hypershell/core/config.py:260-262`;
cmdkit 2.8.0, the lock and the `pyproject.toml` floor), which has two properties. All findings below
were reproduced on `develop` `90ab2fc2` with a probe that starts no service (a child interpreter per
case, an empty `HYPERSHELL_CONFIG_FILE`, a throwaway site). **Pre-existing:** the loader is the same
on the 2.8 line.

**Every underscore is a level of nesting.** A key whose own name has an underscore cannot be named.
`HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3` sets `server.tls.min.version` and leaves
`server.tls.min_version` at `TLSv1.2`. `HYPERSHELL_DATABASE_TIMESCALE_CHUNK_INTERVAL='1 day'` makes
`database.timescale.chunk.interval`, which `src/hypershell/data/timescale.py:111` refuses as an
unknown setting. `HYPERSHELL_RELAY_LAUNCHER_ARGS='--mem 4G'` turns `relay.launcher` into the table
`{args = '--mem 4G'}` and leaves `relay.launcher_args` empty. The same holds for `relay.num_threads`,
`relay.upstream.evict_hint`, `database.timescale.compress_after`, any `database.connect_args` or
`database.pragmas` name with an underscore (the reference's own `journal_mode`), any
`[ssh.nodelist]` group or relay zone whose name has one, and every `_env`/`_eval` variant:
`HYPERSHELL_SERVER_AUTH_ENV=MYKEY` makes `server.auth` the table `{env = 'MYKEY'}`.
`autoscale.failed_launches` (#107) works only because `configured_failed_launches` reads the nested
path `autoscale.failed.launches` as a key-specific workaround (`src/hypershell/cluster/remote.py:360-380`).

**Every value is coerced.** `expand` passes each value through cmdkit's `_coerced`: `''` and `null`
become `None`, `true`/`false` in any case become booleans, and anything `int()` or `float()` accepts
becomes a number. A key that takes text cannot be given those texts:
`HYPERSHELL_AUTOSCALE_LAUNCHER=false` gives `False`, not the command `false`;
`HYPERSHELL_LOGGING_DATEFMT=` gives `None`. A key that takes a boolean gets no check:
`HYPERSHELL_SERVER_EAGER=no` gives the text `'no'`, which every reader treats as true. The worst case
is the key itself. Since #97, `resolve_auth` returns `str(value)` (`src/hypershell/core/config.py:394`),
so an all-digit `HYPERSHELL_SERVER_AUTH` no longer crashes `hs server` as the seed reports, but it is
silently rewritten: `0012345678901234` (16 characters, passes the documented check at
`core/config.py:90`) becomes `12345678901234`, which `hs server` then refuses as shorter than 16, and
`1234567890123456e5` becomes `1.234567890123456e+20`. `HYPERSHELL_SERVER_AUTH_ENV=MYKEY` makes the key
the text `Namespace({'env': 'MYKEY'})`, which `hs server` refuses for its characters and which
`hs client` and `hs submit --queue` would present as the key.

**`[export]` names and values are mangled.** `load_task_env` (`core/config.py:640-642`) flattens the
nested table back with `_` and upper-cases it, and re-stringifies the coerced value:
`HYPERSHELL_EXPORT_RUN_ID=007` gives a task `RUN_ID=7`, `1e3` gives `1000.0`, an empty value gives
`null`, and `HYPERSHELL_EXPORT_MyVar=x` gives `MYVAR`. The guide says the variable is injected "sans
prefix" (`docs/_include/config_task_env.rst:76-78`).

**`blame`, `hs config which` and completion repeat the wrong mapping.** `blame` names `HYPERSHELL_`
plus the path joined by `_` (`core/config.py:299-308`) whatever set it, and `hs config which` builds
its variable name the same way on its own (`src/hypershell/config.py:383-387`), so for a key the
environment set at a nested path it either finds nothing or names a variable that could not have set
the key. `ACTIVE_CONFIG_VARS`, the list behind `hs config`
completion, turns every underscore not followed by `env`/`eval` into a dot (`core/config.py:553-556`),
so it offers `server.tls.min.version` and `autoscale.failed.launches`. It also offers `site` and
`config.file`: `HYPERSHELL_SITE` and `HYPERSHELL_CONFIG_FILE` are process variables
(`docs/_include/config_site_vars.rst`), not configuration keys, yet the environment layer holds them
as keys `site` and `config.file`, as it would `HYPERSHELL_PYTHONPATH` and the `HYPERSHELL_ZONE` a
launched client is given.

**Durations with no option take bare seconds only.** Since `duration-units-remaining` (#106) every
duration option and its configuration key take units and Slurm forms. Ten settings that no option
reads still go straight to the code that uses them: `server.evict`, `client.heartrate`,
`relay.bundlewait`, `relay.poll`, `relay.retention`, `relay.timeout`, `relay.upstream.heartrate`,
`relay.upstream.evict_hint`, `relay.upstream.deadline` and `relay.upstream.backoff` (its Non-goals).
`evict = "10m"` reaches `HeartMonitor` as text and fails its `evict_after >= 10` comparison
(`src/hypershell/server.py:794`), from a file or from `HYPERSHELL_SERVER_EVICT=10m` alike. One of the
ten is partly covered already: `relay.timeout` is handed to each site client as `-T <text>`
(`src/hypershell/cluster/launch.py:515`), whose parser takes units, so `"5m"` works, but a bad value
is refused by every launched client instead of once by the relay at start.

## Outcome / vision

A user can set every documented key from the environment, by the name the guide's rule gives, and
the key holds what they wrote: text stays text, numbers and booleans are checked and converted, and
durations take the same forms as everywhere else. Every variable that sets a key today still sets
it. `hs config` names, completes and blames keys by the same rule the loader uses, and a task sees an
`[export]` variable under the name and with the value the user wrote. The ten remaining duration
keys take units from a file or the environment, and a bad value stops the command at start with a
message that names it. The fix lives in HyperShell; no cmdkit release is needed.

## Acceptance criteria (the contract)

In these criteria a **documented key** is one the parameter reference
(`docs/_include/config_param_ref.rst`) names, whether or not it has a default; an **open table** is
one whose entries the user names: `database.connect_args`, `database.pragmas`, the provider
arguments `[database]` forwards (its `*` entry), `ssh.nodelist` groups, `relay.zone` tables, and
`export`.

- **R1 — Underscore keys.** WHEN a `HYPERSHELL_*` variable spells the path of a documented key, with
  each `.` written as `_` and in any letter case, the configuration SHALL hold the variable's value
  at that key, including keys whose own name has an underscore (`server.tls.min_version`,
  `database.timescale.chunk_interval` and `compress_after`, `relay.launcher_args`,
  `relay.num_threads`, `relay.upstream.evict_hint`, `autoscale.failed_launches`), and
  `hs config get <key>` SHALL show it. Every variable that sets a key on `develop` today SHALL set the
  same key. `autoscale.failed_launches` SHALL be read from its own key, from a file or from
  `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES`. No two documented keys SHALL share a variable name.
- **R2 — Open tables.** WHEN a variable's name continues past an open table's path, the rest of the
  name SHALL be one entry name in that table, underscores kept (`HYPERSHELL_DATABASE_PRAGMAS_JOURNAL_MODE`
  sets `database.pragmas.journal_mode`; `HYPERSHELL_SSH_NODELIST_MY_GROUP` sets the group `my_group`).
  WHEN a variable continues past `relay.zone`, its name SHALL end in one of the zone keys the
  reference lists, and the part before that key SHALL be the zone name
  (`HYPERSHELL_RELAY_ZONE_GPU_A100_LAUNCHER_ARGS` sets `launcher_args` of zone `gpu_a100`). IF a
  `relay.zone` variable ends in no zone key, or `HYPERSHELL_RELAY_ZONE` and a zone-table variable are
  both set, THEN the command SHALL exit with a configuration error naming the variables.
- **R3 — `_ENV` and `_EVAL` variants.** WHEN `HYPERSHELL_<KEY>_ENV` or `HYPERSHELL_<KEY>_EVAL` is set
  for a documented key, the key SHALL resolve as `<key>_env` / `<key>_eval` does in a configuration
  file (e.g. `HYPERSHELL_SERVER_AUTH_ENV`, `HYPERSHELL_RELAY_UPSTREAM_AUTH_EVAL`,
  `HYPERSHELL_DATABASE_PASSWORD_ENV`), including the refusal of two forms of one authkey in one layer
  that a file already gets, and `hs config get server.auth` SHALL show the resolved key as it does
  for the file form.
- **R4 — Text stays text.** WHEN a variable sets a key that takes text, the configuration SHALL hold
  the variable's text unchanged, including `false`, `true`, `null`, an empty value, digits with
  leading zeros and an exponent form. In particular `HYPERSHELL_SERVER_AUTH=0012345678901234` SHALL be
  the key `0012345678901234` for `hs server`, `hs client` and `hs submit --queue` alike.
- **R5 — Typed keys.** WHEN a variable sets a key that takes a whole number, a number or a boolean,
  the configuration SHALL hold the converted value; IF the text does not convert THEN the command
  SHALL exit with a configuration error naming the variable and the form expected. Keys that take a
  size (`task.memory`, `client.memory`) or another documented form besides a number or text
  (`logging.file` as `true`/`enabled`/a path, `autoscale.sites` as a comma list, `relay.zone` as a
  name, `ssh.nodelist` as a host list, `relay.min`/`max`/`init` as whole numbers) SHALL keep every
  value the reference documents for them. An open-table entry, which has no type of its own, SHALL
  follow the rule recorded in Clarifications.
- **R6 — Duration keys.** WHEN `server.evict`, `client.heartrate`, `relay.bundlewait`, `relay.poll`,
  `relay.retention`, `relay.timeout`, `relay.upstream.heartrate`, `relay.upstream.evict_hint`,
  `relay.upstream.deadline` or `relay.upstream.backoff` is given in a configuration file or its
  variable in any form the duration options take, the component that reads it SHALL use that many
  seconds; a bare number SHALL keep its current meaning, fractions included (`relay.poll = 0.1`). IF
  the value is not a duration THEN the command that reads it SHALL exit at start, before it binds a
  port or launches a client, with a configuration error naming the key (and the variable, when the
  environment gave it) and the accepted forms.
- **R7 — `[export]` as written.** WHEN `HYPERSHELL_EXPORT_<NAME>` is set, every task's environment
  SHALL hold `<NAME>`, letter case and underscores as written, with the variable's value unchanged
  (`HYPERSHELL_EXPORT_RUN_ID=007` gives `RUN_ID=007`; `HYPERSHELL_EXPORT_MyVar=x` gives `MyVar=x`;
  an empty value stays empty). WHERE a file's `[export]` entry gives the same task variable, the
  environment's value SHALL win, as it does today. A file's `[export]` table SHALL behave as today.
- **R8 — `blame`, `hs config which` and completion.** For every key a variable sets, `blame` and
  `hs config which` SHALL name that variable, and `hs config` completion SHALL offer each key by its
  dotted path (`server.tls.min_version`), never a path no key has (`server.tls.min.version`).
  `HYPERSHELL_SITE`, `HYPERSHELL_CONFIG_FILE`, `HYPERSHELL_PYTHONPATH` and `HYPERSHELL_ZONE` SHALL NOT
  become configuration keys and SHALL NOT be offered.
- **R9 — Documentation.** The configuration guide SHALL state, once, the rule that maps a variable to
  a key (underscore keys, open tables, zones, `_ENV`/`_EVAL`), the typing rule, the names that are
  process variables rather than keys, and what cannot be reached from the environment (R2's
  open-table corner in Non-goals; names with characters a variable name cannot hold). The
  parameter reference SHALL give the duration forms for R6's ten keys. Neither SHALL claim a key is
  reachable when it is not. Changed entries carry `.. versionchanged:: 2.9.0`, and the release note
  lists every user-visible change below.

### User-visible changes (the release note)

Each is a change from `develop` `90ab2fc2` and from 2.8.x.

1. A variable for a key whose name has an underscore now sets that key. Previously it set a nested
   key no reader used, so the setting was silently ignored (or, for `database.timescale.*`, refused
   as unknown; for `relay.launcher_args`, it also replaced `relay.launcher` with a table).
2. `HYPERSHELL_<KEY>_ENV` and `_EVAL` now work as `_env`/`_eval` do in a file. Previously they
   replaced the key with a table (`server.auth` became a malformed key).
3. Values are no longer coerced by their look. For a key that takes text, `false`, `true`, `null`,
   an empty value and number-like text (`007`, `1e3`, an all-digit `server.auth`) are kept as
   written. Previously they became `False`, `True`, `None`, `None`, `7`, `1000.0` and an integer.
4. For a key that takes a number or boolean, text that does not convert is now a configuration error
   naming the variable. Previously it was kept as text (a boolean key given `no` read as true).
   Booleans accept the words recorded in Clarifications.
5. `HYPERSHELL_EXPORT_<NAME>` now reaches tasks under `<NAME>` as written, value unchanged.
   Previously the name was upper-cased and the value coerced and re-stringified (`007` → `7`,
   empty → `null`, `True` → `true`, `1e3` → `1000.0`).
6. The ten duration keys of R6 take units and Slurm forms in a file and in the environment, and a bad
   value stops the command at start with a message. Previously a unit value crashed the reader with
   a traceback (or, for `relay.timeout`, was refused by each site client).
7. A `HYPERSHELL_RELAY_ZONE_*` variable that names no zone key, or one set together with
   `HYPERSHELL_RELAY_ZONE`, is now a configuration error. Previously it created a malformed zone or
   was silently dropped.
8. `hs config` completion and `hs config which` name keys by their real path, and no longer offer
   `site` or `config.file`.
9. An unknown `HYPERSHELL_*` name continuing past a known table, and a `[database]` provider argument
   given from the environment, are held as one key with underscores kept
   (`HYPERSHELL_DATABASE_SSL_MODE` → `database.ssl_mode`), not as nested tables.

## Non-goals (no-gos)

- **No cmdkit change and no new cmdkit floor** (foreman's direction). `Environ.expand`'s `converter`
  hook sees only values, so it could not fix R1 in any case.
- **No second spelling.** No double-underscore (`HYPERSHELL_SERVER__TLS__MIN_VERSION`) or other
  alternative form (proposed in Clarifications).
- **What stays unreachable from the environment:** a `[database]` provider argument whose name
  begins with the name of a `[database]` sub-table plus `_` (`connect_args_…`, `pragmas_…`,
  `timescale_…`), and any table entry or zone name with a character a variable name cannot hold
  (`gpu-a100`). R9 documents both; a file reaches them.
- **No unknown-variable warning or typo check.** A `HYPERSHELL_*` name that matches no key is held,
  unread, as change 9 says.
- **No merge semantics for zones across forms.** R2 refuses the name form and the table form together
  in the environment; a zone list in a file with zone tables from the environment, or the reverse,
  merges by today's layer precedence.
- **`hs config set`'s own typing.** It passes the value through `smart_coerce`
  (`src/hypershell/config.py:284`), so `hs config set autoscale.launcher false` writes the boolean
  `false` to the file. Same defect class, different surface; proposed as a follow-up seed, not taken.
- **The file form of `[export]`.** Its documented upper-casing (`foo = 1` gives `FOO=1`) and list
  joining stay.
- **Sub-second units** (`ms`) and any change to the duration parser's forms; `-Q`/`--poll` fractions
  (left open by `duration-units-remaining`).
- **The 2.8 line.** 2.9 only.
- **`HYPERSHELL_RELAY_*` fencing from site clients** (`src/hypershell/relay/site.py:407`) and the
  values `--ssh --env` forwards are unchanged; they go through the new mapping on the far side.

## Clarifications

- **Q:** One GOAL for both seeds? — **A:** Yes: `env-config-key-mapping` primary,
  `duration-units-config-keys` with it, both marked `adopted:env-config-keys` (foreman's brief,
  2026-11-15). The duration keys need the per-key typed reader R5 introduces.
- **Q:** Accepted directions (foreman's brief, 2026-11-15)? — **A:** Fix in HyperShell, not cmdkit;
  keep every existing variable name working; match names against the known schema (documented keys
  plus open tables); typing follows the key; durations use the duration parser; `[export]` names and
  values reach tasks as written. Recorded as given, with the refinements below.
- **Q:** Appetite? — **A:** `big`, proposed (header). Both seeds say `small`.
- **Q:** "Typing follows the key's default" — does the default suffice? — **Proposed:** No; the type
  is declared per key, starting from the default. Contradicting cases found: `relay.min`/`max`/`init`
  default to the `'<none>'` sentinel but take whole numbers (by the default, `HYPERSHELL_RELAY_MAX=4`
  would become the text `'4'`); `task.memory`, `client.memory`, `task.timeout`, `client.timeout`,
  `autoscale.period`, `server.poll`, `task.signalwait` and `server.tls.handshake` default to numbers
  but take size or duration text (by the default, `HYPERSHELL_TASK_MEMORY=16G` would become an
  error); `logging.file` defaults to a table but takes `true`/`enabled`/a path, and the reader treats
  the text `'true'` as a path (`src/hypershell/core/logging.py:1024`). R5 binds the outcome.
  **Needs sign-off.**
- **Q:** Keys with no type of their own (open-table entries other than `export`; `[database]`
  provider arguments)? — **Proposed:** the text is taken as a TOML integer, float or boolean when it
  is one, else kept as text. This keeps what works today (`..._CONNECT_ARGS_TIMEOUT=5` stays the
  number `5`, which `sqlite3.connect` needs) and gives text when TOML says it is not a scalar
  (`007`, `null`, an empty value, `True` stay text). **Needs sign-off.**
- **Q:** Which words does a boolean key accept? — **Proposed:** `true`/`false`, `yes`/`no`,
  `on`/`off`, `1`/`0`, in any case. `1`/`0` work today by truthiness; `no` and `off` change from
  true to false (change 4). **Needs sign-off.**
- **Q:** A double-underscore spelling as well? — **Proposed:** No (Non-goals). Schema matching leaves
  only the `[database]` provider-argument corner unreachable, a `__` form would not reach names with
  characters a variable cannot hold either, and two spellings would give `blame` and completion two
  answers. **Needs sign-off.**
- **Q:** Do R6's keys round up to whole seconds, as the duration options do? — **Proposed:** No; each
  key keeps fractions, as its bare number does today (`relay.poll` defaults to `0.1`, and
  `deadline`, `backoff` and `evict_hint` are floats). Whether a whole-number key such as
  `server.evict` rounds a fractional unit value is left to `hs-plan`. **Needs sign-off.**
- **Q:** What happens to a variable that matches no key? — **Proposed:** it is held as one key at the
  deepest known table its name spells, underscores kept (change 9), unread and not an error; the four
  process variables of R8 are dropped. **Needs sign-off.**
- **Q:** Release line? — **Proposed:** 2.9 only, with `versionchanged:: 2.9.0` (Non-goals). The
  behaviour is documented and pre-existing on 2.8, and the fix changes values users may rely on.
  **Needs sign-off.**
- **Q:** Size circuit-breaker? — **A:** Nine R-IDs, two seeds that share one reader; under the
  ~10 limit. If the gate wants it smaller, R6 (the duration keys) splits off cleanly into its own
  cycle after this one, with the duration-seed status reverted; R1–R5 and R7–R8 do not split without
  writing the matcher twice (proposed, not taken).
- **Left for `/hs-plan` (not clarification markers):** where the matcher's table of documented keys
  and types lives and how a test keeps it in step with the parameter reference and the defaults;
  whether `load_env` keeps cmdkit's `Environ` as the container; where each duration key is
  converted (load time or at its reader); the error text and exit status (`exit_status.bad_config`
  is today's for configuration errors).

## Related materials

- Seeds: [`issues/env-config-key-mapping.md`](../../issues/env-config-key-mapping.md) (primary),
  [`issues/duration-units-config-keys.md`](../../issues/duration-units-config-keys.md).
- Prior art: `spec/server-auth-variant-collision/` (#97: the file form of `auth_env`, and
  `resolve_auth`'s `str(value)`); `spec/size-and-duration-units/` and
  `spec/duration-units-remaining/` (#106: the duration parser, `src/hypershell/core/types.py`
  `parse_time`/`parse_delay`/`TIME_FORMS`); `spec/cluster-launch-failures/` (#107: the
  `autoscale.failed.launches` workaround); `spec/relay/` (#82: the zone tables).
- Source: `src/hypershell/core/config.py` (`default`, `reload_env`, `blame`, `resolve_auth`,
  `ACTIVE_CONFIG_VARS`, `load_task_env`), `src/hypershell/config.py` (`hs config`),
  `src/hypershell/cluster/remote.py` (`configured_failed_launches`), `src/hypershell/server.py`
  (`HeartMonitor`), `src/hypershell/relay/__init__.py` (`RelayApp.run`), cmdkit
  `cmdkit/namespace.py` (`Environ.expand`, `_coerced`).
- Docs: `docs/_include/config_intro.rst`, `docs/_include/config_param_ref.rst`,
  `docs/_include/config_task_env.rst`, `docs/_include/config_site_vars.rst`.
