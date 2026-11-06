# PLAN — Mark what is new in the docs, now that the published site tracks `develop`

> **Status:** Draft for review · **Last updated:** 2026-11-06
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Lean plan (`appetite: small`): no
> research fan-out, no `research/`.

## 1. Summary

Docs and harness only. First make the docs build strict and gated in CI, and give the docs a `:pr:`
role so a marker can link its pull request. Then mark every 2.9 page and section, using the
2.8.5..`develop` docs diff and the 2.9.0a1–a6 release notes as the audit inputs and recording the
result in an inventory under `spec/`. Then add the site notice and the "What's new in 2.9" page.
Last, widen the same-commit rule in the harness so later work keeps the convention. Nothing under
`src/` changes.

## 2. Design

### Strict build and the CI gate (R4)

- A new `docs` job in `.github/workflows/tests.yml`, beside `metadata`. Python-independent, so no
  matrix: it runs `uv run --python 3.12 --group docs sphinx-build -W --keep-going -E -b html docs
  docs/_build/html`. The workflow already runs on pull requests into `develop` and on pushes to
  `develop` and `master`, so the job inherits that trigger. `master` runs its own copy of the
  workflow, so the job first runs there when a release merges `develop`. No extras are installed:
  a local build with only the `docs` group autodocs every API page without a warning.
- Fix the three warnings at `92753f0`. `manual.rst:24`: the `hs relay` usage line wraps onto a
  continuation line whose indentation ends the definition-list block. Re-indent it, or fold the
  line, so it parses as one definition term. `manual.rst` and `cli/task_submit.rst`: each is
  "not in any toctree". `manual.rst` is the man-page source (`man_pages` in `conf.py`), and
  `cli/task_submit.rst` is the page the older nested form links to. Each gets `:orphan:` so it
  stays out of the navigation and the warning stops. The build phase confirms each is still
  reachable or built where it is used before choosing `:orphan:` over a toctree entry.
- `AGENTS.md` § Docs currently says the two toctree warnings are pre-existing. That sentence is
  replaced with the strict-build command and "no warnings".

### The marker convention (R1)

- `docs/conf.py` gains `extlinks = {'pr': ('https://github.com/hypershell/hypershell/pull/%s',
  '#%s')}`. `sphinx.ext.extlinks` is already loaded and has no entries today.
- A marker is a native Sphinx directive whose body is the PR link and, optionally, one short clause:

  ```rst
  .. versionadded:: 2.9.0
     :pr:`82`

  .. versionchanged:: 2.9.0
     :pr:`78`: needs a server started with ``--no-db``.
  ```

  Furo renders these as labelled admonitions ("Added in version 2.9.0: #82"). `versionadded` is for
  behaviour 2.8.5 lacks, and `versionchanged` for behaviour that differs. A page that is new as a
  whole (`cli/relay.rst`, the database guide) carries one marker under its title. Elsewhere the
  marker sits under the section heading, or directly after the option or parameter entry it
  qualifies.
- Generated help snippets (`docs/_include/*_help.rst`, `*_usage.rst`) are regenerated from the CLI
  and are never hand-marked. The page that includes them gets one marker beside the `.. include::`,
  listing the new options by name. Hand-written includes (`_include/database.rst`,
  `_include/templates.rst`, `_include/config_param_ref.rst`, the `*_desc.rst` files) are prose, and
  are marked in place.
- API pages: the marker goes in the `.rst` directly after the `.. autofunction::` block it
  qualifies, never in a docstring.
- **The audit.** Two inputs: `git diff 2.8.5 develop -- docs` (every added or changed passage is a
  candidate), and the GitHub release bodies for 2.9.0a1–a6 (every user-visible item must end up
  marked, or be recorded as not user-facing). Each candidate is classified *new*, *changed*, or
  *not 2.9* (a rewording, a fix that only corrects 2.8 docs, or material that also describes 2.8.5
  behaviour). The result is `spec/docs-version-markers/markers.md`: one row per marker giving the
  file, scope, directive, PR and source release, plus a short list of release-note items judged
  not to need one, each with its reason. The PR for each item comes from the release notes, or
  from `git log --first-parent develop` for the merge that introduced the passage.
