# 00 — Research digest: env-config-keys

Five briefs, read-only, on `develop` `90ab2fc2` (the worktree's `src` is identical). Each decision
below resolves the briefs into one recommendation; [`PLAN.md`](../PLAN.md) builds on these.

| Brief | Topic |
|---|---|
| [`01-schema-table.md`](01-schema-table.md) | The documented keys, where the key table lives, collisions, matching order |
| [`02-variables-in-use.md`](02-variables-in-use.md) | Every `HYPERSHELL_*` name in use; what must keep working; env-layer consumers |
| [`03-duration-keys.md`](03-duration-keys.md) | R6's ten keys: readers, #106's conversion, rounding, where to convert |
| [`04-typed-readers.md`](04-typed-readers.md) | What each non-text key's reader accepts; parse rules; empty values |
| [`05-auth-export-blame.md`](05-auth-export-blame.md) | `_ENV`/`_EVAL`, the key, `[export]`, `blame`/`which`/completion, `Environ` |

## Decisions

**D1 — One key table, in its own module.** `src/hypershell/core/config_schema.py`, importing only the
standard library and `core/types.py` (01 §2). It holds `KEYS` (dotted path → kind), `TABLES`
(path → `closed`/`open`/`zone`/`export`), `ZONE_KEYS`, `PROCESS_VARIABLES`, `PROSE_KEYS`, and the
matcher. A unit test keeps it in step with `default` and `docs/_include/config_param_ref.rst`, whose
definition-list terms parse with one regex (01 §1–2; precedent `tests/test_key_option.py:261`).

**D2 — Matching order** (01 §4, 05 §1). Only names starting `HYPERSHELL_` (case-sensitive prefix;
cmdkit's `startswith('HYPERSHELL')` admitted `HYPERSHELLX`, 02 F6). The rest is compared
case-insensitively:
1. the four process variables are dropped;
2. an exact fixed key, or a fixed key plus `_ENV`/`_EVAL` (written as the key's own lower-case name
   plus `_env`/`_eval`, since cmdkit matches variants case-sensitively, 05 §1);
3. else the longest table prefix, by kind: an `open` entry (rest lower-cased, one name); an `export`
   entry (rest as written); a `zone` entry (the zone-key suffix rule); a `closed` table: the rest held
   as one unknown key, underscores kept. `_ENV`/`_EVAL` is never split inside a table (05 §1).
No two fixed keys share a name; the only prefix pairs (`relay.launcher`/`launcher_args`, the auth and
password variants) are settled by exact-first (01 §3).

**D3 — The env layer is a plain `Namespace`**; nothing uses `Environ` (02 §3, 05 §5). The variable
that set each key path is kept in a module-level map beside it, since `Configuration.extend`
re-wraps every layer and drops any attribute (05 §4).

**D4 — Typing by kind, at load, for the environment layer only.** `text` keeps the text (R4).
`bool`, `int`, `number` convert or raise `ConfigurationError` naming the variable and the form (R5);
at import that exits `bad_config` (3) for every command, which is acceptable for a variable (it is
unset as easily as set) and is what R5 asks. `duration` and `size` keys take a number when the text
is one, else keep the text, and **never fail at load** (02 F5, 03 §4: `hs list` and `hs config` must
keep working with a bad duration key; #106's option-backed keys must reach argparse as text, which the
sibling `cluster-nodb-and-zero-period` relies on). Untyped entries take the TOML-scalar rule (GOAL
Clarifications). Parse rules as 04 recommends: ASCII only, surrounding whitespace stripped for typed
kinds; booleans `true|yes|on|1` / `false|no|off|0` any case; integers `[+-]?digits(_digits)*`
(leading zeros allowed; `1.0`, `1e3`, hex refused); numbers add decimal/exponent forms, non-finite
refused; empty text refused for every typed kind.

**D5 — Durations (R6) convert at the reader**, through one helper called from each command's start
check, before any bind, lock, TLS material or launch (03 §4): `ServerApp.check_args`
(`server.evict`), `ClientApp.check_args` (`client.heartrate`), `RelayApp.check_args` (`server.evict`
and the eight `relay.*` keys, beside `site_period`). A refusal is a `ConfigurationError`, exit 3,
`<key>: Duration '<v>' is not valid: expected <TIME_FORMS> (from: <variable or file>)`. Read by
attribute, so `_env`/`_eval` work. Rounding: `relay.timeout` rounds up (it becomes each site
client's `-T`, which rounds up and reads 0 as never); the other nine keep fractions, `int` when whole,
else `float`; `server.evict` is **not** rounded (`"9.5s"` must fail its `>= 10` check as `9.5` does).
The relay's import-time defaults read `default.`, not user text (03 §1).

**D6 — `[export]` per layer** (05 §3). `load_task_env` upper-cases and joins the file layers' tables as
today, then lays the environment layer's entries over them as written. "The same task variable" is
the same name, case-sensitive.

**D7 — `blame`, `hs config which`, completion** (05 §4). Both name the variable from the map (D3).
Completion is the documented keys plus the merged configuration's leaves, by dotted path, one per
line (zsh splits the list on newlines and today gets one candidate, 05 §4).

**D8 — Empty text on an optional text key reads as unset where `None` did** (04, 02 F4): the TLS
fields (`core/tls.py:472-477`), `logging.file.path`/`compress`, `database.user`/`password`
(`data/core.py:90-94`), `task.cwd`. The configuration holds `''` (R4); the reader keeps today's
meaning, so `HYPERSHELL_DATABASE_PASSWORD=` is still refused rather than connecting as another role.

**D9 — The key variable is recognised by the matcher, not by its exact spelling** (05 §2). Since R1
makes any letter case set `server.auth`, the client's task environment and `--ssh --env` exports leave
out every name the matcher maps to `server.auth`, not only `HYPERSHELL_SERVER_AUTH`.

**D10 — Open points the briefs left, settled in PLAN §2 and listed for sign-off:** a scalar and its
table both set (`logging.file`, `ssh.nodelist`, `database`); a name equal to a table; two spellings
of one key; the type of unknown held keys; `ssh.nodelist` group entries as text; `logging.file`'s
words.

## Side findings (filed, not in scope)

`issues/config-keys-unread.md`: `server.eager` never takes effect (`--eager`'s default overwrites it),
`hs cluster` ignores `server.evict`, `hs cluster -R` reads `task.ratelimit` (04, 03 §1).
`issues/config-set-coerces-text.md`: `hs config set`'s coercion (GOAL Non-goals).
