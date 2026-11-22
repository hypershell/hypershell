# REVIEW — Submitted input is checked: `#` is part of the command, a group is an integer, and a group below the reached one is refused

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 44e177b4  ·  **Base:** develop (`origin/develop` at 6a0c28ea)  ·  **Date:** 2026-11-22
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

## Pre-flight

- **Contract drift:** `GOAL.md` has two commits on the branch: the shaping commit (8e047b2f) and
  the GOAL gate (46dcb430), which added the "Ruled at the GOAL gate" block with the maintainer's C4
  and C6 rulings. That block is the gate, not drift. The reviewer was given the gate block as
  binding. It was also given the plan-gate rulings D1–D7 and D9, and the accepted deviation that
  `Task.reached_group(in_flight=False)` counts only completed rows in its scheduled branch, as
  contract readings, because the brief directed it.
- **Invariants:** graded against the **base** copies (`git show origin/develop:…`) of
  `.agents/factory/invariants.md` and `AGENTS.md`. The diff edits §3 of both, and those edits were
  graded as part of the diff.
- **Merge status:**
  - Develop: the branch contains `origin/develop` (6a0c28ea) and is 13 commits ahead and 0 behind,
    so `git merge-tree` with develop is clean.
  - #117: `git merge-tree --write-tree HEAD origin/fix/config-set-and-unread` (d6d83afa) is clean,
    tree 0e97ef58. The two branches share seven files: `src/hypershell/server.py`,
    `src/hypershell/cluster/__init__.py`, `AGENTS.md`, `.agents/factory/invariants.md`,
    `docs/cli/cluster.rst`, `docs/cli/server.rst` and `ROADMAP.md`. None of them conflicts.
  - The merged tree passes this cycle's tests and #117's tests: 585 passed, 56 skipped
    (PostgreSQL unset).

## Verification run

- **Whole suite (SQLite, serial, PostgreSQL unset):**
  `env -u HS_TEST_POSTGRES PATH=<venv>/bin:$PATH pytest -q -p no:cacheprovider tests` gave
  **3723 passed, 186 skipped** in 26 min.
- **PostgreSQL** (`HS_TEST_POSTGRES=postgresql+psycopg://…/postgres`, `HS_TEST_TIMESCALE=` empty):
  - Files run: test_command_hash, test_group_integer, test_group_stream, test_reached_group,
    test_resubmit_lower_group, test_server_initdb, test_zone_postgres, test_writeback_postgres,
    test_contended_write, test_group_order and test_data/test_model.
  - Result: **342 passed, 6 skipped**.
- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` passes, and so does `-b man`
  (Python 3.12, docs group). No warnings.
- **Reviewer's targeted runs:**
  - The new and touched test files: **216 passed, 56 skipped** on SQLite, and **267 passed,
    5 skipped** with PostgreSQL.
  - Red check: test_command_hash and test_resubmit_lower_group run against the base source
    (`PYTHONPATH` pointing at a `git archive` of `origin/develop`) give **20 failed, 11 passed**.
    The tests tell the fix from the base.
- **Reviewer's CLI drives:** run in throwaway sites through `.agents/factory/bin/temp_site.sh`, on
  SQLite and PostgreSQL. The matrix below cites them.
- **F1 reproduced by the orchestrator** (SQLite, throwaway site): `seq 10000 | sed 's/^/echo /' >
  big.in; hs submit big.in`, then `hs submit -- echo again`.
  - The second submit took **8.17 s** on the branch and **0.37 s** on base (the same drive with
    `PYTHONPATH` set to the base source).
  - At 20k unscheduled rows, `Task.reached_group()` alone took **34.58 s** in-process and returned
    `None`.
- **F2 reproduced by the orchestrator:**
  - The drive:

    ```
    echo '[{"args": "false # HYPERSHELL: group:x"}, {"args": "echo fine"}]' > r.json
    hs cluster --from-json r.json --max-retries 1 -N1 --bind 127.0.0.1
    ```

  - Branch: exit **5**, CRITICAL `EOFError`, and no retry row.
  - Base: exit **0**, with a retry row `false` in group `x`, attempt 2.
- **Release-note draft (orchestrator check against the code):**
  - Three claims hold:
    - `-g 3000000000` and `-t group:2147483648` exit 2.
    - Under `--template`, a tag-only line sets the group and the template's own
      `# HYPERSHELL: group:9` is not read as tags.
    - A tag-only line in an `hs cluster` file runs with and without `--no-db`, exit 0.
  - One gap: the draft does not say that R7 can refuse an old task file on `--restart` or
    `--update` (plan-gate D2). The docs do say it (`task_groups_alt.rst`). For `hs-publish` to
    carry it; the release note is not graded here.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Task.split_argline` (model.py), the submit line split and `--template` split (submit.py) | Drives on `hs submit` (FILE, `-f -`, arguments, `--template`), `hs cluster` (FILE and stream, with a database and `--no-db`), `hs submit -q`, and `hs server` stream: every Problem-1 example stored or run as written. Tests: test_command_hash.py, 18 red on base. | ✅ |
| R2 | same | A probe of 31 forms through `split_argline`: comment lines, `#HYPERSHELL:`, tab and indent variants, a marker-only line. Tag-only lines carry their group forward. The upfront count matches the Loader. | ✅ |
| R3 | `ensure_group`, `parse_group` and `tag_text` (model.py); JSON and `-t` checks (submit.py, cluster) | Every refused and accepted value from R3, from files and JSON, plus the 32-bit bounds (D4). The rule is the same with `--no-db` and `--queue`. test_group_integer passes on SQLite and PostgreSQL. | ✅ |
| R4 | `scan_task_lines` and `read_in_full` (submit.py); `ClusterApp.prepare_source`; `ServerApp.prepare_source` | Exit 5 naming the line (files, stdin, arguments) or the record (JSON, from 0, D7). Task, Source and Zone counts are 0 after a refusal, `--zone` included. `-t` and `-g` exit 2, in queue mode too (D6), before stdin is read. | ✅ |
| R5 | the `Loader` (`refuse_lines`) | Streams into `hs cluster` (with a database, `--no-db`, a FIFO), `hs server` stdin and `hs submit -q` stdin. Each bad line gets an ERROR with the correct line number. A refused tag-only line sets nothing. Exit 0. `nan`, `inf` and a 20-digit group do not hang. test_group_stream passes on both dialects. | ✅ |
| R6 | `Task.reached_group`, `refuse_below_reached_group` and `SubmitApp.check_reached_group` | Live run at group 5: `-g 0`, a file, stdin and `-t group:1` exit 2 with nothing inserted. At or above the reached group, and with no group, the submission is accepted (C9 defaults to 5). Once the run finishes, any group is accepted. test_reached_group passes on both dialects. **F1:** on SQLite every database-mode `hs submit` pays a quadratic query. | ⚠️ correct but F1 |
| R7 | `decide_source_gate` / `GateDecision.commit` split; `reached_group(in_flight=False)` in cluster and server | Stranded database: another file, `hs server FILE`, `--repeat`, and `--restart --update` with an added lower line exit 2 with no Source row. The same-file `--restart` runs, and so does an added higher line. Cluster JSON is refused. test_resubmit_lower_group passes on both dialects. **F1** affects these commands' start. | ⚠️ correct but F1 |
| R8 | the five new test files and three updated ones | The suites above. Minor gaps, not filed: JSON and `-t` refusals and the tag-only stream refusal are tested on SQLite only (they are checked before any database read), and no test covers `reached_group` at scale. | ✅ |
| R9 | submit_desc.rst, task_groups_alt.rst, cluster.rst, server.rst, AGENTS.md, invariants.md | Each passage carries `versionchanged:: 2.9.0` and matches the drives, including the quoting escape and the D2 note. The docs build is clean under `-W`. | ✅ |

Unmapped changes (possible scope creep): none beyond the accepted rulings. Those are D9
(`is_file_input` moved to submit.py, and the tag-only-line crash fix), D6 (`-t` checked in queue
mode) and D5 (`--template` splits each line first). The `source_name` and `json_record_tags`
refactors and the gate's decide/commit split serve R4–R7. The ROADMAP and issue status changes to
`adopted:submit-input-checks` match the cycle.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### F1 [HIGH/CONFIRMED] `Task.reached_group` is quadratic on SQLite, so `hs submit` with a database stalls on any sizeable task table
- **Where:** `src/hypershell/data/model.py:853-873`. `pass_start` is a CTE joined `ON true` at
  line 867. It is reached from:
  - every database-mode `hs submit` (`submit.py:1689` → `:1701`; the C9 default reads it too, at
    `:1711`);
  - `hs cluster FILE` (`cluster/__init__.py:618`);
  - `hs server FILE` (`server.py:1977`).
