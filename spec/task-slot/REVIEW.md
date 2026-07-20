# REVIEW — Expose a per-executor TASK_SLOT to tasks

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 9d03047e7fc7589ac81439340ed4813c642e1545  ·  **Base:** develop  ·  **Date:** 2026-07-19
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

## Verification run

Commands actually executed and their outcomes (the spine of the review). The blind correctness pass
ran in a fresh subagent; the two docs findings were then independently re-reproduced by the
orchestrator:

- `uv run pytest -q -k slot` → **10 passed**, 379 deselected.
- `uv run pytest -q tests/test_client.py tests/test_template.py tests/test_cluster.py` → **64 passed**.
- `seq 40 | hsx -N4 -t 'echo {slot}' | sort -un` → `0 1 2 3` (R1/R4).
- `hsx -N4 -t 'printenv TASK_SLOT' | sort -un` → `0 1 2 3` (R2); `printenv TASK_SLOT_COUNT` → `4` (R3).
- `hsx -N1 -t 'echo {slot}/{slot_count}'` → `0/1` (R5).
- `hsx --from-json - -N4 -t 'echo {slot}'` (array input) → submit-side `TASK_TEMPLATE_ERROR` rejection
  (correct — JSON mode expands the user template submit-side, without slot context); env vars
  `TASK_SLOT`/`TASK_SLOT_COUNT` still present in the JSON-mode subprocess (`1`, `3`).
- `seq 8 | hsx --no-db -N4 -t 'echo {slot}' | sort -un` → `0 1 2 3` (**contradicts the new docs** — see F2).
- `grep -niE slot docs/_include/config_task_env*.rst docs/_include/templates_alt.rst` → **no matches**;
  `grep -rniE slot share/man/` → **no matches** (see F1). `manual.rst:214,262` include
  `templates_alt.rst` + `config_task_env_alt.rst`; `conf.py:126` generates the man pages from `manual`.
- `uv run sphinx-build -q docs docs/_build` → exit 0, no new warnings (only pre-existing notes);
  `{slot}` renders in `templates.html`.
- `git status --porcelain` → empty (reviewer left the tree clean).

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 — distinct `0..N-1`, each once | `client.py:1230,1235` (`id=count+1`, `range(num_threads)`) + `slot` property `id-1` (`client.py:680`) | `-N4 → {0,1,2,3}`; `-N1 → {0}` | ✅ |
| R2 — `TASK_SLOT` env (0-based decimal string) | `client.py:459` (`task_env`) + `:756` (called with `self.slot`) | `printenv TASK_SLOT → {0,1,2,3}` | ✅ |
| R3 — `TASK_SLOT_COUNT` env = `N` | `client.py:460` + `:756` | `printenv TASK_SLOT_COUNT → 4` | ✅ |
| R4 — `{slot}`/`{slot_count}` placeholders at client run time | `client.py:723` (expand w/ `context=`); `template.py:109` | `-N4 -t 'echo {slot}' → {0,1,2,3}`; JSON mode correctly rejected | ✅ |
| R5 — always defined; single-exec → `0`/`1` | `task_env` defaults `slot=0, slot_count=1` (`client.py:439`) | `-N1 → 0/1`; JSON-mode env vars present | ✅ |
| R6 — slot constant for executor lifetime | `slot = self.id - 1`, `id` set once at init, never mutated | stable `{0,1,2,3}` across 40 tasks | ✅ |
| R7 — docs: reference + placeholders + man pages + ≥1 worked pin example | `templates.rst:43` (placeholders + `taskset`/`CUDA` examples), `getting_started.rst:160` | placeholders + 2 worked examples ✅; **task-env reference + `_alt` snippets + man pages missing** | ❌ (partial — see F1) |

Unmapped changes (possible scope creep): **none.** Every hunk maps to an R-ID (`client.py` → R1–R6;
`task_env` → R2/R3/R5; template context → R4; docs → R7; three test files → R1–R6 coverage, all
correctly `@mark.unit`/`@mark.integration`, no `parameterize` placeholder). `tests/test_client.py` is
newly created (`client.py` previously had no dedicated test file) — a net positive.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### [HIGH/CONFIRMED] R7 partially unmet — task-environment reference and `share/` man pages omit `TASK_SLOT`/`TASK_SLOT_COUNT`
- **Where:** `docs/_include/config_task_env.rst`, `docs/_include/config_task_env_alt.rst`,
  `docs/_include/templates_alt.rst`, `share/man/man1/{hs,hsx,hyper-shell}.1`.
