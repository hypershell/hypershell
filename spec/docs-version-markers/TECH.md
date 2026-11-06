---
slug: docs-version-markers
title: Mark what is new in the docs, now that the published site tracks develop
kind: docs
appetite: small
status: done
branch: feature/docs-version-markers
base: develop
current_phase: done
last_updated: '2026-11-06'
phases:
- id: P1
  name: 'Strict docs build gated in CI, warnings fixed, :pr: role'
  status: done
  satisfies:
  - R4
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b
    html docs docs/_build/html && grep -q 'sphinx-build -W' .github/workflows/tests.yml
- id: P2
  name: Audit and mark every 2.9 page and section, with inventory and check
  status: done
  satisfies:
  - R1
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b
    html docs docs/_build/html && uv run python spec/docs-version-markers/check_markers.py
- id: P3
  name: Version-keyed site notice and the What's new in 2.9 page
  status: done
  satisfies:
  - R2
  - R3
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b
    html docs docs/_build/html && uv run python spec/docs-version-markers/check_site.py
    docs/_build/html
- id: P4
  name: 'Harness keeps the convention: AGENTS.md, invariants, skills, rubric, template'
  status: done
  satisfies:
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python .agents/factory/bin/next_phase.py spec/docs-version-markers/TECH.md
    && grep -l 'versionadded' AGENTS.md .agents/factory/invariants.md .agents/factory/review-rubric.md
    .agents/factory/templates/TECH.md .agents/skills/hs-build/SKILL.md .agents/skills/hs-review/SKILL.md
    .agents/skills/hs-publish/SKILL.md .agents/skills/hs-release/SKILL.md | wc -l
    | grep -qx ' *8'
review:
  last_reviewed_commit: 3a28887
  verdict: approved
  blocked_reason: ''
  cycle: 4
---
# TECH.md — Mark what is new in the docs, now that the published site tracks `develop`

The **context engine and finite-state machine** for this cycle. Read the frontmatter with
`uv run python .agents/factory/bin/next_phase.py spec/docs-version-markers/TECH.md`.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). No `research/` (lean plan).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; §12 of
  [`invariants.md`](../../.agents/factory/invariants.md) is the only invariant section touched.
- One phase per `hs-build` invocation; one atomic commit with the change and the `TECH.md` state,
  subject `[docs] Build docs-version-markers P<n>: …`. **No `Co-Authored-By` trailer.**
- Nothing under `src/` changes, docstrings included.
- Docs name only top-level commands (`hs list`, `hs info`, `hs wait`, `hs run`, `hs update`).
- Every phase leaves the strict build green: `uv run --python 3.12 --group docs sphinx-build -W
  --keep-going -q -E -b html docs docs/_build/html`.

---

## Phase P1 — Strict docs build gated in CI, warnings fixed, `:pr:` role
**Satisfies:** R4 · **Depends on:** —
**Goal:** the docs build with no warnings, CI fails a pull request whose docs warn, and a `:pr:` role
exists for P2's markers.

- [x] Fix `manual.rst:24` (the wrapped `hs relay` usage line ends the block quote).
- [x] Resolve "not in any toctree" for `manual.rst` and `cli/task_submit.rst`: confirm where each is
      used (man pages; links from the nested-form pages), then add `:orphan:` or a toctree entry.
- [x] Add `extlinks = {'pr': ('https://github.com/hypershell/hypershell/pull/%s', '#%s')}` to
      `docs/conf.py`.
- [x] Add a `docs` job to `.github/workflows/tests.yml`: checkout (`persist-credentials: false`,
      pinned action SHAs as the other jobs use), setup-uv with the cache, then the strict build.
      Comment it in the file's house style.
- [x] `AGENTS.md` § Docs: replace the "pre-existing warnings" sentence with the strict command and
      "no warnings".
- **Verify:** the strict build exits 0; the workflow carries the strict command.
- **Touches:** `docs/manual.rst`, `docs/cli/task_submit.rst`, `docs/conf.py`,
  `.github/workflows/tests.yml`, `AGENTS.md`.

## Phase P2 — Audit and mark every 2.9 page and section
**Satisfies:** R1 · **Depends on:** P1
**Goal:** every passage describing behaviour 2.8.5 lacks or does differently carries a
`versionadded`/`versionchanged` 2.9.0 marker with its `:pr:`, and the audit is recorded.

- [x] Gather inputs: `git diff 2.8.5 develop -- docs`; `gh release view 2.9.0a{1..6} --json body`.
- [x] Classify each candidate *new* / *changed* / *not 2.9*; map each to its PR.
- [x] Write `spec/docs-version-markers/markers.md`: one row per marker (file · scope · directive ·
      PR · first pre-release), then the release-note items judged to need no marker, each with its
      reason.
