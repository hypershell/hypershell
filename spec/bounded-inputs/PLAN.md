# PLAN — Every numeric setting has one declared range, one parser and one error

> **Status:** Approved at the plan gate, with rulings (*Plan gate*) · **Last updated:** 2026-11-24
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (`appetite: big`). Every design element traces to a GOAL R-ID.

## 1. Summary

One table, beside `KEYS` in `core/config_schema.py`, gives each numeric setting its kind, range,
advisory limit and reasons. One parser per kind reads a value against an entry and raises one error.
Options get it through a `RangedOption` type built from the entry; configuration gets it once, when
the configuration loads at import, by checking every numeric key of the merged configuration and
writing the checked value back, so the seventy-odd later reads need no edit. Task resource tags get
it through an `ensure_resources` check beside #118's group check. A backstop turns any stray
`ValueError`/`OverflowError` before work into a clean refusal. Four phases, tests first: the C4 split
does not apply ([`research/00-digest.md`](research/00-digest.md)).

## 2. Design

### 2.1 The table and the parsers (R1–R4, R13) — `core/config_schema.py`, `core/types.py`

- **`Range`** (a frozen dataclass): `kind` (`INT`, `NUMBER`, `DURATION`, `SIZE`, `INT_OR_NONE`),
  `low`, `high`, `zero` (the zero's meaning, e.g. "unconstrained", "all cores", "no limit"), `low_open`
  (the period's "greater than 0"), `advise` (`('above'|'below', limit, reason)` or None), `why`. One
  `Range` per class of the GOAL table (`THREADS`, `COUNT`, `COUNT_OR_ZERO`, `COUNT_OR_NONE`,
  `ATTEMPTS`, `RETRIES`, `CORES`, `PORT`, `GROUP`, `PART`, `DURATION`, `HANDSHAKE`, `INTERVAL`, `EVICT`,
  `PERIOD`, `DELAY`, `SIZE`, `RATE`, `FACTOR`), with the numbers and reasons of the GOAL's table.
- **`SETTINGS: Dict[str, Range]`**: every numeric key of `KEYS`, each zone key as
  `relay.zone.*.<key>`, and the option-only settings under an `option.` prefix (`option.max_retries`,
  `option.group`, `option.part`, `option.limit`, `option.delay`, `option.interval`; threads have
  `relay.num_threads`'s entry). `KEYS` keeps its kinds; a test pins that `SETTINGS`' kinds match.
- **The grammar moves here** from `core/types.py` (`TIME_*`, `MEMORY_*`, `_match_seconds`), so this
  module stays standard-library only and owns one parser per kind:
  - whole number: an `int` that is not a `bool`, or text matching `INT_FORM`; length-checked before
    `int()` so a 5,000-digit text is refused by size, not by Python's digit limit;
  - number: a finite `int`/`float`, or text read by `read_number`;
  - duration: a number, or text in `TIME_FORMS`, exact (`Fraction`), then rounded by the reader as
    today (`whole=True` rounds up as `parse_time` does; otherwise exact, as `config_seconds` does);
  - size: a number, or text in `MEMORY_FORMS`, computed with `Fraction` so `9223372036854775807` is
    exact (today `int(float(…))` gives 2⁶³);
  - `int_or_none`: a whole number, or exactly `<none>`.
  A TOML boolean, a non-finite number and a non-integral number for a whole kind are refused (R3).
- **`Range.parse(value, name, source=None) -> value`** and **`parse_setting(setting, value,
  name=None, source=None)`** check kind then range and raise **`SettingError(ValueError)`** carrying
  `name`, the form, the bounds, the shown value and the source. Its text is R4's one form:
  `<name>: expected <form> from <A> to <B>, given <V>`, the zero's meaning after `0` (`from 0 (all
  cores) to 4,194,304`), ` (from: <file or variable>)` when a source is given. Bounds print with
  thousands separators and the unit (`s`, `bytes`, `tasks per second`).
- **`shown(value)`** cuts `<V>`: the value as given, `repr` up to 32 characters, else head…tail and
  the length; an `int` too wide to print in `Decimal` `.4e` form. Nothing it is given can raise.
- **`advise(setting, value, name)`** (R13): past `Range.advise`'s limit, it records one WARNING
  (`<name>: <V> is above the advised <limit> (<reason>); running anyway`, or "below") at most once per
  setting per process, keyed by the `SETTINGS` entry. Records made before logging exists go to a
  pending list, logged by the app's start (`HyperShellApp.main` after `initialize_logging`); later
  ones log at once. The parsers call it on every accepted value except where the caller is an
  explicit Python API argument (they never call the parsers).
- **`core/types.py`**: `parse_time`, `parse_bytes`, `parse_delay`, `parse_period`, `parse_rate`
  stay as wrappers over the table (tags, templates and the API use them; messages move to R4's form);
  `RATE_MINIMUM` is re-exported from the `RATE` entry. **`RangedOption(setting, name)`**, the
  option type: calls `parse_setting`, then `advise`; on `SettingError` raises cmdkit's
  `ArgumentError` with the R4 line (not a `ValueError`, so argparse does not prefix `argument …:`).
  `ranged(interface, *flags, setting=…, hidden=(), **kw)` declares an option with it; the display name
  is the flags joined by `/` less `hidden` (`-N/--num-threads`, `--num-tasks` hidden).

### 2.2 Every option (R1–R4, R6, R13) — `submit.py`, `server.py`, `client.py`, `cluster/__init__.py`, `relay/__init__.py`, `task.py`

All 59 numeric option declarations ([research 01](research/01-option-inventory.md)) move to
`ranged(...)`, keyed by command and destination, not by flag (`hs relay -p` is `--upstream-port`,
`hs relay -N` is `--size`). The task-command aliases share their app classes, so one declaration
covers both names. Post-parse checks that duplicate a range go: `hs client`'s `-T`/`-W` ≤ 0
(`client.py:1786-1790`), `hs relay`'s size < 1 (`relay/__init__.py:1252`), the option halves of
`hs cluster`'s period and rate checks. `hs list -g`, `--part` and `hs list`/`hs update -l` take the
`option.group`, `option.part`, `option.limit` entries before they become where-clause text
(`task.py:556-559`). Relations (`min ≤ init ≤ max`, `bundlemax ≥ bundlesize`, relay bounds) stay.
Option defaults stay as they are: numbers once the configuration check has run (2.3), so argparse
never re-parses a file's text with the option's name (research 01's hazard).

### 2.3 Configuration at load (R5, R6, R10, R13) — `core/config.py`, `core/config_schema.py`, `config.py`

- **`check_settings(config, exempt_files)`** runs inside the import-time `try` in `core/config.py`,
  after the merge and before `check_auth_forms`, the relay database swap and the engine. For every
  `SETTINGS` key a layer gives, literally or as `_env`/`_eval`, it resolves the form once, parses it,
  advises, and writes the checked value back into the layer that gave it under the plain key
  (removing the `_env`/`_eval` form), so `which()`/`blame` keep naming the file or variable. Whole
  numbers and numbers are written as numbers; a duration or size is written as given (its readers
  round as today, R2). Zone tables are walked by `relay.zone.*`. A refusal is a `SettingError` with
  ` (from: …)`; the handler prints the one line, no traceback, no class prefix, exit 3.
- **The environment layer** (`read_value` for numeric kinds) uses the same parsers, so a variable's
  refusal has the same form at the same place (exit 3 at import).
- **`hs config`** (C2): `CONFIG_COMMAND` sniffs the command line as `_relay_requested` does. Under
  it, file values are not refused: a refused file value is replaced by its built-in default in the
  checked view, so other modules import; `hs config get`/`which` print from an unchecked view
  (`full_config` becomes that view), so the bad value shows and can be set right. Environment values
  are still refused.
- **The retired checks** (R6) become reads of checked values: `config_seconds` keeps its signature as
  a duration reader over the table (no own refusal once load has checked); `check_evict`
  (`server.py:819-834`, exit 5) and the relay's unguarded evict read go; `configured_failed_launches`
  (`cluster/remote.py:372-378`), `configured_retries` (`server.py:117`), `cluster/__init__.py`
  `check_arguments`' period and rate checks, `handshake_seconds`' range (`core/tls.py:160`),
  `set_retention_count` (`logging.file.keep`) and the range parts of `relay_bounds`/`zone_settings`
  become table entries read through the load check. `HeartMonitor`'s guard on an explicit
  `evict_after` (`server.py:889`) reads the `EVICT` entry's bounds (one place) but keeps its exception
  type; the API's other explicit arguments are untouched (C3).
- **R10.** Every API default that comes from configuration (`configured_retries`, `server.queuesize`,
  `server.bundlemax`, `RelayThread`'s defaults, `QueueConfig.load()`) reads the checked
  configuration, since `import hypershell` runs the load check. API docs (`docs/api/*.rst`) name the
  ranges; explicit arguments stay as today and the follow-up seed `api-argument-ranges` covers them.
- **`hs config set`** (R7, `config.py:305-336`): a numeric key, zone keys included, goes through its
  entry (range and kind, advisory too); any other key's integer outside −2⁶³ … 2⁶³−1 is refused, the
  digit count checked before `int()`; exit 2 naming the key, nothing written; the refusal shows the
  value cut.

### 2.4 The backstop (R8) — `core/exceptions.py`, `hypershell/__init__.py`, the apps

`reading(stage)`, a context manager, turns an escaping `ValueError` or `OverflowError` into
`ArgumentError` (stage `arguments`, exit 2) or a configuration refusal (otherwise, exit 3) with one
line naming the error type and the cut text. Applied: around `from_cmdline` (a mixin on the apps),
around each start check up to its first line of work (`run()` heads of server, client, relay, submit,
the task commands; `ClusterApp.__enter__`), around the import-time load (split from the generic
`except Exception` there), and around the app imports in `hypershell/__init__.py`. It is never in the
shared exception mapping, so a `ValueError` during real work keeps today's path.

### 2.5 Task resource tags (R9, R13) — `data/model.py`, `submit.py`, `cluster/__init__.py`

`TaskInputError(RuntimeError)` becomes the base of `TaskGroupError` (name kept) and a new
`TaskResourceError`; the four places that catch the group error (`submit.py:217, :549, :1460, :1871`)
catch the base. `ensure_resources(tags, texts)` beside `ensure_group` reads `cores`, `memory` and
`timeout` by `task.cores`, `task.memory`, `task.timeout` (inline and `-t` text as written, a JSON
value as a configuration file's value of that kind) and is called where the group is checked:
`split_argline` (whole-input scan and the stream `Loader`), `check_tags` (`-t`, exit 2) and the JSON
record check (renamed `validate_json_records`, alias kept for `cluster/__init__.py:406`). Read-in-full
input is refused whole (exit 5, nothing inserted, naming the value and line or record); a stream
refuses the line with an ERROR. `Task.new`'s keyword arguments and `Task.new_retry` stay unchecked.
Advisory: the scan counts tags past an advisory limit and logs one summary per submission (the count
and the first line or record); a stream logs the first and the count when its input ends.

### 2.6 One group parser, shared (with `update-group-and-legacy-filters`)

`GROUP_MIN`/`GROUP_MAX` are read from the `GROUP` entry; `parse_group(text) -> int` keeps its name,
signature and `TaskGroupError`, its body the table's whole-number parser over `GROUP`; `ensure_group`
likewise. `group_option` (`hs submit -g`) and the `hs list`/`hs update -g` filters become `RangedOption`
on `option.group`. The parallel cycle (plan approved, `eec25fc0`) calls `parse_group` from
`hs update`'s `change_value` and asserts exit 2, "group" and the value, not #118's sentence, so the
wording change breaks none of its tests. It lands first (small); this cycle merges develop in P1 and
its call site needs no edit. If this cycle lands first, theirs rebases on the same function.

### 2.7 Reuse by `update-typed-columns`

`parse_setting`, `Range.parse` and `SettingError` are public in `core/config_schema.py` and need
neither argparse nor a configuration load. That seed reads `cores`, `memory` and `timeout` by the
`task.*` entries; for `attempt` (1 … 32,767) and `exit_status` (−32,768 … 32,767), which are columns,
not settings, it builds its own `Range` with the same parser. Nothing here names a column-only
entry, so R1's test stays "every entry is a setting".

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `Range`/`SETTINGS` (2.1); ranges changed under R1 in TECH.md; `tests/test_bounded_table.py` (keys, zone keys, options by set equality, defaults, advisory limits inside ranges) |
| R2   | parsers keep every form (2.1); durations/sizes written back as given (2.3); period rounding, random delay kept; guards list |
| R3   | one parser per kind in `config_schema.py`; env layer and file values through it; text for numeric keys read by kind (2.1, 2.3) |
| R4   | `SettingError`, `shown`, `RangedOption` → `ArgumentError` (exit 2), load handler (exit 3), no class prefix |
| R5   | `check_settings` at import, `_env`/`_eval` resolved once; `hs config` exemption (2.3) |
| R6   | options via `ranged` (2.2); retired checks become entries (2.3); gates (§ 6) |
| R7   | `hs config set` through the entry; 64-bit refusal for untyped keys (2.3) |
| R8   | `reading(stage)` (2.4) |
| R9   | `ensure_resources`, `TaskResourceError` (2.5) |
| R10  | API defaults read the checked configuration; API docs name ranges (2.3) |
| R11  | tests first (P1), red list and guards; PostgreSQL storage of the largest values (P4) |
| R12  | param reference, help pages, env page, API pages, `AGENTS.md`, rubric (P2–P4) |
| R13  | `Range.advise`, `advise()` once per setting, pending queue, tag summary (2.1, 2.3, 2.5) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(§3, §10, §11, §12) and again on this design.

- **§10 Configuration** — `config` is still built once at import; the check runs inside that build
  and writes into the process's own layers, never to disk. `hs config set` still writes disk only. The
  `HYPERSHELL_*` matcher is unchanged; values of numeric kinds now go through the table's parsers. A
  new numeric key needs its `SETTINGS` entry (the R1 test fails otherwise), beside its `KEYS` kind and
  reference entry. `server.evict` is checked at load instead of at each command's start check
  (`check_evict`); §10's sentence is amended in the same commit (P3). `configured_retries()` still
  resolves `max_retries=None`. The relay's `[database]` swap is after the check and unchanged.
- **§3 Retry model / task input** — the group rule is unchanged in substance (32-bit, not `bool`,
  `TaskGroupError` exit 5, `-t` exit 2, read-in-full scanned whole, stream per line); the resource
  check follows exactly the same paths and never reaches `Task.add_all` with a refused value.
  `Task.new_retry` copies stored columns, unchecked. `attempts == max_retries + 1` holds: `ATTEMPTS`
  is 1 … 32,767 and `RETRIES` 0 … 32,766. §3's group sentence names `TaskInputError` (P4).
- **§11 Cluster orchestration** — `client_launch()` still builds every launched argv; the option
  floor (options a 2.9.0a4 client accepts) is unchanged, since only values change and every value
  emitted is in range. A launched client checks its own options and configuration and may log the
  same advisory once in its own process. `plain_rate`'s fractional word is unchanged.
- **§7 Resource accounting** — in-range `cores`/`memory` are what `core/resource.py` handles today;
  a text `cores` (a `TypeError` at `client.py:663` today) can no longer reach it.
- **§12 Conventions** — help snippets and completions in the same commit as each option change
  (P2); `versionchanged:: 2.9.0` above each changed passage; exit statuses from
  `cmdkit.app.exit_status`; only `unit`/`integration` markers; the squash body names every change.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A numeric key's `_eval` runs once, at load, instead of at every attribute read. | R5 checks a key "when it resolves"; pinning the checked value is what makes every later read in range (R6) without editing ~70 read sites. | Re-resolving at each read re-runs the command and can yield an unchecked value; an accessor at every read site touches ~70 sites and still needs the load check (research 03). |
| `HeartMonitor`'s guard on an explicit `evict_after` reads the `EVICT` entry's bounds, so its cap falls to 2³¹−1 s for an API caller too. | R6: the evict cap "SHALL become a table entry"; one cap, not two. | Keeping 86,399,999,913,600 s there leaves a second cap C3 would otherwise not touch; the difference is past 68 years, absurd for any caller. Its exception type is unchanged. |

## 4. Rabbit holes (resolved)

- **Configuration read at import, in class bodies** → a lazy-only check is impossible; the check must
  be in the import-time load, with a sniff for `hs config` ([02](research/02-config-load.md),
  [03](research/03-read-sites-and-no-bypass.md)).
- **argparse re-parses text defaults with the option's name** → configuration is checked and typed
  before the command classes exist ([01](research/01-option-inventory.md)).
- **argparse prefixes `argument …:`** → the option type raises cmdkit's `ArgumentError`, printed as
  written; prototyped ([01](research/01-option-inventory.md)).
- **Python's 4,300-digit limit** → length check before `int()`, `shown()` never converts a wide int
  to text ([02](research/02-config-load.md)).
- **The size ceiling is unreachable today** (`int(float())`) → exact parser ([05](research/05-tests-docs-harness.md)).
- **Resource tags** reach SQLite and hang the committer at 2⁶³ (not only PostgreSQL) → same scan as
  the group ([04](research/04-tags-and-group-parser.md)).
- **The parallel cycle's group parser** → one function, name kept ([04](research/04-tags-and-group-parser.md)).

## 5. Risks & open questions

For the plan gate:

1. **Ranges changed under R1** (TECH.md): threads' floor widens to 0 ("all cores", as documented);
   `-n/--interval`'s floor rises to 1 s (0 spins); `server.tls.handshake` 1 … 86,400 s, its own entry.
2. **`hs client -T 0` and `-W 0` become accepted** ("none", as the keys and `hs cluster` read them),
   where today they exit 2. The GOAL's table puts 0 in range and R1 allows raising a floor only where
   0 hangs. A release-note line; the gate may instead rule the floor at 1 for those two options.
3. **`_eval` runs once** (§ 3 deviation): an `_eval` with side effects or a changing value now runs
   once per process. Release-note line.
4. **C2's reach.** A bad numeric value anywhere in a file now stops every command except `hs config`,
   including `hs list`; `test_cluster_rate_option_over_not_finite_key` (a file's `ratelimit = inf`
   overridden by `-R`, exit 0 today) flips to exit 3. As ruled.
5. **Test flips** in P1 (research 05 § 5): evict 5 → 3 and its cap; period and rate key refusals
   2 → 3; four `hs config set` evict cases become refusals; four message regexes.
6. **Merge order with `update-group-and-legacy-filters`**: separate hunks of `TaskUpdateApp`,
   `task_update_help.rst` and `invariants.md` §3. Theirs lands first; P1 merges develop.
7. **Phase size.** P2 (59 options, every help snippet) and P3 (the load check, the retired checks,
   `hs config set`, the backstop) are large for one build each. The fallback, if a phase overruns, is
   C4's split (R9 and R10 to a follow-up), decided at that phase's gate, not a fifth phase.
8. **The docs build** needs the sandbox off (`sphinx_sitemap` binds a socket).

## 6. Verification strategy

- **Tests first (P1).** New files, unit where they can be: `test_bounded_table.py` (R1 coverage:
  `SETTINGS` = numeric `KEYS` + zone keys by set equality; defaults and option defaults in range and
  under advisory limits; every option that takes a value is a `RangedOption` or in `TEXT_OPTIONS` by set
  equality; no option typed `int`/`float`), `test_bounded_parsers.py` (the R11 matrix per kind in
  process: each bound, one past, `x`, `nan`, `inf`, 4,300 digits, `true`, text; `shown`),
  `test_bounded_options.py`, `test_bounded_config.py`, `test_bounded_config_set.py`,
  `test_bounded_tags.py` (with `db_site` over SQLite and PostgreSQL), `test_bounded_backstop.py` (a
  child that patches a start check to raise `OverflowError`), `test_bounded_advisory.py` (each limit:
  just past warns once and runs, exit 0; at the limit no warning; a tag summary),
  `test_bounded_docs.py` (each help snippet and reference entry gives the range and advisory limit;
  the environment and API pages, `AGENTS.md` and the rubric name the table). Each phase's `verify:`
  selects only the tests whose behaviour it lands, so a test flipped in P1 for configuration (P3) is
  not run by P2. The red list
  ([`drive/red.txt`](drive/red.txt)) is proved RED on `8548053b` with the landed
  `spec/relay-hardening/drive/red_check.sh`; the guards ([`drive/guards.txt`](drive/guards.txt)) pass
  before and after every phase.
- **No-bypass gates.** Gate (a): `test_bounded_config.py::test_loaded_numeric_keys_checked` loads every
  form and asserts every numeric key holds a checked value and no numeric `_env`/`_eval` remains.
  Gate (b): the option walk in `test_bounded_table.py`. Gate (c): [`drive/no_bypass.sh`](drive/no_bypass.sh),
  a grep over `src/hypershell` outside `core/config_schema.py` for `type=int`, `type=float`,
  `check_evict`, `timedelta.max` and the literal bounds (`2**31`, `2**63`, `1e-6`, `86400`): 41 hits on
  the base, none after P3 (the constants that stay, such as `GROUP_MIN`, are read from entries).
- **CLI drive.** `drive/bounds_drive.sh` (modes `options`, `config`, `set`, `tags`, `advise`) in a
  `temp_site`, SQLite: each mode fails on the base (plan dry run in P1) and passes after its phase.
- **Each phase** runs its new tests and the suites around the code it touches, `HS_TEST_POSTGRES` set;
  P4 runs the whole suite serially (bounded 7,200 s), re-proves the red list green, and builds the docs
  (html and man, `-W`, sandbox off).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan gate (2026-11-24)

**Approved** by the foreman, gate judge under the maintainer's mandate. The rulings, by subject (the
numbers in brackets are § 5's):

- **`hs client -T` and `-W` of 0** (5.2): they keep a floor of 1, as today; 0 stays refused, exit 2.
  R2 says a working value behaves as today, and a value refused today stays refused rather than gain
  a new meaning in this cycle. Recorded in TECH.md's range-change notes. The keys `client.timeout` and
  `task.timeout`, and `hs cluster`'s options, keep the Duration range from 0.
- **`_eval` runs once** (5.3, § 3 deviation): accepted. A numeric `_eval` runs once per process. It
  goes in the release note.
- **`HeartMonitor`'s explicit `evict_after` guard** (§ 3 deviation): accepted as a small deviation
  from C3. It reads the `EVICT` entry's bounds. Reported to the maintainer, who may overrule it.
- **C2's reach** (5.4): the maintainer's C2 ruling, as designed.
- **Test flips, merge order, phase size, the docs build** (5.5–5.8): accepted as planned.
- **The two proposed advisory limits** (`autoscale.period` above 3,600 s; `client.ratelimit` below
  0.001 other than 0): accepted. Reported to the maintainer.
- **The R1 range changes** (5.1: the threads' floor of 0 for `-N 0`, all cores; the interval's
  floor of 1 s; `server.tls.handshake` 1 … 86,400 s): accepted.
