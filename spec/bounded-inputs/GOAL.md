# GOAL — Every numeric setting has one declared range, one parser and one error

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** bounded-inputs
- **kind:** fix
- **appetite:** big  ·  *From the seed. The work touches every numeric option of every command, every
  numeric configuration key and its `HYPERSHELL_*` variable, the configuration load, `hs config set`
  and start-up error handling. That is a research pass (the inventory below is from shaping and
  `hs-plan` confirms it) and more than two phases. The criteria run past the size circuit-breaker;
  the split offered is C4.*
- **target:** 2.9.0  ·  *for 2.9.0a10. Refusing values accepted today is a behaviour change, so it
  ships on `develop` only.*

## Problem

Numeric input is validated piecemeal. `env-config-keys` (#113) gave every configuration key a
declared kind (`src/hypershell/core/config_schema.py`, `KEYS`: `INT`, `NUMBER`, `INT_OR_NONE`,
`DURATION`, `SIZE`). Bounds were then added one cycle at a time, each as its own check with its own
message and its own exit status:

| Bound | Where (on `develop` `03a02bd8`) | Refused with |
| --- | --- | --- |
| Period greater than zero; below 1 s rounds up to 1 s (#112) | `core/types.py` `parse_period` | option: exit 2; `autoscale.period` from a file under `hs cluster`: exit 2 (`cluster/__init__.py:384-390`) |
| Evict floor of 10 s, cap of 86,399,999,913,600 s, no infinity (#117) | `server.py:819-834` `check_evict` | `RuntimeError`, exit 5 |
| Rate of 0 or at least 10⁻⁶ (#117) | `core/types.py` `parse_rate`, `RATE_MINIMUM` | option: exit 2; a file's `client.ratelimit` under `hs cluster`: exit 2 (`cluster/__init__.py:393-400`); under `hs client`: not checked |
| `autoscale.failed_launches` non-negative (#117) | `cluster/remote.py:372-378` | `ConfigurationError`, exit 3 |
| Task group in 32 bits (#118) | `data/model.py:64-67`, `submit.py:1464` | `-g`: exit 2; input: exit 5 |
| Duration and size forms | `core/types.py` `parse_time`, `TIME_FORMS`, `parse_bytes`; `core/config.py:345-375` `config_seconds` | option: exit 2; key: exit 3 |
| Kind of a `HYPERSHELL_*` value | `config_schema.py` `read_value` | exit 3 at import, every command |

Everything else has no bound at all. Most count options are plain `type=int` or `type=float`, and a
file's value for a numeric key is used as TOML gives it. On `develop` `03a02bd8`, in a throwaway
SQLite site, shaping saw these accepted with exit 0: `hs submit -b 0`, `hs submit -c 100000`,
`hs submit -W 99999999999`, `hs list -l -5`, `hs cluster --no-db -N 0`, `hs cluster --no-db -b 0`
and `hs cluster --no-db -F nan`.

Each review then finds the next value that slips through. The notes filed for this seed:

- `hs submit -m 8388608TB` (2⁶³ bytes) exits 5 with a `DataError` from PostgreSQL's `BIGINT`, with
  nothing stored (`spec/memory-column-overflows-postgres/REVIEW.md`). `-m 8EB` and `-m 9000PB` are
  already refused by `parse_bytes`, exit 2.
- On PostgreSQL, `cores:100000` overflows the `SMALLINT` `cores` column. The submit committer dies
  and `hs cluster` hangs (`spec/update-client-retry/REVIEW.md`, pre-existing).
- `-R 1e-300` hung a cluster before #117's floor. A file's `ratelimit = 1e-300`, or
  `HYPERSHELL_CLIENT_RATELIMIT=1e-300`, still reaches `hs client` unchecked: the first task runs,
  then the executor fails with `OverflowError` (`spec/config-set-and-unread/REVIEW.md`, N1).
- A `server.evict` of 4,300 digits cannot be printed in its own refusal, and `config_seconds` has the
  matching case (`spec/config-set-and-unread/REVIEW.md`, waived N2). An integer rate wider than a
  float has the same shape.
- A text `attempts = "3"` in a file crashes every command at import with a traceback
  (`issues/config-text-attempts-crashes.md`). `hs config set` writes integers wider than 64 bits,
  which TOML readers then refuse (`issues/config-set-wide-integers.md`).

Every case is absurd input that no real run uses. The maintainer wants it handled once, centrally:
"I don't want to spend the next week chasing down every absurd thing someone may ask for … define
something like 'only positive integers ≥ 1' in a centralized way and just have the program bail on
bad input" (2026-11-22). The review rubric's "Absurd input is not a finding"
(`.agents/factory/review-rubric.md`) already points here.

## Outcome / vision

Every numeric setting has one range, declared beside its kind in one table. One parser per kind
reads it from an option, a configuration file and a `HYPERSHELL_*` variable alike. A value outside
its range, or not a number of that kind, stops the command before it starts work, with one message
naming the setting, the range and the value, and exit 2 for an option or 3 for configuration. No
numeric value of any size or form ends in a traceback at start-up. A value inside its range behaves
exactly as it does today. Once this lands, a reviewer has one place to check: is the value in range,
and did it go through the parser?

## Ranges (the table)

Each range is a **floor** taken from what the setting means (0 runs nothing, 0 means "none") and a
**ceiling** taken from the narrowest place the value is stored or used, so that no in-range value
can overflow downstream. Real use sits far inside every range. Counts are of the setting's
configuration keys and its options without a key; a key with several options counts once.

| Class | Kind | Range | Advise | Why | Settings (keys · options without a key are listed too) |
| --- | --- | --- | --- | --- | --- |
| Threads | int | 1 … 4,194,304 | above 1,024 | 0 threads runs nothing (today `-N 0` runs). The ceiling is the most thread ids a 64-bit Linux host gives a process, the figure `RATE_MINIMUM`'s floor already rests on. | `relay.num_threads` · `-N/--num-threads` (`hs client`, `hs cluster`) |
| Count | int | 1 … 2³¹−1 | — | 0 is an empty bundle, queue, page or relay. The ceiling is a 32-bit signed integer, PostgreSQL's `INTEGER` and a C `int`. | `server.queuesize`, `server.bundlesize`, `server.bundlemax`, `client.bundlesize`, `submit.bundlesize`, `relay.bundlesize`, `relay.upstream.bundlesize`, `relay.size`, zone `size` · `-l/--limit` (`hs list`, `hs update`) |
| Count or none | int (`int_or_none`) | 0 … 2³¹−1 | — | 0 is documented as none or never; `relay.min`, `relay.max` and `relay.init` also take `<none>`, as today. | `logging.file.keep`, `autoscale.failed_launches`, `autoscale.size.init`, `.min`, `.max`, zone `min`, `max`, `init`, `relay.min`, `relay.max`, `relay.init` |
| Attempts | int | `server.attempts` 1 … 32,767; `-r/--max-retries` 0 … 32,766 | `server.attempts` above 100; `-r` above 99 | A task's `attempt` column is `SMALLINT` on PostgreSQL. | `server.attempts` · `-r/--max-retries` (`hs server`, `hs cluster`) |
| Cores | int | 0 … 32,767; 0 is unconstrained | above 1,024 | `task.cores` is `SMALLINT` on PostgreSQL (`cores:100000` hangs today); no node is near it. | `task.cores`, `client.cores` |
| Port | int | 1 … 65,535 | — | A TCP port. | `server.port`, `relay.port`, `relay.upstream.port`, `database.port` |
| Group | int | −2³¹ … 2³¹−1 | — | `task.group` is `INTEGER` (#118, unchanged). `hs list -g` and `hs update -g` take it too, unchecked today. | · `-g/--group` (`hs submit`, `hs list`, `hs update`) |
| Part | int | 0 … 2³¹−1 | — | `task.part` is `INTEGER`. | · `--part` (`hs list`) |
| Duration | duration | 0 … 2³¹−1 s (about 68 years) | — | `task.timeout` is `INTEGER` seconds; the ceiling is also under the longest wait a thread can block (`threading.TIMEOUT_MAX`, about 292 years) and a `timedelta`. | `server.poll`, `server.tls.handshake`, `client.bundlewait`, `client.heartrate`, `client.timeout`, `submit.bundlewait`, `task.timeout`, `task.signalwait`, `relay.bundlewait`, `relay.poll`, `relay.retention`, `relay.timeout`, `relay.upstream.heartrate`, `.deadline`, `.backoff`, `.evict_hint` · `-n/--interval` (`hs wait`, `hs run`) |
| Evict | duration | 10 s … 2³¹−1 s | — | The floor is #117's. The cap falls from 86,399,999,913,600 s (2.7 million years) to the duration ceiling. | `server.evict` |
| Period | duration | greater than 0 … 2³¹−1 s; under 1 s reads as 1 s, as today | above 3,600 s | #112's floor and rounding. | `autoscale.period` |
| Start delay | duration | −(2³¹−1) … 2³¹−1 s; a leading `-` waits a random time up to it, as today | — | The duration ceiling, either sign. | · `-d/--delay-start` (`hs client`, `hs cluster`) |
| Size | size | 0 … 2⁶³−1 bytes; 0 is unconstrained | — | `task.memory` is `BIGINT` (#120). 2⁶³ bytes is the `DataError` case. | `task.memory`, `client.memory` |
| Rate | number | 0 (no limit), or 10⁻⁶ … 10⁹ tasks per second | below 0.001, other than 0 | The floor is #117's (`RATE_MINIMUM`). At 10⁹ a client waits a nanosecond per task, less than starting any process takes, so a higher rate is no limit. | `client.ratelimit` |
| Factor | number | 10⁻⁶ … 10⁹ | above 100 | 0 divides by zero in the scaling pressure (`Task.task_pressure`); the same decimal span as a rate. | `autoscale.factor` |

Options that set a listed key take its range: `-b/--bundlesize`, `-w/--bundlewait`, `-c/--cores` and
`--task-cores`, `-C/--client-cores`, `-m/--memory` and `--task-memory`, `-M/--client-memory`,
`-W/--timeout` and `--task-timeout`, `-T/--timeout`, `-S/--signalwait`, `-Q/--poll`, `-p/--port`,
`--upstream-port`, `-R/--ratelimit`, `-F/--factor`, `-P/--period`, `-I/--init-size`,
`-X/--min-size`, `-Y/--max-size` and `hs relay -N/--size`, on every command that has them, including
the backwards-compatible aliases of the task commands.

**Tally (from shaping; `hs-plan` confirms).** 50 configuration keys: 28 integer (21 `int`, 4 zone
keys, 3 `int_or_none`), 18 duration, 2 size, 2 number. 7 settings exist only as options: 5 integer
(threads, retries, group, part, limit) and 2 duration (start delay, interval).

**Advisory limits (the second tier; maintainer, 2026-11-24).** The range is what HyperShell can
hold and store. A few settings also have an advisory limit, well inside the range, past which real use
is very unlikely. A value past it is accepted and runs as today, with one WARNING (R13). The first
four are the maintainer's; the last two are proposed with a real-use reason. Every other setting has
none: no limit is clearly sensible for it.

| Advise | Setting | Why |
| --- | --- | --- |
| above 1,024 | threads (`-N`, `relay.num_threads`) | No good reason to run more executor threads in one client; a wider node runs more clients. (Maintainer.) |
| above 100 | `server.attempts`; `-r` above 99 | A task that fails 100 times will not pass on the 101st. (Maintainer.) |
| above 1,024 | cores (`task.cores`, `client.cores`, the `cores` tag) | No node a task runs on has more. (Maintainer.) |
| above 100 | `autoscale.factor` | Dynamic scaling adds clients when the estimated time to completion passes the factor times the average task's duration (`Task.task_pressure`); past 100 it hardly scales at all. (Maintainer.) |
| above 3,600 s | `autoscale.period` | A scaling check less often than hourly leaves new work unanswered for an hour; the default is 60 s. |
| below 0.001, other than 0 | `client.ratelimit` | Under one task per 1,000 s per client a throttle is a schedule, usually a rate meant per minute or hour; 0 is no limit and never advised. |

The advisory limits never apply to an explicit Python API argument (C3), and every built-in default
is inside them.

## Acceptance criteria (the contract)

**The table.**

- **R1** — Every numeric setting SHALL have one declared range, held in one table beside its kind,
  with its justification, as in *Ranges* above. That covers each numeric key in `KEYS`, each zone
  key, and each numeric option of every command, including the task commands' backwards-compatible
  aliases. A test SHALL fail when a numeric key or option has no range, and when a built-in default
  lies outside its range. `hs-plan` MAY change an entry only to (a) widen it so that a value that
  works and means something today stays inside, (b) lower a ceiling to a narrower place the value is
  stored or used, or (c) raise a floor of 0 where 0 hangs or spins today. Each change SHALL be
  recorded in `TECH.md` with its reason.
- **R2** — A value inside its range SHALL be accepted and SHALL behave exactly as today, in every
  form accepted today: units, the Slurm time forms, TOML integers and floats, text, `_env` and
  `_eval`, a period under one second read as one, a negative start delay read as random.

**One parser, one error.**

- **R3** — Each kind SHALL have one parser: whole number, decimal number, duration and size (a
  whole number or `<none>` for `int_or_none`). It SHALL read the option's text, the configuration
  file's value and the `HYPERSHELL_*` variable's text the same way. A file value given as text for a
  numeric key SHALL be read by the key's kind, so `attempts = "3"` is 3. A boolean, a non-finite
  number, and a non-integral number for a whole-number kind SHALL be refused.
- **R4** — IF a value is out of range or is not a number of its kind, THEN the command SHALL stop
  before it starts work (opens a database, binds, dials, launches or submits), print one message,
  and print no traceback. The message SHALL have one form,
  `<name>: expected <form> from <A> to <B>, given <V>`, with the zero meaning where a range has one,
  and ` (from: <file or variable>)` for configuration. `<name>` is the option as defined
  (`-N/--num-threads`) or the key (`server.evict`). `<V>` SHALL be cut to a short printable form, so
  that no value of any length raises while it is printed. The command SHALL exit **2** for an
  option and **3** for a configuration key, from a file or a variable. *(C1, ruled 2026-11-24:
  this moves a refused `server.evict` from 5 to 3, and `hs cluster`'s refusal of a file's
  `autoscale.period` and `client.ratelimit` from 2 to 3; release note changes 2 and 3.)*

**Every way in.**

- **R5** — WHEN a command loads its configuration, it SHALL check every numeric key in the merged
  configuration (all files and the environment) against its range, and refuse under R4 before it
  starts work. A key given by `_env` or `_eval` SHALL be checked when it resolves, as its value is
  only known then. `hs config` SHALL still run when a file holds a bad value, so the file can be
  repaired; `hs config` is exempt for file values only. *(C2, ruled 2026-11-24.)*
- **R6** — No numeric setting SHALL be read around the parsers. The existing checks (the period,
  the evict floor and cap, the rate floor and finiteness, `failed_launches`, the 32-bit group,
  `config_seconds`) SHALL become table entries read through them. `hs client`, `hs relay`,
  `hs cluster`, `hs server`, `hs submit`, `hs list`, `hs update`, `hs wait` and `hs run` SHALL read
  every numeric option and key through them, including a key read after start-up by a relay, an
  autoscaler or a launched client.
- **R7** — `hs config set` SHALL refuse a value for a numeric key that the key's parser refuses,
  and an integer outside TOML's 64-bit range for any other key. It SHALL write nothing and exit 2
  naming the key.
- **R8** — IF a `ValueError` or `OverflowError` escapes while a command reads its arguments and
  configuration, before it starts work, THEN the command SHALL exit 2 (while reading its command
  line) or 3 (otherwise) with one message naming the error, and no traceback. This backstop SHALL
  hold for a numeric case the table misses; it is not the route for the cases the table covers.
- **R9** — The task resource tags `cores`, `memory` and `timeout`, inline, from `-t/--tag` and in a
  `--from-json` record, SHALL take the ranges of `task.cores`, `task.memory` and `task.timeout`
  through the same parsers. A refusal SHALL follow #118's group refusal: read-in-full input is
  refused whole, exit 5, nothing inserted, naming the value and its line or record; on a stream
  only the line is refused, with an ERROR; `-t` exits 2. *(C5, ruled 2026-11-24: in this cycle.)*
- **R10** — The Python API SHALL take its configuration-derived defaults through R5's check. A
  value a Python caller passes explicitly SHALL be handled as today, and the API documentation SHALL
  name the ranges. *(C3, ruled 2026-11-24; explicit arguments are the follow-up seed
  `issues/api-argument-ranges.md`, 2.10.0.)*

**The advisory tier** (maintainer, 2026-11-24).

- **R13** — A setting MAY have an advisory limit in the table, beside its range (*Advisory limits*
  above). WHERE a value is inside the range but past its advisory limit, it SHALL be accepted and
  SHALL behave as today, and HyperShell SHALL log one WARNING naming the setting, the value and the
  advisory limit and saying the command runs anyway; the exit status SHALL NOT change. The warning
  SHALL apply wherever the range is checked: an option, the configuration at load (a file or a
  variable), `hs config set`, and the resource tags of R9. It SHALL NOT apply to an explicit Python
  API argument. It SHALL be logged at most once per setting per process. For resource tags it SHALL
  be one summary per submission, giving the count of tasks past the limit and the first such line or
  record, not one warning per task. A value at the advisory limit SHALL NOT warn. Tests SHALL cover,
  for each advisory limit, a value just past it (the WARNING logged once, the run unaffected, exit 0
  where the command completes) and a value at it (no warning). The documentation SHALL give each
  advisory limit beside its range (R12).

**Proof.**

- **R11** — Tests SHALL cover, for each kind, through an option, a file key and a variable: each
  bound, one past each bound, a non-number, `nan` and `inf`, a 4,300-digit integer, a TOML boolean,
  and a file value given as text. They SHALL also cover the cases in *Problem*: `-m 8388608TB` exits
  2 and `-m 8EB` still exits 2; `-c 100000` exits 2; `-R 1e-300` exits 2; a file's
  `client.ratelimit = 1e-300` under `hs client` exits 3; a 4,300-digit `server.evict` exits 3 with a
  printable message; `attempts = "3"` runs with three attempts; `hs config set` of a 4,300-digit
  value exits 2 and writes nothing; R8 with an injected error; and, on PostgreSQL, the largest cores,
  memory, timeout and attempt values are stored.
- **R12** — Documentation, per `AGENTS.md` § Docs, each passage marked
  `.. versionchanged:: 2.9.0`: the parameter reference (`docs/_include/config_param_ref.rst`) gives
  each numeric key's range and any advisory limit beside it (R13); each command's page gives the
  range and any advisory limit of each numeric option; the
  environment-variable page says a variable is held to its key's range. `AGENTS.md` § Configuration
  names the table, and the review rubric's "Absurd input is not a finding" drops "before
  `bounded-inputs` lands" and points at the table. The squash commit body SHALL name each change in
  *User-visible changes* as a behaviour change.

### User-visible changes (the release note)

1. **Out-of-range numbers are refused.** Every numeric option, configuration key and `HYPERSHELL_*`
   variable has a range, listed in the documentation. A value outside it stops the command before it
   starts, exit 2 for an option and 3 for configuration, naming the setting, the range and the value.
   Values accepted before and now refused include `-N 0`, `-b 0`, `-l` below 1, `-c` above 32,767,
   any duration over 2³¹−1 seconds (about 68 years), `-m` of 2⁶³ bytes or more, a negative rate, and
   a factor of 0, below 0, `nan` or `inf`.
2. **`server.evict`'s cap is lower,** 2³¹−1 seconds instead of 86,399,999,913,600, and a refused
   evict exits 3 instead of 5.
3. **Configuration refusals exit 3.** `hs cluster`'s refusal of a file's `autoscale.period` or
   `client.ratelimit` exited 2; it exits 3, as every configuration refusal does. `hs client` now
   refuses a `client.ratelimit` under the floor.
4. **A file's numeric key may be text.** `attempts = "3"` reads as 3, as the variable does, instead
   of failing every command at import.
5. **`hs config set` refuses an out-of-range value,** and any integer TOML cannot hold, exit 2.
6. **Some settings warn past an advisory limit,** and run anyway: more than 1,024 threads, more
   than 100 attempts (`-r` above 99), more than 1,024 cores, a scaling factor above 100, a scaling
   period above an hour, and a rate below 0.001 tasks per second other than 0. One WARNING per
   setting per run; one summary per submission for resource tags. The exit status is unchanged.

## Non-goals (no-gos)

- **Host limits.** A value inside its range that the host cannot supply (threads, memory, a port in
  use, open files) fails as it does today. A range bounds what HyperShell can hold and store, not
  what a host provides.
- **Relations between settings** (`min ≤ init ≤ max`, `bundlemax ≥ bundlesize`, relay sizes) stay
  where they are, unchanged.
- **Text settings.** An empty or odd text value (`--remote-exe ''`, a host, a path,
  `logging.file.rotate`, `database.timescale.chunk_interval`) is out of scope; the empty
  `--remote-exe` stays as `relay-client-exe-deprecated-name` ruled it (Q6).
- **New units.** No `P` or `E` size units, no week durations; `-m 8EB` stays a form error.
- **Booleans and other kinds** read from a file by kind; **unknown keys**
  (`issues/config-unknown-key-warning.md`).
- **Query values.** `hs list` and `hs update` field comparisons are queries, not settings.
- **The database schema.** No `CHECK` constraints and no migration; the table keeps values inside
  the columns' types.
- **Wire values from peers** (frame lengths and the like, `spec/queue-wire-codec/REVIEW.md`).
- **Explicit Python API arguments,** per C3: the follow-up seed `issues/api-argument-ranges.md`,
  target 2.10.0.

## Clarifications

Decided in shaping (2026-11-24):

- **Q:** Kind, appetite and target? — **A:** `fix`, `big`, 2.9.0, from the seed (`status:
  unshaped`).
- **Q:** What does this GOAL absorb? — **A:** As the seed lists: `config-set-wide-integers`
  (R7, all of it, including untyped keys), `config-text-attempts-crashes` (R3; it concerns only
  `server.attempts`, a numeric key, so all of it), #117's waived N2 and `config_seconds`' matching
  case (R4, R6), `hs client`'s read of `client.ratelimit` (#117 N1, R6), and an integer rate wider
  than a float (the rate ceiling). Both seeds are marked `adopted:bounded-inputs` with this GOAL.
- **Q:** How are ceilings chosen? — **A:** From the narrowest place a value is stored or used, not
  picked: PostgreSQL column types, `threading.TIMEOUT_MAX`, the Linux thread-id limit. Floors come
  from meaning. Duration floors stay as today (0, or #112's and #117's), so no duration accepted
  today is refused at the low end (R1 lets the plan raise a 0 that hangs).
- **Q:** One ceiling for all durations? — **A:** Yes, 2³¹−1 seconds, the `task.timeout` column. It
  lowers the evict cap from #117's; both are absurd, and one ceiling is simpler.

Proposed in shaping, each blocking `hs-plan`; all five were resolved at the GOAL gate on
2026-11-24 (below):

- **C1 — Exit status and wording, configuration versus option. Resolved 2026-11-24**
  (maintainer): **A.** Today a bad option exits 2 (`bad_argument`), a bad variable or
  `ConfigurationError` exits 3 (`bad_config`), and some configuration refusals exit 2
  (`hs cluster`'s period and rate) or 5 (evict).
  - (A) Option 2, configuration 3, one message form with ` (from: …)` for configuration. Matches
    cmdkit's statuses and the environment layer.
  - (B) Everything 2: one status for all bad input.
  - (C) Keep each site's status as today; unify the message only.
  - **Recommendation: A.** It is what the seed proposes and what most sites already do; B hides
    whether to fix the command line or a file.
- **C2 — What the start-up check covers. Resolved 2026-11-24** (maintainer): **A.**
  - (A) The whole merged configuration, at load, for every command; `hs config` is not refused for
    a bad file value, so it can repair it; `_env` and `_eval` keys are checked when they resolve.
  - (B) Only the keys a command reads, when it reads them, plus R8's backstop.
  - **Recommendation: A.** One place makes "no bypass" structural, it is how the environment layer
    already behaves, and it catches keys read after start-up (a relay, an autoscaler, a launched
    client), which under B would fail mid-run. The cost: a bad `relay.retention` stops `hs list`.
    That is "bail on bad input".
- **C3 — The Python API. Resolved 2026-11-24** (maintainer): **B.**
  - (A) Validate every public parameter that mirrors a setting, raising `ValueError`.
  - (B) Validate only what comes from configuration (R5); explicit arguments as today, ranges
    documented.
  - (C) No API change in this cycle.
  - **Recommendation: B.** A Python caller gets a Python exception, not a traceback at a terminal,
    and A touches every public signature. A follow-up seed can take A if wanted. The seed asked for
    the API to read through the parsers; B narrows that.
- **C4 — Appetite: one cycle or two. Resolved 2026-11-24** (foreman, under the maintainer's
  mandate): **A, falling back to B.** Twelve criteria is past the circuit-breaker.
  - (A) One `big` cycle in phases: table and parsers; options; configuration and `hs config set`;
    backstop, tags and docs.
  - (B) Two cycles: R1–R8 with R11–R12 now (the table, parsers, error, every way in, backstop);
    R9 (tags) and R10 (API) as a follow-up seed.
  - **Recommendation: A, falling back to B** if `hs-plan` finds more than four phases. Most criteria
    are one mechanism applied many times, and the backstop is small. R9 and R10 are the natural cut.
- **C5 — Task resource tags. Resolved 2026-11-24** (maintainer): **A.** The rubric's rule names
  options, keys and variables, not task input.
  - (A) In: R9 as written.
  - (B) Out: a follow-up seed.
  - **Recommendation: A.** `memory` and `timeout` tags already go through `parse_bytes` and
    `parse_time`, so they get the range with the parser. `cores` is the one more, and it closes the
    PostgreSQL `cores:100000` hang. #118 already settled how task input is refused.

**Ruled at the GOAL gate** (2026-11-24). Approved by the foreman, gate judge under the maintainer's
mandate, with the maintainer's own rulings on C1, C2, C3 and C5.

- **C1 (maintainer):** an option out of range exits 2, and a configuration value, from a file or a
  variable, exits 3, with one message form. This moves a refused `server.evict` from 5 to 3, and
  `hs cluster`'s refusal of a file's `autoscale.period` and `client.ratelimit` from 2 to 3. The
  release note says so (changes 2 and 3).
- **C2 (maintainer):** the whole merged configuration is checked at load, for every command. Keys
  given by `_env` or `_eval` are checked when they resolve. `hs config` is exempt for file values,
  so a bad file can be repaired.
- **C3 (maintainer):** the Python API checks only the defaults that come from configuration;
  explicit arguments behave as today, and the API documentation gives the ranges. Validating explicit
  arguments is the follow-up seed `issues/api-argument-ranges.md`, filed with this gate, target
  2.10.0.
- **C4 (foreman, under the mandate):** one `big` cycle in phases. If `hs-plan` finds more than four
  phases are needed, it falls back to the split: R9 and R10 go to a follow-up cycle.
- **C5 (maintainer):** the resource tags `cores`, `memory` and `timeout` are in this cycle, as R9.

The absorbed seeds `config-set-wide-integers` and `config-text-attempts-crashes` stay adopted, as
shaped. Target 2.9.0. The contract is locked; `hs-plan` designs against it.

**Amended at the GOAL gate** (2026-11-24, maintainer): a second, advisory tier. In the maintainer's
words: "These ranges look fine if we're guarding technical correctness … For some of these I think
we can apply warnings for even more restrictive inputs. Like more than 1024 threads (no good reason
to go higher), or more than 100 attempts, or more than 1024 cores, or factor greater than 100, etc.
… I don't know that I want to halt the software for them, a warning would suffice. I think your
values are correct for the software functioning." The table gains an Advise column and *Advisory
limits*; R13 states the behaviour, tests and docs; release note change 6. The maintainer seeded
threads, attempts, cores and factor; `hs-plan` proposed the period and the rate, each with its
reason, and left every other setting without one. The tier counts against C4's phase budget.

## Related materials

- Seed: `issues/bounded-inputs.md`; absorbed: `issues/config-set-wide-integers.md`,
  `issues/config-text-attempts-crashes.md`.
- Rubric: `.agents/factory/review-rubric.md`, "Absurd input is not a finding" (2026-11-22).
- Prior cycles: `spec/env-config-keys/` (#113, kinds), `spec/cluster-nodb-and-zero-period/` (#112),
  `spec/config-set-and-unread/` (#117, REVIEW N1 and N2), `spec/submit-input-checks/` (#118),
  `spec/memory-column-overflows-postgres/` (#120), `spec/update-client-retry/` (REVIEW notes).
- Source: `src/hypershell/core/config_schema.py` (`KEYS`, `ZONE_KEYS`, `read_value`),
  `src/hypershell/core/types.py` (`parse_bytes`, `parse_time`, `parse_delay`, `parse_period`,
  `parse_rate`, `RATE_MINIMUM`, `TIME_FORMS`), `src/hypershell/core/config.py` (`config_seconds`,
  start-up load), `src/hypershell/core/exceptions.py` (`get_shared_exception_mapping`),
  `src/hypershell/server.py` (`check_evict`, `configured_retries`),
  `src/hypershell/cluster/__init__.py` (`check_arguments`), `src/hypershell/cluster/remote.py`
  (`configured_failed_launches`), `src/hypershell/data/model.py` (`GROUP_MIN`, `GROUP_MAX`, column
  types), `src/hypershell/submit.py` (`group_option`), `src/hypershell/task.py` (list, update, wait,
  run options), `src/hypershell/relay/__init__.py`.
