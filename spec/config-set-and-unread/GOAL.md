# GOAL — `hs config set` writes each key by its type, and `hs server` and `hs cluster` read the keys they document

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** config-set-and-unread
- **kind:** fix
- **appetite:** small  ·  *Two `small` seeds promoted together on the foreman's brief
  (Clarifications). Both reuse what `env-config-keys` (#113) built: the key table and the typed
  reader in `src/hypershell/core/config_schema.py`. The `hs config set` change is one command's
  argument path; the unread keys are four option defaults in `ServerApp` and `ClusterApp`, one
  pass-through of the eviction period, and the type of `-R`. Shaping reproduced every defect on
  `develop` at `d9901cf0`. No new option, configuration key or wire change.*
- **target:** 2.9.0  ·  *both seeds.*

## Problem

Two defects in how configuration keys are written and read, both **pre-existing** on `develop` and
the 2.8 line, and both in the class `env-config-keys` (#113) fixed for `HYPERSHELL_*` variables.
Each was reproduced on `develop` `d9901cf0` in a throwaway site (`--local` writes, a child
interpreter per probe, no service started).

**`hs config set` writes a value by its look, not by its key.** Its VALUE argument goes through
`smart_coerce` (`src/hypershell/config.py:284`, `src/hypershell/core/types.py:36`), whatever the key:
`true`/`false` in any case become booleans, anything `int()` or `float()` accepts becomes a number,
and `null`/`none` become `None`.

- `hs config set autoscale.launcher false` writes `launcher = false`, the boolean, not the command
  `false`.
- `hs config set server.auth 0012345678901234` writes `auth = 12345678901234`; `hs server` then
  exits 2 with "Authentication key must be at least 16 characters" (`resolve_auth` stringifies the
  integer, `src/hypershell/core/config.py:458`).
- `hs config set export.RUN_ID 007` writes `RUN_ID = 7`, and a task sees `RUN_ID=7`.
- `hs config set logging.datefmt null` fails with tomlkit's `ConvertError` and a traceback file,
  exit 5.
- A key that takes a number or a boolean gets no check: `hs config set server.port abc` and
  `hs config set server.eager no` are written as the text `"abc"` and `"no"`; `server.port 1.5` and
  `autoscale.factor inf` as floats. A text `"no"` is truthy wherever the key is read.

Since #113 the environment layer types each value by the key's declared kind, so the same key now
takes different forms from a variable and from this command. The guide states the environment's
rule (`docs/_include/config_intro.rst`, "The value is read by the key's type"); `hs config set`'s
own page (`docs/_include/config_set_desc.rst`, `config_set_help.rst`) says nothing about types.

**Four documented keys are never read by a command they configure.**

- **`server.eager`** never takes effect in `hs server` or `hs cluster`. Each app sets its class
  attribute `eager_mode` from `config.server.eager` and then registers `--eager` with
  `action='store_true', dest='eager_mode'` (`src/hypershell/server.py:1769-1772`,
  `src/hypershell/cluster/__init__.py:162-165`), so the parsed default `False` replaces it. Probed
  with `eager = true` in the file and with `HYPERSHELL_SERVER_EAGER=true`: `eager_mode` is `False`
  in both apps; only `--eager` turns it on. So today a `server.eager` value of any form has no
  effect, and the "`no` reads as true" case above is harmless only because nothing reads the key;
  the two defects have to be fixed together.
- **`server.attempts` under `hs cluster`** *(found while verifying; not in either seed).* `hs server`
  defaults `-r` to `config.server.attempts - 1` (`server.py:1770`); `hs cluster` defaults it to the
  built-in `DEFAULT_ATTEMPTS - 1` (`cluster/__init__.py:163`, `DEFAULT_ATTEMPTS` is
  `default.server.attempts`, `server.py:108`). Probed with `attempts = 3`: `hs server` holds
  `max_retries == 2`, `hs cluster` holds `0`. The reference calls the key the counterpart of
  `-r`/`--max-retries`, which both commands take, and `server.eager` has no effect without retries.
- **`server.evict` under `hs cluster`.** `hs server` reads it at start (`ServerApp.check_args`,
  `server.py:1913`) and `hs relay` reads it for its site server. `ClusterApp` reads no eviction
  period, and none of its launch paths passes one: `LocalCluster`, `RemoteCluster` and `SSHCluster`
  build their `ServerThread` with the built-in period, and `AutoScalingCluster` defaults
  `evict_after` to `DEFAULT_EVICT = default.server.evict` (`server.py:741`,
  `cluster/remote.py:983`). A configured `evict`, valid or not, is ignored.
- **`client.ratelimit` under `hs cluster`.** `hs cluster -R` defaults to
  `config.task.get('ratelimit', None)` (`cluster/__init__.py:240`), a key the reference does not
  document; the documented `client.ratelimit` (which `hs client -R` reads, `client.py:1616`) is
  ignored. Probed: with `[client] ratelimit = 5`, `hs cluster` holds `ratelimit is None`; with
  `HYPERSHELL_TASK_RATELIMIT=7`, it holds `7`. A second gap sits behind it: `client.ratelimit` is a
  number (`2.5` is accepted, and #113 types it so), but `-R` takes whole numbers only in both
  commands (`hs client -R 2.5` exits 2: "invalid int value"), and `hs cluster` hands its value to
  each launched client as `-R <value>` (`cluster/launch.py:526-527`). Once `hs cluster` reads the
  key, a fractional rate would make every launched client refuse its command line.

## Outcome / vision

`hs config set KEY VALUE` writes what a TOML author would write for that key, by the same rule the
environment layer uses: text stays text, a number or boolean is converted or refused before anything
is written, and a key with no type of its own takes TOML's own scalars. `server.eager`,
`server.attempts`, `server.evict` and `client.ratelimit` take effect in every command the reference
says they configure, and a user who set one of them and saw nothing happen gets the behaviour they
asked for, named in the release note.

## Acceptance criteria (the contract)

In these criteria a **documented key** is one in `KEYS` (`src/hypershell/core/config_schema.py`,
kept equal to the parameter reference by `tests/test_config_schema.py`); its **kind** is the one
declared there. "As the environment layer reads it" means the reading `HYPERSHELL_<KEY>` gets for
the same text since #113. Each criterion holds for `--user`, `--system` and `--local` alike.

- **R1 — Text as written.** WHEN `hs config set` names a key that takes text (a documented text key;
  a `<key>_env` or `<key>_eval` variant of a documented key; an `[export]` entry; an `[ssh.nodelist]`
  group; a zone's `launcher` or `launcher_args`), the file SHALL hold VALUE as a TOML string exactly
  as given, including `true`, `false`, `null`, `none` in any letter case, an empty value, digits with
  leading zeros and an exponent form. `hs config set server.auth 0012345678901234` SHALL give a key
  that `hs server` accepts as `0012345678901234`; `hs config set export.RUN_ID 007` SHALL give a task
  `RUN_ID=007`.
- **R2 — Typed keys converted or refused.** WHEN `hs config set` names a key whose kind is a whole
  number, a number, a boolean, `relay.min`/`max`/`init` (a whole number or `<none>`), a zone's
  `size`/`min`/`max`/`init`, or `logging.file`, the file SHALL hold the value as the environment
  layer reads it (a boolean takes `true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0` in any letter case;
  `logging.file` takes `true`/`enabled` or a path). IF VALUE does not convert (an empty value
  included) THEN the command SHALL exit with an error naming the key, the form expected and the value
  given, with no traceback, and SHALL write nothing: the file and its backups are as before.
- **R3 — Durations and sizes.** WHEN `hs config set` names a key whose kind is a duration or a size,
  the file SHALL hold a TOML number when VALUE is a finite number (`600`, `0.5`) and the text as
  written otherwise (`10m`, `16G`), as the environment layer reads it; the command that reads the key
  checks it, as today.
- **R4 — No type of its own.** WHEN `hs config set` names a key with no type of its own (an entry of
  `database.connect_args` or `database.pragmas`, a `[database]` provider argument, or a path that is
  no documented key), the file SHALL hold a TOML integer, float or boolean when VALUE is exactly one
  in TOML's grammar (`5`, `1_000`, `2.5`, `true`), and the text otherwise (`007`, `True`, `null`,
  `wal`, an empty value), as the environment layer reads it. No VALUE SHALL make `hs config set` fail
  with a traceback.
- **R5 — `server.eager`.** WHEN `server.eager` is true in the merged configuration (any file layer or
  `HYPERSHELL_SERVER_EAGER`) and `--eager` is not given, `hs server` and `hs cluster` SHALL schedule
  failed tasks before new ones, as `--eager` does. WHEN it is false or unset, both SHALL behave as
  today, and `--eager` SHALL still turn it on.
- **R6 — `server.attempts` under `hs cluster`.** WHEN `server.attempts` is set and `-r` is not given,
  `hs cluster` SHALL allow that many attempts per task, as `hs server` does (`attempts = 3` gives
  `-r 2`). `-r` SHALL still override it.
- **R7 — `server.evict` under `hs cluster`.** WHEN `server.evict` is set, `hs cluster` SHALL evict a
  client after that period without a heartbeat, in every mode (local, `--launcher`, `--mpi`, `--ssh`,
  autoscaling), as `hs server` does; it takes the same forms. IF the value is one `hs server` refuses
  (not a duration, or under its 10-second floor) THEN `hs cluster` SHALL refuse it with `hs server`'s
  message and exit status, before it binds a port or launches a client.
- **R8 — `client.ratelimit` under `hs cluster`.** WHEN `client.ratelimit` is set and `-R` is not
  given, every `hs cluster` client SHALL be limited to that many tasks per second, in every mode, as
  an `hs client` reading the key is; `0` or unset SHALL mean no limit, as for `hs client`. A
  fractional rate (`0.5`) SHALL limit the rate, never make a launched client fail. `hs cluster` SHALL
  NOT read `task.ratelimit`.
- **R9 — Documentation and release note.** The `hs config set` page SHALL state that VALUE is written
  by the key's type, pointing at the guide's rule. The parameter reference SHALL name `hs cluster`
  wherever R5–R8 make it read a key (`server.evict`'s "stop before they start" sentence included),
  and the `-R` help of `hs client` and `hs cluster` SHALL give the form R8 settles. Changed entries
  carry `.. versionchanged:: 2.9.0`; the release note lists every change below and says who is
  affected. Docs, help and notes use top-level commands only.

### User-visible changes (the release note)

Each is a change from `develop` `d9901cf0` and from 2.8.x.

1. **`hs config set` keeps text as text.** For a key that takes text, `true`, `false` (any case),
   `null`, `none` and number-like text (`007`, `1e3`, an all-digit `server.auth`) are written as
   given. Previously `true`/`false` became booleans, number-like text became a number (`7`,
   `1000.0`, an integer that lost its leading zeros and then failed `hs server`'s 16-character
   check), and `null`/`none` failed with a traceback (exit 5). *Affects:* anyone who wrote such a
   value with `hs config set`; files already written are not rewritten.
2. **`hs config set` converts or refuses typed keys.** A whole-number, number or boolean key is
   written as that type; a boolean takes `yes`/`no`, `on`/`off`, `1`/`0` as well as `true`/`false`,
   in any case. A value that does not convert is refused, naming the key and the form, and nothing
   is written. Previously it was written as given: `no` and `off` as text that reads as true, `1`
   and `0` as integers, `abc` as text, `1.5` on a whole-number key and `inf`/`nan` on a number key as
   floats. *Affects:* scripts that pass such a value; they now fail instead of writing a broken
   setting.
3. **`hs config set` follows TOML for keys with no type of its own** (`database.connect_args`,
   `database.pragmas`, `[database]` provider arguments, undocumented paths): only lower-case
   `true`/`false` are booleans, and only TOML numbers are numbers. Previously `True`/`FALSE` became
   booleans and `null`/`none` failed with a traceback; `007` became `7`; `.5` and `5.` became floats
   (now text); `0x1F`, `0o17` and `0b11` were text (now the TOML integers 31, 15 and 3).
4. **`server.eager` now takes effect.** With `server.eager` true (a file, or
   `HYPERSHELL_SERVER_EAGER`), `hs server` and `hs cluster` schedule failed tasks before new ones
   without `--eager`. Previously the key was ignored and only `--eager` did this. *Affects:* anyone
   with `server.eager` true who runs `hs server` or `hs cluster` with a database and retries
   (`server.attempts` above 1, or `-r` 1 or more); without retries nothing changes. To turn it off
   for one run, set `HYPERSHELL_SERVER_EAGER=false`.
5. **`hs cluster` now reads `server.attempts`.** Without `-r`, it retries failed tasks up to
   `attempts - 1` times, as `hs server` does. Previously it never retried unless `-r` was given.
   *Affects:* anyone with `server.attempts` above 1 who runs `hs cluster` with a database and no
   `-r`.
6. **`hs cluster` now reads `server.evict`.** Its server evicts a client after the configured period,
   in every mode, and a value `hs server` refuses now stops `hs cluster` at start. Previously
   `hs cluster` always used 600 seconds and ignored the key, valid or not. *Affects:* anyone with
   `server.evict` other than 600 who runs `hs cluster`; and anyone whose `server.evict` is invalid or
   under 10 seconds, whose `hs cluster` now refuses to start.
7. **`hs cluster` now reads `client.ratelimit`, and no longer `task.ratelimit`.** Without `-R`, each
   client is limited to `client.ratelimit` tasks per second, as `hs client` is. Previously
   `hs cluster` ignored the documented key and read an undocumented `task.ratelimit`. *Affects:*
   anyone with `client.ratelimit` set who runs `hs cluster` (its clients are now throttled); anyone
   who set `task.ratelimit` (it is now ignored: move it to `client.ratelimit`).
8. **`-R` and a fractional rate** — the form R8 settles (Clarifications): proposed, `-R` takes a
   number on `hs client` and `hs cluster` (`-R 0.5` is one task every two seconds per client).
   Previously `-R` took whole numbers only.

## Non-goals (no-gos)

- **No checks beyond the key's kind in `hs config set`.** A text key with a fixed set of words
  (`logging.level`, `logging.style`, `autoscale.policy`, `console.theme`) is written as given, and
  a duration or size is checked by the command that reads it (R3). Observed while shaping, not taken:
  `hs config set logging.style ''` writes a value that makes every command, `hs config set`
  included, exit 3 until the file is edited by hand (candidate seed).
- **No new spelling or path rules in `hs config set`.** The path is matched as written (no letter-case
  folding, so `Server.eager` stays an undocumented path), no warning for an unknown path, no way to
  write a TOML null, list or table, no unset, and a one-word path (`database`) is still refused as
  "Missing section in variable path". How a value merges into an existing file is unchanged.
- **`hs config get`, `which` and `edit`** are unchanged; values already in a file are not rewritten.
- **No `--no-eager` option** (Clarifications).
- **No fallback for `task.ratelimit`** (Clarifications).
- **`client.heartrate` under a local `hs cluster`.** Its in-process client uses the built-in
  heartrate, not the key (`cluster/local.py:239`, `client.py:1031`); the reference names only
  `hs client` as its reader. Found, not taken (candidate seed).
- **`client.bundlesize` / `bundlewait`**: their own seed, `issues/client-bundlesize-config-key.md`.
- **No wider audit** of which command reads which key beyond the four named here; `hs relay` and
  `hs client` are unchanged except `-R`'s form (R8).
- **The 2.8 line.** 2.9 only.

## Clarifications

- **Q:** One GOAL for both seeds, under a new slug? — **A:** Yes, `config-set-and-unread`, both seeds
  marked `adopted:config-set-and-unread` (foreman's brief, 2026-11-21). They meet at `server.eager`:
  fixing the read without the typed write would make `hs config set server.eager no` turn eager mode
  on.
- **Q:** Typing rule for keys with no type of their own and open tables? — **A:** #113's TOML rule,
  as the environment layer applies it (foreman's brief, 2026-11-21; #113 GOAL gate, 2026-11-15).
  R4 binds it; change 3 lists what it changes.
- **Q:** Appetite? — **A:** `small` (brief; both seeds). Nine R-IDs, under the ~10 limit.
- **Q:** Should `server.attempts` under `hs cluster` be in scope? It is in neither seed. —
  **Proposed:** yes (R6). Same defect, same command, found while verifying `server.eager`, which does
  nothing without retries. If the gate prefers, R6 splits off into a seed with no effect on the
  rest. **Needs sign-off.**
- **Q:** Exit status when `hs config set` refuses a value (R2)? — **Proposed:** 2, a usage error, as
  a command-line value refused by its type is elsewhere (`hs server -p abc` exits 2) and as
  `hs config set`'s own "Missing section" refusal does; nothing has been read from a configuration
  source. The environment layer's refusal stays 3. **Needs sign-off.**
- **Q:** Should `hs config set` check durations and sizes at write time? — **Proposed:** no (R3,
  Non-goals). The environment layer does not; the forms depend on the reader (`relay.timeout` rounds,
  `logging.file.rotate` is text); and a bad duration stops only the command that reads it, at start,
  with a message naming the key. **Needs sign-off.**
- **Q:** How does `hs cluster` handle a fractional `client.ratelimit` (R8)? — **Proposed:** `-R`
  takes a number on `hs client` and `hs cluster`, matching the key's type, so a launched client gets
  the rate as given (change 8). The client already divides a rate per thread as a float. The
  alternative is to refuse a fraction at `hs cluster` start, which would contradict the key's type
  and `hs client`'s in-process use of `2.5`. **Needs sign-off.**
- **Q:** Keep reading `task.ratelimit` as a fallback? — **Proposed:** no. It was never documented,
  #113's guide names no such key, and the release note tells anyone who set it to move it (change
  7). A fallback would keep an undocumented key alive with a second precedence rule. **Needs
  sign-off.**
- **Q:** A `--no-eager` option, now that the key can turn eager mode on? — **Proposed:** no
  (Non-goals). `HYPERSHELL_SERVER_EAGER=false` overrides a file for one run; a new option is a new
  feature. **Needs sign-off.**
- **Q:** Does R7 cover the local mode too? — **Proposed:** yes, every mode, since each runs a server
  with a heartbeat monitor; the period's floor and forms are `hs server`'s. **Needs sign-off.**
- **Q:** Release line? — **A:** 2.9 only, `versionchanged:: 2.9.0`; both seeds carry
  `target: 2.9.0`, and the changes alter behaviour users may rely on.
- **Left for `/hs-plan` (not clarification markers):** how `hs config set` finds a path's kind
  (a path lookup beside the variable matcher in `core/config_schema.py`, or the matcher fed the
  path); the refusal's message text; how `ClusterApp` reads `server.evict` before launch (the
  existing `config_seconds` helper is the likely route); where `ServerApp`/`ClusterApp` stop the
  `store_true` default from replacing the key.

## Related materials

- Seeds: [`issues/config-set-coerces-text.md`](../../issues/config-set-coerces-text.md),
  [`issues/config-keys-unread.md`](../../issues/config-keys-unread.md).
- Prior art: `spec/env-config-keys/` (#113: `KEYS`, the kind readers, the guide's typing rule; its
  GOAL Non-goals name `hs config set`'s typing; its research `03-duration-keys.md` and
  `04-typed-readers.md` found the unread keys).
- Source: `src/hypershell/config.py` (`ConfigSetApp`), `src/hypershell/core/types.py`
  (`smart_coerce`), `src/hypershell/core/config_schema.py` (`KEYS`, `TABLES`, `ZONE_KEYS`,
  `read_value`, `read_untyped`, `match_name`), `src/hypershell/core/config.py` (`update`,
  `config_seconds`, `resolve_auth`), `src/hypershell/server.py` (`ServerApp`, `HeartMonitor`),
  `src/hypershell/cluster/__init__.py` (`ClusterApp`), `src/hypershell/cluster/local.py`,
  `remote.py`, `ssh.py`, `launch.py`, `src/hypershell/client.py` (`ClientApp`, `-R`).
- Docs: `docs/_include/config_set_desc.rst`, `config_set_help.rst`, `config_intro.rst`,
  `config_param_ref.rst`, `cluster_help.rst`, `client_help.rst`, `server_help.rst`.
