# PLAN — `hs cluster --no-db` leaves the database alone, and an autoscaling period of zero or less is refused

> **Status:** Draft for review · **Last updated:** 2026-11-15
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Two start-up edits. `ClusterApp.__enter__` makes no database call at all under `--no-db`, as
`ServerApp.check_database` does since #105. And one function, `parse_period`, beside `parse_time`
in `core/types.py`, reads a period the way argparse already treats `-P` and its key (a string in the
duration forms, rounded up; a number as given) and refuses one that is not greater than zero. `-P`
on `hs cluster` and `hs server` takes it as its `type=`, which covers the option and a key string;
each app's argument check runs it once more on the parsed value, which catches a key number or
environment number; and `RelayApp.site_period` calls it in place of its own string/number branch.
The autoscalers are not touched. Small: three source files' start-up code, the relay's one
property, help text, completions and docs; tests first.

## 2. Design

### `hs cluster --no-db` (R1, R2)

`src/hypershell/cluster/__init__.py`, `ClusterApp.__enter__`: wrap the existing database step in
`if not self.in_memory:`, keeping its two branches as they are:

```python
if not self.in_memory:
    if DATABASE_DIALECT == 'sqlite' or self.auto_initdb:
        initdb()  # Auto-initialize if local sqlite provider
    else:
        checkdb()
```

- `--no-db` → no call on any provider: nothing creates, initializes, upgrades or checks the
  configured file or server, and an unopenable path is never reached (R1). Mode-independent: the
  step runs before `run()` picks local, `--launcher`, `--mpi` or `--ssh`. The probe in
  [01 §1](research/01-start-up-seams.md) shows nothing later opens the database under `--no-db`.
