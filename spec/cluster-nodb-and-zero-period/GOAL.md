# GOAL — `hs cluster --no-db` leaves the database alone, and an autoscaling period of zero or less is refused

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** cluster-nodb-and-zero-period
- **kind:** fix
- **appetite:** small  ·  *Two `small` seeds promoted together on the foreman's brief
  (Clarifications). Each is local: one start-up branch in `ClusterApp.__enter__`, and one check on
  the period that `hs cluster`, `hs server` and `hs relay` read. Shaping reproduced every defect on
  `develop` at `90ab2fc2`. No new option, configuration key or wire change.*

## Problem

Two start-up gaps, each **pre-existing** on `develop` and on the 2.8 line, that make a run do work
it was told not to do, or never stop doing it.

**`hs cluster --no-db` initializes the SQLite database it never uses.** `--no-db` means the cluster's
server holds every task in memory and never reads or writes the database. On start-up, though,
`ClusterApp.__enter__` (`src/hypershell/cluster/__init__.py:596-603` at `90ab2fc2`) runs
`initdb()` whenever the provider's dialect is SQLite (the `sqlite` and `turso` providers,
`data/__init__.py:53`), before it looks at `--no-db`; only the `checkdb()` branch for PostgreSQL
and TimescaleDB is skipped. So `hs cluster --no-db` (and `hsx --no-db`) creates or upgrades the
configured SQLite file, the user's own site database by default. Reproduced in a throwaway site
(2026-11-15): `hs cluster in.txt --no-db -N1 -t 'echo {}' --no-tls` runs both tasks and leaves
`task.db` holding the `client`, `source`, `task` and `zone` tables. And when the configured file
cannot be opened (its directory does not exist), the same command runs no task and exits 5 with
`OperationalError: unable to open database file`, although the run needs no database at all.
`hs server --no-db` was fixed for the same gap by `server-initdb` (#105, its R5): its
`check_database` (`server.py:1920-1923`) never opens the database under `--no-db`, and its help
says so. That cycle left the cluster half as deferred work.

**An autoscaling period of zero makes the autoscaler loop without waiting.** `-P`/`--period` on
`hs cluster` and `hs server`, and the `autoscale.period` key behind it, accept `0` (`0`, `0s`,
`0:00`, …): `duration-units-remaining` (#106) made them read units and refuse a sign, and kept `0`
valid. Each autoscaler turns the period into a `timedelta` and leaves its wait state for a check
once the time waited exceeds it, sleeping one second only when it does not; with a period of zero
that comparison is true on every pass, so the loop never sleeps. Three loops share the pattern:

- `hs cluster --autoscaling` (`AutoScaler`, `cluster/remote.py:501`, `:563-574`): each check
  cleans up exited clients and reads the database to size every zone. Reproduced (2026-11-15,
  `--launcher` a script that sleeps, `--autoscaling fixed -I 1`, debug logging, about 6 s):
  `-P 0` logged 11,159 `Autoscale check` lines, `-P 2` logged two.
- `hs server --autoscaling` (`TopologyPublisher`, `cluster/topology.py:377`, `:401-408`), which
  plans every zone from the database on each check. Reproduced: `hs server --autoscaling fixed
  -P 0` used 7.9 s of CPU in about 8 s of wall time; `-P 2` used 0.3 s. The seed named `hs server`
  among the options but located only the cluster's loop.
- `hs relay`'s site fleet (`SiteScaler`, `relay/site.py:342-348`, which defers to the `AutoScaler`
  wait), whose period is `autoscale.period` read by `RelayApp.site_period`
  (`relay/__init__.py:1207-1219`). Confirmed by reading; not driven (a relay needs a server and a
  site).

A value below one second can also reach the period without the parser: the configuration loader
reads `HYPERSHELL_AUTOSCALE_PERIOD=-5` or `=0.5`, and a TOML number, as a number, and a number is
used as given (#106 R5). A negative period passes the same comparison on every pass, and nothing
refuses it today (`HYPERSHELL_AUTOSCALE_PERIOD=-5 hs cluster in.txt --no-db` starts and exits 0). A
`-P 5MB`, or the same text in the key, already stops every `hs cluster` and `hs server` launch with
exit status 2 and `argument -P/--period: Duration '5MB' is not valid: …`, autoscaling or not;
`hs relay` refuses it as `autoscale.period: Duration …`.

## Outcome / vision

`hs cluster --no-db` behaves like `hs server --no-db`: on every provider it neither creates,
initializes, upgrades nor checks the configured database, so a missing or unusable database never
stops a run that does not use it. And an autoscaling period of zero or less is refused at start-up
the way an invalid duration already is, by `hs cluster`, `hs server` and `hs relay` alike, so no
autoscaler can spin; every period greater than zero works as today.

## Acceptance criteria (the contract)

- **R1** — WHEN `hs cluster` (or `hsx`) is launched with `--no-db` under the `sqlite` or `turso`
  provider, the run SHALL NOT create, initialize, upgrade or alter the configured database file: a
  file that did not exist SHALL still not exist after the run, an existing one SHALL be left
  unchanged, and IF the configured file cannot be opened or created, THEN the run SHALL still run
  its tasks and exit as it would with a usable file (not exit 5). This SHALL hold in every launch
  mode (local clients, `--ssh`, `--launcher`).
- **R2** — `hs cluster` without `--no-db` SHALL keep today's database behaviour: under SQLite the
  database is initialized automatically, with `--initdb` it is initialized on any provider, and
  otherwise a PostgreSQL or TimescaleDB database is checked and upgraded in place and a
  never-initialized one refused with today's message and exit status. With `--no-db`, a
  never-initialized PostgreSQL or TimescaleDB database SHALL still not stop the run, as today.
- **R3** — IF `-P`/`--period` is given to `hs cluster` or `hs server` a duration of zero
  (`0`, `0s`, `0m`, `0:00`, `0-0`, `0.0`), THEN the command SHALL exit with status 2 before it
  listens or launches any client, with an error that names `-P`/`--period`, the value given, and
  that the period must be greater than zero.
- **R4** — IF the `autoscale.period` key, in a configuration file or as
  `HYPERSHELL_AUTOSCALE_PERIOD`, holds a value that is not greater than zero (zero in any
  duration form, or a negative number; see Clarifications), THEN `hs cluster` and `hs server`
  SHALL refuse it as R3 does, with an error that names `-P`/`--period` or `autoscale.period`, the
  value, and that the period must be greater than zero.
- **R5** — IF `autoscale.period` holds a value R4 refuses, THEN `hs relay` SHALL exit before it
  connects upstream or launches any site client, with an error that names `autoscale.period`, the
  value, and that the period must be greater than zero, and the exit status it gives today for an
  `autoscale.period` that is not a duration.
- **R6** — Every period greater than zero, in every form accepted today, SHALL be accepted and
  read as the same number of seconds as before on all three commands (a fraction given as a string
  or on the command line still rounds up, so `-P 0.5` is one second; a number such as `period = 0.5`
  keeps its meaning, per #106 R5), and an autoscaler SHALL wait that period between checks as today
  (one second at the least, by its existing granularity).
- **R7** — The documentation SHALL match: the `hs cluster` and `hs server` help text for
  `-P`/`--period` and `docs/_include/cluster_help.rst` and `docs/_include/server_help.rst` SHALL
  state that the period must be greater than zero; the configuration reference entry for `autoscale.period`
  (`docs/_include/config_param_ref.rst`) SHALL state it for all three commands; and the `--no-db`
  passage of `docs/_include/cluster_help.rst` SHALL say, as the `hs server` one does, that the
  configured database is not opened at all, so it need not exist or be initialized. Each changed
  passage SHALL carry `.. versionchanged:: 2.9.0` placed as `AGENTS.md` § Docs says.
- **R8** — The change SHALL ship with regression tests that fail on `develop` at `90ab2fc2` and pass
  with the fix: `hs cluster --no-db` driven through its command line in a throwaway site leaves no
  SQLite file and runs its tasks with an unopenable database path (R1); a zero period on
  `-P`/`--period` for `hs cluster` and `hs server`, in the key as a string and as a number, and in
  `HYPERSHELL_AUTOSCALE_PERIOD`, is refused through each command's own argument handling (R3, R4);
  `hs relay`'s read of the key refuses the same values (R5); and a one-second period is accepted
  (R6).

**Ruled at the GOAL gate** (2026-11-15): approved by the foreman, gate judge under the maintainer's
mandate, with one amendment.
- **Every launch:** confirmed as proposed. A period that is not greater than zero is refused on every
  launch of `hs cluster`, `hs server` and `hs relay`, as #106 refuses `-P 5MB`, autoscaling or not.
- **Numbers below one second:** not refused. The floor is "greater than zero", not one second. A
  positive period below one second already waits one second (the autoscalers sleep a second at a
  time, so it cannot spin), `-P 0.5` already rounds up, and #106 R5 keeps a configuration number's
  meaning; refusing `period = 0.5` would break a value that works today and create the asymmetry the
  proposal named. R3–R7, the Evidence plan, the Outcome and the Non-goals are amended to match; the
  error names the option or key, the value, and that the period must be greater than zero. The
  defect is only zero and below.

The contract is locked; `hs-plan` designs against it.

## Evidence plan

- **CLI drive (`.agents/factory/bin/temp_site.sh`).** `hs cluster in.txt --no-db -N1 -t 'echo {}'
  --no-tls` completes both tasks and leaves no `task.db`; with `HYPERSHELL_DATABASE_FILE` under a
  directory that does not exist it still completes and exits 0; without `--no-db` it creates the
  four tables as today. Red on `90ab2fc2`: the tables are created, and the unopenable path exits 5.
- **Refusals.** `hs cluster in.txt --no-db --no-tls -P 0`, `hs server --autoscaling fixed --no-tls
  -P 0s` (with `HYPERSHELL_SERVER_AUTH` set, never a key on the command line), and each with
  `HYPERSHELL_AUTOSCALE_PERIOD=0` and `=-5`, and with `period = 0` and `period = "0s"` under
  `[autoscale]` in the site `config.toml`, exit 2 naming the option or key and that the period
  must be greater than zero, before any listener starts. `-P 1`, `-P 0.5`,
  `HYPERSHELL_AUTOSCALE_PERIOD=0.5` and `period = 0.5` start. Red on `90ab2fc2`: each zero or
  negative value starts.
- **Relay.** `RelayApp`'s argument checks refuse the same values for `autoscale.period` and accept
  `1` and `0.5`, without a running server.
- **Docs.** The docs build passes with `-W`; R7's passages are read against the behaviour above.

## Non-goals (no-gos)

- Clamping a zero or negative period instead of refusing it (ruled below), and refusing a positive
  period below one second (ruled at the GOAL gate).
- Any change to `hs server --no-db` (fixed by #105), to `hs submit`, or to what `--initdb` does.
- Any other duration's floor (`-w`/`--bundlewait`, `-Q`/`--poll`, `-S`/`--signalwait`, the relay's
  other keys), or a maximum for the period.
- A contract for the Python API: `AutoScaler`, `TopologyPublisher` and the cluster functions keep
  taking `period=` as they do; the plan may enforce the floor there too if that is where the check
  naturally lives, but only the commands and the key are graded.
- The autoscaler's one-second sleep granularity, or how often it checks for a period of one second
  or more.
- Regenerating `share/man/` pages or editing release notes (`hs-release`'s job); a backport to the
  2.8 line (a separate hotfix).

## Clarifications

Proposed in shaping on 2026-11-15; the two marked **pending** need the foreman's sign-off at the
GOAL gate before `/hs-plan`. The rest follow the foreman's brief or the seeds directly.

- **Q:** Kind and appetite? — **A:** `fix`, `small`: both seeds' frontmatter and the foreman's
  brief, which promotes them together as one cycle (shaping, 2026-11-15).
- **Q:** Do the seeds still hold against `develop` at `90ab2fc2`? — **A:** Yes in substance, both
  reproduced (Problem). Drift: their `file:line` citations have moved
  (`cluster/__init__.py:555-562` → `:596-603`; `remote.py:430` → `:501`, `:492-503` → `:563-574`;
  `relay/site.py:85-88` → `:86-89`, `:134` → `:136`). The zero-period seed names `hs server`'s
  `-P` but locates only the cluster's loop; `hs server --autoscaling` spins in a second loop of
  its own (`cluster/topology.py:401-408`), reproduced above and in scope through R3–R4 (shaping,
  2026-11-15).
- **Q:** Refuse a too-small period, or clamp it to the smallest allowed wait? — **A:** Refuse, as
  the foreman directed: a usage error naming the option, or an error naming the key, consistent
  with how #106 reports an invalid duration (R3–R5). Shaping found no reason to prefer clamping: no
  test, example or doc uses a zero period, and zero has never meant anything but a loop that does
  not wait (foreman's brief, 2026-11-15).
- **Q:** Where is the floor? — **A:** Greater than zero (amended at the GOAL gate, 2026-11-15; the
  shaping proposal was one second). The autoscalers already wait a second at a time, so any positive
  period is honoured as at least one second.
- **Q (ruled at the gate: as proposed):** Is a too-small period refused on every launch, or only when the autoscaler runs
  (`--autoscaling`)? — **Proposed:** every launch of `hs cluster` and `hs server` (and every
  `hs relay`, whose fleet always runs), as R3–R4 say. That is how #106 already treats `-P 5MB` and
  `period = "5MB"`, autoscaling or not, so one rule covers both kinds of bad period. The cost: a
  site whose configuration holds `period = 0` cannot start any `hs cluster` or `hs server` until it
  is fixed. The alternative refuses only with `--autoscaling` and leaves an unused value alone.
- **Q (ruled at the gate: accepted, not refused):** A number below one second in configuration (`period = 0.5`,
  `HYPERSHELL_AUTOSCALE_PERIOD=0.5`, which the loader reads as a number): refuse, or round up?
  — **Proposed:** refuse (R4). #106 R5 keeps a number's meaning unchanged, so the value as read is
  half a second, below the floor. The cost is an asymmetry a user can see: `-P 0.5` and
  `period = "0.5"` round up to one second and are accepted, while the same `0.5` as an environment
  variable or TOML number is refused. The alternative rounds a number up as the parser rounds a
  string, which amends #106 R5 for this key.
- **Q:** Which release, and which marker? — **A:** The next 2.9.0 pre-release only; each changed
  passage carries `.. versionchanged:: 2.9.0` (`AGENTS.md` § Docs: behaviour that differs from the
  stable line); `hs-release` adds the `:pr:` link (shaping, 2026-11-15).
- **Left for `/hs-plan` (not clarification markers):** where the floor is checked (the argument
  type, a post-parse check, or the autoscalers) so that option, key, number and environment
  variable all reach it; the error's exact wording; and how `hs cluster` skips the database under
  `--no-db` without disturbing the in-memory SQLite case (no database configured), which never
  reaches a file.

## Related materials

- Seeds: [`issues/cluster-no-db-initializes-sqlite.md`](../../issues/cluster-no-db-initializes-sqlite.md),
  [`issues/autoscale-zero-period-spins.md`](../../issues/autoscale-zero-period-spins.md)
- `spec/server-initdb/` (GOAL R5 and Non-goals: the server half of `--no-db`, and this deferral);
  `spec/schema-upgrade-step/research/02-upgrade-seam.md` §5 ("Hole C").
- `spec/duration-units-remaining/` (GOAL R3–R5: the parser behind `-P` and `autoscale.period`, and
  how an invalid duration is reported).
- Source: `src/hypershell/cluster/__init__.py` (`ClusterApp.__enter__`, `-P`), `src/hypershell/server.py`
  (`check_database`, `-P`), `src/hypershell/cluster/remote.py` (`AutoScaler.wait`),
  `src/hypershell/cluster/topology.py` (`TopologyPublisher.wait`), `src/hypershell/relay/__init__.py`
  (`RelayApp.site_period`), `src/hypershell/relay/site.py` (`SiteScaler`),
  `src/hypershell/core/types.py` (`parse_time`), `src/hypershell/data/__init__.py` (`initdb`,
  `DATABASE_DIALECT`).
- Docs: `docs/_include/cluster_help.rst`, `docs/_include/server_help.rst`,
  `docs/_include/config_param_ref.rst`.
- Tests: `tests/test_autoscale_failed_launches.py`, `tests/test_topology_server.py`,
  `tests/test_relay_topology.py` (autoscaler construction); `tests/conftest.py` (`temp_site`).
