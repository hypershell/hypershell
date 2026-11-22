# PLAN — `hs config set` writes each key by its type, and `hs server` and `hs cluster` read the keys they document

> **Status:** Draft for review · **Last updated:** 2026-11-21
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID. Test prototypes: [`research/prototypes/`](research/prototypes/),
> dry-run on `d9901cf0` (99 of 99 red) and on a scratch copy of `src` with this design applied (all
> 162 pass; the whole suite passes).

## 1. Summary

`hs config set` stops guessing a value's type from its look: it finds the key's kind with a new
`match_path()` beside the environment's matcher in `core/config_schema.py` and reads VALUE with the same
`read_value()` the environment layer uses, refusing a value that does not convert with exit 2 before
anything is written. `hs server` and `hs cluster` stop letting `--eager`'s parsed `False` replace
`server.eager`; `hs cluster` takes `-r` from `server.attempts`, `server.evict` from the same start check
`hs server` has (with the floor moved into one shared helper so it refuses before any bind), and `-R`
from `client.ratelimit`, which `-R` on both commands now takes as a number. Each fix reuses what #113
built or what `hs server` already does; nothing new is configured and nothing on the wire changes.
The appetite is `small`, but the edits reach `server.py`, `client.py`, `cluster/remote.py` and
`cluster/ssh.py`, so the full research fan-out ran (high-blast-radius exception) and every contract it
touches is pinned below.

## 2. Design

### 2.1 `hs config set` (R1–R4) — `core/config_schema.py`, `config.py`

