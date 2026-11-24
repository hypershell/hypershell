---
slug: bounded-inputs
title: Every numeric setting has one declared range, one parser and one error
kind: fix
appetite: big
status: in_progress
branch: fix/bounded-inputs
base: develop
current_phase: P1
last_updated: '2026-11-24'
phases:
- id: P1
  name: 'Tests first: merge develop; the nine test files, the flipped tests, the red
    list on 8548053b and the guards'
  status: pending
  satisfies:
  - R11
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && git merge-base --is-ancestor d132d45c HEAD
    && RED_LIST=spec/bounded-inputs/drive/red.txt RED_BASE_REV=8548053b python3 spec/submit-input-checks/drive/bounded.py
    2700 sh spec/relay-hardening/drive/red_check.sh && sh spec/submit-input-checks/drive/run_list.sh
    spec/bounded-inputs/drive/guards.txt 1200 && python3 spec/submit-input-checks/drive/bounded.py
    120 uv run pytest -q -p no:cacheprovider --collect-only tests/test_bounded_table.py
    tests/test_bounded_parsers.py tests/test_bounded_options.py tests/test_bounded_config.py
    tests/test_bounded_config_set.py tests/test_bounded_tags.py tests/test_bounded_backstop.py
    tests/test_bounded_advisory.py tests/test_bounded_docs.py
- id: P2
  name: 'The table, the parsers and every option: SETTINGS beside KEYS, one parser
    per kind, RangedOption on all 59 numeric options, the advisory tier, help snippets'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R6
  - R13
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    2400 uv run pytest -q -p no:cacheprovider tests/test_bounded_table.py tests/test_bounded_parsers.py
    tests/test_bounded_options.py tests/test_group_integer.py tests/test_submit.py
    && python3 spec/submit-input-checks/drive/bounded.py 1200 uv run pytest -q -p
    no:cacheprovider tests/test_duration_options.py tests/test_size_duration.py tests/test_period_floor.py
    tests/test_bounded_advisory.py tests/test_bounded_docs.py -k "option or help or
    parse" && sh spec/submit-input-checks/drive/run_list.sh spec/bounded-inputs/drive/guards.txt
    1200 && for m in options advise; do .agents/factory/bin/temp_site.sh uv run sh
    "$PWD/spec/bounded-inputs/drive/bounds_drive.sh" $m || exit 1; done
- id: P3
  name: 'Configuration: the check at load with write-back, the hs config exemption,
    the retired checks as entries, hs config set, the backstop, API defaults'
  status: pending
  satisfies:
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  - R10
  - R13
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_bounded_table.py tests/test_bounded_parsers.py
    tests/test_bounded_options.py tests/test_bounded_config.py tests/test_bounded_config_set.py
    tests/test_bounded_backstop.py tests/test_bounded_advisory.py tests/test_config_schema.py
    tests/test_env_config.py tests/test_env_duration.py tests/test_env_blame.py tests/test_config_set_types.py
    tests/test_cluster_evict.py tests/test_period_floor.py tests/test_config_unread.py
    tests/test_autoscale_failed_launches.py tests/test_retry_defaults.py && python3
    spec/submit-input-checks/drive/bounded.py 900 uv run pytest -q -p no:cacheprovider
    tests/test_bounded_docs.py -k "not agents" && sh spec/submit-input-checks/drive/run_list.sh
    spec/bounded-inputs/drive/guards.txt 1200 && sh spec/bounded-inputs/drive/no_bypass.sh
    && for m in options config set advise; do .agents/factory/bin/temp_site.sh uv
    run sh "$PWD/spec/bounded-inputs/drive/bounds_drive.sh" $m || exit 1; done
- id: P4
  name: Resource tags through the parsers with one advisory summary; PostgreSQL storage;
    AGENTS.md, invariants, rubric; the whole suite, the red list green and the docs
    build
  status: pending
  satisfies:
  - R9
  - R11
  - R12
  - R13
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && env -u HS_TEST_POSTGRES python3 spec/submit-input-checks/drive/bounded.py
    7200 uv run pytest -q -p no:cacheprovider && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_bounded_tags.py tests/test_bounded_advisory.py
    tests/test_group_integer.py tests/test_group_stream.py tests/test_memory_column.py
    tests/test_size_duration.py && sh spec/submit-input-checks/drive/run_list.sh spec/bounded-inputs/drive/red.txt
    2700 && sh spec/submit-input-checks/drive/run_list.sh spec/bounded-inputs/drive/guards.txt
    1200 && sh spec/bounded-inputs/drive/no_bypass.sh && for m in options config set
    tags advise; do .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/bounded-inputs/drive/bounds_drive.sh"
    $m || exit 1; done && python3 spec/submit-input-checks/drive/bounded.py 900 uv
    run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    /tmp/claude-501/docs-html-bnd && python3 spec/submit-input-checks/drive/bounded.py
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b man
    docs /tmp/claude-501/docs-man-bnd
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Every numeric setting has one declared range, one parser and one error