- **Failure scenario:** R7 requires the two new vars be described **"in the task-environment
  reference"** and the **"share/ man pages"** updated. The canonical task-env reference
  (`config_task_env.rst`/`_alt`, the `TASK_ID…TASK_ERRPATH` list) lists neither var. The man pages
  regenerate from `manual.rst`, which `.. include::`s the **`_alt`** snippets (`templates_alt.rst`
  line 214, `config_task_env_alt.rst` line 262) — but the diff only edited the non-`_alt`
  `templates.rst`. So `man hsx` shows no slot docs today, and a regeneration would *still* omit them
  because the `_alt` sources were never touched.
- **Evidence:** `grep -niE slot docs/_include/config_task_env*.rst docs/_include/templates_alt.rst`
  → no matches; `grep -rniE slot share/man/` → no matches; `docs/conf.py:126` `man_pages` sourced from
  `manual`; `manual.rst:214,262` include the un-edited `_alt` snippets.
- **Touches invariant / requirement:** **R7** (partially unmet) and **§12** project-conventions
  same-commit rule ("a CLI/feature change updates the affected `docs/_include/*.rst` help snippets
  **and** `share/` … in the same commit"). *Not* auto-CRITICAL (§12 is HIGH); the user-facing
  Templates page and `getting_started.rst` **do** carry the placeholders + two worked pinning
  examples, so R7's "placeholders" and "worked example" clauses are met — the gap is the reference
  table + `_alt`/man-page surfaces.

### [MEDIUM/CONFIRMED] Docs wrongly claim `{slot}` is unavailable under `--no-db`
- **Where:** `docs/_include/templates.rst:65-68` (and the `templates.html` it renders).
- **Failure scenario:** The new prose says the placeholders "are *not* available when a template is
  expanded at submit time (`--no-db`/JSON mode, or the `submit` command)." But `--no-db`
  (`in_memory`) is independent of `--from-json`: with **line** input, `--no-db` still ships
  `DEFAULT_TEMPLATE` to the client and expands the user template **client-side at run time**, so
  `{slot}` resolves. A user running `hsx --no-db -N4 -t 'taskset -c {slot} …'` is told by the docs to
  abandon `{slot}` for `$TASK_SLOT`, when `{slot}` in fact works. (No functional harm — the env-var
  alternative also works — but the guidance is factually wrong about a common mode.)
- **Evidence:** `seq 8 | hsx --no-db -N4 -t 'echo {slot}' | sort -un` → `0 1 2 3` (docs predict it
  would not resolve). The genuinely-submit-side cases are `--from-json` and the `submit` command
  (both verified). Fix: drop `--no-db` from that list, or qualify it as only-when-combined-with
  `--from-json`.
- **Touches invariant / requirement:** R7 (docs accuracy); no code defect.

### No code-correctness bugs
R1–R6 are implemented correctly and consistently threaded through both template expansion
(`create_task`) and env construction (`start_task`). The slot is derived once (`self.id - 1`,
1-based `id` set at executor init and never mutated → stable, no off-by-one, no gaps/dupes; N=1 →
`0`). `TASK_SLOT`/`TASK_SLOT_COUNT` are appended **after** the `**load_task_env()` spread in
`task_env`, so a user `HYPERSHELL_EXPORT_TASK_SLOT` cannot clobber the real slot. The
template-error / resource-error early-exit paths are unchanged and still use the correct `< -1000`
sentinels without acquiring resources (§7 intact). No §1–§11 invariant violation observed; the
`client.py` change is additive and behind defaults.

## Human-gate triggers

**Not triggered.** Both CONFIRMED findings are documentation-only
(`docs/_include/*.rst`, `share/man/`). Neither touches the high-blast-radius core
(`client.py` code, `data/model.py`, `server.py`, `core/queue|tls|fsm|thread|signal.py`,
`cluster/remote|ssh.py`) nor a security / DB-lifecycle invariant. The `client.py` code change itself
passed the correctness pass clean. No mandatory human sign-off required — this is a normal doc-only
remediation loop back to `hs-build`.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

Not run this cycle (both findings are already actionable; the completeness pass is optional). The
correctness pass surfaced the one completeness-relevant gap on its own: TECH.md phase P3 asserted the
man pages/`share/` were "unaffected (nothing to regenerate)", but the man pages are built from
`manual.rst`, which includes `templates_alt.rst` + `config_task_env_alt.rst` — so the P3 man-page
reasoning was incorrect and the man-page/reference surface was left unshipped (F1).