- A check script, `spec/docs-version-markers/check_markers.py` (stdlib only), reads the inventory
  and asserts that each row's file contains a marker with that directive, `2.9.0` and that `:pr:`.
  It also asserts that no 2.9.0 marker in `docs/` is missing from the inventory, so the two can't
  drift during the build. It is a cycle artifact, not a CI check: R5 keeps the convention for later
  work through review.

### Site notice (R2)

- `docs/conf.py` sets `html_theme_options['announcement']` when, and only when, the built version is
  a pre-release or development version. `packaging.version.Version(hypershell.__version__)` with
  `is_prerelease or is_devrelease` decides; `packaging` already comes in through Sphinx. The
  version is read, not written, so §12's single-source rule holds, and the notice goes away by
  itself when `develop` carries a final version.
- Text, derived from the version (`2.9` from `2.9.0a6`): *These docs track the 2.9 development line.
  Items marked "Added/Changed in version 2.9.0" are not in the 2.8.x stable release. Install the
  pre-release with `pip install --pre hypershell`. See What's new in 2.9.* The last sentence links
  the R3 page. Furo's announcement accepts raw HTML; the link is a root-relative URL, so it resolves
  from every page depth.
- The existing `color-announcement-background` (dark mode only) is kept. A light-mode value is added
  if the default contrast fails a visual check of the local build.

### What's new in 2.9 (R3)

- A new page, `docs/whats_new.rst`, first in the *Intro* toctree. It has three sections:
  - **Upgrading from 2.8:** the cautions carried in the release notes (don't mix 2.8 and 2.9
    servers and clients; the forward-only in-place database upgrade and backup; the SQLite
    retry-chain constraint note; the PostgreSQL lock during upgrade; `hs submit -q` needing
    `--no-db`; task files loading fully before scheduling; key handling for launched clients).
  - **New and changed:** one entry per user-visible item. Each gives the pre-release that first
    carried it, the `:pr:` link and a `:ref:` to the marked section. Entries are grouped by area
    (scheduling and zones, relay, database and providers, submission and listing, security,
    packaging).
  - **Not yet released:** items merged on `develop` after the latest pre-release. It is empty today
    and says so in one sentence. `hs-release` moves entries out of it.
- Targets need anchors. Where a marked section has no `.. _anchor:` yet, one is added, following the
  `AGENTS.md` docs convention.
- The page itself carries no version marker: it is about 2.9.

### Harness (R5)

- `AGENTS.md`, in the conventions bullet beside the help-snippet rule and in § Docs: a user-visible
  change also updates the prose page that describes it in the same commit, and marks the passage
  `versionadded`/`versionchanged` with the release line it ships in and its `:pr:`. The PR number
  isn't known until the PR is open, so the marker is written with the line and completed with the
  number at `hs-publish`, before merge. § Docs gains the strict build command.
- `.agents/factory/invariants.md` §12: the same widening, one bullet.
- `hs-build`: the conventions bullet naming the same-commit rule names prose and markers too.
- `hs-review` and `.agents/factory/review-rubric.md`: a user-visible change with no prose update, or
  with no marker, is an R-ID-independent finding under "AGENTS.md invariant violations" (§12 is
  already in that scope). The reviewer's docs build runs with `-W`.
- `hs-publish`: the PR body's *Docs & completions* line confirms prose, markers and their `:pr:`.
  This step fills in the PR number in any marker written during build.
- `hs-release`: at each pre-release, move the "Not yet released" entries on `docs/whats_new.rst`
  under that pre-release. At a final release, rename or retire the page for the next line. The
  notice needs no step, since it keys off the version. Markers stay.