The **context engine and finite-state machine** for building this cycle. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/bounded-inputs/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R1–R13 are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.

## Frontmatter field reference

As the template: top `status` `planned | in_progress | blocked | in_review | done`; phase `status`
`pending | in_progress | done | blocked`; `satisfies` the GOAL R-IDs; `depends_on`; `parallel`
(false throughout: every phase touches `core/*`); `hammerable` (false throughout: correctness);
`hill`; `attempts` (the durable circuit breaker); `verify`; `review.cycle`.

## Conventions (apply to every phase)

- `AGENTS.md` is the constitution; [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md)
  §3, §7, §10, §11 and §12 are the ones this cycle touches (PLAN § 3).
- One phase per `hs-build` invocation; one atomic, signed commit with the code and this file's state,
  subject `[fix] Build bounded-inputs P<n>: …`. **No `Co-Authored-By` trailer.**
- An option change updates its `docs/_include/*_help.rst` snippet (and `share/` completions where a
  value list changes) **in the same commit**; each changed prose passage carries
  `.. versionchanged:: 2.9.0` above it (never inside an included snippet).
- Python: `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-bnd`, `UV_CACHE_DIR` writable; pytest
  serially, never `-n`. PostgreSQL lines and the docs build need the sandbox off. Bind 127.0.0.1.
- Docs and help name top-level commands only; no example shows the key option.

## Ranges changed under R1

R1 allows a change only to (a) widen so a value that works and means something today stays in, (b)
lower a ceiling to a narrower place the value is stored or used, (c) raise a floor of 0 where 0 hangs
or spins. Each change, with its reason:

| Setting | GOAL | Plan | Rule | Reason |
| --- | --- | --- | --- | --- |
| Threads (`-N/--num-threads`, `relay.num_threads`) | 1 … 4,194,304 | **0** … 4,194,304; 0 is "all cores" | (a) | `-N 0` sets `CPU_COUNT` threads (`client.py:1362`), documented in `client_help.rst` and `cluster_help.rst` ("0=auto") and offered by both shells' completions. Refusing it would break a documented form. The advisory limit (above 1,024) is unchanged; 0 is never advised. |
| Interval (`-n/--interval`, `hs wait`, `hs run`) | 0 … 2³¹−1 s | **1 s** … 2³¹−1 s | (c) | `-n 0` polls the database in a tight loop (research 01). Durations round up, so 1 s is the least non-zero interval. |
| `server.tls.handshake` | 0 … 2³¹−1 s (Duration) | **1 … 86,400 s**, its own entry | (b) | `core/tls.py` `handshake_seconds` already refuses anything above one day (`MAX_HANDSHAKE`) and 0; the narrower place it is used. The floor stays 1, as today (0 is refused today, so no value accepted today is refused). |

Not range changes, stated so the review grades them as intended:

- `-N/--num-threads` is named so in messages; its third flag `--num-tasks` is a hidden alias.
- "Count or none": only `relay.min`, `relay.max` and `relay.init` take `<none>` (`int_or_none`);
  `logging.file.keep`, `autoscale.failed_launches`, `autoscale.size.*` and zone `min`/`max`/`init`
  are whole numbers from 0.
- `hs client -T/--timeout 0` and `-W/--task-timeout 0`, refused today by `client.py:1786-1790`, are
  inside the Duration range and read as "none", as `client.timeout`/`task.timeout` and `hs cluster`
  read 0. PLAN § 5.2 puts it to the plan gate.
- The size parser is exact, so 2⁶³−1 bytes (`9223372036854775807`) is reachable and 2⁶³ refused.
- `database.port` has no built-in default; R1's default test skips it.

Advisory limits (R13), as the GOAL's *Advisory limits*: threads above 1,024; `server.attempts` above
100 and `-r` above 99; cores above 1,024 (`task.cores`, `client.cores`, the `cores` tag); factor above
100; period above 3,600 s; rate below 0.001 other than 0. Every built-in default is inside them.

---

## Phase P1 — Tests first
**Satisfies:** R11 · **Depends on:** —
**Goal:** the branch carries develop (and `update-group-and-legacy-filters` if it has landed), and every
new test exists, collects on the base, and either fails there for what it checks (the red list) or
passes there (the guards).

