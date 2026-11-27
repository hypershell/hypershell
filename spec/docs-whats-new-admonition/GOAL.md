# GOAL — Replace the docs banner and What's new page with an index admonition

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** docs-whats-new-admonition
- **kind:** docs
- **appetite:** small

## Problem

The Read the Docs site carries two things that now duplicate work done elsewhere. Every page shows a
site notice (a Furo `announcement` banner, built by `docs/_ext/site_notice.py`) while the version is
a pre-release. The *Intro* navigation opens on `docs/whats_new.rst`, a page that lists every 2.9
change since the stable release. Release notes for the 2.8.x patch series and the 2.9.0 pre-release
series now live on hypershell.org as blog posts. The docs also mark every new or changed passage
with `versionadded`/`versionchanged`. The banner reads as noise on every page, and the page is a
second copy of the release notes that has to be kept in step by hand.

The software factory is built around that page. `AGENTS.md`, `invariants.md`, the review rubric,
the `TECH.md` template, and the `hs-build`, `hs-review`, `hs-publish` and `hs-release` skills all
require or check a *Not yet released* entry in `docs/whats_new.rst`, and `hs-release` Step 3a
curates it. If the page goes and the factory keeps pointing at it, the next cycle is told to edit a
file that no longer exists.

## Outcome / vision

The docs home page opens with a short "What's new" admonition. It calls out a few headline changes
and links to the two recent release-notes posts on hypershell.org. No page carries a banner, and the
What's new page is gone. An old link to `whats_new.html` lands on the 2.9.0 release notes instead of
a 404. The version markers stay as they are.

The factory follows the maintainer's ruling. A cycle's user-facing note travels in its PR's squash
commit body, not in a docs file. `hs-release` drafts the GitHub release notes from `git log`, and
the hypershell.org post is written from those. At each pre-release or full release, `hs-release`
completes the markers' `:pr:` links (unchanged) and refreshes the index admonition's highlights and
its two links.

Proposed highlights for the admonition (the plan owns the final wording; the R-IDs bound it):

- **Zones and relays** (2.9.0a4, a6): place tasks and clients by name, and run one campaign across
  several sites.
- **TimescaleDB and in-place upgrades** (2.9.0a2, a3): a TimescaleDB provider with columnstore
  compression, and a 2.8 database upgraded in place by the first 2.9 process.
- **Tasks from JSON, safe re-submission, and per-executor task slots** (2.9.0a1).
- **Installing 2.9:** it is a pre-release, so `pip install --pre hypershell`. Upgrade the server and
  every client together, and back up the database first.
- **2.8.1 to 2.8.7:** fixes for cancelled tasks, retries and stranded task groups, and the run key
  kept off command lines.

## Acceptance criteria (the contract)

- **R1** — The docs SHALL no longer contain `docs/whats_new.rst`, and no toctree SHALL list it. The
  docs build passes with `-W` (`AGENTS.md` § Docs).
- **R2** — *Withdrawn 2026-11-11 (maintainer ruling; see Clarifications).* ~~WHEN the built site
  is asked for `whats_new.html`, the docs SHALL serve a redirect to
  `https://www.hypershell.org/blog/hypershell-2-9-0`, through the `redirects` map `docs/conf.py`
  already feeds to `sphinx-reredirects`.~~
- **R3** — The built docs SHALL carry no site notice on any page, whatever the version, pre-release
  or final. `docs/_ext/site_notice.py` and everything that served only it are removed: its import
  and `sys.path` wiring and the `announcement` assignment in `docs/conf.py`, the
  `color-announcement-background` option, the `_ext` entry in `exclude_patterns` if `_ext/` is left
  empty, and the `.announcement` rules in `docs/_static/custom.css`.
- **R4** — `docs/index.rst` SHALL carry a "What's new" admonition above the *Usage* section. It
  holds three to five highlights drawn from the two release-notes posts, and links to both:
  `https://www.hypershell.org/blog/hypershell-2-9-0` and
  `https://www.hypershell.org/blog/hypershell-2-8-x`. Both links answer HTTP 200 at review. It
  names top-level commands only and passes no key with `-k`.
- **R5** — `AGENTS.md` (the conventions bullet and § Docs), `.agents/factory/invariants.md`,
  `.agents/factory/review-rubric.md`, `.agents/factory/templates/TECH.md`, `hs-build` and `hs-review`
  SHALL state the same rule. A user-visible change updates its prose page, in the same commit, with a
  `versionadded`/`versionchanged` marker. Its user-facing note goes in the PR's squash commit body.
  None of them asks for a `docs/whats_new.rst` entry, and § Docs no longer describes a site notice.
- **R6** — For a user-visible change, `hs-publish` SHALL draft the change's user-facing note into
  the PR body, written for the squash commit body, and its *Docs & completions* line SHALL no longer
  cite `docs/whats_new.rst`. The marker's `:pr:` is still not added at publish.
- **R7** — `hs-release` Step 3a SHALL be "complete the markers and refresh the index admonition".
  At a pre-release or full release it keeps the existing `:pr:`/`:commit:` marker step unchanged,
  then refreshes the admonition's highlights and its two links to the current release-notes posts.
  The `whats_new` move-and-reset steps, the `whats_new_X_Y.rst` copy and the notice note go, and
  the step's commit subject names the admonition instead of What's new.
