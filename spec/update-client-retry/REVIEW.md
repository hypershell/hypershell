# REVIEW — `hs update --delete` refuses changes it would ignore, `hs client` reads its own bundle keys, and a task that never ran is not retried

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `0bea846e` (`0bea846e3d399f8e5aec4671d19e0c5818458d08`) · **Base:** `origin/develop`
  `67019c7f` · **Date:** 2026-11-22
- **Verdict:** changes-requested. Two CONFIRMED findings, both introduced by the diff: F1 (MEDIUM,
  R8's pin is missing for `AutoScalingServer`) and F2 (LOW, a false sentence in the API pages). No
  correctness bug and no invariant violation was found. Route: `/hs-build` for F1 and F2.
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`.
- **Mode:** a single fresh blind reviewer (not `debate`), plus the orchestrator's whole-suite,
  PostgreSQL and docs runs.
- **Merge:** HEAD contains the merge of `develop` with #118 (`84c53e06`). `origin/develop` has
  since gained two `ROADMAP.md`/`issues/` commits. `git merge-tree --write-tree HEAD origin/develop`
  exits 0: it merges cleanly.
- **Contract drift:** `GOAL.md` has two commits on the branch: the shaping commit `4df71265`, and
  `b52d6734`, the GOAL gate's approval. That commit adopted `cluster-max-retries-defaults` into R8
  and recorded the rulings. It is the locked contract, and this review grades against it.
- **Reviewer inputs:** `GOAL.md`, the diff with `spec/` excluded, and the **base** copies of
  `invariants.md`, `review-rubric.md` and `AGENTS.md`. The branch's own edits to `AGENTS.md` and
  `invariants.md` were graded as diff prose. On the foreman's brief, the reviewer also got the
  plan-gate rulings and the P5 gate ruling, quoted from the end of `PLAN.md`, as binding contract
  readings (S1–S5, the §11 bend for `max_retries`, the absurd-input rule, the P5 environment). It
  got no other `PLAN.md` content.

## Verification run

Every run was serial, on Python 3.14, from the worktree.

- **Whole suite on SQLite** (orchestrator): `env -u HS_TEST_POSTGRES python -m pytest -p no:cacheprovider -q` → **4102 passed, 193 skipped**, 2 warnings (1690 s).
- **PostgreSQL files** (orchestrator): `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE= python -m pytest -q -p no:cacheprovider`
  with the twelve files `test_command_hash`, `test_contended_write`, `test_group_integer`,
  `test_group_stream`, `test_reached_group`, `test_resubmit_lower_group`, `test_retry_declarations`,
  `test_retry_terminal`, `test_server_initdb`, `test_timescale`, `test_writeback_postgres` and
  `test_zone_postgres`. It deselected the two TimescaleDB version-match tests, per the P5 ruling.
  Result: **445 passed, 22 skipped, 2 deselected** (251 s).
- **The cycle's five new test files on PostgreSQL**, run with `-rs`: `test_retry_terminal`,
  `test_retry_declarations`, `test_retry_defaults`, `test_update_refusals` and
  `test_client_bundle_keys` → **97 passed, 0 skipped**.
- **Docs** (orchestrator): `sphinx-build -W --keep-going -q -E -b html docs …` → exit 0, no warnings.
- **Reviewer, targeted tests** (PostgreSQL set):
  - the five new files plus #118's three retry tests (`test_cluster_json_retry_keeps_command_and_group`,
    `test_cluster_json_retry_keeps_tags`, `test_cluster_retry_ignores_group_tag`, both dialects)
    → 104 passed, 0 skipped;
  - `test_retry_gate`, `test_cancel`, `test_client_request`, `test_zone_model`, `test_zone_postgres`,
    `test_zone_autoscale`, `test_autoscale`, `test_config_unread` and `test_group_order` → 255 passed.
- **Reviewer, CLI drives** in throwaway sites (`temp_site.sh`), on SQLite and on PostgreSQL:
  - `hs cluster -r 2 -t 'echo {nope}'`: one `-1001` row; the client logs "(task did not run)".
  - `-r 2 --eager`, mixing `cores:100000` / `cores:1000`, `exit 3` and `kill -9`: the `-1002` task
    gets one row; `exit 3` and `-9` each run attempts 1–3.
  - `hs cluster -r 1 -W 2 -c 1 -m 1MB` on `sleep 6`: both attempts keep cores 1, memory 1048576 and
    timeout 2, and each is killed at 2 s.
  - Every R1/R2 combination exits 2, including with `HYPERSHELL_DATABASE_FILE` pointing at a path
    that cannot be opened, and with PostgreSQL pointed at a closed port. The filters-only control
    reaches the database and fails with exit 5, so the refusal comes first.
  - The `client.bundlesize`/`bundlewait` probe covered every form: file, environment variable,
    `_env` and `_eval`, in the file and in the environment, plus `[submit]` keys set alone.
  - The R8 probe built every entry point with the built-in configuration and with
    `HYPERSHELL_SERVER_ATTEMPTS=3`.
- **Reviewer, mutations** (each reverted; the tree was clean on hand-back):
  - never-ran filter removed from `__awaiting_retry` → 21 failed;
  - `timeout` dropped from `retry_columns` → 3 failed;
  - gate filter cut back to `attempt >= attempts` → 21 failed;
  - `count_retryable_by_zone` without the never-ran filter → 2 failed;
  - `AutoScalingServer`'s default forced to 1 → **54 passed** (this is F1).
- **Orchestrator, F2 reproduction:** a script builds
  `ServerThread(address=('127.0.0.1', 0), auth=…, in_memory=True)` and prints its
  `queue.config.size`. Run in a throwaway site, it prints `queuesize 1`; with
  `HYPERSHELL_SERVER_QUEUESIZE=7` it prints `queuesize 7`.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `task.py` `TaskUpdateApp.run`, refused before `ensuredb()` | CLI drives: `group=9`, `foo:bar` and `--remove-tag` with `--delete`, with and without `--limit`, with `-f`/`--no-confirm` → exit 2, rows unchanged; refused with an unopenable database (SQLite and PostgreSQL); `test_update_refusals` | ✅ |
| R2 | `task.py` `TaskUpdateApp.run` (single-task form) | `hs update ID group 7` with `--cancel`/`--revert`/`--delete`/`--remove-tag` → exit 2, "single-task form", nothing written; the plain legacy form still writes (guard test) | ✅ |
| R3 | `client.py` `advertised_size` (`client.bundlesize` in every form) | Fresh-interpreter probe: env 7→7, `_ENV`→6, `_EVAL`→5, file 4/`_env` 3/`_eval` 2; `[submit]` set alone → advertises nothing; `-b` wins (`test_explicit_options_win`) | ✅ |
| R4 | `client.py` `ClientApp.bundlewait = config.client.bundlewait` | Probe: `1:30`→90 from env, `_ENV`, file and file `_env`; `_EVAL "echo 30s"`→30; `submit.*` → 5; `-w` wins | ✅ |
| R5 | `data/model.py` `NO_RETRY_STATUSES` in `__awaiting_retry`, which feeds `query_retryable`/`select_failed`, the retry mark, `count_retryable` and `count_retryable_by_zone`, and through them `demand.py` and `watch.py`; `client.py` log text | CLI drives on both dialects; mutations M1 and M5; `test_retry_terminal` | ✅ |
| R6 | `data/model.py` `increment_group`: `or_(attempt >= attempts, never_ran)`, reason text | Halt message "at least 1 tasks not retried because they did not run, example: …", and the mixed form; `--forever` waits (PostgreSQL); mutation M3; `test_retry_terminal` | ✅ |
| R7 | `data/model.py` `Task.new_retry`, `retry_columns` | `-c`/`-m`/`-W` drive; `test_retry_declarations` on both dialects; mutation M2 | ✅ |
| R8 | `server.py` `configured_retries()`; `max_retries=None` across `ServerThread`, `serve_*`, the cluster classes and `AutoScalingServer` | R8 probe, default and `attempts=3`: all correct; `hs server`/`hs cluster` `-r` unchanged; `test_retry_defaults` covers 12 entries × 2, but **not `AutoScalingServer`** | ⚠️ F1 |
| R9 | `docs/_include/*`, `docs/cli/*`, `docs/api/*`, in-code help | Text checked against each R9 clause; `versionchanged:: 2.9.0` on each changed entry; no `hs task …`; no option changed, so `share/` is untouched; docs build `-W` clean. One false sentence (F2) | ⚠️ F2 |
| S1 | `cluster/topology.py:601` | Behaviour correct in the probe; no test (F1) | ⚠️ F1 |
| S2 | `Task.new_retry` copies the stored columns only | A legacy text group `'x'` with tags `{"group":"y","cores":"9"}` and an inline `# HYPERSHELL: group:5` keeps group `'x'`, cores, memory, timeout, tags and args; `cores:x` test on PostgreSQL | ✅ |
| S3 | `config_param_ref.rst` `[submit].bundlewait` | Matches the readers (`submit.py:1586`, `server.py:1839`, `cluster/__init__.py:160`) | ✅ |
| S4 | refusal messages | "single-task form" | ✅ |
| S5 | `advertised_size` raises `ConfigurationError` | `hs client` exits 3 at start for `"abc"`, `2.5` or `true` in a file, an unset `_env`, a non-numeric `_eval`, and env `abc`; the message never echoes an `_eval` value | ✅ |
| Column fence | `Task.retry_columns` / `result_columns` / fresh set | Every `Task` column is in exactly one set (9 / 13 / 12); `test_retry_columns_are_fixed` fails when `timeout` is dropped | ✅ |
| #118 guarantees | merge resolved to `cls.new_retry(task)` (`model.py:1066`), the only retry constructor | No inline re-parse; a `group` tag never moves or stops a retry; #118's three retry tests pass on both dialects | ✅ |

**Release note (GOAL's User-visible changes 1–6):** each item matches the behaviour the drives
observed. The note itself is drafted at publish.

**Unmapped changes:** the `invariants.md` §11 sentence now also names `evict_after=` (#117's
parameter) where it restates the rule that R8's bend narrows. It is accurate and trivial. Nothing
else falls outside an R-ID.

## Findings

### [MEDIUM/CONFIRMED] F1 — No test pins `AutoScalingServer`'s `max_retries` default
- **Where:** `tests/test_retry_defaults.py:131` (`ENTRY_POINTS`). The behaviour lives at
  `src/hypershell/cluster/topology.py:601`.
- **Failure scenario:** R8 says each entry point "SHALL be pinned by a test", and S1 makes
  `AutoScalingServer` an R8 entry point. `ENTRY_POINTS` lists the twelve others but not this one,
  and no other test reads its retries (`grep` of `tests/`). A regression of its default, such as
  restoring a fixed `1`, would pass the suite.
- **Evidence:** the reviewer's probe gives `AutoScalingServer` `[1, 1]` (server and publisher) with
  the built-in default and `[3, 3]` with `HYPERSHELL_SERVER_ATTEMPTS=3`, so the behaviour is
  correct. Mutation: with `configured_retries()` at `topology.py:601` replaced by `1`,
  `pytest tests/test_retry_defaults.py tests/test_topology_server.py` → 54 passed.
- **Touches:** R8 (with S1). This is a partial requirement, not a coverage nicety: R8's own text
  requires the pin.
- **Remedy target:** add an `AutoScalingServer` entry that reads both the server's scheduler
  attempts and the publisher's.

### [LOW/CONFIRMED] F2 — The API pages say `server.attempts` is the library's one configuration read
- **Where:** `docs/api/server.rst:16`, `docs/api/cluster.rst:20`.
- **Failure scenario:** both pages now say configuration files have no impact on the library, and
  that "the one exception is `server.attempts`". That is false. `ServerThread` reads
  `server.queuesize` (`server.py:1283`), and its dispatcher reads `server.bundlemax`
  (`core/queue.py:1207`). Every cluster class builds a `ServerThread`. The first sentence was
  already inexact on `develop`; the diff adds the "one exception" claim.
- **Evidence:** the orchestrator's F2 reproduction (see Verification run): `queuesize 1` by
  default, `queuesize 7` with `HYPERSHELL_SERVER_QUEUESIZE=7`.
- **Touches:** R9 accuracy (docs graded as prose). The marker is present and the build is clean,
  so this is not a §12 finding.
- **Remedy target:** drop the "one exception" claim. State only that a `max_retries` not given
  reads `server.attempts`.

### Notes (not findings)
- **Absurd input** (for `bounded-inputs`): on PostgreSQL, `cores:100000` overflows the `SMALLINT`
  `cores` column. The submit committer then dies and `hs cluster` hangs. This predates the diff.
- **Pre-existing, out of scope, already seeded:** on PostgreSQL, `-m 4GB` overflows the `INTEGER`
  `memory` column (`DataError`), while `-m 1GB` works. `issues/memory-column-overflows-postgres.md`
  tracks it.

## Human-gate triggers

- **Not triggered.** Neither CONFIRMED finding touches a high-blast-radius file or a
  security/DB-lifecycle invariant:
  - F1 is a missing test in `tests/`, for behaviour in `cluster/topology.py` that is correct.
  - F2 is in `docs/api/`.
- The diff itself changes `data/model.py`, `server.py`, `client.py`, `cluster/remote.py` and
  `cluster/ssh.py`, and the coupled retry selection, gate and demand queries. The review found them
  correct and in step, on SQLite and PostgreSQL (M1, M3 and M5 show the tests bind each side).