- **`match_path(path: str) -> Kind`**, new in `core/config_schema.py`, after `match_name` (before
  `_set`), exported in `__all__`. The dotted path is matched **as written** (no case folding, no
  `_`/`.` confusion), in order:
  1. a documented key: `KEYS[path]` (so `logging.file` is `LOGGING_FILE`, `relay.min` `INT_OR_NONE`,
     `ssh.nodelist` and `database` as scalars `TEXT`);
  2. a documented key plus `_env`/`_eval` (`VARIANTS`): `TEXT`;
  3. one entry past an `[export]` table, or past `ssh.nodelist`: `TEXT`;
  4. `relay.zone.<name>.<key>`: `ZONE_KEYS[key]` (`launcher`/`launcher_args` text, `size/min/max/init`
     whole numbers), an unknown zone key `UNTYPED`;
  5. everything else `UNTYPED`: entries of `database.connect_args`, `database.pragmas`, `[database]`
     provider arguments, unknown entries of closed tables, unknown sections, a path below a key
     (`server.port.x`) or deeper than one entry, and a differently-cased documented path
     (`Server.eager`).
  A key added to `KEYS` later (for instance `relay.advertise`, #116) is picked up with no edit here.
- **`read_value(Kind.LOGGING_FILE, '')` refuses** (`text != ''` on the path branch). The environment
  layer never reaches that case (`logging.file` is in `EMPTY_UNSET`, skipped before `read_value`), so
  its behaviour is unchanged; R2 asks `hs config set logging.file ''` to be refused.
- **`ConfigSetApp`** (`config.py`): `value` loses `type=smart_coerce` (a plain `str`); `run()` keeps the
  one-word check ("Missing section in variable path", exit 2, unchanged), then
  `value = read_value(match_path(self.varpath), self.value)`, converting a `ValueError` into
  `cmdkit.cli.ArgumentError(f'{self.varpath}: expected {error}, given {self.value!r}')` — exit 2
  (`exit_status.bad_argument`) with the message alone, no traceback, ruled at the GOAL gate — and only
  then builds the table and calls `update()`. `update()` takes its backup (or writes a new file's
  header) before it assigns, so refusing first leaves the file, its backups and `Logs/` exactly as they
  were. The written value is whatever `read_value` returns: text as `str`, numbers as `int`/`float`,
  booleans as `bool`, durations and sizes as a number when VALUE is a finite number and the text
  otherwise (R3, checked later by the reader). `read_value` never returns `None`, so tomlkit's
  `ConvertError` on `null`/`none` (exit 5 with a traceback today) is gone. The import of `smart_coerce`
  leaves `config.py`; the function stays in `core/types.py` (`task.py`, `core/template.py`,
  `core/tag.py` use it). Anchor the edit in `ConfigSetApp.run`: `ConfigGetApp.run` has the same
  `split('.')` line.
- Messages (`read_value`'s `FORMS`): `server.port: expected a whole number, given 'abc'`;
  `server.eager: expected a boolean (true/false, yes/no, on/off, 1/0), given 'maybe'`;
  `relay.min: expected a whole number or <none>, given 'none'`;
  `logging.file: expected true, enabled, or a path, given 'false'`.

### 2.2 `server.eager` (R5) — `server.py`, `cluster/__init__.py`

- Cause: cmdkit's `from_cmdline` sets every parsed name on the app, and `store_true` defaults to
  `False`, replacing the class attribute `eager_mode = config.server.eager` in both apps.
  Fix: `interface.add_argument('--eager', action='store_true', dest='eager_mode', default=eager_mode)`
  in `ServerApp` and `ClusterApp`. `--eager` still forces `True`; `HYPERSHELL_SERVER_EAGER=false`
  overrides a file's `true` for one run (the Non-goals' way off).
- **The value is read as a boolean at each app's start check**, before anything binds:
  `configured_eager(value) -> bool`, new in `server.py` beside `DEFAULT_EAGER_MODE`, returns a `bool`
  as is and otherwise `read_value(Kind.BOOL, str(value))`, raising `ConfigurationError`
  (`server.eager: expected a boolean (...), given <value!r> (from: <file or variable>)`, via `blame`)
  — exit 3, the status every configuration refusal has. Called as
  `self.eager_mode = configured_eager(self.eager_mode)` in `ServerApp.check_args` (beside the
  `server.evict` read) and in `ClusterApp.check_arguments` (after the `autoscale.period` block). Never
  at import: a raise there would stop `hs config set`, the command that repairs the file. So a file's
  `eager = "no"` (what today's `hs config set server.eager no` writes, and files are not rewritten)
  reads as off, `"yes"`/`1` as on, and `"maybe"`/`2.5` is refused. *Sign-off item S1.*
- Every mode then sees the value through `ClusterApp.run`'s single call (`eager=self.eager_mode`):
  `LocalCluster`, `RemoteCluster` (`--launcher`, `--mpi`), `SSHCluster`, `AutoScalingCluster` and its
  `AutoScalerThread`; `hs server --autoscaling` through `ServerApp`'s attributes. No further edit.

### 2.3 `server.attempts` under `hs cluster` (R6) — `cluster/__init__.py`

`max_retries: int = config.server.attempts - 1` (was `DEFAULT_ATTEMPTS - 1`), as `ServerApp`. `-r`
still overrides. The `-r` help keeps printing the built-in default, as `hs server`'s and every other
option's help does (the convention: help shows `default.*`, the option reads `config.*`).
`ServerThread` keeps `attempts=max_retries + 1` (invariant §3). With `--no-db` and `attempts > 1`,
`hs cluster` now logs "Retries disabled when database disabled", as `hs server` already does.

### 2.4 `server.evict` under `hs cluster` (R7) — `server.py`, `cluster/__init__.py`, `cluster/local.py`, `cluster/remote.py`, `cluster/ssh.py`

- **One floor, one message.** New in `server.py` beside `DEFAULT_EVICT`: `EVICT_MINIMUM = 10` and
  `check_evict(seconds) -> seconds`, raising
  `RuntimeError(f'Evict period must be greater than {EVICT_MINIMUM} seconds: given {seconds}')` when
  `not seconds >= EVICT_MINIMUM` (so `nan` is refused and `10` accepted, as today), and
  `RuntimeError(f'Evict period must be a finite number of seconds: given {seconds}')` for an infinite
  period (today a TOML `inf` crashes `HeartMonitor` with an `OverflowError` traceback, exit 5; *sign-off
  item S2*). `HeartMonitor.__init__` calls it in place of its inline check, so library callers keep the
  same refusal.
- **Read and refused at the start check.** `ClusterApp` gains `evict_after: int | float = DEFAULT_EVICT`
  and, in `check_arguments` right after the `autoscale.period` block (the first thing `__enter__` does,
  before `enable_tls`, `initdb`/`checkdb` and the `find_available_ports` probe bind):
  `self.evict_after = check_evict(config_seconds('server', 'evict'))`. `ServerApp.check_args` wraps its
  existing read the same way. So both commands refuse a non-duration with the `config_seconds`
  `ConfigurationError` (exit 3) and a short period with the floor's `RuntimeError` (exit 5, the shared
  exception mapping), with the same text; only the log tag differs (`[hypershell.cluster]`).
- **Plumbing.** `ClusterApp.run` passes `evict_after=self.evict_after` in its one launcher call;
  `run_local`/`run_cluster`/`run_ssh` forward `**options` unchanged. `LocalCluster`, `RemoteCluster` and
  `SSHCluster` each gain a last keyword `evict_after: int = DEFAULT_EVICT` passed to their
  `ServerThread(...)` (`DEFAULT_EVICT` imported in `local.py` and `ssh.py`; `remote.py` has it) and one
  docstring Args entry. `AutoScalingCluster` already takes and passes it. The constructors take the
  period and never read configuration (as for `advertise=`, invariant §11). `client_launch()` is
  untouched.

### 2.5 `client.ratelimit` under `hs cluster`, and `-R` (R8) — `core/types.py`, `client.py`, `cluster/__init__.py`, `cluster/launch.py`

- **`parse_rate(value) -> int | float`**, new in `core/types.py` beside `parse_period`, over
  `core/config_schema.read_number` (top-level import; `config_schema` imports only the standard
  library): a whole or decimal number, finite; a whole word stays an `int` (`-R 5` holds `5`, as
  today); `inf`, `nan`, `1e400`, `abc` and an empty word raise `UnitError` (an `ArgumentTypeError`),
  so argparse exits 2 naming `-R/--ratelimit`, as today's `int` does. Negatives are accepted, as today
  (they mean no limit in `wait_ratelimit`; refusing them would be a new check, Non-goals). `type=parse_rate`
  on `-R` in `ClientApp` and `ClusterApp`.
- `ClusterApp.ratelimit = config.client.ratelimit or None` (was `config.task.get('ratelimit', None)`):
  `0` or unset is no limit, as for `hs client`; `task.ratelimit` is no longer read (no fallback, ruled).
  Local mode hands the rate to its in-process `ClientThread`, which already divides it per thread as a
  float; every other mode goes through `client_launch()`.
- **`plain_rate(value) -> str`**, new in `cluster/launch.py` beside `plain_seconds`: a whole rate as an
  integer word (`5`, `5.0` → `'5'`; `0` → `'0'`), any other as a plain decimal through `plain_seconds`
  (`0.5`, `1e-05` → `'0.00001'`), which `parse_rate` reads back exactly. `client_options` emits
  `['-R', plain_rate(ratelimit)]` under the existing `if ratelimit is not None` (an explicit `-R 0` is
  still passed on and still overrides a client's own key). A whole rate keeps a 2.9.0a4 client working;
  a fractional one needs a client of this release (§3 deviation, *sign-off item S3*).
- Type annotations and docstrings that say the rate is an `int` say `float`: `client.py`
  (`TaskExecutor.ratelimit`, the `ClientThread`/`run_client` docstrings and signatures — not lines
  1312–1314, see §5), `cluster/__init__.py`, `cluster/launch.py`, `cluster/local.py`,
  `cluster/remote.py`, `cluster/ssh.py`.

### 2.6 Documentation and release note (R9)

- **`hs config set`:** `SET_HELP`'s `VALUE` line and `docs/_include/config_set_help.rst` say the value
  is written by the key's type; `docs/_include/config_set_desc.rst` gains a short paragraph stating the
  rule and pointing at the guide with `:ref:`by the key's type <config_value_types>`` (explicit text,
  required for a label without a title); the label `.. _config_value_types:` goes on its own line before
  `docs/_include/config_intro.rst`'s "The value is read by the key's type:" (line 142); and
  `docs/cli/config_set.rst` gets `.. versionchanged:: 2.9.0` between the `|` and the help include. The
  page says once that a refused value exits 2 and writes nothing, and that a text key's `""` is written
  (and so overrides a lower file, unlike an empty variable).
- **Parameter reference** (`docs/_include/config_param_ref.rst`, the house `.. only:: html` block):
  `.attempts` and `.eager` name `hs server` and `hs cluster` (and `.eager` the boolean words a file's
  text takes); `.evict`'s sentence becomes "``hs server``, ``hs cluster`` and ``hs relay`` stop before
  they start if the value is not a duration; ``hs server`` and ``hs cluster`` also if it is under 10
  seconds" and its `only` block gains a third marker; `[client] .ratelimit` names `hs client` and
  `hs cluster`, "a number (e.g., ``0.5``)". Never a body line holding only a ``` ``.name`` ``` term
  (`tests/test_config_schema.py` would parse it as an entry).
- **`-R` help:** `client.py` and `cluster/__init__.py` `HELP` and `docs/_include/client_help.rst`
  (155–157), `cluster_help.rst` (358–360): "a number, e.g. 0.5". The `-r`/`--eager` help is unchanged.
- **Markers** on `docs/cli/client.rst` (`-R`), `docs/cli/server.rst` (`server.eager`) and
  `docs/cli/cluster.rst` (`server.eager`, `server.attempts`, `server.evict`, `client.ratelimit`, `-R`),
  placed **above** the existing 2.9.0 markers (after client :28, server :33, cluster :64), not before
  the help include where #115 adds its own.
- **AGENTS.md / invariants lockstep:** AGENTS.md § Configuration and invariants §10 say `hs config set`
  writes a value by its key's kind (`match_path` + `read_value`, exit 2 on refusal, nothing written);
  AGENTS.md's `client_launch` paragraph and invariants §11 say a whole rate is emitted as an integer
  word and a fractional `-R` needs a client of this release (the sanctioned bend).
- **No `share/` change:** completions give `-R` free text and `hs config set` values no types; man
  pages are rebuilt at release. Docs name top-level commands only.
- **Release note** (drafted by `hs-publish` from GOAL's eight changes) adds, per the sign-offs: under
  change 4, a `server.eager` that is no boolean word now stops `hs server`/`hs cluster` with exit 3
  (S1); under change 6, an infinite `server.evict` is refused cleanly by both (S2); under change 8, a
  fractional rate needs clients of 2.9.0 or later on the launched hosts (S3).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `match_path` → `TEXT` for text keys, `_env`/`_eval` variants, `[export]`, `ssh.nodelist` groups, zone `launcher`/`launcher_args`; `read_value(TEXT)` keeps VALUE; `smart_coerce` removed from `ConfigSetApp` (§2.1) |
| R2 | `match_path` → `BOOL`/`INT`/`NUMBER`/`INT_OR_NONE`/zone sizes/`LOGGING_FILE`; `read_value` converts or raises; `ArgumentError` exit 2 before `update()`; empty `logging.file` refused in `read_value` (§2.1) |
| R3 | `read_value(DURATION/SIZE)`: a finite number as a number, else the text (§2.1) |
| R4 | `match_path` → `UNTYPED`; `read_untyped`'s TOML-scalar rule; no `None` reaches tomlkit (§2.1) |
| R5 | `--eager` `default=eager_mode` in both apps; `configured_eager` at each start check (§2.2) |
| R6 | `ClusterApp.max_retries = config.server.attempts - 1` (§2.3) |
| R7 | `check_evict` shared floor; `ClusterApp.check_arguments` reads and refuses first; `evict_after` through `ClusterApp.run` to `LocalCluster`/`RemoteCluster`/`SSHCluster`/`AutoScalingCluster` (§2.4) |
| R8 | `ClusterApp.ratelimit = config.client.ratelimit or None`; `parse_rate` on `-R` of both commands; `plain_rate` launch word (§2.5) |
| R9 | `hs config set` page and guide label; reference entries; `-R` help; markers; AGENTS.md/invariants lockstep; release-note clauses (§2.6) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(gate #1: §3, §4, §10, §11, §12 touched; the GOAL is sane against each) and again on this design
(gate #2):

- **§3 Retry model** — `attempts == max_retries + 1` is kept: R6 changes only where `hs cluster`'s
  `max_retries` default comes from; `ServerThread` and `AutoScalingCluster` still add one. No query
  changes; the `CANCEL_STATUS` filters are untouched. Eager mode changes which failed tasks run first,
  through the existing `Task.next(eager=...)` path `--eager` already takes.
- **§4 Server modes** — `in_memory` has no scheduler, so eager and retries stay inert there; with
  `--no-db` and `attempts > 1` both commands log the existing "Retries disabled" warning.
- **§5/§6 Concurrency and shutdown** — no FSM state, thread or ordering changes; `HeartMonitor` keeps
  its timing (`wait_check`, `evict_after`) and only moves its check into `check_evict`.
- **§9 Security** — no key handling changes. A key written by `hs config set` is now kept as text, so an
  all-digit key keeps its leading zeros and its length (R1); `AUTH_MINIMUM_LENGTH` and
  `AUTH_ALLOWED_CHARS` are enforced as before. Tests use `HYPERSHELL_SERVER_AUTH`, never a key option.
- **§10 Configuration** — `config` stays built once at import; `hs config set` still only writes disk.
  `hs config set` now reads a value through `core/config_schema.py` exactly as the environment layer
  does (`read_value`), with the path matched as written; its refusal is exit 2 (a command-line value),
  the environment's stays exit 3. No new key, so `KEYS` and the reference stay in step. The new start
  checks (`configured_eager`, `check_evict`) run in `check_args`/`check_arguments`, never at import.
- **§11 Cluster orchestration** — `client_launch()` stays the one builder; only the `-R` word's
  formatting changes, in `client_options`. Constructors take `evict_after=` and never read
  configuration. **Bend:** a fractional `-R` is a value a 2.9.0a4 client refuses (see below).
- **§12 Conventions** — help snippets change with their option in the same phase as the docs (P5;
  see the deviation row); markers `versionchanged:: 2.9.0`; tests tagged; `exit_status` constants only.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| §11: `client_options` may emit `-R <fraction>`, which a 2.9.0a4 client refuses (its `-R` is `type=int`). The contract test checks option names only, so it still passes. A whole rate is emitted as an integer word (`plain_rate`), so every configuration that works with 2.9.0a4 clients today keeps working, except a fractional `client.ratelimit` read by 2.9.0a4 clients from their own configuration (today `hs cluster` passes no `-R`, so they run it; after R8 they are handed `-R 0.5` and exit 2). | R8 (ruled at the GOAL gate): `-R` takes a number, and `hs cluster` hands each client the rate it read. | Refusing a fraction at `hs cluster` start contradicts the key's type and was ruled out at the gate; omitting `-R` when the rate came from the key (letting each client read its own) would break R8 for launched clients whose configuration differs from the server host's, and adds a second precedence path. Blast radius: 2.9.0a4 is a pre-release; the release note names the requirement. |
| §12: the `-R` option's type and help text change in different phases (P4 code, P5 help snippets), as do the `hs config set` behaviour (P2) and its help line (P5). | Keeps each code phase a vertical slice with its own verify, and puts every docs edit, the guide label and the marker placement (#115 overlap) in one reviewed phase. | Editing help in each code phase spreads the #115-sensitive docs edits over three commits; the branch squashes at publish, so the released commit carries both together. |

## 4. Rabbit holes (resolved)

- *How does `hs config set` find a path's kind without new spelling rules?* → `match_path`, matched as
  written; `match_name` folds case and splits at `_` ([`01`](research/01-config-set-typing.md) §1).
- *Can a refusal leave the file untouched?* → yes, only if it precedes `update()`, which backs up first
  (01 §3). Exit 2 comes free from cmdkit's `ArgumentError` handling (01 §2).
- *Why is `server.eager` never read?* → `store_true`'s `False` default replaces the class attribute
  ([`02`](research/02-eager-attempts.md) Q1). A file text `"no"` would read as true once fixed → read
  by the boolean words at the start check (02, option C).
- *Is `hs server`'s evict floor before its bind, and can `hs cluster` match it?* → `hs server` refuses
  in `HeartMonitor.__init__`, before its queue binds; `hs cluster` would only reach that after `initdb`
  and its port probe, so the floor moves into `check_evict` at its start check
  ([`03`](research/03-cluster-evict.md) §1–2).
- *Does every cluster mode reach a `ServerThread` that can take the period?* → three constructors gain
  one keyword; autoscaling already has it (03 §3).
- *Can `-R` take a fraction without breaking older launched clients?* → whole rates as integer words;
  fractions need this release; the contract test checks names only ([`04`](research/04-ratelimit.md) §2).
- *Where do #115 and #116 collide?* → nowhere if the build keeps off the listed lines
  ([`05`](research/05-docs-conflicts.md) Part B; §5 below).

## 5. Risks & open questions

**Foreman sign-off (decisions this plan makes beyond the GOAL's rulings):**
- **S1 — `server.eager` read by the boolean words, refused otherwise (exit 3)** at each start check
  (§2.2). Alternative: plain truthiness, which turns an existing `eager = "no"` into eager mode on
  upgrade.
- **S2 — `check_evict` also refuses an infinite `server.evict`** (§2.4), cleanly, exit 5, in `hs server`
  as well (today a traceback, exit 5). Alternative: leave `inf` to fail after `hs cluster`'s database
  and port probe, against R7's "before it binds".
- **S3 — the §11 bend** (§3): a fractional rate needs launched clients of 2.9.0 or later.
- **S4 — test markers:** child-interpreter and CLI tests are `@mark.integration`, as every such test in
  `test_env_config.py`/`test_duration_options.py` is; in-process option and launch checks `@mark.unit`.
- **S5 — one docs phase** for help snippets and markers (§3 deviation row 2).

**In flight:**
- **#116 landed on `develop` during planning** (`dfdbaed5`). It adds `relay.advertise` to `KEYS`, which
  `match_path` reads with no edit. No textual overlap with this design (05 Part B); the branch stays on
  `d9901cf0` through build (the red list's base) and is brought up to `develop` at publish.
- **#115 (`fix/relay-shutdown-tail`)** — expected clean if the build does not edit `client.py`
  1312–1314 (the per-thread rate division; it needs no change), `cluster/__init__.py` 86–88, `server.py`
  1213–1215, `config_param_ref.rst` 300–302, `cluster_help.rst` 127, `server_help.rst` 62; and puts the
  `docs/cli/{client,server,cluster}.rst` markers above the existing ones. If a marker lands next to
  #115's, the conflict is two independent marker blocks: keep both.

**Residual risks:**
- `hs cluster` starts logging "Retries disabled when database disabled" for `--no-db` runs whose
  configuration has `attempts > 1` (consistent with `hs server`; noted in change 5's "with a database").
- A test asserting today's `hs cluster` default `-R None` with `task.ratelimit` set does not exist
  (searched); the whole-suite phase is the check.
- Found, not taken (candidate seeds for the foreman): the cluster classes' `max_retries` defaults
  disagree with `ServerThread`'s; a text `attempts = "3"` crashes every command at import; integers
  beyond 64 bits are written to TOML by `hs config set` and the environment alike; the `--eager` help
  says "greater than one" where `-r` must be greater than zero.

## 6. Verification strategy

- **Tests first (P1):** three new files from [`research/prototypes/`](research/prototypes/):
  `tests/test_config_set_types.py` (R1–R4, 63 tests, `hs config set` in a child process, the file read
  back with `tomllib`), `tests/test_config_unread.py` (R5, R6, R8, 57 tests: child-interpreter readings
  of both apps, `-R` parsing and `client_options` words in process), `tests/test_cluster_evict.py` (R7,
  42 tests: `hs cluster` in a child whose `ServerThread` records its period and stops, and whose
  `initdb`/`checkdb`/`find_available_ports` are recorded, so nothing binds or launches; plus real
  bounded `hs server`/`hs cluster` refusals). 99 are red on `d9901cf0`
  ([`red.txt`](red.txt)), checked by `spec/relay-hardening/drive/red_check.sh`; the rest are guards,
  checked with the neighbouring files by `spec/contended-writes/drive/rest_green.sh`.
- **Per phase:** the phase's test files plus the existing files around the code it touches, and a CLI
  drive in a throwaway site (`.agents/factory/bin/temp_site.sh`) where the behaviour is a command's:
  `hs config set` writing a digit key, a typed refusal, `hs cluster` refusing a short `server.evict`.
- **Unit table (P2, not red-listed):** `match_path` over every case in §2.1, appended at the end of
  `tests/test_config_schema.py` (not beside `MATCHES`, which #116 edited), and `logging.file ''` added
  to the `read_value` refusals.
- **Gate (P6):** the whole suite serially under Python 3.14, the red list green, `red_check` still 99 of
  99 red on `d9901cf0`, every drive, the docs build with `-W`.
- **Plan-time dry-runs** (TECH.md "Running the verify commands here" has the numbers): the prototypes
  on `d9901cf0` (99 failed, 63 passed; `red_check` 99 of 99 red in 85 s); on a scratch copy of `src`
  with this design applied, the three files pass (162), the P2–P4 neighbour sets pass, the four drive
  modes pass (and fail on `d9901cf0`, except `eager`'s first check, a guard), and the whole suite passes
  serially (3684 passed, 138 skipped, 27 min; on `d9901cf0` without the new files 3522 passed, 138
  skipped). The key docs edits (the label and its `:ref:`, markers) were applied to a scratch copy of
  `docs/` and built with `-W`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-21)

Foreman, gate judge under the maintainer's mandate. Approved as planned, with S1–S5 accepted.

- **S1:** `server.eager` is read by the boolean words, and anything else is refused with exit 3. A
  2.8 file's `eager = "no"` therefore stays off. The release note says so.
- **S2:** `check_evict` refuses an infinite `server.evict` with a clean exit 5 in `hs server` and
  `hs cluster`.
- **S3:** the invariant §11 bend is accepted. A fractional `-R` is sent to launched clients, and
  whole rates stay integer words. Only a 2.9.0a4-era client reading a fractional rate refuses it, a
  prerelease-only gap. It is recorded as a deviation and as a release-note clause.
- **S4:** child-interpreter and CLI tests are `@mark.integration`, in-process tests `@mark.unit`.
- **S5:** one docs phase (P5).
- **Base:** the branch builds on `d9901cf0` (the red list's base) and merges `develop` at publish. By
  then `develop` holds #115 and #116; keep off the fenced lines as TECH says.
- **Candidate seeds** from research are filed by the foreman: the cluster classes' `max_retries`
  defaults, a text `attempts` crashing at import, integers wider than 64 bits written to TOML, and
  the `--eager` help wording.
