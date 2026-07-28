# PLAN — Trim docs sections that have moved to hypershell.org

> **Status:** Draft for review · **Last updated:** 2026-07-28
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Delete the **Tutorial** and **Project** (blog + roadmap) sections from the Sphinx docs — the pages,
their two `docs/index.rst` toctree captions, and the two inbound `:ref:`s that would otherwise dangle
— in one atomic change so the build stays at its clean 2-warning baseline. Then preserve the old
ReadTheDocs URLs two ways ("defense in depth", per the human decision): in-repo `sphinx-reredirects`
that emits meta-refresh stubs at the old paths pointing to `www.hypershell.org`, **plus** a committed
runbook for the maintainer to add native RTD dashboard Page Redirects. This is a small, docs-only
change with no source/CLI/runtime impact; the one wrinkle (a docs-group dev dependency) is recorded in
the deviation table below.

## 2. Design

Two vertical slices, each independently verifiable by a clean `sphinx-build`:

**Slice 1 — Remove the migrated sections (the core).** In `docs/index.rst`, delete the tail
**Tutorial** toctree block (lines 205–212) and **Project** toctree block (lines 214–219). Delete the
backing sources: `docs/tutorial/` (4 `.rst` + dir), `docs/blog/` (12 posts + `index.rst` + dir),
`docs/roadmap.rst`. Delete the only two inbound references from *retained* pages (verified exhaustive;
`README.rst` has none):
- `docs/cli/index.rst:9` — remove the whole line (`See our :ref:\`tutorials <tutorial_basic>\` (COMING SOON) …`).
- `docs/alternatives.rst:518` — remove the trailing parenthetical, leaving the sentence ending `… best.`

`docs/conf.py` needs **no** edits for this slice — the blog is a plain toctree (no `ablog`), and
nothing in `_static`/`_include`/theme config references the removed pages. All three edit-types must
land together: a partial removal transiently produces "undefined label" or new "not in any toctree"
warnings and fails R5. ([`research/03`](research/03-inbound-refs-and-conf-coupling.md),
[`research/05`](research/05-removal-surgery-and-baseline.md).)

**Slice 2 — Preserve the old URLs (R3, both mechanisms).**
- *In-repo:* add `sphinx-reredirects` to the `docs` dependency group in `pyproject.toml`, add
  `'sphinx_reredirects'` to `extensions` in `docs/conf.py`, and an **explicit** `redirects = {…}` map
  (the 18-entry table in [`research/00-digest.md`](research/00-digest.md)) whose keys are the deleted
  source docnames and whose values are absolute `https://www.hypershell.org/…` URLs. Explicit keys
  emit a stub even though the source doc is gone (wildcards would not); the build writes
  `docs/_build/<old-path>.html` meta-refresh pages that RTD serves at the old URLs.
- *Out-of-repo (runbook):* commit `spec/docs-trim-migrated-sections/redirect-runbook.md` — the same
  map as a maintainer checklist for adding native RTD Page Redirects (real HTTP 30x, version-agnostic)
  in the RTD admin, since RTD redirects cannot be set from `.readthedocs.yaml`/repo files.

Host/target facts: old URLs are on `hypershell.readthedocs.io`; the new site is a Flask app on the
separate origin `www.hypershell.org`, so it can only be the redirect *target*.
([`research/01`](research/01-docs-hosting-and-deploy.md),
[`research/02`](research/02-website-url-map.md),
[`research/04`](research/04-redirect-mechanism-options.md).)

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Slice 1: delete Tutorial toctree block (`docs/index.rst` 205–212) + `docs/tutorial/*`. |
| R2 | Slice 1: delete Project toctree block (`docs/index.rst` 214–219) + `docs/blog/*` + `docs/roadmap.rst`. |
| R3 | Slice 2: `sphinx-reredirects` explicit `redirects` map (in-repo stubs) **and** the RTD Page-Redirect runbook — both point old paths at `www.hypershell.org`. |
| R4 | Slice 1: delete `docs/cli/index.rst:9` and the `docs/alternatives.rst:518` parenthetical (only two inbound refs; README clean). |
| R5 | Both slices verify with a **fresh** (`del docs/_build`) `sphinx-build` staying at the 2-warning baseline (`task_submit.rst`, `manual.rst`), 0 errors; stubs add no toctree warnings. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before research and again after this design. This is a docs-only change; the only invariant
family it touches is **§12 (project conventions)**.

- **§12 — docs same-commit rule:** N/A in the usual direction (no CLI change, so `docs/_include/*.rst`
  help snippets and `share/` completions are untouched) — but honored: this *is* the docs change, made
  in its own commits.
- **§12 — supported Python 3.11–3.14:** `sphinx-reredirects` (1.1.0) is `requires-python>=3.11`, a
  superset match; no 3.9/3.10 shim reintroduced.