- `.agents/factory/templates/TECH.md`: the conventions bullet gets the same widening.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `:pr:` extlink; the marker convention; the audit, `markers.md` and `check_markers.py`; markers across `docs/` |
| R2   | version-keyed `announcement` in `docs/conf.py` |
| R3   | `docs/whats_new.rst` in the Intro toctree; anchors on marked sections |
| R4   | `docs` job in `tests.yml`; the three warning fixes; `AGENTS.md` § Docs build line |
| R5   | `AGENTS.md`, `invariants.md` §12, `hs-build`, `hs-review` and rubric, `hs-publish`, `hs-release`, TECH template |
| R6   | post-landing check of the live site (§6), recorded in `REVIEW.md` |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before design and again after it. The change touches only §12 (project conventions); no
source, task lifecycle, transport or configuration code changes, so §1–§11 are untouched.

- **§12 version single-sourced:** the notice reads `hypershell.__version__`, which `conf.py` already
  imports. A `2.9.0` in a marker is a historical fact about when behaviour arrived, not the package
  version, and it is what Sphinx's `versionadded` exists for.
- **§12 same-commit rule:** widened by R5, not weakened. The help snippets and completions rule
  stays as written.
- **§12 Python 3.11–3.14:** the CI job pins 3.12, inside the range and matching `.readthedocs.yaml`.
- **Public-repo hygiene (`AGENTS.md`):** docs name only top-level commands. Every new passage,
  including the what's-new entries, uses `hs list`/`hs info`/`hs wait`/`hs run`/`hs update`, never
  the nested forms.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| R6 is graded after landing, not at `hs-review` | The live site builds `develop` only once the branch merges; the project does not build pull-request previews | Enabling RTD PR previews is a project setting outside the cycle; a local strict build plus a post-landing live check covers the same ground |

## 4. Rabbit holes (resolved)

- **Will a strict build pass on CI without the native extras?** Yes. A local build at `92753f0` with
  only the `docs` group gives exactly the three warnings named in the GOAL, with no import or
  autodoc warnings. `--all-extras` fails locally only because `psycopg-c` needs `pg_config`.
- **Can a marker carry a link?** Yes: a `versionadded` directive takes body content, and
  `sphinx.ext.extlinks` is already loaded.
- **Does a version-keyed notice need a new dependency?** No: `packaging` is a Sphinx dependency.

## 5. Risks & open questions

- **Audit judgement.** Some passages describe behaviour that changed subtly (for example `-b` on a
  client now means a requested bundle size). The inventory records the judgement for each, so
  review can contest it.
- **The PR number for unreleased work** isn't known at build time. R5 has `hs-publish` complete
  it. Every 2.9 item in this cycle already has a PR.
- **Over-marking.** A page with a dozen markers reads badly. Where most of a section is new, one
  section-level marker that lists the items is preferred to one per line.

## 6. Verification strategy

- Every phase: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
  docs/_build/html` exits 0. That is exactly what the new CI job runs.
- P2 adds `uv run python spec/docs-version-markers/check_markers.py`.
- P3 asserts on the built HTML: the announcement appears on the index and on a nested page with a
  working link, and the what's-new page exists with its three sections. Also run a version-keyed
  negative check: build `conf.py`'s notice logic with a final version and confirm no announcement.
- P4: `uv run python .agents/factory/bin/next_phase.py spec/docs-version-markers/TECH.md` still
  validates, plus a grep that each named harness file carries the widened rule.
- **R6, after landing:** the foreman confirms through the RTD API that `latest` built the merge
  commit successfully. On `hypershell.readthedocs.io` it then checks the notice on the root and
  one nested page, opens `whats_new.html`, and checks one sampled marked section (`cli/relay.html`)
  renders "Added in version 2.9.0" with a working `#82` link. The result goes in `REVIEW.md` as a
  post-landing addendum.
- The PR's CI shows the new `docs` job green alongside the existing jobs.
