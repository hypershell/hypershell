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

## Review cycle 2 — changes-requested (2026-11-22)

- **Reviewed commit:** 3dd066a4  ·  **Base:** develop (`origin/develop` at 6a0c28ea)  ·  **Date:** 2026-11-22
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3. One more build and review are left before the loop escalates to the human.
- **Mode:** remediation of F1 and F2 graded, plus a fresh blind pass over the delta `44e177b4..3dd066a4`
  and over the full diff wherever the delta touches it. The delta touches `Task.reached_group` and its
  callers, the retry construction in `Task.__schedule_next_failed_tasks` and the `Task.new` it calls,
  and the `--from-json` passage of `submit_desc.rst`. That scope was set by the foreman's brief.

### Pre-flight

- **Contract drift:** none since cycle 1. `GOAL.md` still has two commits on the branch, the shaping
  commit (8e047b2f) and the GOAL gate (46dcb430).
- **Contract readings given to the reviewer:**
  - the GOAL-gate block with the maintainer's C4 and C6;
  - plan-gate D1–D9, and the cycle-1 acceptance of `reached_group(in_flight=False)`;
  - the review-cycle-1 rulings: the scalar-subquery pass start; retries copy group and tags without
    re-parsing; the accepted retry window; D2 in the release note.
- **Invariants:** graded against the base copies (`git show origin/develop:…`) of
  `.agents/factory/invariants.md` and `AGENTS.md`.
- **Merge status:**
  - Develop: `origin/develop` is still 6a0c28ea after a fresh fetch. The branch is 19 ahead and
    0 behind, and `git merge-tree` is clean.
  - #117: `origin/fix/config-set-and-unread` is unchanged at 28117f27 and has not landed on develop.
    `git merge-tree --write-tree HEAD origin/fix/config-set-and-unread` is clean (tree 747da2b0).
- **Disclosure:** the orchestrator found G1 by reading the code before the reviewer returned, and
  the brief told the reviewer "`hs update` can write tags". The reviewer's N1 therefore confirms G1
  by its own executed drives; it is not an independent discovery.

### Verification run

- **Whole suite (SQLite, serial, PostgreSQL unset):** **3727 passed, 186 skipped** in 26 min 17 s
  (`pytest -q -p no:cacheprovider tests`, Python 3.14).
- **PostgreSQL-aware files** (`HS_TEST_POSTGRES` set, `HS_TEST_TIMESCALE` empty): **391 passed,
  6 skipped** in 5 min 46 s. The files: test_command_hash, test_group_integer, test_group_stream,
  test_reached_group, test_resubmit_lower_group, test_submit, test_groups, test_zone_postgres,
  test_contended_write, test_server_initdb, test_writeback_postgres, test_data/test_model and
  test_group_order.
- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) exits 0
  with no warnings.
- **Red check:** the four new tests run against the 44e177b4 source (`PYTHONPATH` set to a
  `git archive` of it) give **4 failed**. The scale test fails by its 10 s bound, and the three
  retry tests fail by assertion (exit 5, or the command cut to `false` in group 3). All four pass on
  HEAD. Against the base source, `test_cluster_json_retry_keeps_tags` fails too (exit 5, `ValueError:
  Tag value must only contain…`).
- **Reviewer's targeted runs:**
  - the four new tests on HEAD, c1 and base;
  - test_reached_group, test_group_integer and test_retry_gate with PostgreSQL: 161 passed, 1 skipped;
  - test_contended_write, test_model and test_group_order: 67 passed, 1 skipped;
  - test_resubmit_lower_group and test_group_stream: 23 passed;
  - the docs build, exit 0.
- **Process and database hygiene:** every server, cluster and client started was stopped by its
  recorded PID or process group. Every scratch PostgreSQL database (`rv2o_*` and `rv2r_*`) was
  dropped. The out-of-sandbox `ps` check shows none left.

### Remediation of cycle 1

