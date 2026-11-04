---
slug: writeback-prebuilt-update
title: Result writeback at or below 2.8.3's CPU cost
kind: fix
appetite: small
status: in_progress
branch: fix/writeback-prebuilt-update
base: develop
current_phase: P1
last_updated: '2026-11-04'
phases:
- id: P1
  name: Red statement-reuse guard and reference-equivalence pins (tests only)
  status: pending
  satisfies:
  - R4
  - R3
  - R2
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; l="${TMPDIR:-/tmp}/hs-wb-p1.log"; git diff --quiet bc6547a
    -- src tests/test_writeback.py && ! git diff bc6547a -- tests/test_writeback_postgres.py
    | grep -qE '^-[^-]' && { kt 600 pytest -m unit -rxX tests/test_writeback.py tests/test_writeback_statement.py
    > "$l" 2>&1; r=$?; tail -n 12 "$l"; test $r -eq 0; } && grep -q 'XFAIL tests/test_writeback_statement.py::test_writeback_reuses_its_statements'
    "$l" && kt 120 pytest --collect-only -q tests/test_writeback_postgres.py | grep
    -q test_applied_values_match_reference && kt 300 pytest -q -rs tests/test_writeback_postgres.py
- id: P2
  name: Prebuilt writeback statements in Task
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; f=src/hypershell/data/model.py; git diff --quiet bc6547a
    -- tests/test_writeback.py && test "$(git diff --name-only bc6547a -- src)" =
    "$f" && ! git diff bc6547a -- "$f" | grep -qE '^[-+].*(mapped_column|__tablename__|__table_args__|Index\()'
    && ! grep -q xfail tests/test_writeback_statement.py && kt 900 pytest -m unit
    -q tests/test_writeback.py tests/test_writeback_statement.py tests/test_server.py
    tests/test_cancel.py tests/test_update.py && kt 300 .agents/factory/bin/temp_site.sh
    sh -c "seq 100 | uv run --frozen hsx -t 'test {} -ne 7' -N4 -b1 --no-tls -f failed.txt
    2> err.log; test \"\$(cat failed.txt)\" = 7 && test \$(uv run --frozen hs list
    -c -S) -eq 99 && test \$(uv run --frozen hs list -c -F) -eq 1 && test \$(uv run
    --frozen hs list -c -w 'client_id == null') -eq 0 && ! grep -qiE 'stale|duplicate'
    err.log"
- id: P3
  name: 'R1 CPU evidence: interleaved in-process measurement'
  status: pending
  satisfies:
  - R1
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; kt 1800 python spec/writeback-prebuilt-update/cpu_bench.py
    && kt 120 python spec/writeback-prebuilt-update/check_evidence.py r1
- id: P4
  name: Final local gate and the foreman hand-back
  status: pending
  satisfies:
  - R2
  - R5
  - R6
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'kt() { uv run --frozen python -c ''import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)'' "$@"; }; f=$(git log -1 --format=%H -- src); l="${TMPDIR:-/tmp}/hs-wb-p4.log";
    git diff --quiet "$f" HEAD -- src tests && git diff --quiet bc6547a -- tests/test_writeback.py
    && test "$(git diff --name-only bc6547a -- src)" = src/hypershell/data/model.py
    && grep -q "$f" spec/writeback-prebuilt-update/EVIDENCE.md && grep -q 4d5771b
    spec/writeback-prebuilt-update/EVIDENCE.md && grep -q bc6547a spec/writeback-prebuilt-update/EVIDENCE.md
    && kt 120 python spec/writeback-prebuilt-update/check_evidence.py r1 && { kt 2400
    pytest -q -l > "$l" 2>&1 && tail -n 3 "$l" || { tail -n 40 "$l"; grep -E ''^(FAILED|ERROR)''
    "$l"; echo ''One rerun of the failed tests (known localhost flakes): record both
    runs''; kt 2400 pytest -q -l --lf; }; }'
- id: P5
  name: Record the CI and cluster evidence
  status: pending
  satisfies:
  - R6
  - R3
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; f=$(git log -1 --format=%H -- src); git diff --quiet
    "$f" HEAD -- src tests && kt 120 python spec/writeback-prebuilt-update/check_evidence.py
    r1 ci r6
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Result writeback at or below 2.8.3's CPU cost

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/writeback-prebuilt-update/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Why five phases

