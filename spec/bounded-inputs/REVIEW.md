# REVIEW — Every numeric setting has one range, one parser and one error

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only**. Every finding cites
> an **executed** command.

- **Reviewed commit:** a0c227a6  ·  **Base:** develop (39834600, merged in)  ·  **Date:** 2026-11-24
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3
- **Contract drift check:** `GOAL.md` changed after shaping only in the GOAL gate and the dated
  advisory-tier amendment (maintainer, 2026-11-24).

## Verification run

Fresh reviewer, xhigh effort, on Python 3.14 and 3.11, SQLite and local PostgreSQL 18.

- **Options:** every option of every command just inside, at and just outside each bound: exit 2,
  one line `<name>: expected <form> from A to B, given V`, nothing started, no database or TLS
  files created.
- **Configuration:** every numeric key from a file, a variable, `_env` and `_eval`: exit 3 with the
  source named.
- **R2 corpus:** units, Slurm forms, `1_000`, `+5`, spaces, sub-second periods, negative delays —
  identical to a develop checkout wherever develop accepted an in-range value (except F8).
- **Advisory tier:** one WARNING just past each limit, none at it, once per setting per process, one
  summary per submission for tags (except F5).
- **Suites:** full suite on 3.14 with PostgreSQL: 5690 passed, 32 environment failures (30 upgrade
  templates, 2 TimescaleDB version). On 3.11, the bounded and argparse-sensitive files: 2116 passed,
  1 failed (F3). Docs build `-W`: passes.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | `SETTINGS` covers all 46 numeric keys plus the zone keys and 8 option-only entries; defaults in range | ✅ (deviations ruled at the plan gate) |
| R2 | Option corpus identical to develop | ⚠️ F8 |
| R3 | One parser per kind across option, file text, variable, `_env`, `_eval` | ✅ |
| R4 | One-line refusal, exit 2/3 | ❌ F2 |
| R5 | Whole config checked at load; `hs config` exempt for a bad file value | ❌ F4 |
| R6 | No numeric read around the parsers (grep + drive) | ✅ |
| R7 | `hs config set` refuses out-of-range, 64-bit overflow, `nan`, booleans | ✅ (docs: F1) |
| R8 | Backstop: 4,301-digit TOML integer, invalid UTF-8, injected errors | ✅ |
| R9 | Tags from file, stdin, `--from-json`, streams; `hs submit -t` exit 2 | ❌ F2, ⚠️ F8 |
| R10 | Config-derived API defaults checked; explicit arguments unchanged (HeartMonitor ruled) | ✅ (F7) |
| R11 | Tests | ❌ F3 |
| R12 | Help snippets and parameter reference match the table; markers present | ❌ F1 |
| R13 | Advisory warnings | ❌ F5 |

## Findings

### [HIGH/CONFIRMED] F1 — The `hs config set` page was not updated
- **Where:** `docs/_include/config_set_desc.rst:7-9`, `docs/cli/config_set.rst` (not in the diff).
- **Failure scenario:** the page still says a duration or size "is checked by the command that reads
  it". `hs config set` now refuses an out-of-range value for every numeric key, refuses an untyped
  integer outside 64 bits, and warns past an advisory limit; none of it is documented, with no
  `versionchanged` marker.
- **Touches:** R7, R12.

### [MEDIUM/CONFIRMED] F2 — `-t` resource tags on `hs run` (and the single-task submit app) skip the table
- **Where:** `src/hypershell/task.py:447` (`TaskRunApp.run`) and `task.py:123` (`check_tags`); both
  hand tags to `Task.new`, which never runs `ensure_resources`.
- **Failure scenario:** `hs run -n 1 -t cores:100000 -- echo hi` stores cores 100000 on SQLite and
  exits 5 with a `smallint out of range` traceback on PostgreSQL; `-t memory:8388608TB` exits 5 with
  a `UnitError` traceback, or exit 2 naming `size` instead of `task.memory`. Pre-existing on develop
  for cores, but R9 requires `-t` to exit 2.
- **Touches:** R4, R9.

### [MEDIUM/CONFIRMED] F3 — A new test fails on Python 3.11
- **Where:** `tests/test_bounded_options.py:154`, `'-d', '-10m'` as two tokens.
- **Failure scenario:** argparse on 3.11 reads `-10m` as an option. CI runs 3.11. Write
  `--delay-start=-10m`.
- **Touches:** R11.

### [MEDIUM/CONFIRMED] F4 — `hs config` stops on a file `_env`/`_eval` that cannot resolve
- **Where:** `src/hypershell/core/config.py:445`; `_resolved()` raises before the `hs config`
  exemption branch.
- **Failure scenario:** a file with `ratelimit_env = "UNSET_VAR"` makes `hs config get`, `which`,
  `edit` and `set` exit 3, so `hs config` cannot repair the file. On develop `hs config get` ran.
- **Touches:** R5.

### [LOW/CONFIRMED] F5 — No advisory warning for a single task submitted from arguments
- **Where:** `src/hypershell/submit.py:1806-1817` (`submit_one`).
- **Failure scenario:** `hs submit -t cores:1025 -- echo x` stores 1025 with no WARNING; the file,
  stdin and `--from-json` paths give the summary.
- **Touches:** R13.

### [LOW/CONFIRMED] F6 — The seed `issues/api-argument-ranges.md` misstates the problem
- **Where:** `issues/api-argument-ranges.md:20`.
- **Failure scenario:** it says `evict_after=10**30` "is used as given"; it raises on both branch and
  develop. It also lists `num_threads=0` as out of range, which is inside (0 = all cores).

### [LOW/CONFIRMED] F7 — `HeartMonitor` reports a very wide integer with the wrong exception
- **Where:** `src/hypershell/server.py:879`; `math.isfinite(10**400)` raises `OverflowError`.
- **Failure scenario:** `HeartMonitor(queue=Queue(), evict_after=10**400)` raises OverflowError, not
  the evict entry's refusal. API only.
- **Touches:** R10 (the plan-gate ruling).

### [LOW/CONFIRMED] F8 — Inline resource tags in a decimal form are read differently
- **Where:** the `task.cores`/`task.timeout` entries as used by `ensure_resources`.
- **Failure scenario:** `# HYPERSHELL: cores:4.0` refuses the whole file (exit 5); develop stored 4.
  `timeout:90.5` is rounded up to 91. A `--from-json` `"cores": 4.0` is accepted as 4. Tension
  between R2 and R9.

## Absurd-input and non-goal notes (not findings)
- `logging.file.rotate = "8388608TB"` (a text setting) now reaches croniter and exits 1 with a
  traceback; `"bogus"` does the same on develop.
- `hs update ID cores 100000` on PostgreSQL still exits 5: update assignments are the next cycle,
  `update-typed-columns`.
- An empty `HYPERSHELL_TASK_TIMEOUT=` is now refused (exit 3); develop read it as none.
- Advisory warnings from configuration loaded at import wait in `PENDING` until `main()` flushes
  them, so a library process never logs them.

## Human-gate triggers

F7 is in `server.py` and F2 names `data/model.py` (high-blast-radius core): the maintainer signs off
before publish.