**F1 — fixed.**
- **Results identical.** The orchestrator's randomized comparison of HEAD's `reached_group` with
  the 44e177b4 CTE form, both run in one process with both `in_flight` values:
  - SQLite: 1500 tables, text and real groups included, no mismatch;
  - PostgreSQL: 1000 tables, no mismatch.
  - The reviewer's 10 edge scenarios and 400 random tables per dialect, 820 comparisons each, also
    gave 0 mismatches.
- **The cycle-1 drive** (a 10k-line file, then `hs submit -- echo again`): HEAD 0.37 s, c1 8.43 s,
  base 0.38 s. The reviewer measured 0.4, 8.7 and 0.4 s.
- **SQLite at scale (HEAD, in-process):**
  - The orchestrator's four table shapes at 10k, 20k, 50k and 100k rows: at most 58 ms per call at
    100k. Growth is linear.
  - The plan is `SCALAR SUBQUERY 1/2/3`, each one search of `task`, run once.
  - The reviewer measured the c1 form at 4–8 s per call at 10k rows and 15–33 s at 20k, and HEAD at
    about 0.49 s at 1M.
- **PostgreSQL:**
  - 100k rows: HEAD 1–29 ms, c1 6–61 ms.
  - 1M rows, mixed shape, alternating calls: HEAD 10–195 ms, c1 380–778 ms.
  - All-finished table, where the pass start is NULL: HEAD 234–260 ms, c1 143–153 ms
    (orchestrator). The reviewer measured 0.67–0.79 s against 0.15 s, and at 3M rows about 2.0 s
    against 0.42 s.
- **The bound test** `test_reached_group_at_scale` is red on c1 and green on HEAD; the read takes
  milliseconds against a 10 s bound.

**F2 — fixed as ruled, with a residual path (G1).**
- The cycle-1 drive now exits 0. The retry row keeps `false # HYPERSHELL: group:x` in group 0,
  attempt 2, linked by `previous_id`. The first row is `retried` and names the retry as `next_id`.
- **Other input routes, by the orchestrator:**
  - `hs submit --template '{} # HYPERSHELL: group:x'` and `hs cluster --restart -r 1`: HEAD keeps the
    command and group 0, exit 0. c1 exits 5.
  - `--from-json` in two groups with `--eager -r 2`: the retries stay in group 0 with their command.
    The group then fails permanently and halts group 1, which is the documented group gate.
- **Other input routes, by the reviewer:** file, file with `--eager`, stdin to `hs cluster`,
  arguments, `--template` and `--from-json`. Every retry kept its args, group and fingerprint. The
  `retried`, `next_id` and `previous_id` chain was consistent on 4 databases.
- **The residual path:** a retry still takes a `group` from the failed task's stored tags and checks
  it. That is G1.

**Builder deviations (TECH, review cycle 1 remediation):**
- **`strict_tag=False` on the retry: accepted.** `strict_tag` only gates `ensure_valid_tag` on the
  `tag` argument of `Task.new`. The retry at `model.py:1010` is the only caller the delta changed:
  - Every first-submission caller (`submit.py:241/257/563/587/1765`, `task.py:114/445`) is unchanged.
    The JSON path was already relaxed on base.
  - A retry only copies tags that were checked when they were stored: strictly for inline tags,
    `-t` and `hs update`, relaxed for JSON.
  - It fixes a pre-existing crash. On base, the retry of a `--from-json` task whose tag value holds a
    space exits 5, as shown in the red check above. The ruling's "copies the failed task's tags"
    covers it.
- **Three InitPlans on PostgreSQL: accepted.** Results are identical, and each InitPlan runs at most
  once. The cost is equal or lower in every shape measured except a table with every task finished,
  which is linear and under a second at 1M rows (see *Triaged*).
- **The retry sentence in the docs: accepted.** `submit_desc.rst:57-63` says, with its own
  `versionchanged:: 2.9.0`, that a retry runs the stored command in the task's group with its tags.
  The docs build is clean. The sentence is wrong only in G1's case; a fix to G1 makes it true.