- **§12 — dependency floors pinned low for EPEL parity:** the new dependency is **docs/dev-only**
  (same tier as the existing `sphinx`/`furo`/`sphinx_sitemap`), not a runtime dep, not an extra, and
  not shipped in the wheel — so the EPEL-runtime-floor concern does not apply; still, use a low floor.
- **§12 — wheel `share/` + CI metadata lockstep:** untouched (sphinx-reredirects is not shared-data;
  no `share/` path changes), so the CI metadata assertion is unaffected.
- No §1–§11 subsystem is involved (no `data/model.py`/`server.py`/`client.py`/`core/*`/cluster). Not
  a high-blast-radius change.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Adds `sphinx-reredirects` to `pyproject.toml` `[dependency-groups].docs` — brushes the GOAL non-goal "no packaging change". | Human chose **Both** for R3 (2026-07-28): in-repo, build-verifiable URL preservation needs an in-repo mechanism, and `sphinx-reredirects` is the least-effort one that emits stubs for *deleted* pages. It is a docs/dev tool, not part of the distributed wheel/runtime/extras, so it does not alter the shipped package. | RTD-dashboard-only (zero in-repo change) — rejected: not landable/verifiable in the PR and the human wanted defense-in-depth. Hand-written `html_extra_path` meta-refresh stubs (0 dep) — rejected: ~18 error-prone manual HTML files to maintain. |

## 4. Rabbit holes (resolved)

- **Where are the docs hosted / what redirect facility exists?** → ReadTheDocs; RTD redirects are
  dashboard/API-only (not repo-configurable). Old URLs on `hypershell.readthedocs.io`.
  ([`research/01`](research/01-docs-hosting-and-deploy.md),
  [`research/04`](research/04-redirect-mechanism-options.md).)
- **Does every removed page have a hypershell.org equivalent (the redirect map)?** → Yes for all 4
  tutorials + all 12 blog posts (verified 1:1 against `../hypershell.org/content/`); **roadmap has
  none** → `/about` fallback. Full map in [`research/00-digest.md`](research/00-digest.md).
  ([`research/02`](research/02-website-url-map.md).)
- **Is the blog an `ablog` install (hidden coupling)?** → No; plain toctree of `.rst`. conf.py needs no
  removal edits. ([`research/03`](research/03-inbound-refs-and-conf-coupling.md).)
- **Which inbound refs would dangle, and what's the warning baseline?** → Exactly two refs; baseline is
  2 pre-existing "not in any toctree" warnings; stale `docs/_build` masks new warnings.
  ([`research/05`](research/05-removal-surgery-and-baseline.md).)
- **Will `sphinx-reredirects` emit stubs for deleted pages?** → Yes, for **explicit** keys (not
  wildcards); verified against 1.1.0. The P2 verify greps the emitted stub HTML as the real gate.
  ([`research/04`](research/04-redirect-mechanism-options.md).)

## 5. Risks & open questions

- **R3's durable half is out-of-repo.** The `sphinx-reredirects` stubs cover future `latest`/`stable`
  builds; the native RTD Page Redirects (the runbook) are a **human RTD-admin action** the PR cannot
  land or verify. `/hs-publish` should surface the runbook so it isn't forgotten. Old *pinned*
  versions (e.g. `/en/2.8.0/…`) keep their real pages, so no 404 there regardless.
- **Tutorial route plural/singular footgun.** Targets use `/tutorials/<slug>` (plural) per the
  verified Flask routes, though the content dir is `content/tutorial/` (singular). `/hs-build` must
  reconfirm the live route before finalizing (noted in the digest).
- **`sphinx-reredirects` floor.** If the resolved version doesn't emit stubs for deleted explicit keys,
  the P2 grep verify fails — bump the floor. Low risk (behavior verified on current release).
- **Soft vs hard redirect.** The in-repo stubs are HTTP-200 + meta-refresh (not 30x); acceptable for
  "graceful", and the RTD runbook provides the real 30x. No SEO regression expected beyond the interim.

## 6. Verification strategy

Docs build is the spine; every verify uses a **fresh** build (`del docs/_build` first) so a stale
pickle can't hide new warnings.

- **Slice 1 (R1/R2/R4/R5):** `del docs/_build; uv run sphinx-build -b html docs docs/_build` →
  succeeds with **exactly** the two baseline warnings (`docs/cli/task_submit.rst`, `docs/manual.rst`)
  and 0 errors; grep the log for any *other* WARNING/ERROR (must be none); confirm removed files/dirs
  are gone and `docs/index.rst` has no Tutorial/Project caption.
- **Slice 2 (R3):** after `uv sync` (picks up the new docs dep) + a fresh build, assert the stub pages
  exist and carry the right absolute targets, e.g. `grep -q www.hypershell.org/about
  docs/_build/roadmap.html`, `…/tutorials/basic docs/_build/tutorial/basic.html`, `…/blog
  docs/_build/blog/index.html`; build stays at the 2-warning baseline; and each blog/tutorial target
  slug resolves in `../hypershell.org/content/`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