- [ ] Merge develop into the branch (signed merge commit); at plan time `d132d45c`. If
      `update-group-and-legacy-filters` has landed, it comes with it; its `change_value` calls
      `parse_group`, which this cycle keeps (PLAN § 2.6).
- [ ] Shared helpers reused, not copied: `tests/input_checks.py` (`bounded`, `db_site`, `rows`),
      `tests/test_config_unread.py` (`file_layer`, `child_env`, `run_bounded`). A small
      `tests/bounded_checks.py` (not collected) for the R4 message regex and one CLI runner.
- [ ] `tests/test_bounded_table.py` (R1, R6, R13): `SETTINGS` keys = numeric `KEYS` + `relay.zone.*`
      zone keys + the `option.*` entries, by set equality; `SETTINGS` kinds match `KEYS`; every default
      leaf of `core/config.py` `default` parses into range and is inside its advisory limit
      (`database.port` skipped); every option that takes a value, walked over
      `HyperShellApp.commands` and each `interface._actions`, is a `RangedOption` naming an entry, or
      is in `TEXT_OPTIONS` (set equality); `test_no_plain_numeric_option_type` (no `type` is `int` or
      `float`; RED on the base by assertion); each `RangedOption`'s default in range.
- [ ] `tests/test_bounded_parsers.py` (R2–R4, unit): per kind and entry, each bound, one past each
      bound, `x`, `nan`, `inf`, `-inf`, a 4,300-digit and a 5,000-digit integer (text and `int`),
      `True`, a float for a whole kind, text for a numeric key; the message form; `shown` never raises
      and cuts; the duration and size forms and rounding as today; `parse_bytes('9223372036854775807')`
      exact; the period's `0.5` → 1; the delay's random form.
- [ ] `tests/test_bounded_options.py` (R2–R4, R6): `test_option_refused[...]` per `drive/red.txt` and
      one per kind for every command, exit 2, the R4 line naming the option as defined, no traceback,
      nothing started (no database file created, nothing bound); `test_option_in_range_unchanged`
      (each upper bound accepted and stored or used, joins the guards); the task-command aliases.
- [ ] `tests/test_bounded_config.py` (R3–R6, R10): file and variable refusals exit 3 with
      ` (from: …)` for every command family, before work; `_env`/`_eval` checked; `hs config get` and
      `hs config set` run with a bad file value, and a bad variable still refuses; `attempts = "3"`
      runs three attempts; the C1 status changes (period, ratelimit, evict → 3); gate (a)
      `test_loaded_numeric_keys_checked`; an API default from configuration (`ServerThread`'s
      `max_retries`) reads the checked value; `test_file_value_in_range_unchanged` (joins the guards).
- [ ] `tests/test_bounded_config_set.py` (R7): numeric and zone keys refused per entry, untyped
      integers outside 64 bits, a 4,300-digit value; exit 2, the key named, the file byte-identical.
- [ ] `tests/test_bounded_backstop.py` (R8): a child patches a start check, then an argument parse, to
      raise `OverflowError`/`ValueError`; exit 3/2, one line, no traceback; a `ValueError` after work
      starts keeps today's path.
- [ ] `tests/test_bounded_tags.py` (R9, R11): `-t cores:100000`/`memory:8388608TB`/`timeout:2147483648`
      exit 2; a file, stdin and `--from-json` refused whole (exit 5, nothing inserted, the value and line
      or record named) over `db_site`; a stream refuses only the line; on PostgreSQL the largest
      `cores` (32,767), `memory` (2⁶³−1), `timeout` (2³¹−1) and `attempt` (32,767, written through
      the engine and read back) are stored.
- [ ] `tests/test_bounded_advisory.py` (R13): for each advisory limit, just past it through an option,
      a file key and (for cores) a tag: one WARNING naming the setting, value and limit, the run goes
      on, exit 0 where the command completes; at the limit, no warning; twice in one process, one
      warning; many tag lines past the limit, one summary with the count and the first line.
- [ ] `tests/test_bounded_docs.py` (R12, R13): each help snippet gives each ranged option's range and
      any advisory limit (`rst_entry`, `MARKER` from `tests/test_client_host_default.py`); each
      `config_param_ref.rst` entry gives its key's range and advisory limit (`parse_reference`); the
      environment page, API pages, `AGENTS.md` § Configuration and the review rubric name the table.
- [ ] Flip the tests PLAN § 5.5 lists (research 05 § 5): `test_cluster_evict.py` (`REFUSALS` → exit 3,
      cap 2³¹−1, message), `test_period_floor.py::test_period_key_refused` (→ 3),
      `test_config_unread.py`'s three rate refusals (→ 3) and
      `test_cluster_rate_option_over_not_finite_key` (→ 3, C2), `test_config_set_types.py` `SIZED`
      evict cases (→ refused), the four message regexes.