- **R8** — Outside `spec/`, no tracked file SHALL refer to `docs/whats_new.rst`, `whats_new` or
  `site_notice`~~, except the R2 redirect key in `docs/conf.py`~~ (exception withdrawn with R2,
  2026-11-11).

## Non-goals (no-gos)

- **A native Read the Docs redirect.** It needs the RTD dashboard, so it is Geoffrey's to add if he
  wants one. The Sphinx-side stub (R2) is in scope, as it was for the migrated tutorial and blog
  pages.
- **Editing `spec/*`.** Earlier cycles' GOAL, PLAN, TECH and REVIEW files are history and keep
  their `whats_new` references.
- **Changing the version markers** or their `:pr:` link step at release.
- **Editing the hypershell.org posts** or that repo. The admonition only links to them.
- **Preserving the What's new page elsewhere** (no `whats_new_2_9.rst` record, no orphan page). Its
  content lives on in the 2.9.0 release-notes post.
- **Carrying the notice's text forward** as its own requirement. The "Installing 2.9" highlight
  covers `pip install --pre`; anything more is the plan's call.
- **Configuring GitHub's squash-merge default** or retro-writing note bodies onto past squash
  commits.

## Clarifications

- **Q:** What does the maintainer want? — **A:** Verbatim: "For the RTD docs site I don't like the
  banner and full what's new page. We just built release notes for both 2.8.x and 2.9.x series on
  the main site. Since we've adopted the version annotations throughout the docs I think what would
  be more graceful is to instead get rid of the 'whats new' page and the banner, and instead just
  have a "What's new" admonition on the main page calling out a few things and linking to the 2
  recent release notes pages." (resolved 2026-11-11, maintainer)
- **Q:** With `docs/whats_new.rst` gone, where does each change's user-facing note live? — **A:** In
  the PR's squash commit body. `hs-release` drafts the GitHub release notes from `git log`, and the
  hypershell.org post is written from those. `hs-release` also refreshes the index admonition (its
  highlights and its two links) at each pre-release or full release. The
  `versionadded`/`versionchanged` markers stay, and so does their `:pr:` link step at release.
  (resolved 2026-11-11, maintainer ruling via the foreman)
- **Q:** Which skill puts the note in the squash body? — **A:** `hs-publish`, which already writes
  the PR body. Read from the ruling above, not a new decision; the plan decides the section's shape.
  (resolved 2026-11-11, shaping, from the ruling)
- **Q:** Is a redirect for `whats_new.html` in scope? — **A:** The Sphinx-side stub is.
  `sphinx-reredirects` is already enabled with an explicit `redirects` map for moved pages, so one
  more key costs a line. The RTD dashboard redirect is a non-goal. (resolved 2026-11-11, shaping)
- **Q:** What are the live URL forms? — **A:** The hypershell.org app routes `/blog/<slug>`
  (`src/org_hypershell/server.py`), and the slug is the content file's stem
  (`content.py`, `path.stem`). The two posts are `content/blog/hypershell-2-9-0.md` and
  `content/blog/hypershell-2-8-x.md`. The host `https://www.hypershell.org` matches the existing
  `redirects` targets in `docs/conf.py`. (resolved 2026-11-11, shaping)
- **Q:** Is anything pending in *Not yet released* that would be lost? — **A:** No. At `5f22298` it
  reads "Nothing is pending", and no user-visible change has landed since 2.9.0a7. (resolved
  2026-11-11, shaping)
- **Q:** Eight criteria is near the soft size limit for `small`. — **A:** It stays one cycle. Every
  edit is text, and the ruling needs the docs and factory changes in lockstep, so splitting would
  leave the factory pointing at a deleted file. (resolved 2026-11-11, shaping)
- **Q:** Does any test cover the notice? — **A:** No test under `tests/` imports `site_notice` or
  checks the banner. (resolved 2026-11-11, shaping)
- **Q:** Does `whats_new.html` still need a redirect? — **A:** No. Verbatim: "Ignore the old
  'whats_new' page, it was only live for a day." R2 is withdrawn: no redirect stub and no
  `redirects` key for it, and the Outcome's redirect sentence falls away with it. R8's exception
  for the R2 key goes too, so no tracked file outside `spec/` refers to `whats_new` or
  `site_notice` at all. Supersedes the redirect clarification above. (resolved 2026-11-11,
  maintainer via the foreman)

## Related materials

- Release notes: <https://www.hypershell.org/blog/hypershell-2-9-0> (pinned, 2.9.0a1 to a7) and
  <https://www.hypershell.org/blog/hypershell-2-8-x> (2.8.1 to 2.8.7); sources in the
  hypershell.org repo under `content/blog/`.
- The cycle that added the page and the notice: `spec/docs-version-markers/` (GOAL R2, R3).
- The precedent for redirect stubs: `docs/conf.py` `redirects`, and
  `spec/docs-trim-migrated-sections/redirect-runbook.md` for the RTD dashboard side.
- Files: `docs/whats_new.rst`, `docs/index.rst`, `docs/conf.py`, `docs/_ext/site_notice.py`,
  `docs/_static/custom.css`, `AGENTS.md`, `.agents/factory/{invariants.md,review-rubric.md,templates/TECH.md}`,
  `.agents/skills/{hs-build,hs-review,hs-publish,hs-release}/SKILL.md`.