- **P1** commits the statement-reuse guard red against `bc6547a`'s source (R4 must fail there), and
  the reference-equivalence pins green (they pass on `bc6547a` and must keep passing).
- **P2** is the one coupled-core edit, in `data/model.py`, and turns P1's guard green.
- **P3** records R1's CPU evidence with the script the plan committed.
- **P4** is the final local gate and writes the foreman's hand-back.
- **P5** is an external wait: it records the CI run and the cluster runs once the foreman delivers
  them. `hs-build` never pushes, opens a PR, or touches a cluster.

## Touched surface

| File | Symbol | Change | Phase |
|------|--------|--------|-------|
| `tests/test_writeback_statement.py` | new module | guard + SQLite reference equivalence | P1, P2 (drop xfail) |
| `tests/test_writeback_postgres.py` | appended test | PostgreSQL/TimescaleDB reference equivalence | P1 |
| `src/hypershell/data/model.py` | `sqlalchemy` import; prebuilt statements; `Task.__apply_result` body | PLAN §2.1 | P2 |
| `spec/writeback-prebuilt-update/EVIDENCE.md` | new | R1 record (P3), hand-back (P4), CI and cluster (P5) | P3–P5 |

**Unchanged by design:** `tests/test_writeback.py` (byte-identical to `bc6547a`), `server.py`,
`Entity.update_all`/`update`, `Task.apply_results`'s body, `ResultOutcome`, `Task.result_columns`,
the wire codec, the schema, `data/upgrade.py`, `.github/workflows/tests.yml`, `docs/`, `share/`.

## Conventions (apply to every phase)

