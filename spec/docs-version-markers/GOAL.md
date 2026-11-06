# GOAL — Mark what is new in the docs, now that the published site tracks `develop`

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** docs-version-markers
- **kind:** docs  ·  *R5 also touches the harness (`AGENTS.md`, `.agents/`).*
- **appetite:** small  ·  *One cycle, docs and harness only, no change under `src/`. The audit behind
  R1 is the real work; R2–R4 are mechanical.*

## Problem

The documentation site is built from one branch and served at the root, with no version in the
path (Read the Docs single-version mode). Until 2026-11-06 that branch was `master`, so everything
written for 2.9 since 2.8.5 sat unpublished on `develop`. That is about 25 commits across 60
files: the `hs relay` page, the database guide, the configuration reference for zones, bundles and
TimescaleDB, the new submit, search and update options, the template environment rules, and the
security changes. Someone who installed 2.9.0a6 with `pip install --pre` read 2.8 documentation.

The site now builds from `develop`. That publishes the 2.9 material, but nothing on any page says
which behaviour is new. `develop` carries no `versionadded` or `versionchanged` marker at all. A
reader on 2.8.5, still the release `pip install hypershell` gives them, cannot tell `--zone`,
`TASK_SLOT`, `--from-json` or `hs relay` from options they already have. The same is true of changed
behaviour, such as `hs submit -q` now needing a server started with `--no-db`. The upgrade cautions
in the 2.9.0a2–a6 release notes (don't mix 2.8 and 2.9 servers and clients; the forward-only
in-place database upgrade) live only in the GitHub releases.

Nothing stops the gap from reopening. The same-commit rule in `AGENTS.md` covers the generated help
snippets in `docs/_include/` and the completions under `share/`, but not the prose pages, and no
rule asks for a marker. The docs are not built in CI either. A local build of `develop` at
`92753f0` gives three warnings: the two known "not in any toctree" warnings (`cli/task_submit.rst`,
`manual.rst`) and a new one, `manual.rst:24: Block quote ends without a blank line`. Nothing would
have caught a broken page before it reached the site.

## Outcome / vision

A reader on either line can trust the published site. Everything new or changed in 2.9 is marked
where it appears, with a link to the pull request that shipped it. A banner says what the site
tracks and how to get the pre-release, and one page lists what changed since 2.8.5 with the upgrade
cautions. From then on the factory keeps it that way: a user-visible change updates its page and
adds its marker in the same commit, review grades it, and a broken page fails CI before it merges.

## Acceptance criteria (the contract)

- **R1** — WHERE a page or section on `develop` describes behaviour that 2.8.5 does not have, or that
  behaves differently than in 2.8.5, it SHALL carry a `versionadded` or `versionchanged` marker for
  `2.9.0` that links the pull request that shipped the behaviour. The marker sits at the narrowest
  scope that is accurate: the whole page for `hs relay` or the database guide, the section or
  option elsewhere. Generated help snippets in `docs/_include/` get their marker in the page that
  includes them, not in the snippet. API reference markers go in the `.rst` around the
  `autofunction`, not in docstrings. The audit works from the 2.9.0a1–a6 release notes and the
  2.8.5..`develop` docs diff, and lists what it marked in the spec.
- **R2** — The site SHALL show a notice on every page. It says the documentation tracks the 2.9
  development line, that marked items are not in the 2.8.x stable release, and how to install the
  pre-release. It links to the R3 page.
- **R3** — The docs SHALL have a "What's new in 2.9" page. It lists every user-visible change since
  2.8.5 with the pre-release that first carried it and a link to the pull request and the marked
  section, and it carries the upgrade cautions from the release notes. It covers what is merged on
  `develop`, so an item merged after the latest pre-release is listed as not yet released.
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

## Non-goals (no-gos)

- Any docs merge onto `master`, any change to branch policy, or any change to the Read the Docs
  versioning scheme.
- Markers for anything from 2.8 or earlier. An unmarked item is in the stable release.
- Changes under `src/`, including docstrings.
- Rewriting pages beyond what an accurate marker needs. If the audit finds a shipped 2.9 option with
  no prose at all, a minimal section within the appetite is in scope. Anything larger becomes a seed.
- The hypershell.org site (tutorials and blog), the PyPI README and the man pages.

## Clarifications

- **Q:** How does the 2.9 material get published without a version in the URL? — **A:**
  Single-version mode stays. The maintainer switched the Read the Docs project's default branch to
  `develop`, a project setting outside the cycle; `latest` built `92753f0` successfully and serves
  the `hs relay` page. No docs are merged onto `master` and branch policy does not change. A docs
  sync onto `master` was considered and rejected: it would build the API pages' autodoc against
  2.8 code, and it would collide with the docs fixes each 2.8.x backport carries. (resolved
  2026-11-06)
- **Q:** What does a marker name? — **A:** The release line, `2.9.0`, not the alpha, plus a link to
  the pull request that shipped the behaviour. Both are stable once written. Which pre-release
  first carried each item is recorded once, on the R3 page. The site follows `develop`, so it can
  describe work merged but not yet in any pre-release, and the R2 notice says so. (resolved
  2026-11-06)
- **Q:** What does a marker link when the change reached `develop` as a direct commit, with no pull
  request? — **A:** The commit, through a `:commit:` link. Four 2.9 changes landed that way
  (`961fb22`, `210a93e`, `27db1b0`, `ee03014`), and a commit link is as stable as a PR link.
  (resolved 2026-11-06, during build)
- **Q:** Is keeping the docs current for later work in scope? — **A:** Yes, approved: R5 adds the
  standing instructions. (resolved 2026-11-06)
- **Q:** Has scope drifted since the seed was written? — **A:** No. The seed was written and adopted
  the same day against `develop` at `92753f0`. (resolved 2026-11-06)

## Related materials

- Seed: [`issues/docs-version-markers.md`](../../issues/docs-version-markers.md)
- Release notes: GitHub releases `2.9.0a1`–`2.9.0a6`
- `docs/conf.py` (the theme's `announcement` option is present, commented out),
  `.github/workflows/tests.yml`, `.readthedocs.yaml`
- Related seed: [`issues/docs-and-runtime-surfacing.md`](../../issues/docs-and-runtime-surfacing.md)
- Prior docs cycle: `spec/docs-trim-migrated-sections/`