- [ ] `drive/bounds_drive.sh` (modes `options`, `config`, `set`, `tags`, `advise`), in a `temp_site`;
      a plan dry run shows each mode fails on the base.
- [ ] Name tests so each phase's `verify:` selects what it lands: option tests carry `option`,
      help-page tests `help`, parser tests `parse`; the agent-docs test is
      `test_agents_and_rubric_name_table` (P4 only).
- [ ] Make every id in `drive/red.txt` and `drive/guards.txt` exist; amend the lists only to fix an id,
      never to drop a requirement. Add the two new in-range tests to the guards.
- **Verify:** red list RED on `8548053b` (PostgreSQL lines included), guards green, all new files
  collect.
- **Touches:** `tests/`, `spec/bounded-inputs/drive/`.

## Phase P2 — The table, the parsers and every option
**Satisfies:** R1, R2, R3, R4, R6, R13 (options) · **Depends on:** P1
**Goal:** `SETTINGS` holds every range and advisory limit; one parser per kind; every numeric option of
every command reads through it, refuses with the R4 line (exit 2) and advises once.

- [ ] `core/config_schema.py`: `Range`, the classes and `SETTINGS` (PLAN § 2.1), with this file's R1
      changes; the duration and size grammar moved in from `core/types.py`, the size exact; the
      whole-number length check; `parse_setting`, `Range.parse`, `SettingError`, `shown`, `advise`
      and its pending queue (flushed by `HyperShellApp.main` once logging is up).
- [ ] `core/types.py`: `parse_time`, `parse_bytes`, `parse_delay`, `parse_period`, `parse_rate` as
      wrappers over the entries (messages in the R4 form); `RATE_MINIMUM` from `RATE`;
      `RangedOption` and `ranged(...)` raising cmdkit's `ArgumentError`.
- [ ] `data/model.py`: `GROUP_MIN`/`GROUP_MAX` from `GROUP`; `parse_group`/`ensure_group` keep name,
      signature and `TaskGroupError`, the body the table's parser (PLAN § 2.6).