- Without `--no-db`, the branches are today's: SQLite or `--initdb` → `initdb()`, else `checkdb()`
  (`DatabaseUninitialized` → today's message and exit status) (R2). `--initdb` and `--no-db` stay
  mutually exclusive at parse.
- No database configured (`:memory:`) without `--no-db`: unchanged, `initdb()` on the in-memory
  engine. With `--no-db`: no call, and the server runs in memory as before.
- Optional, the builder's call: name the method body as a `check_database()` method mirroring
  `ServerApp.check_database` (one line docstring: "never open it with --no-db"). Not required.

### The period floor (R3–R6)

**`src/hypershell/core/types.py`** — new public `parse_period` (add to `__all__`):

```python
def parse_period(value: str | int | float) -> int | float:
    """Seconds in a scaling period: a string as parse_time reads it, a number as given; it must be greater than zero."""
    seconds = parse_time(value) if isinstance(value, str) else value
    if not seconds > 0:
        raise UnitError(f'Period {value!r} is not valid: must be greater than zero')
    return seconds
```

- A string keeps `parse_time`'s forms, its rounding up (`'0.5'` → 1) and its `Duration … is not
  valid: expected …` error for a bad form or a sign (#106 unchanged). Then the floor.
- A number keeps its value and type (#106 R5: `0.5` stays `0.5`); only the floor applies.
- `not seconds > 0` also refuses `nan` (the loader reads `HYPERSHELL_AUTOSCALE_PERIOD=nan` as a
  float), which is "not greater than zero" by R4's wording. Infinity and non-numeric TOML values
  are outside the GOAL (no maximum) and keep today's behaviour.
- The error is a `UnitError` (`ValueError` + `ArgumentTypeError`), so argparse shows it under the
  option's name and callers wrap it with the key's name, exactly as #106 does for a bad duration.

**Wording** (R3–R5), by route:

| Route | Where it is caught | Message (exit 2) |
|---|---|---|
| `-P 0s` / `--period=0:00` | argparse `type=` | `argument -P/--period: Period '0s' is not valid: must be greater than zero` |
| `period = "0s"`, `HYPERSHELL_AUTOSCALE_PERIOD=0s` | argparse, string default through `type=` | same, naming `-P/--period` (as #106 reports `"5MB"`) |
| `period = 0`, `= -5`, `HYPERSHELL_AUTOSCALE_PERIOD=0` / `=-5` | the app's argument check | `autoscale.period: Period 0 is not valid: must be greater than zero` |
| `hs relay`, any of the above in the key | `RelayApp.check_args` → `site_period` | `autoscale.period: Period '0s' is not valid: must be greater than zero` |

**`src/hypershell/cluster/__init__.py`** (`hs cluster`, `hsx`):

- `-P/--period`: `type=parse_period` in place of `type=parse_time` (import it).
- `check_arguments()`: first statement (every launch, autoscaling or not, as ruled at the gate):
  run `parse_period(self.autoscaling_period)`; on `UnitError` raise
  `ArgumentError(f'autoscale.period: {error}')`. Only a number default can fail here: anything from
  the command line or a string default has already passed the `type=`, so the key is the right
  name. `check_arguments()` is the first thing `__enter__` does, before TLS, the database, the port
  and any launch.
- `APP_HELP` `-P` line: `Scaling period, greater than zero; seconds, 30s, 5m or [D-]HH:MM:SS
  (default: …)`.

**`src/hypershell/server.py`** (`hs server`) — argument handling only:

- `-P/--period`: `type=parse_period` (import it).
- `check_args()`: the same check and `ArgumentError` as the cluster's, at the top, unconditionally.
  `run()` calls `check_args()` before `enable_tls()`, `check_database()` and any `serve_*`.
- `APP_HELP` `-P` line as for the cluster.

**`src/hypershell/relay/__init__.py`** (`hs relay`):

- `RelayApp.site_period`: `return parse_period(config.autoscale.period)`, wrapping `UnitError` in
  `ArgumentError(f'autoscale.period: {error}')` as today. Its string/number rule moves into
  `parse_period` unchanged, so `'5m'` → 300 (int) and `0.5` → `0.5` (float) as the existing tests
  pin. `check_args()` already reads it before `run()` binds the relay database, builds
  `RelayThread`, connects upstream or launches a site client; exit status 2, today's for a period
  that is not a duration (R5). Docstring: add "greater than zero".

**Not changed:** `cluster/remote.py` (`AutoScaler`), `cluster/topology.py` (`TopologyPublisher`),
`relay/site.py` (`SiteScaler`, `DEFAULT_SITE_PERIOD`). Their `period=` keeps its Python API (GOAL
non-goal) and their one-second wait granularity (R6). Every command route to them is refused
upstream, so a guard there would be a second, ungraded copy of the rule on high-blast-radius files.

### Documentation (R7)

Generated help snippets are never hand-marked; markers go beside the `.. include::` (`AGENTS.md`
§ Docs). `docs/_include/config_param_ref.rst` carries its own markers.

- `docs/_include/cluster_help.rst`: `--no-db` body gains "The configured database is not opened
  at all, so it need not exist or be initialized." (the server's sentence); `-P` summary line
  `Scaling period, greater than zero (default: 60).` and a sentence in its body: "A period of zero
  or less, here or in ``autoscale.period``, is refused."
- `docs/_include/server_help.rst`: the same `-P` summary and sentence.
- `docs/_include/config_param_ref.rst` `[autoscale] .period`: say the period must be greater than
  zero and that `hs cluster`, `hs server` and `hs relay` each refuse zero or a negative value at
  start-up, a number below one second included as given (waits are a second at a time); add a
  second `.. versionchanged:: 2.9.0` ("A period of zero or less is refused.") in the existing
  `.. only:: html` block, after the #106 marker.
- `docs/cli/cluster.rst`: two markers before the include, after the existing 2.9.0 ones —
  "``--no-db`` leaves the configured database alone: it is not opened, created or initialized."
  and "``-P``/``--period`` and ``autoscale.period`` must be greater than zero; zero or a negative
  value is refused."
- `docs/cli/server.rst`: the period marker likewise.
- `share/zsh/site-functions/_hs`: both `-P` descriptions (`:563`, `:633`) gain ", greater than
  zero" after "Scaling period" (§12: completions in the same commit). Bash completions carry no
  descriptions; their `-P` candidates (`30 60 300 600 5m 10m`) are all valid.
- Not touched: `share/man/` (`hs-release`), `docs/manual.rst` (includes the snippets; #106 added no
  marker there either).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `if not self.in_memory:` around `ClusterApp.__enter__`'s database step; mode-independent (before `run()`) |
| R2   | The step's two branches unchanged without `--no-db`; `--initdb`/`--no-db` exclusive at parse |
| R3   | `parse_period` as `-P`'s `type=` on `ClusterApp` and `ServerApp`; argparse names `-P/--period` and the value; exit 2 before `__enter__`/`run()` go further |
| R4   | String key → argparse default through `parse_period`; number key/env → `parse_period(self.autoscaling_period)` first in `check_arguments()`/`check_args()`, `ArgumentError('autoscale.period: …')` |
| R5   | `RelayApp.site_period` → `parse_period`, `ArgumentError('autoscale.period: …')` from `check_args()` before `run()` does anything; exit 2 |
| R6   | `parse_period` returns `parse_time`'s value for strings (rounded up) and the number as given; autoscalers untouched |
| R7   | Help lines, `_include` snippets, `config_param_ref.rst`, `cli/cluster.rst`/`cli/server.rst` markers, zsh descriptions |
| R8   | `tests/test_cluster_no_db.py`, `tests/test_period_floor.py` (P1, red on `90ab2fc2`); `test_duration_options.py`'s `-P 0` case moves to the refusals |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (before research) found the change sane against §4, §10, §11, §12 and the high-blast-radius
list (`server.py` is edited; `cluster/remote.py` is named by the seed). Gate #2 on this design:

- **§4 Server modes** — `--no-db` already means "no database"; the cluster now honours it at
  start-up as the server does. The server threads' `in_memory` guards are unchanged; nothing new
  writes under `--no-db`.
- **§10 Configuration** — the singleton is read, never mutated; the string/number rule for the key
  is #106's, now in one function. `0` never meant "unlimited/auto" for the period (the `X or None`
  idiom does not apply to it; shaping found no test, example or doc using it).
- **§11 Cluster orchestration** — `client_launch()`, the autoscaler, the publisher and the relay's
  fleets are untouched; `hs server --autoscaling` stays refused with `--no-db`. The relay's period
  is still the one `SiteScaler` gets.
- **§12 Conventions** — help snippets and completions in the same commit as the CLI change; prose
  markers `.. versionchanged:: 2.9.0` beside the includes and in the config reference; new tests
  tagged `unit`/`integration`; exit status through `ArgumentError` (cmdkit `bad_argument`), no
  literals; docs build with `-W`.
- **High-blast-radius** — `server.py`: only the `-P` declaration, the help string and one check at
  the top of `ServerApp.check_args()`; no FSM, thread, queue or database code. `cluster/remote.py`:
  not edited.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

- *Does anything besides the start-up call open the database under `hs cluster --no-db`?* → No:
  with the call stubbed out, local and launcher runs leave no file and run under a missing
  directory ([01 §1](research/01-start-up-seams.md)).
- *Which routes reach `-P`, and which are numbers?* → Option text and key strings pass `type=`; the
  loader reads `0`, `-5`, `0.5`, `nan` as numbers that skip it, so a post-parse check is needed
  besides the `type=` ([01 §2](research/01-start-up-seams.md)). Checking only post-parse would lose
  the value as written (`-P 0s` would report `0`), which R3 asks for.
- *Floor in the autoscalers instead?* → Rejected: it would edit `cluster/remote.py` and
  `cluster/topology.py` (high blast radius), fire only once the run is up (R3–R5 want a refusal
  before anything listens), and miss the "every launch" ruling.
- *Research depth.* `appetite: small`, `kind: fix`, root causes known and reproduced in shaping; the
  high-blast-radius exception fired on `server.py`, so the planner pinned the touched contracts
  itself in one brief ([01](research/01-start-up-seams.md)) rather than a fan-out.

## 5. Risks & open questions

- **Every launch refuses a stored `period = 0`** (ruled at the gate): a site with that value cannot
  start `hs cluster` or `hs server` until it is fixed. The message names the key, so the fix is
  obvious.
- **A string default fails at parse,** so `hs server --available-ports` (shell completion's helper)
  also stops on `period = "0s"`, as it does on `period = "5MB"` since #106. A number default does
  not (its check runs after `special_options`). Accepted: same class as #106.
- **`test_seconds_option_parsed`'s `('0', 0)` case** asserts the old behaviour for `-P`; P1 narrows
  it to the other options and moves `-P 0` into the refusal tests. This is the ruled behaviour
  change, not a regression.
- **Red tests must not hang on the base.** Every period test runs the app in-process (or in a fresh
  interpreter for key defaults) with the serve/launch functions replaced by recorders, never a real
  server that a zero period would leave running. The `--no-db` CLI drives finish on the base (one
  creates the file, one exits 5).
- **Sign-off asked of the foreman:** the wording table above (key number errors name
  `autoscale.period`, key strings name `-P/--period`, as #106 does); refusing `nan`; leaving the
  autoscalers' Python API unguarded.

## 6. Verification strategy

**Tests first (P1, R8), red on `90ab2fc2`, none may hang:**

- `tests/test_cluster_no_db.py`
  - `@mark.unit` matrix `test_cluster_database_step[dialect-mode-flag]`: `ClusterApp.from_cmdline`
    + `__enter__`/`__exit__` with `hypershell.cluster.initdb`/`checkdb` replaced by recorders and
    `hypershell.cluster.DATABASE_DIALECT` set; dialect `sqlite`/`postgres` × mode (local,
    `--launcher env`, `--ssh localhost`) × flag (none, `--initdb`, `--no-db`). Expected:
    sqlite → `[initdb]`, `[initdb]`, `[]`; postgres → `[checkdb]`, `[initdb]`, `[]`. Red: the
    three sqlite `--no-db` rows (`['initdb']`).
  - `@mark.integration` (`temp_site`, real CLI `hs cluster FILE --no-db -N1 -t 'echo {}' --no-tls`):
    `test_no_db_creates_no_file` (no file after, both outputs, exit 0); `test_no_db_leaves_file_unchanged`
    (a pre-existing file of non-database bytes keeps its bytes; the base exits 5 on it);
    `test_no_db_runs_with_unopenable_database` (file under a missing directory: exit 0, both
    outputs, directory still absent; the base exits 5); `test_no_db_runs_with_launcher`
    (`--launcher env -H 127.0.0.1 --remote-exe <the venv's hs>`: exit 0, no file); and the guard
    `test_without_no_db_initializes_kept` (no `--no-db`: `client`, `source`, `task`, `zone`
    tables exist — green on the base).
- `tests/test_period_floor.py`
  - `@mark.unit` `test_period_option_zero_refused[app-value-spelling]`: `ServerApp`/`ClusterApp`
    `interface.parse_args` with `-P v` and `--period=v` for `0 0s 0m 0:00 0-0 0.0` → `ArgumentError`
    naming `-P/--period`, `repr(v)` and `greater than zero`. Red: all.
  - `@mark.integration` `test_period_key_refused[app-route-value]`: a fresh interpreter (as
    `READ_DEFAULTS` in `test_duration_options.py`) that replaces `server.serve_from`,
    `server.serve_forever`, `cluster.topology.serve_with_sites`, `cluster.run_local`,
    `cluster.run_cluster` and `cluster.run_ssh` with recorders, then runs `App.main(argv)`
    (server: `--no-tls FILE --no-db` and `--no-tls --autoscaling fixed`; cluster: `FILE --no-db
    --no-tls`), `HYPERSHELL_SERVER_AUTH` in the environment, never `-k`. Routes: environment
    `0`, `-5`, `0s`; config file `period = 0`, `period = -5`, `period = "0s"`, `period = 0.0`.
    Expect exit `bad_argument`, nothing recorded, stderr naming `-P/--period` or
    `autoscale.period`, the value and `greater than zero`. Red: all (exit 0, a serve recorded).
  - `@mark.integration` `test_period_cli_zero_refused`: real `hs cluster FILE --no-db --no-tls -P 0`
    exits 2 naming the option and the floor, and runs no task (red: exit 0, tasks run; finishes).
  - `@mark.unit` `test_relay_period_zero_refused[value]`: `0`, `0.0`, `-5`, `'0'`, `'0s'`, `'0:00'`
    in `config['autoscale']['period']` → `RelayApp.from_cmdline([]).check_args()` raises
    `ArgumentError` starting `autoscale.period: ` with `repr(value)` and `greater than zero`; and
    `RelayApp.main([])` with `RelayThread` and the start-up helpers stubbed (as
    `test_relay_passes_site_period`) returns `bad_argument` with nothing built. Red: all.
  - Guards, green on the base (`_accepted`): `-P 1` → 1, `-P 0.5` → 1, `--period=1s` → 1 on both
    apps; key `0.5` (env and file) → `0.5` (float) reaching the recorded `serve_with_sites`/`run_cluster`
    `period=`; relay `1`, `0.5`, `'1s'`, `'0.5'` → `1`, `0.5`, `1`, `1`.
- `tests/test_duration_options.py`: `test_seconds_option_parsed` keeps `('0', 0)` for every option
  but `-P` (`ServerApp`/`ClusterApp`), whose zero is now refused (covered above).

**Fix phases** re-run those files plus their neighbours (`test_duration_options.py`,
`test_server_initdb.py`, `test_cluster*.py`, `test_relay*.py`), drive the CLI in a
`temp_site.sh` site, assert `cluster/remote.py`, `cluster/topology.py` and `relay/site.py` are
unchanged since `90ab2fc2`, and build the docs with `-W`. The last phase runs the whole suite
serially (`pytest -n auto` fails collection on this suite: parametrized ids differ between workers);
on the base tree that took 23 minutes, with one load-sensitive SQLite lock test
(`test_waits_for_slow_peer_upgrade`) that passes alone.

Every `verify:` was dry-run on the base tree (2026-11-15): the existing-test, `git diff` and
accepted-period stages pass; each stage that checks the fix fails as expected (the `--no-db`
drive at "no file", `-P 0` at "exit 2", the help, completion and docs greps); P1's red/guard logic
was exercised with throwaway stand-in tests. The docs build needs the sandbox off (the sitemap
extension fails inside it), where `$TMPDIR` is a different directory, so the first run there builds
its own docs environment.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-15)

