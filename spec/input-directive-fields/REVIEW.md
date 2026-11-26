# REVIEW — Task input sets every declarable field, with `KEY=VALUE` or `KEY:VALUE`

> Adversarial QA by `hs-review`. The correctness pass graded the branch diff (spec/ excluded) against
> [`GOAL.md`](GOAL.md) and the AGENTS.md invariants only, without `PLAN.md`/`TECH.md`. Every finding
> cites an executed command.

## Review cycle 1 — changes-requested (2026-11-26)

- **Reviewed commit:** ca288b1e · **Base:** develop · **Mode:** fresh blind pass, xhigh
- **Contract drift:** GOAL changed once after shaping (dac3d1fb), recording the maintainer's
  plan-gate rulings; graded as locked.
- **Verdict:** changes-requested (1 CONFIRMED HIGH under §12, 3 CONFIRMED LOW; all documentation)

### Verification run

- 22 input, update, group, zone, model and submit files, serially with `HS_TEST_POSTGRES` → 1,376
  passed, 1 skipped (by design: PostgreSQL stores only integer groups).
- 7 cluster and zone dispatch, display, workload and autoscale files, plus group order and resubmit
  → 187 passed.
- `sphinx-build -W --keep-going -b html` → exit 0; a fresh `-b man` matches `share/man/man1` bar `.TH`.
- About 13 CLI probe scripts in throwaway sites (submit, cluster, server, run, task submit, the
  queue path against a live `--no-db` server), one on PostgreSQL; develop's `src` exported beside the
  branch for side-by-side comparison of the directive reader and `hs update`'s `read_changes`.

### Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | inline `zone:`/`zone=`, `-t`, JSON, `hs run`; zone table on SQLite and PostgreSQL; `none`/`default` read as `--zone` reads them; JSON non-string zone exit 5 | ✅ |
| R2 | directive > tag-only line > `-t` > option, for submit, JSON, `hs run` and `hs cluster` | ✅ |
| R3 | both separators; each field by its option's parser and range (`cores=4.0` → 4 per ruling) | ✅ |
| R4 | `grop=2`, `GROUP=1`, `Cores=2`, `port=80` refused; `grop:2` a tag; `-t` exit 2 | ✅ |
| R5 | `exit_status=`, `tag=`, `id=` refused; with `:` they are tags | ✅ |
| R6 | file exit 5 naming the line; stdin whole input exit 5; streams refuse one line; `-t` exit 2 | ✅ |
| R7 | `--queue`, `--no-db` (cluster, server), no database configured | ✅ |
| R8 | 39 `hs update` forms identical to develop, bar a long-key message (now correct); `test_update_*` pass | ✅ |
| R9 | naked tag after another refused on `-t`, inline and a queue stream | ✅ |
| R10 | submit, run, cluster, server pages and markers; `-W` build | ❌ partly (F1, F2) |

Unmapped changes: a tag-rule refusal on a stream now refuses one line rather than ending the input
(documented, `test_group_stream` updated); `hs task submit` on the shared reader (fits the GOAL's
outcome; its docs are F1); the unserved-zone warning (the plan-gate clarification); `check_tag`'s
key-size message now correct for a non-text value. None harmful.

### Findings

#### [HIGH/CONFIRMED, §12] F1 — `hs task submit` changed, its page did not
- **Where:** `src/hypershell/task.py:82,117-121`; `docs/_include/task_submit_help.rst`,
  `docs/cli/task_submit.rst`.
- **Failure scenario:** `hs task submit -t zone=tz cores=3 -- echo ts1` stores zone `tz`, cores 3, no
  tags; `--help` says so, but the page documents `-t` as tags only and has no 2.9.0 marker for it.
- **Evidence:** the command above and `hs list`; `git diff develop...HEAD` over the three task-submit
  docs files is empty; the built page has no `KEY=VALUE`.
- **Requirement:** R10 / AGENTS.md §12. The page is `:orphan:` and the GOAL's list does not name it.

#### [LOW/CONFIRMED] F2 — `hs cluster` help contradicts the behaviour
- **Where:** `docs/_include/cluster_help.rst:51-57,163-165`, `docs/_include/cluster_desc.rst:10`.
- **Failure scenario:** the help says tasks read from FILE are placed in the `--zone` zone and every
  `--from-json` key becomes a tag; an input zone wins over `--zone` and the five field keys are fields.
- **Evidence:** `hs cluster -N1 --zone gpu z.in` with `zone:other` inline stores the task in `other`
  and warns it is unserved.
- **Requirement:** R10.

#### [LOW/CONFIRMED] F3 — A new example does not run as written
- **Where:** `docs/_include/submit_desc.rst:30` (`echo '…' | hs submit`); the same pattern pre-exists
  in `docs/_include/task_groups_alt.rst:10-11`.
- **Failure scenario:** with no argument `hs submit` prints its usage and exits 1; `hs submit -`
  stores the task.
- **Evidence:** both commands in a throwaway site; develop behaves the same.

#### [LOW/CONFIRMED] F4 — A removed behaviour is not in the change marker
- **Where:** `docs/_include/submit_desc.rst`'s `versionchanged:: 2.9.0`.
- **Failure scenario:** a 2.8 user's `# HYPERSHELL: cores:none` cleared `-c` for that task; it now
  exits 5 (R3 requires this, and the prose states it, but the marker does not).
- **Evidence:** `Task.new(args='echo # HYPERSHELL: cores:none', cores=8)` on develop's export →
  `cores: null`; on the branch → `TaskResourceError`.

Notes (not findings): `.agents/factory/invariants.md:383` still says queue mode silently ignores
`-t`; `-t zone` is now refused there.

### Human-gate triggers

- None. Every finding is in documentation; no CONFIRMED finding in `data/model.py` or another
  high-blast-radius file, nor a security or DB invariant.