- **Failure scenario:** on SQLite the CTE is planned as a co-routine searched inside the loop over
  `task`, per the reviewer's EXPLAIN QUERY PLAN. So `min(submit_time) WHERE completion_time IS NULL`,
  which has no index, is re-scanned for every row the outer query visits.
  - A plain second `hs submit` onto a database holding one earlier 10k-line file takes 8.17 s
    against 0.37 s on base.
  - 20k rows: 34 s, the reviewer and the orchestrator agreeing.
  - Each doubling of the rows takes about 4× as long. The reviewer's 100k-row call had not returned
    after several minutes; by extrapolation that is about 7 min per call at 100k and hours at 1M.
  - Timings depend on the table's shape. A table that is nearly all finished with the top group
    scheduled is fast: 7 ms for `in_flight=True`. `in_flight=False` took 0.23 s at 100k rows and
    2.3 s at 900k.
  - PostgreSQL is unaffected (16–20 ms at 100k rows).
- **Evidence:**
  - Orchestrator drive: branch against base timings as above, and an in-process
    `Task.reached_group()` of 34.58 s at 20k unscheduled rows.
  - Reviewer: the same drive at 20k (34 s against 1 s); `hs submit -g 0` and `hs cluster small.in`
    at 26 s each on 20k finished rows, against 2 s on base; in-process 1.03 s at 5k and 4.11 s at
    10k.
  - The reviewer's scratch diagnostic: an uncorrelated scalar subquery with the same predicate
    returned in 0.01 s where the branch query took 6.97 s (10k finished rows).
- **Touches invariant / requirement:** R6 and R7 (every refusal check and the C9 default pay it). It
  is a real bug on the most common path (`hs submit` with the default SQLite database), not an
  invariant violation. `data/model.py` is high-blast-radius. The remediation should add a timing or
  query-plan test at scale, which R8 does not cover today.

### F2 [MEDIUM/CONFIRMED] A retried `--from-json` task whose literal args hold `# HYPERSHELL: group:<non-integer>` now crashes the run
- **Where:** `src/hypershell/data/model.py:686`, the `ensure_group` added to `split_argline`.
  The retry re-parses stored args through `cls.new(args=task.args, …)` at `model.py:1009`
  (`parse_inline` defaults to True).
- **Failure scenario:** a JSON record's `args` are stored literally, marker text included. When
  that task fails and is retried, the retry re-parses the args. The new integer check raises
  `TaskGroupError` in the scheduler, and the run dies:
  - Branch: exit 5, CRITICAL `EOFError`, no retry row.
  - Base: exit 0. Base's own retry was already wrong (command cut to `false`, group the text `x`).
- **Evidence:** the drive in *Verification run*, run by both the orchestrator and the reviewer.
- **Touches invariant / requirement:** R3 and R5's "no group value SHALL hang a run". It is a
  crash, not a hang, but the same class. This diff widens a crash that predates it: base already
  exits 5 on a malformed tag in that marker text (`k:a"b`, per the reviewer). The input is
  contrived, so the path is an edge path; it grades MEDIUM rather than the reviewer's LOW, because
  the result is a whole run aborted, not a risk. It does not meet the deferral exception, since no
  GOAL criterion requires keeping the crash and it can be repaired this cycle. `data/model.py` is
  high-blast-radius.

### Triaged, not findings

- **Pending retries and the reached group.** The GOAL's term counts "incomplete tasks or pending
  retries". The implementation counts a failure whose retry row is not yet created as finished
  (scenario S7 → `None`), so `hs submit -g 0` is accepted in that window, on both dialects
  (reviewer's crafted rows).
  - Not a finding:
    - The GOAL left "how exhausted failures count and how it is computed" to the plan.
    - Whether a failure will be retried depends on the server's `--max-retries`, which is not
      stored.
    - The docs state the behaviour ("a failure counts as finished unless its retry has been
      created"), and an under-estimate is covered by the late-group warning, as the GOAL's
      racing-server note accepts.
  - For the human: confirm this reading of "pending retries".
- **`--template` identity drift after a tag-only line** (reviewer): under `--template`, lines after
  a tag-only line now take its group, so their identity changes on `--update`. This is a direct
  consequence of plan-gate D5, not a finding.

## Human-gate triggers

- **Triggered.** F1 and F2 are CONFIRMED in `data/model.py`, a high-blast-radius file. Neither
  violates a security or DB-lifecycle invariant: `reached_group` keeps the `CANCEL_STATUS` filter
  and writes nothing. The rule is by file. Human sign-off is required before any further step
  (`/hs-build` remediation, then review cycle 2).

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
