# REVIEW — Every duration option takes the forms the timeouts take

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 21c868f  ·  **Base:** develop (`origin/develop` 87002e6; merge-base 6c46cbd)  ·  **Date:** 2026-11-14
- **Verdict:** approved (one reproduced finding, F1, ruled contract-conformant and not blocking; it
  needs the gate's confirmation, below)
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

The correctness pass was one fresh reviewer given `GOAL.md`, the spec-excluded diff
(`git diff origin/develop...HEAD -- . ':(exclude)spec/'`), `invariants.md`, `review-rubric.md` and
the gate's contract readings: `-P` on `hs server` and `-Q` on both commands are in scope; the
random delay is a leading `-` with fractions kept, written attached; signed values on the
whole-second options are refused; `hs relay`'s read of `autoscale.period` is in scope; 2.9 only;
plan-gate ruling D1 (`-d` refuses `+5`, `.5`, `5.`, `1e3`, `inf`, `nan`, `1_000`, one grammar with
`-W`); whole-second options round fractions up; the launched delay is a plain decimal; two-word
`-d -10m` on Python 3.11–3.13 is a non-goal.

## Verification run

- `uv run pytest -q tests/test_duration_options.py` (3.14) → 386 passed.
- `UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run --python 3.11 pytest -q -p no:cacheprovider tests/test_duration_options.py`
  → 384 passed, 2 skipped (the two-word random form, skipped below 3.14).
- `uv run pytest -q -p no:cacheprovider` (full suite, 3.14) → 2509 passed, 95 skipped, 0 failed.
- `uv run pytest tests/test_cluster_launch.py tests/test_completions.py tests/test_size_duration.py -k "contract or completion or duration or time or launch"`
  → 362 passed, 1 skipped (launch contract test included).
- New tests against `develop`: `tests/test_duration_options.py` fails to import there
  (`parse_delay`); with only the branch's `core/types.py` swapped in, 270 fail for the defect
  (`invalid int/float value`, `'1e-05' != '0.00001'`, `KeyError: 'period'`, relay `rc 5 != 2`).
- Parser tables (3.14 and 3.11, identical): every option × command through its own `interface`, for
  `30s`, `5m`, `1h30m`, `1.5h`, `1:30`, `1:00:00`, `1-12`, `1-0:30`, `45`, `1.5` and the refusals
  `5MB`, `1hr`, `2hours`, `1w`, `30m1h`, `''`, `'  '`, `-5`, `+60`, `-5m`, `+5m`, `1e3`, `inf`, `.5`,
  `5.`, `1_000`. All 268 non-`-d` refusals carry `-W`'s message for the same text (0 differences);
  `-d` adds the random-form note.
- `.agents/factory/bin/temp_site.sh`: `hs cluster t.in --no-db -N1 -w 30s -S 5s -P 2m -Q 1m --delay-start=-2s`
  → exit 0; `hs server` with `hs client --delay-start=0.5s` → exit 0; `hs cluster --no-db --period 5MB`,
  `hs server --period=5MB`, `hs submit -w 1hr`, `hs client -S -5`, `hs wait abc -n 1w`,
  `hs run -n +60 echo`, `hs client -d +5` → exit 2, naming the value.
- Config and environment in fresh interpreters: `HYPERSHELL_AUTOSCALE_PERIOD` = `5m`/`1:30`/`1-0:30`/`1.5h`
  and TOML `period = "2m"` reach the relay's site period, `hs cluster -P` and `hs server -P` as
  300/90/88200/5400/120; `HYPERSHELL_SUBMIT_BUNDLEWAIT=30s`, `HYPERSHELL_TASK_SIGNALWAIT=5s`,
  `HYPERSHELL_SERVER_POLL=1m` reach submit/server/client/cluster as 30/5/60; TOML numbers 45 and 2.5
  pass through unchanged. A bad string (`5MB`, `"1hr"`, `"-5"`) exits 2 without a traceback on
  relay, server, cluster and submit, while `hs --help`, `hs config get` and `hs list --count` still
  exit 0 (the relay no longer reads configuration at import).
- Launched client words (`client_options`) for `-d -10m -w 1m -S 5s`, `--delay-start=-0.5`, `-d 1.5s`,
  `-w 1.5`, `-0.00001`, `-1-12`, `-0.3333h`, `1e20` → `-w 60 -d -600.0 -S 5`, `-0.5`, `1.5`, `-0.00001`,
  `-129600.0`, `-1199.88`, `100000000000000000000`: no exponent. The 2.9.0a4 and 2.9.0a8 clients
  parse every one on 3.11 and 3.14; bare argparse on 3.12/3.13 takes `-0.00001` as a value but
  refuses `-1e-05`, which is why the plain decimal matters.
- `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs …`
  → exit 0 (sandbox off: `sphinx_sitemap` binds a socket).
- `git diff origin/develop...HEAD -- . ':(exclude)spec/'` was the only diff the reviewer saw;
  `git status --porcelain` was empty on hand-back.

### Merge with the moved base

`develop` moved from 6c46cbd to 87002e6 (#105) after the branch was cut. `git merge-tree --write-tree
origin/develop HEAD` reports **one conflict, in `docs/cli/server.rst`**: both sides add a
`.. versionchanged:: 2.9.0` block at the same point. Keeping both blocks resolves it; `server.py`,
`docs/_include/server_help.rst` and `ROADMAP.md` merge on their own. On that merged tree (built
outside the worktree), `tests/test_duration_options.py`, `tests/test_server_initdb.py`,
`tests/test_server.py`, `tests/test_size_duration.py` and `tests/test_cluster_launch.py` → 822
passed, 12 skipped, and the Sphinx `-W` build exits 0. The branch needs that resolution before it
publishes; it is not a defect in the branch.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `parse_time` as the type of `-w` (`submit.py`, `server.py`, `client.py`, `cluster/__init__.py`), `-S` (client, cluster), `-P` and `-Q` (server, cluster), `-n` (`task.py`); 69ca302, b8e5b2a | Parser tables on 3.14 and 3.11, two-word and attached; `temp_site` cluster run | ✅ |
| R2 | `parse_delay` (`core/types.py`) on `-d` (client, cluster); 69ca302 | `--delay-start=-10m` → -600.0, `=-1:30` → -90.0, `-d-10m`, `=1.5s` → 1.5, `-d 0.5`, `-d -600` on 3.11 and 3.14; `test_delay_waits` stubs sleep and the draw | ✅ |
| R3 | Option defaults through `parse_time`; relay `RelayApp.site_period` (`relay/__init__.py`), `DEFAULT_SITE_PERIOD` from the built-in default (`relay/site.py`); 21c868f | Env and TOML strings for all four keys reaching every reader found by grep (option defaults, both autoscalers, relay site period) | ✅ |
| R4 | `parse_time`/`parse_delay` refusal with `TIME_FORMS` | Refusal table (0 message differences from `-W`); CLI exits 2; bad config strings exit 2 cleanly | ✅ |
| R5 | Bare numbers unchanged; config numbers passed through | Develop-vs-branch table: only `+45`/`-5` (ruled), `1.5` (now 2, ruled) and `1_000` (F1) differ; `-d` bare numbers bit-identical (`test_delay_bare_number_unchanged`) | ✅ (F1 noted) |
| R6 | `plain_seconds` in `cluster/launch.py` `client_options` | Launched words above; 2.9.0a4 and 2.9.0a8 clients parse them; launch contract test passes | ✅ |
| R7 | Help text, `docs/_include/*_help.rst`, `config_param_ref.rst`, `share/` completions, `versionchanged:: 2.9.0` on six CLI pages and the config reference | `hs <cmd> --help`; zsh descriptions; `zsh -n`/`bash -n`; `test_completions`; Sphinx `-W` | ✅ |
| R8 | `tests/test_duration_options.py` (23 tests, all marked) | Pass on 3.14 and 3.11; fail against develop for the defect | ✅ |

Unmapped changes (possible scope creep): none. `plain_seconds` maps to R6; the relay's import-time
default maps to R3; `issues/duration-units-config-keys.md` and its `ROADMAP.md` entry map to the
first Non-goal, and the seed's claim holds (`HYPERSHELL_SERVER_EVICT=10m hs server t.in --no-db`
exits 5 with `TypeError: '>=' not supported between instances of 'str' and 'int'`; the roadmap link
resolves); `docs/getting_started.rst` maps to R7.

## Findings

### F1 [MEDIUM / CONFIRMED behaviour · ruled contract-conformant, not blocking] Underscore-grouped integers are refused on the whole-second options
- **Where:** `src/hypershell/core/types.py:155-160` (`parse_time`), the type of `-w`, `-S`, `-P`,
  `-Q` and `-n`.
- **Failure scenario:** `hs submit -w 1_000` and `hs cluster -P 1_000 f` read 1000 on `develop`
  (`type=int`); on the branch they exit 2 with `Duration '1_000' is not valid: expected …`. The same
  holds for a TOML string `bundlewait = "1_000"`. An environment variable is unaffected: it is coerced
  to the integer 1000 before HyperShell reads it.
- **Evidence:** the reviewer's develop-vs-branch parser table, and the orchestrator's spot check
  through each app's `interface.parse_args` with `PYTHONPATH` on each tree: branch `-w 1_000` and
  `-P 1_000` → `ArgumentError`; develop → 1000; `-W 1_000` → the same `ArgumentError` on both trees.
- **Touches:** R5 (every value accepted today reads the same, signed values excepted) against R4
  (a value that is not a duration in full exits with "the error `-W` gives for the same text"), the
  Outcome ("the same forms and the same refusal") and the Non-goal ("any spelling `-W` does not
  take … the grammar is the grammar").
- **Disposition:** not a build defect. `-W` has refused `1_000` since 2.9.0a7, so R4 and the
  Outcome require this refusal, and accepting it would fail them. That is the same choice plan-gate
  ruling D1 made for `-d` on the same spelling. R5's carve-out names only signed values, so the gate
  should confirm that D1's "one grammar with `-W`" covers the whole-second options as well. The
  compatibility note drafted at publish should name underscore-grouped numbers next to signed values.
  If the gate rules that R5 governs instead, F1 blocks: it would then be a CONFIRMED finding on
  options declared in `client.py` and `server.py`, so the human gate applies.

### Observations (pre-existing, identical on `develop`; not graded)
- A number in an environment variable bypasses the refusals because the configuration loader
  coerces it before any parser runs: `HYPERSHELL_AUTOSCALE_PERIOD=+60` → 60, `=-5` → -5, `=1e3` →
  1000.0, `=inf` → inf. R5 protects numbers in configuration, and R4's sign clause speaks of options.
- A float in configuration reaches the launched client as written: `HYPERSHELL_SUBMIT_BUNDLEWAIT=1.5`
  makes `hs cluster` emit `-w 1.5`, which a 2.9.0a4 client refuses (`invalid int value`). `develop`
  emits the same word. R5 (a configured number keeps its meaning) and R6 pull against each other
  here, and the branch does not change it.
- R8 coverage: a refused configuration string is exercised by tests only through `hs cluster`; the
  other readers were checked by hand above.

## Human-gate triggers

- Not triggered. No CONFIRMED defect remains, and F1 is ruled contract-conformant. The diff touches
  `client.py` and `server.py`, but only their option types and help text. If the gate rules F1 a
  defect, the gate is triggered (see F1).

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
