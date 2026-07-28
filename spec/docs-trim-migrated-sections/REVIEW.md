# REVIEW — Trim docs sections that have moved to hypershell.org

> Adversarial QA by `hs-review`, run with a freshly-spawned blind reviewer. The correctness pass grades
> the branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md`/`research/`/`META.md` (avoids grading-its-own-homework / plan-sycophancy). Every
> finding cites an **executed** command, not an assertion.

- **Reviewed commit:** 047e6f38bcc077976e9f24e9045f1b0eeb1c3833  ·  **Base:** develop  ·  **Date:** 2026-07-28
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Verification run

Commands actually executed (the spine of the review) — run both by the blind reviewer subagent and
independently re-confirmed by the orchestrator:

- `uv run sphinx-build -E -b html docs docs/_build 2>&1 | grep -E 'WARNING:|ERROR:'` →
  exactly the **2** pre-existing baseline warnings (`docs/cli/task_submit.rst`, `docs/manual.rst` —
  "document isn't included in any toctree"), **0 errors, 0 new warnings**; the build log emitted
  `Creating redirect …` for all 18 removed pages and ended `build succeeded, 2 warnings`.
- `ls docs/tutorial docs/blog docs/roadmap.rst` → all "No such file or directory" (removed).
- `grep caption-text docs/_build/index.html` → only `Intro` and `Reference`; no Tutorial/Project/
  Blog/Roadmap nav entry in the built sidebar.
- Exhaustive dangling-ref grep over retained `docs/**.rst` + `README.rst` for `:ref:`/`:doc:`/
  toctree pointers to removed docnames → **no matches** (only benign `joblog` prose substring).
- Inspected the emitted redirect stubs: 18 meta-refresh stub files under `docs/_build/` at the old
  paths (`roadmap.html`→`/about`, `tutorial/basic.html`→`/tutorials/basic`, `blog/index.html`→`/blog`,
  `blog/20260705_2_8_0_release.html`→`/blog/hypershell-2-8-0`, …), targets cross-checked against the
  sibling website checkout `../hypershell.org/` (all blog slugs, `content/pages/about.md`, and the
  `/tutorials/<slug>` route confirmed to exist; canonical host `www.hypershell.org` matches).
- `git status --porcelain` → empty (clean tree; only gitignored `docs/_build/` is untracked).

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `docs/tutorial/{basic,distributed,hybrid,advanced}.rst` deleted; Tutorial toctree caption removed from `docs/index.rst` (0c96d5b) | `ls` shows dir gone; built sidebar has no Tutorial entry | ✅ |
| R2 | `docs/blog/` (index + 12 posts) & `docs/roadmap.rst` deleted; Project toctree caption removed from `docs/index.rst` (0c96d5b) | `ls` shows removed; built sidebar has no Blog/Roadmap entry | ✅ |
| R3 | `sphinx_reredirects` extension + explicit 18-entry `redirects` dict in `docs/conf.py`; `sphinx-reredirects>=0.1.3` in `pyproject.toml` docs group; `redirect-runbook.md` for native RTD dashboard redirects (047e6f3) | 18 meta-refresh stubs emitted at old paths → correct `www.hypershell.org` targets, all resolving to real website routes | ✅ |
| R4 | `docs/alternatives.rst:518` dropped the `:ref:`roadmap`` clause; `docs/cli/index.rst:9` dropped the `:ref:`tutorials`` line (0c96d5b) | exhaustive grep finds no surviving pointer; build has no "undefined label" warning | ✅ |
| R5 | Atomic per-phase removals keep the build at baseline | build emits only the 2 pre-existing warnings, 0 errors, 0 new | ✅ |

Unmapped changes (possible scope creep): **none**. Changed surface is exactly `docs/index.rst`,
`docs/conf.py`, `docs/cli/index.rst`, `docs/alternatives.rst`, the deletions under
`docs/tutorial|blog` + `docs/roadmap.rst`, `pyproject.toml`, `uv.lock`. No `src/`, `share/`, or
`docs/_include/*.rst` touched (all Non-goals respected). The `pyproject.toml`/`uv.lock` addition is
docs-only `sphinx-reredirects` and maps to R3, not creep.

## Findings

**None.** No CONFIRMED or PLAUSIBLE defects. Every candidate gap (dangling refs, missing stubs,
nonexistent redirect targets, host mismatch, scope creep) was actively probed and refuted by command.

## Human-gate triggers

**None.** This is a `kind: docs` change touching only §12-style conventions — no high-blast-radius
core file (`data/model.py`, `server.py`, `client.py`, `core/queue|tls|fsm|thread|signal.py`,
`cluster/remote|ssh.py`) and no security/DB-lifecycle invariant is involved.

## Invariants

Only docs conventions in play. RST edits are single-line removals (no underline-length risk); no
`raw::` added to `README.rst`; no version hardcoded; the new dependency floor is pinned low per the
EPEL-parity convention and correctly placed in the `docs` group; docs build clean (R5). No §1–§11
subsystem invariant is touched.