### Requirement → evidence matrix (cycle 2)

| R-ID | Delta | Verified how | Status |
|------|-------|--------------|--------|
| R1, R2 | untouched | Not re-graded. Retries now keep `#` and marker text on every route (drives above). | ✅ (cycle 1) |
| R3 | retry construction | Submission is unchanged. The retry no longer re-reads the command's marker text, but it still checks a `group` held in the stored tags (G1). | ⚠️ G1 |
| R4, R5 | untouched | Not re-graded. | ✅ (cycle 1) |
| R6, R7 | `reached_group` | Values identical on both dialects (randomized and edge scenarios). Cost is linear: 58 ms or less at 100k on SQLite, and under a second at 1M on PostgreSQL. The PostgreSQL files pass. | ✅ |
| R8 | two test files | The four new tests are red on 44e177b4 and green on HEAD. A per-row pass start or a re-parsing retry would fail them. Both are SQLite-only, as the rulings ask. No test covers G1. | ✅ (G1 needs one) |
| R9 | `submit_desc.rst` | The retry passage has `versionchanged:: 2.9.0`, and the build is clean under `-W`. The retry window is documented twice in `task_groups_alt.rst`. `RELEASE-NOTE.md` names D2 (not graded). | ✅ (sentence wrong only in G1's case) |

Unmapped changes in the delta: none. Every change maps to the F1 or F2 ruling.

### Findings

#### G1 [MEDIUM/CONFIRMED] A retry still crashes the run when the failed task's stored tags hold a non-integer `group`

- **Where:**
  - `src/hypershell/data/model.py:612`:
    `other['group'] = ensure_group(tag.pop('group')) if 'group' in tag else group`
  - It is reached from the retry construction at `model.py:1010-1020` (`tag=task.tag`,
    `group=task.group`).
- **Failure scenario:**
  - `hs update group:x` is the tag form, documented as "tag KEY:VALUE". It writes `{"group": "x"}`
    into a task's tags, passing `ensure_valid_tag`.
  - When that task fails and is retried, `Task.new` pops the copied tag and checks it.
    `TaskGroupError` is raised in the Scheduler, the sole `Task.next` caller, and the run dies.
  - It dies again on every `--restart` with retries on, until the user removes the tag.
  - The retry does not copy the task's group either: an integer tag (`hs update group:7`) moves the
    retry to group 7 and drops the tag. That predates the diff, but it contradicts the F2 ruling
    ("copies the failed task's group and tags") and the new docs sentence.
- **Evidence (orchestrator):**
  - The drive, in a throwaway site:

    ```
    hs submit -- false
    hs update group:x -R -f
    hs cluster --restart --max-retries 1 -N1 --no-tls -H 127.0.0.1 -p $PORT
    ```

  - HEAD and 44e177b4: exit 5, `CRITICAL … EOFError`, and no retry row.
  - Base on SQLite: exit 0, with a retry row `false` in the text group `x`, attempt 2.
  - In-process on HEAD, `Task.next(limit=10, attempts=2)` on that failed row raises
    `TaskGroupError: task group must be an integer, not "x"`.
  - PostgreSQL, same drive: HEAD exits 5 the same way. Base also exits 5, with `DataError: invalid
    input syntax for type integer: "x"` from the Scheduler's insert.
- **Evidence (reviewer, N1):**
  - The same drive with a group-1 task waiting: `echo later` never runs, and every later `--restart`
    exits 5 in 0.4 s with `CRITICAL [hypershell.cluster] TaskGroupError`.
  - `hs server --restart -r 1` with a client exits 5 with `CRITICAL [hypershell.server]
    TaskGroupError`.
  - In-process, HEAD and c1 raise for the tag values `"x"`, `1.5` and `""` on both dialects.
  - Base stores them on SQLite. On PostgreSQL, base refuses `x` and `""` at insert and stores `1.5`
    as 2.
- **Predates the diff?**
  - No, on SQLite for every non-integer value, and on PostgreSQL for `1.5`. On PostgreSQL, base
    already crashed for `x` and `""`.
  - It was introduced by the diff's R3 check in `Task.new` and is present at 44e177b4. The delta
    rerouted the retry around the command's marker text but not around the stored tags.
- **Why it is in scope:**
  - It is the F2 class: a retry built from stored data raises in the scheduler.
  - The cycle-1 ruling says a retry copies the failed task's group and tags and "the run goes on".
  - The GOAL's non-goals keep rows already in a database unrefused, and the cycle is submit time
    only.
  - `issues/update-group-stores-text.md` covers only the field form, `group=`.
  - It is not deferrable: no GOAL criterion requires keeping it, and it can be repaired this cycle.
- **Touches invariant / requirement:**
  - R3, the F2 ruling, and the R9 sentence at `submit_desc.rst:57-63`.
  - No invariant is violated. The exception is raised while the retries are built, before any mark,
    so no mark or retry row is written. The failed row stays unmarked and retryable, which keeps one
    retry per failure (§3).
  - `data/model.py` is high-blast-radius.
- **Remediation:** for `hs-build` to design. It needs a test red on 3dd066a4, in the style of the F2
  tests.

### Triaged, not findings

- **PostgreSQL cost on an all-finished table** (reviewer N2, which it graded LOW/CONFIRMED). Not a
  finding: it is a performance difference with identical results, and it is linear and below a
  second at 1M rows. The rubric's scope is correctness. F1 was a finding because its cost was
  quadratic: minutes per `hs submit` at 100k rows on SQLite.
  - Cause: with the scalar subquery, PostgreSQL rewrites `max("group")` as a `Limit` over
    `Index Scan Backward using index_tasks_unscheduled`. When the pass start is NULL, no row passes
    the filter and the whole index is walked.
  - Measured at 1M rows: 0.23–0.26 s (orchestrator) or 0.67–0.79 s (reviewer) on HEAD, against
    0.14–0.16 s on c1. At 3M rows: about 2.0 s against 0.42 s (reviewer).
  - In other shapes HEAD is as fast as c1 or faster.
  - It is paid once per database-mode `hs submit`, `hs cluster FILE` or `hs server FILE`. Base does
    not run this query at all.
  - For the human: whether a 2.9.x follow-up should make the all-finished case cheap, for example by
    reading the pass start first and returning None when it is NULL.

### Outside the diff (not graded)

- **A retry does not carry `cores`, `memory` or `timeout`** (reviewer). The retry call does not
  pass those columns, and `Task.new` takes them from the tags, which no longer hold them after the
  first submission. So a retried task runs with no timeout. This is the same on base and HEAD. By
  the code at `model.py:1010-1020` and the reviewer's drive (a file line `false  # HYPERSHELL:
  timeout:30 cores:1 memory:1MB` with `-r 1`). A candidate seed for the foreman; not graded here.

### Human-gate triggers

- **Triggered.** G1 is CONFIRMED in `data/model.py`, a high-blast-radius file.
  - It violates no security or DB-lifecycle invariant: the `CANCEL_STATUS` filters are unchanged,
    and no mark or retry row is written on the failing path.
  - Cycle 1's trigger was discharged by the maintainer's sign-off on the review-cycle-1 rulings. This
    one needs a new sign-off before `/hs-build` remediates G1 and review cycle 3, the last before
    escalation, grades it.

## Review cycle 3 — approved (2026-11-22)

- **Reviewed commit:** f699c6f8  ·  **Base:** develop (`origin/develop` at 1f688c7c)  ·  **Date:** 2026-11-22
- **Verdict:** approved
- **Cycle:** 3 of ≤3, the last. The loop converged.
- **Mode:**
  - The remediation of G1 is graded.
  - A fresh blind pass covers the delta `3dd066a4..b1a086db`, spec excluded: the retry construction in
    `Task.__schedule_next_failed_tasks` and one test. Wherever the delta touches the full diff, the
    pass covers that too: `Task.new`, `ensure_group`, and the retry mark and commit in `Task.next`.
  - The orchestrator checked the merge with develop (#117).
  - The foreman's brief set this scope.

### Pre-flight

- **Contract drift:** none since cycle 1. `GOAL.md` still has two commits on the branch, the shaping
  commit (8e047b2f) and the GOAL gate (46dcb430).
- **Contract readings given to the reviewer:**
  - the GOAL-gate block with the maintainer's C4 and C6;
  - plan-gate D1–D9;
  - the review-cycle-1 rulings: the scalar-subquery pass start; retries copy group and tags without
    re-parsing; the accepted retry window; D2 in the release note;
  - the review-cycle-2 rulings: G1; N2 triaged; the retry's missing cores, memory and timeout belong
    to another cycle. The builder's choice to keep the group tag on the retry's tags without reading
    it was given to the reviewer to judge.
- **Rules:** graded against the base copies (`git show origin/develop:…`, now 1f688c7c) of
  `.agents/factory/invariants.md`, `AGENTS.md` and `.agents/factory/review-rubric.md`. The base
  rubric's "Absurd input is not a finding" was applied. No candidate this cycle turned on it.
- **Blindness:** the brief named the rulings only. Unlike cycle 2, the orchestrator suggested no
  defect to the reviewer.

### Merge status

- **Position:** HEAD f699c6f8 is a signed merge of `origin/develop` 1f688c7c (#117,
  config-set-and-unread) onto b1a086db. After a fresh fetch, `origin/develop` is still 1f688c7c, and
  the branch is 24 commits ahead and 0 behind.
- **No hand resolution:** `git merge-tree --write-tree b1a086db 1f688c7c` exits 0 with no conflicts.
  Its tree, 5eff472f, is the tree of f699c6f8.
- **Files both sides changed since 6a0c28ea:** `server.py`, `cluster/__init__.py`, `AGENTS.md`,
  `invariants.md`, `docs/cli/cluster.rst`, `docs/cli/server.rst` and `ROADMAP.md`. Each composes:
  - `server.py`:
    - The branch moved `is_file_input` to `submit.py` (D9) and imports `os`. #117 added
      `import math`, `check_evict` and `configured_eager`. All of these sit side by side.
    - `is_file_input` is imported from `submit.py` and still exported. `io` is neither imported nor
      used.
  - `cluster/__init__.py`:
    - One import block takes both sides' names.
    - In `check_arguments`, #117's evict, eager and rate checks run before the branch's JSON group
      check. Both run before any input is read.
  - `AGENTS.md` and `invariants.md`: against base, they differ only by the branch's §3 group text and
    the `AGENTS.md` queue-mode note. #117's §10 text is intact.
  - Docs: #117's `versionchanged` passages and the branch's are separate and do not contradict.
  - `ROADMAP.md`: #117's new memory-column entry sits beside the branch's `adopted:submit-input-checks`
    status.
- **Static check:** the four merged modules (`server.py`, `cluster/__init__.py`, `submit.py`,
  `data/model.py`) import cleanly. A symtable check finds no undefined global name in them.
- **Tests:** the merged tree passes the whole suite, #117's tests included (below).

### Verification run

- **Whole suite (SQLite, serial, PostgreSQL unset):** **4010 passed, 188 skipped** in 30 min 33 s
  (`pytest -q -p no:cacheprovider tests`, Python 3.14, merged HEAD).
- **PostgreSQL-aware files** (`HS_TEST_POSTGRES` set, `HS_TEST_TIMESCALE` empty): **426 passed,
  6 skipped, 1 failed** in 7 min 33 s.
  - The files: test_command_hash, test_group_integer, test_group_stream, test_reached_group,
    test_resubmit_lower_group, test_submit, test_groups, test_zone_postgres, test_contended_write,
    test_server_initdb, test_writeback_postgres, test_data/test_model, test_group_order and
    test_relay_advertise.
  - The failure: `test_server_initdb.py::test_postgres_initdb_forever`. A `--forever` server that had
    run its one task did not exit within 60 s of SIGINT, and was killed (status -9, expected 4). The
    whole SQLite suite and the reviewer's drives were running alongside it.
  - Reruns: the test alone passed 3 of 3, and the whole file 5 of 5 (53 passed, 4 skipped each).
  - It is not attributed to the delta: that test fails no task and builds no retry. It is recorded
    as a load flake, not a finding.
- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) exits 0
  with no warnings; 63 pages were rebuilt.
- **Reviewer's targeted runs:**
  - `test_cluster_retry_ignores_group_tag` on HEAD and on 3dd066a4, both dialects (the red check
    below);
  - the three earlier retry tests in `test_group_integer.py`, which pass on HEAD.
- **Process and database hygiene:**
  - The orchestrator's two pytest runs ran to completion. The reviewer recorded 128 PIDs and stopped
    each by its process group.
  - The reviewer created 19 scratch PostgreSQL databases (`rv3r_*`) and dropped all 19. A listing
    after the runs shows no database but the defaults.
  - The out-of-sandbox `ps` checks show none of these processes left.

### Remediation of cycle 2

**G1: fixed as ruled.**
- **The code** (`model.py:1010-1027`):
  - The retry pops a `group` key from a copy of the failed task's tags before `Task.new`. So
    `Task.new` takes `group=task.group`, the column, and never calls `ensure_group` on the tag.
  - The popped tag is put back on the retry's tags after construction (`:1026`).
  - The fingerprint is copied, as before.
- **Drives (reviewer, SQLite and PostgreSQL):** each ran `hs submit [-g N] -- false`, then
  `hs update group:V -R -f`, then the run:
  - `hs cluster --restart --max-retries 1 -N1 --no-tls -H 127.0.0.1`, with `x` and `7`: exit 0, both
    rows in group 0.
  - `hs server --restart -r 1` with an `hs client`, with `x` and `7`: server and client exit 0, both
    rows in group 0.
  - `--max-retries 2` after `-g 3`, and `--max-retries 2 --eager` after `-g 4`, with `x` and `7`:
    exit 0, three rows, all in the task's group.
  - `1.5`, empty, `nan`, `99999999999`, `-5` and `true`: exit 0, both rows in group 0. On PostgreSQL
    `hs update` itself refuses `nan` (exit 5), so that tag never reaches a retry.
  - In every drive the chain is consistent: `retried`, `next_id`, `previous_id`, attempt + 1 and one
    fingerprint throughout. Every attempt carries the same tag. On SQLite the group is
    `typeof integer`.
- **Red check:** `test_cluster_retry_ignores_group_tag` has four cases: sqlite and postgres, each
  with text and integer.
  - On 3dd066a4 (a `git archive` on `PYTHONPATH`; the subprocess `hs` was confirmed to import it), all
    four fail. Text: the cluster exits 5. Integer: the retry moves to group 7.
  - On HEAD, all four pass. The ruling's "tests for both are red on 3dd066a4" holds.
- **Docs:** `submit_desc.rst:57-63` says a retry runs the stored command "in the task's group, with
  its tags". Its `versionchanged:: 2.9.0` says the same. Both are now true in G1's case as well.

**Builder choice, the group tag kept on the retry but never read: accepted.**
- **Code search (spec excluded):** only `Task.new` and `split_argline` read a `group` tag key.
  - The client builds tasks with `Task.from_json`, with no group check.
  - The relay reads no tags.
  - `reached_group`, `increment_group`, `select_failed`, `count_retryable` and `mark_group_started`
    read the column.
  - The fingerprint is copied, so the tag does not enter it.
- **Executed on both dialects (reviewer):**
  - A task line with `# HYPERSHELL: group:2`, then `hs update group:x`.
  - The client's environment: `TASK_GROUP=2` and `TASK_TAG_GROUP=x` on attempts 1 and 2 alike.
  - The re-submission gate: a same-file `--restart`, and `--restart --update`, find the task present,
    insert nothing and exit 0. There is no R7 refusal.
  - `hs list -t group:x` returns both attempts, and `hs info` shows `group: 2` with `tags: group:x`.
  - Once the run has finished, `hs submit -g 0` is accepted.
- **Why it holds:** keeping the tag matches the F2 ruling ("copies the failed task's … tags"), so a
  tag filter matches every attempt of a chain.

### Requirement → evidence matrix (cycle 3)

| R-ID | Delta | Verified how | Status |
|------|-------|--------------|--------|
| R1, R2 | untouched | Not re-graded. Their tests pass on the merged tree, on both dialects. | ✅ (cycle 1) |
| R3 | retry construction | Submission checks are unchanged. A retry takes the group column, which was checked at submit, and never reads a group tag. The drives cover 8 tag values on both dialects. | ✅ |
| R4, R5 | untouched | Not re-graded. Their tests pass on the merged tree. | ✅ (cycle 1) |
| R6, R7 | untouched | Not re-graded. test_reached_group and test_resubmit_lower_group pass on both dialects on the merged tree. | ✅ (cycle 2) |
| R8 | one test | `test_cluster_retry_ignores_group_tag` covers text and integer tags on SQLite and PostgreSQL: red on 3dd066a4, green on HEAD. | ✅ |
| R9 | none (docs untouched) | The `submit_desc.rst` retry sentence is now true. `AGENTS.md` and `invariants.md` say "a retry copies a legacy text group", meaning the column, which still holds. The docs build is clean under `-W`. | ✅ |

Unmapped changes in the delta: none. Every change maps to the G1 ruling. The merge brings #117,
already on develop, whose code is graded in its own cycle's record, not here. This cycle checked
only that the merge composes.

### Findings

None. No finding is CONFIRMED or PLAUSIBLE.

### Triaged, not findings

- **The group key ends up last in the retry's tags** (reviewer). Because of
  `{**retry.tag, **group_tag}`, the retry's JSON text orders its keys differently from the failed
  task's on SQLite. Nothing compares a whole tag: every tag filter works by key. Cosmetic.
- **`hs update timeout:x` or `memory:x` stopping a retry.** This dissolves: `hs update` refuses both
  with exit 5 (UnitError), on HEAD and on base, so no such tag reaches a retry.

### Outside the diff (not graded)

- **`hs update cores:x`, then a retry, on PostgreSQL** (reviewer):
  - The Scheduler's insert fails with `DataError: invalid input syntax for type smallint: "x"`, and
    `hs cluster --restart --max-retries 1` exits 5. No retry row is committed.
  - `origin/develop` behaves the same.
  - This is the family of the retry reading `cores`, `memory` and `timeout` from its tags, which the
    review-cycle-2 rulings place in `update-client-retry` (its R7).
  - For the foreman: a candidate note for that cycle.
- **The `--forever` SIGINT flake** in test_server_initdb, above. It passed 8 of 8 reruns.

### Human-gate triggers

- **Not triggered.** This cycle has no CONFIRMED finding.
  - F1, F2 and G1 triggered the gate in cycles 1 and 2. The maintainer discharged each trigger by
    signing off the cycle's rulings before its remediation. The review-cycle-2 sign-off ("Approve
    the fix") also approved a merge on approval.
  - The G1 fix is in `data/model.py`, a high-blast-radius file, and it is the change that sign-off
    approved. The develop merge brings #117's edits to `server.py` and `cluster/__init__.py`. They
    are already on develop, and the merge adds no change of its own to them.
  - Nothing in this cycle asks for a new sign-off.
- **Next step:** `/hs-publish`.
