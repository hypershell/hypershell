# GOAL — Trim docs sections that have moved to hypershell.org

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** docs-trim-migrated-sections
- **kind:** docs
- **appetite:** small

## Problem

We now maintain a dedicated project website, **hypershell.org** (source in the sibling checkout
`../hypershell.org/`), which has become the home for the narrative and marketing-style material that
used to live in the Sphinx docs under `docs/`. With that content duplicated, the in-repo docs carry
material that is better served from the website and that clutters the reference documentation's
navigation.

Concretely, three groups of pages have a new home on the website and should leave `docs/`:

- the **Tutorial** section — `docs/tutorial/{basic,distributed,hybrid,advanced}.rst` (its own toctree
  caption in `docs/index.rst`);
- the **blog** — `docs/blog/` (an `index.rst` plus ~12 dated release/announcement posts); and
- the **project roadmap** — `docs/roadmap.rst`.

In `docs/index.rst` the blog and roadmap together form the **Project** toctree caption, and the
tutorial pages form the **Tutorial** caption. The user's intent is to drop both of those sections
entirely. Because some of these pages have been published for a long time, people may have linked to
their URLs, so a bare deletion that returns 404s is not acceptable — the old URLs must redirect
gracefully to the equivalent material on hypershell.org.

## Outcome / vision

The Sphinx documentation is trimmed to the material that still belongs in-repo (the **Intro** and
**Reference** sections). The Tutorial and Project (blog + roadmap) sections no longer appear anywhere
in the built site's navigation or table of contents, and their source pages are gone from `docs/`.
Anyone following an old, previously-published URL to one of the removed pages is redirected to the
corresponding content on hypershell.org rather than hitting a dead link. The docs still build cleanly,
with no dangling cross-references left behind. No code, CLI, or packaging behavior changes.

## Acceptance criteria (the contract)

- **R1** — The documentation SHALL no longer present the **Tutorial** section: the Tutorial toctree
  caption in `docs/index.rst` and the backing pages under `docs/tutorial/` SHALL be removed, so the
  built docs contain no Tutorial navigation entry and no tutorial pages.
- **R2** — The documentation SHALL no longer present the **Project** section: the Project toctree
  caption in `docs/index.rst`, the blog under `docs/blog/` (index and all posts), and
  `docs/roadmap.rst` SHALL be removed, so the built docs contain no blog or roadmap navigation entry
  and no such pages.
- **R3** — IF a reader requests a previously-published URL for one of the removed pages (a tutorial
  page, the blog index or a blog post, or the roadmap), THEN the documentation site SHALL redirect
  them to the corresponding page on hypershell.org (or, where no one-to-one equivalent exists, to a
  sensible fallback on that site) rather than returning a 404.
- **R4** — WHERE a **retained** documentation page (or `README.rst`) currently links to one of the
  removed pages, that inbound reference SHALL be removed outright (not repointed), leaving no dangling
  `:ref:`/`:doc:` link or broken toctree entry.
- **R5** — The docs build (`uv run sphinx-build docs docs/_build`) SHALL complete with no new warnings
  relative to the known pre-existing ones (the `manual.rst` / `task_submit.rst` "not in any toctree"
  warnings).

## Non-goals (no-gos)

- Touching the **Intro** section (`getting_started`, `install`, `security`, `alternatives`) or the
  **Reference** section (`cli/`, `api/`, `config`, `logging`, `database`, `templates`) — those stay.
- Authoring, migrating, or editing any content **on** hypershell.org; the content already has its new
  home there. This task only removes it from `docs/` and points old URLs at it.
- Any change to source code, CLI behavior, `share/` completions, packaging, or the generated
  `docs/_include/*.rst` CLI help snippets (except removing a reference that points at a deleted page,
  should one exist).
- A broader documentation restructure, redesign, or content refresh beyond removing these two
  sections and wiring the redirects.

## Clarifications

- **Q:** When the pages are deleted, old hosted-docs URLs would 404. How should removed pages be
  handled? — **A:** Remove them from the docs site and the table of contents, but **preserve the old
  URLs via a graceful redirect** to the equivalent content on hypershell.org (resolved 2026-07-28).
- **Q:** What should happen to references *from retained pages* (e.g. a "next steps" link into the
  tutorial) that point at removed pages? — **A:** Remove those references outright; do **not** repoint
  them to the website (resolved 2026-07-28).

## Related materials

- New website (the content's new home): sibling checkout `../hypershell.org/` — its published URL
  structure is what R3's old→new redirect map must target; `/hs-plan` should confirm the equivalent
  URLs (and the fallback) from it.
- `docs/index.rst` — the **Tutorial** toctree caption and the **Project** toctree caption
  (`blog/index` + `roadmap`).
- Pages to remove: `docs/tutorial/{basic,distributed,hybrid,advanced}.rst`; `docs/blog/` (`index.rst`
  + ~12 dated posts); `docs/roadmap.rst`.
- **Redirect mechanism is a `/hs-plan` design decision** — it depends on where the docs are hosted
  (e.g. ReadTheDocs redirect rules vs. an in-repo approach such as `sphinx-reredirects` /
  meta-refresh pages). `/hs-plan` must identify the hosting/mechanism and, if graceful redirects
  cannot be achieved from this repo alone, surface that to the human before building.
- Docs conventions and the pre-existing build-warning baseline: `AGENTS.md` (Docs section).