Foreman, gate judge under the maintainer's mandate. Approved as planned.

- **D1 (wording):** accepted. A key number names `autoscale.period`; a key string names `-P/--period`,
  as #106 does for `"5MB"`.
- **D2 (NaN):** accepted. A NaN period is refused as not greater than zero.
- **D3 (Python API):** accepted. `AutoScaler`, `TopologyPublisher` and `SiteScaler` stay unguarded;
  only the commands and the key are graded.
- **D4 (ordering):** accepted. P2 and P3 are independent slices after P1, run in order.
- **Sibling cycle:** `env-config-keys` is in flight and will give `autoscale.period` a declared type
  from the environment. `check_args()` re-runs `parse_period` on the parsed value, so the floor holds
  whichever type arrives; whichever cycle lands second rebases onto the other.
- **Flaky test:** a `database is locked` failure in `test_upgrade_cli.py` under load is not this
  cycle's; re-run it alone, as TECH says.

## Review-cycle-1 rulings (2026-11-15)

The review's human gate was triggered by F2 (the autoscalers re-check without sleeping for a positive
period shorter than one check, in `cluster/remote.py`). The maintainer ruled in session (2026-11-15):
"Round numbers up to 1 s", and approved proceeding to review cycle 2 and merging on approval.

- **F2.** `parse_period` rounds a positive number below one second up to one second, as `-P` and a
  key string already do. This applies to `autoscale.period` from a file or `HYPERSHELL_AUTOSCALE_PERIOD`,
  on `hs cluster`, `hs server` and `hs relay`. It amends GOAL R6 for this one key: #106 R5 ("a number
  in a configuration file keeps today's meaning") no longer holds for a positive number below one
  second, which now reads as one second. Nothing that starts today is refused. `cluster/remote.py`,
  `cluster/topology.py` and `relay/site.py` stay unchanged (D3 and the sleep-granularity Non-goal
  stand).
  - **Tests:** `period = 0.001`, `period = 0.5` and `HYPERSHELL_AUTOSCALE_PERIOD=0.001` start and
    reach the autoscaler as one second, on all three commands; a number of one or more is unchanged.
    Red on `53f93922`.
- **F1.** `config_param_ref.rst`'s period entry states the rule as built: zero or less is refused;
  a period below one second, in any form, is one second.
- **F3.** `tests/test_period_floor.py` covers NaN, `-inf` and `-0.0` on the key and the environment
  variable, so a floor written `seconds <= 0` would fail.

## Review-cycle-2 ruling (2026-11-15)

Review cycle 2 approved at `77a3af79` with no CONFIRMED and no PLAUSIBLE finding. The maintainer's
approval to merge on approval was given in session with the review-cycle-1 rulings (2026-11-15):
"Yes, proceed and merge".
