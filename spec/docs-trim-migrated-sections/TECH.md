---
slug: docs-trim-migrated-sections
title: Trim docs sections that have moved to hypershell.org
kind: docs
appetite: small
status: in_review
branch: feature/docs-trim-migrated-sections
base: develop
current_phase: done
last_updated: '2026-07-28'
phases:
- id: P1
  name: Remove Tutorial + Project (blog/roadmap) sections, toctrees, and inbound refs
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run sphinx-build -E -b html docs docs/_build 2>&1 | tee /tmp/hs_docs_p1.log
    | grep -E 'WARNING:|ERROR:'; test $(grep -cE 'WARNING:|ERROR:' /tmp/hs_docs_p1.log)
    -eq 2 && ! grep -q 'ERROR:' /tmp/hs_docs_p1.log && echo VERIFY_OK
- id: P2
  name: 'Preserve old URLs: sphinx-reredirects stubs + RTD dashboard runbook (R3)'
  status: done
  satisfies:
  - R3
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run sphinx-build -E -b html docs docs/_build 2>&1 | tee /tmp/hs_docs_p2.log
    | grep -E 'WARNING:|ERROR:'; test $(grep -cE 'WARNING:|ERROR:' /tmp/hs_docs_p2.log)
    -eq 2 && grep -q 'www.hypershell.org/about' docs/_build/roadmap.html && grep -q
    'www.hypershell.org/tutorials/basic' docs/_build/tutorial/basic.html && grep -q
    'www.hypershell.org/blog/hypershell-2-8-0' docs/_build/blog/20260705_2_8_0_release.html
    && echo VERIFY_OK
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Trim docs sections that have moved to hypershell.org

The **context engine and finite-state machine** for building this change. The YAML frontmatter above
is the resume ground-truth (read it with `uv run python .agents/factory/bin/next_phase.py
spec/docs-trim-migrated-sections/TECH.md`); the per-phase checklists below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.

## Conventions (apply to every phase)

- Invariants and code style come from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). Only **§12** (project conventions) is
  touched — no §1–§11 subsystem is involved. The one recorded deviation (a docs-group dev dependency)
  is in PLAN §3.
- One phase per `hs-build` invocation; one atomic commit containing **both** the docs change and the
  `TECH.md` state change. Subjects: `[docs] Build docs-trim-migrated-sections P<n>: …`.
- **No `Co-Authored-By` trailer** (repo convention).
- **Verify uses a fresh read** (`sphinx-build -E`) so a stale pickled env can't mask new warnings;
  the R5 baseline is **exactly 2** pre-existing `WARNING:` lines (`docs/cli/task_submit.rst`,
  `docs/manual.rst` — "document isn't included in any toctree") and **0** `ERROR:`.
- **Use `del`, not `rm`** if any scratch path must be removed (repo env rule).

---

## Phase P1 — Remove the migrated sections + inbound refs
**Satisfies:** R1, R2, R4, R5 · **Depends on:** —
**Goal:** The Tutorial and Project (blog + roadmap) sections are gone from the built docs — pages,
navigation captions, and the two would-dangle inbound refs — with the build still at its clean
2-warning baseline. One atomic edit (partial removal would spike new "undefined label" / "not in any
toctree" warnings and fail R5).

- [x] `docs/index.rst`: delete the **Tutorial** toctree block (lines 205–212) and the **Project**
      toctree block (lines 214–219) at the file tail. No other `index.rst` edits.
- [x] Delete the Tutorial pages: `docs/tutorial/{basic,distributed,hybrid,advanced}.rst` and the now-empty
      `docs/tutorial/` dir (use `del`).
- [x] Delete the Project pages: all of `docs/blog/` (the 12 posts + `index.rst`) and its dir, and
      `docs/roadmap.rst` (use `del`).
- [x] Delete the only two inbound refs from retained pages (verified exhaustive; `README.rst` clean):
      - `docs/cli/index.rst:9` — remove the whole line
        (`See our :ref:`tutorials <tutorial_basic>` (COMING SOON) …`).
      - `docs/alternatives.rst:518` — remove the trailing parenthetical only, leaving the sentence
        ending `… best.` (drop `(A ``hypershell-nextflow`` integration is on the :ref:`roadmap <roadmap>`.)`).
- [x] Confirm `docs/conf.py` needs **no** edit for removal (no `ablog`, no coupling) — do not touch it here.
- **Verify:** `uv run sphinx-build -E -b html docs docs/_build …` → prints only the two baseline
      `WARNING:` lines, asserts `VERIFY_OK` (exactly 2 warnings, 0 errors). Also eyeball that the
      removed files/dirs are gone and `docs/index.rst` has no Tutorial/Project caption.
- **Touches:** `docs/index.rst`, `docs/cli/index.rst`, `docs/alternatives.rst`, and deletions under
      `docs/tutorial/`, `docs/blog/`, `docs/roadmap.rst`.

## Phase P2 — Preserve the old URLs (R3, both mechanisms)
**Satisfies:** R3 · **Depends on:** P1
**Goal:** Old ReadTheDocs URLs for the removed pages no longer 404: the build emits meta-refresh stubs
at the old paths pointing to `www.hypershell.org`, and a committed runbook lets the maintainer add the
durable native RTD Page Redirects.

- [x] Add `sphinx-reredirects` to the **`docs`** dependency group in `pyproject.toml` (low floor,
      e.g. `>=0.1.3`; keep EPEL-parity spirit — it's docs/dev-only, not shipped). Run `uv lock` /
      `uv sync` so `uv.lock` updates.
- [x] `docs/conf.py`: add `'sphinx_reredirects'` to `extensions`, and an **explicit** `redirects = {…}`
      dict — the 18 entries from [`research/00-digest.md`](research/00-digest.md) (keys = deleted source
      docnames, values = absolute `https://www.hypershell.org/…` URLs). Explicit (non-wildcard) keys are
      required so stubs emit for the *deleted* pages.
- [x] **Reconfirm targets before finalizing** (digest "build-time" notes): the live tutorial route is
      `/tutorials/<slug>` (plural) — verify against `../hypershell.org/src/`; confirm each blog slug
      exists in `../hypershell.org/content/blog/` and `content/pages/about.md` backs `/about`.
- [x] Commit `spec/docs-trim-migrated-sections/redirect-runbook.md`: the same 18-row map as a maintainer
      checklist for native RTD **Page Redirects** (RTD admin/API — not repo-configurable), each old path
      → its `www.hypershell.org` target.
- **Verify:** `uv run sphinx-build -E -b html docs docs/_build …` → still exactly 2 baseline warnings,
      and the emitted stubs carry the right absolute targets
      (`docs/_build/roadmap.html`→`/about`, `docs/_build/tutorial/basic.html`→`/tutorials/basic`,
      `docs/_build/blog/20260705_2_8_0_release.html`→`/blog/hypershell-2-8-0`); asserts `VERIFY_OK`.
- **Touches:** `pyproject.toml`, `uv.lock`, `docs/conf.py`,
      `spec/docs-trim-migrated-sections/redirect-runbook.md`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative).
2. Pre-flight: clean tree, on `feature/docs-trim-migrated-sections`, `develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone. `VERIFY_OK` must print.
5. Amend this file if reality diverges; STOP and escalate only on a `GOAL.md` contradiction (e.g. a
   removed page turns out to have a live inbound ref not caught here, or a redirect target 404s).
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[docs]` commit; stop and report.