- Commit conventions, style and invariants come from [`AGENTS.md`](../../AGENTS.md); curated
  footguns in [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §3, §4, §12.
- One atomic commit per phase containing **both** the change and this file's state change. Subject:
  `[fix] Build writeback-prebuilt-update P<n>: …`. **No `Co-Authored-By` trailer.** Stage by
  explicit path.
- No CLI change, so **no** `docs/_include/*.rst` or `share/` updates (§12 does not trigger).
- Comments are declarative statements; **no R-IDs or phase ids in source or tests** (AGENTS.md).
- `verify:` commands run from the worktree root. `kt` runs a command in its own session and kills
  its process group at the deadline (exit 124); it runs under `uv run --frozen`, so the commands it
  starts resolve in the project environment. CLI drives isolate the site with
  `.agents/factory/bin/temp_site.sh`. Integration tests and the drive bind a loopback port: a
  sandbox that forbids it is an environment fault, not a red verify.
- No cluster tools, no network, no GitHub actions in any phase.

---

## Phase P1 — Red statement-reuse guard and reference-equivalence pins (tests only)
**Satisfies:** R4 (red half), R3 (pins), R2 (unchanged file) · **Depends on:** —
**Goal:** a guard that fails **by assertion** on `bc6547a`, and equivalence pins that pass there.
**No `src/` edit.** Every test references only symbols that exist on `bc6547a`.

- [ ] Create `tests/test_writeback_statement.py`, every test `@mark.unit`. Module docstring: the
      writeback reuses its statements, and stores exactly what the per-result statement stored.
- [ ] Copy (do not move) `task_db`, `submit`, `dispatch`, `dispatch_one`, `finish`, `receive`, `row`
      from `tests/test_writeback.py`. **`tests/test_writeback.py` is not edited in any phase.**
- [ ] `test_writeback_reuses_its_statements`: `monkeypatch.setattr(Session, 'execute', wrapper)` on
      the scoped session that `hypershell.data.model` imports. The wrapper appends the statement
      object to a list (keep the objects, not `id()`s) and delegates. Dispatch at least 8 tasks; return
      them through `receive` in at least two bundles, so that some are APPLIED, one is returned again
      (DUPLICATE), and one is returned after `Task.revert_orphaned` and a re-dispatch (STALE). Use
      `pytest.fail` for preconditions (outcome mix not reached), never `assert`. Then assert the
      UPDATE statements (`stmt.is_update`) number at least 8 and are all one object, and the SELECT
      statements issued by the writeback are at most one object. Mark it
      `@mark.xfail(strict=True, reason='the writeback builds a new statement for every result')`.
- [ ] `test_applied_values_match_reference`, `@mark.parametrize` over cases: `+05:30` with
      microsecond 999999; `-07:00` with microsecond 0; UTC; `outpath`/`errpath`/`csvpath` `None`;
      exit status 3. Two submitted tasks, dispatched together; finish both copies with identical
      result values for the case. Apply one through `Task.apply_results([...])`. Apply the twin
      through a **reference statement written in the test**, the way `bc6547a` builds it:
      `update(Task).where(Task.id == c.id, Task.schedule_time == c.schedule_time,
      Task.completion_time.is_(None)).values({n: getattr(c, n) for n in Task.result_columns})
      .execution_options(synchronize_session=False)`, then commit. Assert:
      the raw stored result columns (`Session.execute(text('SELECT … FROM task WHERE id = :id'))`)
      are equal between the two rows; the ORM-read result columns are equal; every other column of
      each row is as before the write. Capture the `apply_results` UPDATE's SQL with a
      `before_cursor_execute` listener (removed afterwards). Assert its SET list is exactly
      `set(Task.result_columns)`, and its WHERE names `id`, `schedule_time` and
      `completion_time IS NULL`.
- [ ] Append `test_applied_values_match_reference` to `tests/test_writeback_postgres.py` (existing
      lines untouched), using that module's `pg_tasks` fixture (both session time zones) and its
      helpers, with the same cases and assertions. Raw values come from a `text()` select through
      psycopg's own loaders. It skips locally and runs in the five CI `postgres` entries.
- **Verify:** `src/` and `tests/test_writeback.py` byte-identical to `bc6547a`; nothing removed
  from the PostgreSQL module. The writeback unit tests pass, with the guard reported as XFAIL. The
  PostgreSQL module collects the new test and skips.
- **Touches:** `tests/test_writeback_statement.py`, `tests/test_writeback_postgres.py`, this file.

## Phase P2 — Prebuilt writeback statements in Task
**Satisfies:** R1 (mechanism), R2, R3, R4, R5 · **Depends on:** P1
**Goal:** the conditional UPDATE and the classifying SELECT are built once per process; a result
binds only its values. Behaviour, SQL and stored values unchanged.

- [ ] In `src/hypershell/data/model.py`, add `bindparam` to the `sqlalchemy` import.
- [ ] Build the two statements once per process, per PLAN §2.1: against `Task.__table__`, every
      bind typed with `t.c[name].type`, every bind name prefixed (`r_…`), and the
      `(column, bind key)` pairs precomputed. Either a `functools.cache`d private `Task`
      classmethod or a module-level pair defined after `Task` is fine. Keep it private.
- [ ] Rewrite only the body of `Task.__apply_result`: keep the `schedule_time is None` → STALE
      return and its comment. Execute the UPDATE with **one parameter dict**; `rowcount == 1` →
      APPLIED; otherwise execute the SELECT with `r_id`/`r_schedule_time` → DUPLICATE if a row,
      else STALE. Keep the cancel-status comment.
- [ ] One declarative comment on the statements, saying why they are built once.
- [ ] Remove the `xfail` marker from `test_writeback_reuses_its_statements`.
- **Verify:** the `src/` diff is `data/model.py` only, with no column/table/index line;
  `tests/test_writeback.py` untouched; no `xfail` left. The writeback, statement, server, cancel and
  update unit tests pass. A CLI drive in a throwaway site, 100 tasks with one failure through
  `hsx -b1 -f`, gives 99 succeeded, 1 failed, `7` in the failures file, no row without a
  `client_id`, and no stale or duplicate warning.
- **Touches:** `src/hypershell/data/model.py`, `tests/test_writeback_statement.py`, this file.

## Phase P3 — R1 CPU evidence: interleaved in-process measurement
**Satisfies:** R1 · **Depends on:** P2
**Goal:** the CPU per result is recorded for 2.8 (`b819de6`), develop (`bc6547a`) and the fix, with
the method, and meets the bar.

- [ ] With the fix committed (P2), run `uv run --frozen python spec/writeback-prebuilt-update/cpu_bench.py`
      under a kill timer (defaults: N 5,000, six recorded rounds after one warm-up, 300 s per
      child). Do not edit the script's method. If it must change, amend PLAN §2.3 and say why in the
      commit body.
- [ ] Create `spec/writeback-prebuilt-update/EVIDENCE.md` with: **R1 method** (one paragraph,
      pointing at `cpu_bench.py`'s docstring); the host line (machine, OS, Python, SQLAlchemy, CPUs);
      the Markdown table the script prints; and the script's JSON summary as a fenced `json` block
      directly after the marker line `<!-- evidence:r1 -->`. State the fix's margin against 2.8 and
      against develop as percentages.
- [ ] If the bar is missed, **stop and escalate**. Don't tune the benchmark to pass.
- **Verify:** the script exits 0 (bar met on a fresh run), and `check_evidence.py r1` passes on the
  recorded block.
- **Touches:** `spec/writeback-prebuilt-update/EVIDENCE.md`, this file.

## Phase P4 — Final local gate and the foreman hand-back
**Satisfies:** R2, R5 (final), R6 (request) · **Depends on:** P3
**Goal:** the whole suite is green on the final fix, and the foreman has an exact run request.

- [ ] Run the full suite (the `verify:` allows one recorded `--lf` rerun for the known localhost
      flakes; record both runs in the commit body).
- [ ] Append to `EVIDENCE.md` a section **"R6 hand-back — cluster run request"**:
      - builds, by label and full commit: **2.8.3** = tag `2.8.3`, commit
        `4d5771b8a70a148142022117a30c5a4bf31e9afe`; **develop** = `bc6547a52fcf0ebd8dd04235a3e188a1480463f0`;
        **fix** = the output of `git log -1 --format=%H -- src` (the final fix commit; any later
        commit on the branch changes only `spec/`);
      - configuration: 256 single-executor clients, bundle size 1, TLS on, SQLite, a 16-CPU server
        node, at least 3 interleaved runs per build (each round runs all three builds; rotate the
        order);
      - what to return: a fenced `json` block after `<!-- evidence:r6 -->`, of the form
        `{"config": {"clients": 256, "executors_per_client": 1, "bundlesize": 1, "tls": true,
        "database": "sqlite", "server_cpus": 16}, "runs": [{"label": "2.8.3"|"develop"|"fix",
        "sha": "<full sha>", "order": <1..>, "tasks_per_s": <float>}, …], "ruling": "<only if
        short within the spread>"}`, plus the harness note (the foreman's own words) above it;
      - also: push the branch and open the draft PR so CI runs. Return a fenced `json` block after
        `<!-- evidence:ci -->`: `{"run_id": …, "head_sha": "<full sha>", "jobs": {"<job name>":
        "<conclusion>", …}}`, covering `tests (3.11)`…`tests (3.14)` and the five
        `PostgreSQL (…)` entries;
      - the bar, verbatim from GOAL R6, and how `check_evidence.py r6` reads "the runs' spread".
- **Verify:** no `src/` or `tests/` change since the final fix commit; R5's `src/` scope holds;
  `EVIDENCE.md` names all three commits; `check_evidence.py r1` passes; the full suite passes.
- **Touches:** `spec/writeback-prebuilt-update/EVIDENCE.md`, this file.

## Phase P5 — Record the CI and cluster evidence
**Satisfies:** R6, R3 (PostgreSQL/TimescaleDB green) · **Depends on:** P4
**Goal:** the foreman's evidence is in `EVIDENCE.md` and meets the bars.

- [ ] **External wait.** Until the foreman delivers both blocks, `hs-build` sets this phase
      `blocked` with `blocked_reason: awaiting foreman CI run and cluster runs` and stops. It
      never runs a cluster job, pushes, or reads GitHub itself.
- [ ] Paste the foreman's `ci` and `r6` blocks, and their notes, under their markers as delivered.
      Add a short results table (mean, min, max tasks/s per build; the fix against 2.8.3 and against
      develop in %).
- [ ] If `check_evidence.py r6` fails on a shortfall within the spread, record the numbers and stop
      for the maintainer's ruling (GOAL R6). Their ruling goes in the block's `ruling` field, quoted.
      A CI entry red for a reason outside this change needs a rerun or the foreman's ruling,
      recorded next to the `ci` block.
- **Verify:** no `src/` or `tests/` change since the final fix commit, and
  `check_evidence.py r1 ci r6` passes.
- **Touches:** `spec/writeback-prebuilt-update/EVIDENCE.md`, this file.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