- [x] Place the markers per PLAN § "The marker convention": page-level for wholly new pages,
      section-level elsewhere, beside the `.. include::` for generated help, after the
      `autofunction` for API entries. Prefer one section marker listing items over many line markers.
- [x] Write `spec/docs-version-markers/check_markers.py` (stdlib): every inventory row is present in
      its file with directive, `2.9.0` and `:pr:`; every 2.9.0 marker in `docs/` is in the inventory.
- *Amended in build:* four 2.9 changes reached `develop` as direct commits with no pull request
      (`hs list --all` and its result guard, colorized listings, signal mapping, container images).
      Their markers link the commit through a `:commit:` extlink added beside `:pr:` in
      `docs/conf.py`, and the check accepts either. The database guide predates 2.9, so it gets
      section markers (Initialization, Relay Database, TimescaleDB), not a page marker.
      `_include/config_param_ref.rst` also feeds the man page, so its markers sit in
      `.. only:: html`.
- **Verify:** the strict build exits 0, and the check exits 0.
- **Touches:** `docs/**/*.rst` (not `_include/*_help.rst` / `*_usage.rst`), `spec/docs-version-markers/`.

## Phase P3 — Version-keyed site notice and the What's new in 2.9 page
**Satisfies:** R2, R3 · **Depends on:** P2
**Goal:** every page shows the notice while the built version is a pre-release or dev version, and
`whats_new.rst` maps every 2.9 change to its pre-release, PR and marked section.

- [x] `docs/conf.py`: compute the notice from `packaging.version.Version(release)`; set
      `html_theme_options['announcement']` only when `is_prerelease or is_devrelease`; link to
      `/whats_new.html` root-relative. Add a light-mode announcement colour only if contrast needs it.
- [x] `docs/whats_new.rst`: *Upgrading from 2.8*, *New and changed* (grouped by area; each entry:
      first pre-release, `:pr:`, `:ref:` to the marked section), *Not yet released* (empty, says so).
      Add `.. _anchor:` targets the refs need.
- [x] Add `whats_new` first in the *Intro* toctree in `docs/index.rst`.
- [x] Write `spec/docs-version-markers/check_site.py` (stdlib): the built index and one nested page
      contain the announcement with a link to `whats_new.html`; `whats_new.html` exists with the
      three section headings; the notice logic, given a final version, yields no announcement.
- *Amended in build:* the notice logic is `announcement()` in `docs/_ext/site_notice.py`, which
      `conf.py` imports and `check_site.py` loads directly. Section anchors inside
      `_include/database.rst` and `_include/templates.rst` duplicated, because Sphinx also built
      every `_include/` fragment as a page of its own; `_include` (and `_ext`) join
      `exclude_patterns`, which also drops those stray fragment pages from the site. Furo's
      announcement does not wrap, so `_static/custom.css` lets it wrap rather than scroll the
      What's new link out of view. No light-mode colour was needed: Furo's default is dark grey
      with light text.
- **Verify:** the strict build exits 0, and the site check exits 0.
- **Touches:** `docs/conf.py`, `docs/whats_new.rst`, `docs/index.rst`, anchors in marked pages,
  `spec/docs-version-markers/check_site.py`.

## Phase P4 — The harness keeps the convention
**Satisfies:** R5 · **Depends on:** P1
**Goal:** later cycles update prose and add markers in the same commit, review grades it, publish
completes the PR number, and release maintains the what's-new page.

- [x] `AGENTS.md`: the conventions bullet and § Docs name the prose update and the marker
      (`versionadded`/`versionchanged`, release line, `:pr:`).
- [x] `.agents/factory/invariants.md` §12: the widened same-commit bullet.
- [x] `.agents/skills/hs-build/SKILL.md`: the conventions bullet names prose and markers.
- [x] `.agents/skills/hs-review/SKILL.md` and `.agents/factory/review-rubric.md`: a missing prose
      update or marker on a user-visible change is a §12 finding; the reviewer's docs build uses `-W`.
- [x] `.agents/skills/hs-publish/SKILL.md`: the *Docs & completions* line confirms prose, markers and
      the what's-new entry. *(Amended: `hs-release` adds the `:pr:`, not publish; see PLAN § Harness.)*
- [x] `.agents/skills/hs-release/SKILL.md`: each pre-release moves "Not yet released" entries in
      `docs/whats_new.rst` under it; a final release renames or retires the page for the next line;
      markers stay; the notice keys off the version.
- [x] `.agents/factory/templates/TECH.md`: the conventions bullet gets the widening.
- **Verify:** `next_phase.py` validates; each of the eight files names `versionadded`.
- **Touches:** the eight files above.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges; STOP and escalate only on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[docs]` commit; stop and report.
