---
status: shaped
kind: docs
appetite: small
lane: public
shaped-as: docs-version-markers
---

# Mark what is new in the docs, now that the published site tracks `develop`

> **Shaped candidate — not yet a contract.** The shaping conversation happened with a human on
> 2026-11-06: the clarifications below are dated and resolved, the appetite and non-goals are
> agreed, and the R-IDs are drafted. Acceptance into a cycle has not happened — `/hs-feature` does
> that, creating the branch and `spec/{slug}/GOAL.md`, and `hs-review` grades the result.
>
> Promotion should re-confirm the scope against current `develop` and cite anything that has
> drifted since this was written, then adopt it largely as written — do **not** re-litigate the
> resolved clarifications.

- **slug:** docs-version-markers
- **kind:** docs (R5 also touches the harness)
- **appetite:** small · *One cycle, docs and harness only, no change under `src/`. The audit behind
  R1 is the real work; R2–R4 are mechanical.*

## Problem

The documentation site is built from one branch and served at the root, with no version in the
path (Read the Docs single-version mode). Until 2026-11-06 that branch was `master`, so everything
written for 2.9 since 2.8.5 sat unpublished on `develop`. That is about 25 commits across 60
files: the `hs relay` page, the database guide, the configuration reference for zones, bundles and
TimescaleDB, the new submit, search and update options, the template environment rules, and the
security changes. Someone who installed 2.9.0a6 with `pip install --pre` read 2.8 documentation.

The site now builds from `develop` (clarification C1). That publishes the 2.9 material, but nothing
on any page says which behaviour is new. `develop` carries no `versionadded` or `versionchanged`
marker at all. A reader on 2.8.5, still the release `pip install hypershell` gives them, cannot tell
`--zone`, `TASK_SLOT`, `--from-json` or `hs relay` from options they already have. The same is true
of changed behaviour, such as `hs submit -q` now needing a server started with `--no-db`. The
upgrade cautions in the 2.9.0a2–a6 release notes (don't mix 2.8 and 2.9 servers and clients; the
forward-only in-place database upgrade) live only in the GitHub releases.

Nothing stops the gap from reopening. The same-commit rule in `AGENTS.md` covers the generated help
snippets in `docs/_include/` and the completions under `share/`, but not the prose pages, and no
rule asks for a marker. The docs are not built in CI either. A local build of `develop` at
`92753f0` gives three warnings: the two known "not in any toctree" warnings (`cli/task_submit.rst`,
`manual.rst`) and a new one, `manual.rst:24: Block quote ends without a blank line`. Nothing would
have caught a broken page before it reached the site.

## Clarifications (2026-11-06, resolved)

- **C1 — Publishing.** Single-version mode stays, because it gives the stable URLs without a version
  in the path. The maintainer switches the Read the Docs project to build from `develop`, which is a
  project setting and not a cycle task. No docs are merged onto `master`, and branch policy does not
  change: `master` stays what is released. A docs sync onto `master` was considered and rejected.
  It would build the API pages' autodoc against 2.8 code, and it would collide with the docs fixes
  each 2.8.x backport carries.
- **C2 — What a reader learns from the site.** The site follows `develop`, so it can describe work
  merged but not yet in any pre-release. The site-wide notice (R2) says so. Markers carry the
  release line, not the alpha: `.. versionadded:: 2.9.0`, and each links the pull request that
  shipped it. Both are stable once written. Which pre-release first carried each item is recorded
  once, on the what's-new page (R3).
- **C3 — Keeping it current is in scope.** Approved: the cycle adds standing instructions so every
  later user-visible change updates the prose docs and carries its marker (R5).

## Outcome / vision

A reader on either line can trust the published site. Everything new or changed in 2.9 is marked
where it appears, a banner says what the site tracks and how to get the pre-release, and one page
lists what changed since 2.8.5 with the upgrade cautions. From then on the factory keeps it that
way: a user-visible change updates its page and adds its marker in the same commit, review grades
it, and a broken page fails CI before it merges.

## Sketch of the acceptance criteria

- **R1** — WHERE a page or section on `develop` describes behaviour that 2.8.5 does not have, or that
  behaves differently than in 2.8.5, it SHALL carry a `versionadded` or `versionchanged` marker for
  `2.9.0` that links the pull request that shipped the behaviour. The marker sits at the narrowest
  scope that is accurate: the whole page for `hs relay` or
  the database guide, the section or option elsewhere. Generated help snippets in `docs/_include/`
  get their marker in the page that includes them, not in the snippet. API reference markers go in
  the `.rst` around the `autofunction`, not in docstrings. The audit works from the 2.9.0a1–a6
  release notes and the 2.8.5..`develop` docs diff, and lists what it marked in the spec.
- **R2** — The site SHALL show a notice on every page. It says the documentation tracks the 2.9
  development line, that marked items are not in the 2.8.x stable release, and how to install the
  pre-release. It links to the R3 page. The theme's `announcement` option already exists, commented
  out, in `docs/conf.py`.
- **R3** — The docs SHALL have a "What's new in 2.9" page. It lists every user-visible change since
  2.8.5 with the pre-release that first carried it and a link to the marked section, and it carries
  the upgrade cautions from the release notes. It covers what is merged on `develop`, so an item
  merged after the latest pre-release is listed as not yet released.
- **R4** — WHEN a pull request into `develop` is opened or updated, CI SHALL build the docs and fail
  on any warning. The three warnings above are fixed so that the first such run passes.
- **R5** — The harness SHALL require the R1 convention for later work:
  - `AGENTS.md` widens the same-commit rule so a user-visible change also updates the prose page
    that describes it, with its version marker.
  - `hs-build` honours it, and the `hs-review` rubric grades a missing marker or missing prose as a
    finding.
  - `hs-publish` confirms both in the PR body's docs line.
  - `hs-release` adds each pre-release to the R3 page. At a final release, it retires the notice or
    points it at the next line. The markers stay, as the Python docs keep theirs.
- **R6** — WHEN the cycle has landed, the published site SHALL show the R2 notice and the R3 page,
  and a marked section that the review samples. The review checks the live site, not only a local
  build.

## Non-goals

- Any docs merge onto `master`, any change to branch policy, or any change to the Read the Docs
  versioning scheme (C1).
- Markers for anything from 2.8 or earlier. An unmarked item is in the stable release.
- Changes under `src/`, including docstrings.
- Rewriting pages beyond what an accurate marker needs. If the audit finds a shipped 2.9 option with
  no prose at all, a minimal section within the appetite is in scope. Anything larger becomes a seed.
- The hypershell.org site (tutorials and blog), the PyPI README and the man pages.

## Notes

- Horizon: near-term. Once the site builds from `develop`, 2.8 readers see unmarked 2.9 material
  until this lands, so it is worth running before SC26 (2026-11-13) if a cycle is free.
- Related: [`docs-and-runtime-surfacing.md`](docs-and-runtime-surfacing.md) (behaviour that is
  deliberate but unannounced, a separate gap); `spec/docs-trim-migrated-sections/` (the tutorial,
  blog and roadmap pages moved to hypershell.org and redirect there from `develop`).
- Found by: an out-of-cycle review of the published docs, 2026-11-06.