- [ ] Every numeric option to `ranged(...)` (research 01's 59, by command and destination):
      `submit.py`, `server.py`, `client.py`, `cluster/__init__.py`, `relay/__init__.py`, `task.py`
      (`hs list -l/-g/--part`, `hs update -l/-g`, `hs wait`/`hs run -n`). Remove the option checks the
      table replaces (`client.py:1786-1790`, the relay's size < 1, the option halves of
      `check_arguments`); keep the relations.
- [ ] Help snippets (`docs/_include/*_help.rst`) give each ranged option's range and advisory limit;
      the `versionchanged:: 2.9.0` above each `.. include::` in `docs/cli/*.rst`; completions unchanged
      (`-N` still offers 0, PLAN § 5.1).
- [ ] Amend tests only where the GOAL changed the behaviour they pin; the red list's option lines,
      the table and parser tests, the option advisory tests and the help-page docs test pass.
- **Verify:** the frontmatter `verify:` (the new table, parser and option suites; the option, help and parser tests of the touched suites; guards;
  `bounds_drive.sh options` and `advise`).
- **Touches:** `src/hypershell/core/config_schema.py`, `core/types.py`, `data/model.py`, `submit.py`,
  `server.py`, `client.py`, `cluster/__init__.py`, `relay/__init__.py`, `task.py`, `__init__.py`,
  `docs/_include/*_help.rst`, `docs/cli/*.rst`, `tests/`.

## Phase P3 — Configuration at load, `hs config set` and the backstop
**Satisfies:** R3, R4, R5, R6, R7, R8, R10, R13 (configuration) · **Depends on:** P2
**Goal:** every numeric key of the merged configuration is checked once at import and written back;
the retired checks are table entries; `hs config` still repairs a bad file; `hs config set` refuses by
the table; nothing numeric before work ends in a traceback.

- [ ] `core/config.py`: `CONFIG_COMMAND` (moved above the load, with `_relay_requested`);
      `check_settings(config, exempt_files=CONFIG_COMMAND)` inside the import-time `try`, before
      `check_auth_forms` and the relay swap (PLAN § 2.3); `_env`/`_eval` of numeric keys resolved once
      and written back; zone tables walked; refused file values replaced by defaults in the checked
      view under `hs config`; an unchecked view for `hs config get`/`which`; the refusal printed as
      one line without a class prefix, exit 3; `config_seconds` over the table.
- [ ] `core/config_schema.py` `read_value`: numeric kinds through the table's parsers (same message).
- [ ] Retire into entries (R6): `check_evict` and its call sites (`server.py`, `cluster/__init__.py`,
      the relay's evict read); `configured_failed_launches`; `configured_retries` (reads the checked
      value); `check_arguments`' period and rate; `handshake_seconds`' range; `set_retention_count`;
      the range parts of `relay_bounds`/`zone_settings`/`zone_bounds`. `HeartMonitor`'s explicit
      `evict_after` guard reads `EVICT`'s bounds (PLAN § 3 deviation).
- [ ] `config.py` `ConfigSetApp` (R7): numeric and zone keys through their entries; untyped integers
      outside −2⁶³ … 2⁶³−1 refused (digit count first); exit 2, nothing written, the value cut.
- [ ] `reading(stage)` (R8) in `core/exceptions.py`; applied around `from_cmdline` (mixin), the start
      checks, the import-time load and the app imports in `hypershell/__init__.py` (PLAN § 2.4).
- [ ] R10: API defaults from configuration read the checked values; `docs/api/*.rst` name the ranges.
- [ ] Docs: `config_param_ref.rst` gives each numeric key's range and advisory limit (a `Range:`
      sentence after the forms sentence, its marker in the entry's `.. only:: html` group);
      `config_intro.rst` (and `config_intro_alt.rst`) say a variable is held to its key's range and
      drop "checked by the command that reads it"; `invariants.md` §10 and `AGENTS.md` § Configuration
      updated for the load-time check (§10's `check_evict` sentence).
- **Verify:** the frontmatter `verify:` (configuration, `hs config set`, backstop and advisory suites;
  the touched suites; docs tests but the agent docs; guards; `no_bypass.sh` clean; `bounds_drive.sh`
  `options`, `config`, `set`, `advise`).
- **Touches:** `src/hypershell/core/config.py`, `core/config_schema.py`, `core/exceptions.py`,
  `core/tls.py`, `core/logging.py`, `config.py`, `server.py`, `cluster/__init__.py`,
  `cluster/remote.py`, `relay/__init__.py`, `__init__.py`, `docs/`, `AGENTS.md`,
  `.agents/factory/invariants.md`, `tests/`.

## Phase P4 — Resource tags, the agent docs and the whole suite
**Satisfies:** R9, R11, R12, R13 (tags) · **Depends on:** P3
**Goal:** `cores`, `memory` and `timeout` tags are read by their `task.*` entries and refused as #118
refuses a group; one advisory summary per submission; every doc names the table; the whole suite,
the red list and the docs build pass.

- [ ] `data/model.py`: `TaskInputError` base of `TaskGroupError` and a new `TaskResourceError`;
      `ensure_resources(tags, texts)` beside `ensure_group`, called from `split_argline`, `check_tags`
      and the JSON record check (`validate_json_records`, alias `validate_json_groups` kept).
- [ ] `submit.py`: the four group-error catches widen to `TaskInputError`; the whole-input scan and
      the stream `Loader` count advisory tags and log one summary per submission.
- [ ] PostgreSQL: the largest `cores`, `memory`, `timeout` and `attempt` stored (the P1 test).
- [ ] Docs: the task-input page (`docs/_include/submit_desc.rst` and the task tags page) gives the
      resource tags' ranges, advisory limit and refusal; `AGENTS.md` § Configuration names the table and
      the advisory tier; `invariants.md` §3 names `TaskInputError` and the resource check; the review
      rubric's "Absurd input is not a finding" drops "before `bounded-inputs` lands" and points at
      `SETTINGS`.
- [ ] The whole suite serially (bounded 7,200 s), the red list passing, the guards, `no_bypass.sh`,
      every `bounds_drive.sh` mode, and the docs build (html and man, `-W`, sandbox off).
- [ ] A `RELEASE-NOTE.md` draft naming the six user-visible changes, the R1 range changes and the
      `_eval`-once note, for `hs-publish`'s squash body.
- **Verify:** the frontmatter `verify:`.
- **Touches:** `src/hypershell/data/model.py`, `submit.py`, `cluster/__init__.py`, `docs/`,
  `AGENTS.md`, `.agents/factory/invariants.md`, `.agents/factory/review-rubric.md`,
  `spec/bounded-inputs/RELEASE-NOTE.md`, `tests/`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `fix/bounded-inputs`, develop reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (through `set_phase.py`; note it in the commit body).
   STOP and escalate only on a `GOAL.md` contradiction. If P2 or P3 overruns, the fallback is C4's
   split (R9 and R10 to a follow-up), ruled at that phase's gate, never a fifth phase.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one signed `[fix]` commit; stop.
