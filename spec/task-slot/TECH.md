---
slug: task-slot
title: Expose a per-executor TASK_SLOT to tasks
kind: feature
appetite: small
status: blocked
branch: feature/task-slot
base: develop
current_phase: done
last_updated: '2026-07-19'
phases:
- id: P1
  name: Wire slot/count through the client executor (env vars + template placeholders)
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: .agents/factory/bin/temp_site.sh sh -c "seq 40 | uv run hsx -N4 -t 'echo
    {slot}' 2>/dev/null | sort -un"
- id: P2
  name: Automated tests (task_env vars, template context, slot-set integration)
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P1
  parallel: true
  hammerable: false
  hill: uphill
  verify: uv run pytest -v -k slot
- id: P3
  name: Document TASK_SLOT/TASK_SLOT_COUNT + placeholders with a worked pinning example
  status: done
  satisfies:
  - R7
  depends_on:
  - P1
  parallel: true
  hammerable: false
  hill: uphill
  verify: uv run sphinx-build docs docs/_build && grep -rq TASK_SLOT docs/getting_started.rst
    docs/templates.rst
review:
  last_reviewed_commit: 9d03047e7fc7589ac81439340ed4813c642e1545
  verdict: changes-requested
  blocked_reason: 'R7 docs incomplete: task-env reference (_alt) + man pages omit
    TASK_SLOT/TASK_SLOT_COUNT; templates.rst --no-db claim is wrong'
  cycle: 1
---
# TECH.md — Expose a per-executor TASK_SLOT to tasks

The **context engine and finite-state machine** for building this feature. The YAML frontmatter
above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/task-slot/TECH.md`); the per-phase checklists
below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).

## Conventions (apply to every phase)

- Invariants and code style come from [`AGENTS.md`](../../AGENTS.md); footgun checklist in
  [`invariants.md`](../../.agents/factory/invariants.md).
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change. Subjects: `[feature] Build task-slot P<n>: …` (no `WIP:`).
- **No `Co-Authored-By` trailer** (repo convention).
- The whole runtime change is confined to `src/hypershell/client.py`; `core/template.py`,
  `server.py`, and CLI flags are **not** touched (so `docs/_include/` help snippets and `share/`
  completions are unaffected — see P3 for the man-page check).

---

## Phase P1 — Wire slot/count through the client executor
**Satisfies:** R1, R2, R3, R4, R5, R6 · **Depends on:** —
**Goal:** every task run by the client sees `TASK_SLOT`/`TASK_SLOT_COUNT` in its environment and can
use `{slot}`/`{slot_count}` in a client-side template, with slots distinct (`0..N-1`) and stable per
executor. Verifiable end-to-end by driving `hsx`.

- [x] `task_env` (`client.py:439`): widen to `def task_env(task: Task, slot: int = 0, slot_count: int = 1)`;
      add `'TASK_SLOT': str(slot)` and `'TASK_SLOT_COUNT': str(slot_count)` to the returned dict (last, so they win).
- [x] `TaskExecutor.__init__` (`client.py:647`): add `slot_count: int = 1` param, store
      `self.slot_count`. Add a `slot` property returning `self.id - 1` with a one-line declarative
      docstring (0-based execution slot; `id` is 1-based).
- [x] `TaskExecutor.create_task` (`client.py:711`): pass
      `context={'slot': self.slot, 'slot_count': self.slot_count}` to `self.template.expand(...)`.
- [x] `TaskExecutor.start_task` (`client.py:743`): `env = task_env(self.task, self.slot, self.slot_count)`.
- [x] `TaskThread.__init__` (`client.py:916`): add `slot_count: int = 1`; forward it to
      `TaskExecutor(..., slot_count=slot_count)`.
- [x] `ClientThread` executor list (`client.py:1215`): pass `slot_count=num_threads` to each
      `TaskThread(...)`. (`count` stays `id=count+1`; `slot` is derived as `id-1`.)
- **Verify:** `.agents/factory/bin/temp_site.sh sh -c "seq 40 | uv run hsx -N4 -t 'echo {slot}' 2>/dev/null | sort -un"` → `0 1 2 3`.
      Also spot-check env var (`-t 'printenv TASK_SLOT'`) and degenerate (`-N1 -t 'echo {slot}/{slot_count}'` → all `0/1`).
- **Touches:** `src/hypershell/client.py`.

## Phase P2 — Automated tests
**Satisfies:** R1, R2, R3, R4, R5, R6 · **Depends on:** P1 · **Parallel:** yes
**Goal:** lock the behavior with deterministic unit tests plus one integration slot-set check.

- [x] `tests/test_template.py` (new, `@mark.unit`): `Template('{slot}/{slot_count}').expand('x',
      context={'slot': 2, 'slot_count': 4}) == '2/4'`; `{slot}` with no context raises
      `Template.UnmatchedPattern`; `{slot}` coexists with `{}` argument expansion.
- [x] `tests/test_client.py` (new, `@mark.unit`, uses `temp_site`): `task_env(task, 2, 4)` yields
      `TASK_SLOT=='2'`, `TASK_SLOT_COUNT=='4'`; `task_env(task)` defaults to `'0'`/`'1'` (R5); plus
      the `TaskExecutor.slot` derivation (`id-1`).
- [x] `tests/test_cluster.py` (`@mark.integration`): DB-mode `-N4 -t 'echo {slot}'` over 40 tasks →
      observed slot set `== {0,1,2,3}`; `-N1 -t 'echo {slot}/{slot_count}'` → `{'0/1'}`. Assert on
      the **set**, not per-task mapping.
- **Verify:** `uv run pytest -v -k slot`.
- **Touches:** `tests/test_template.py`, `tests/test_client.py`, `tests/test_cluster.py`.

## Phase P3 — Documentation
**Satisfies:** R7 · **Depends on:** P1 · **Parallel:** yes
**Goal:** users can discover and use the slot, with a copy-pasteable pinning example.

- [x] `docs/getting_started.rst` (§Dynamic, ~line 158): added `TASK_SLOT` / `TASK_SLOT_COUNT` to the
      task-env-var narrative, a "Pin resources per task slot" admonition (taskset + CUDA examples),
      and a `{slot}`/`{slot_count}` bullet in the template-pattern list.
- [x] `docs/_include/templates.rst` (rendered by the `templates.rst` stub): added an **Execution
      Slot** section documenting `{slot}`/`{slot_count}`, noting they resolve client-side at run time
      (DB-backed mode) — under `--no-db`/JSON or `submit` use `$TASK_SLOT`.
- [x] Man pages: `docs/manual.rst` only `.. include::`s the command `_include/{cmd}_*` snippets, not
      `getting_started.rst`/`_include/templates.rst`, so the man pages and `share/` are unaffected (no
      CLI flag added) — nothing to regenerate.
- [x] Confirmed no new Sphinx warnings (only the pre-existing `task_submit.rst`/`manual.rst` toctree
      warnings remain); build exits 0 and content renders in `templates.html`/`getting_started.html`.
- **Verify:** `uv run sphinx-build docs docs/_build && grep -rq TASK_SLOT docs/getting_started.rst docs/templates.rst`.
- **Touches:** `docs/getting_started.rst`, `docs/templates.rst`, possibly `docs/manual.rst` + `share/man/man1/*`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative).
2. Pre-flight: clean tree, on `feature/task-slot`, `develop` reachable.
3. Execute every `[ ]` (consult `PLAN.md` for detail).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (`set_phase.py`); STOP only on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
