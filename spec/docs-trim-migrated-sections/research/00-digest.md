# 00 — Research digest (consolidated decisions)

Synthesis of briefs `01`–`05` + the completeness-critic verify pass, with the one cross-brief
contradiction resolved. This is the decision record `PLAN.md` builds from.

## Confirmed ground truth

- **Docs host = ReadTheDocs.** `.readthedocs.yaml` (v2) builds `docs/conf.py`; `docs/conf.py:68`
  sets `html_baseurl = 'https://hypershell.readthedocs.io'`. No competing host (no
  `CNAME`/`netlify.toml`/`_redirects`/`vercel.json`, no `gh-pages` branch, no docs-deploy GH
  workflow). Old published URLs live at `https://hypershell.readthedocs.io/en/{latest,stable}/…`.
  (`01`)
- **hypershell.org = a Flask app** (`org_hypershell`, gunicorn) on a **different origin**
  (`https://www.hypershell.org`). It is only the redirect *target*; it never receives the
  readthedocs.io requests, so it **cannot** perform the redirects itself. (`02`)
- **conf.py has zero coupling** to the removed sections — **no `ablog`** (the blog is a plain,
  hand-maintained toctree of `.rst`), no `blog_*`/`html_sidebars`/`html_extra_path`/intersphinx/
  `exclude_patterns` entry touching them. `_static`/`_include` clean; `_templates` doesn't exist.
  conf.py needs **no edits** for R1/R2/R4/R5. (`03`)
- **R5 warning baseline = exactly 2 pre-existing warnings**, build succeeds, 0 errors:
  `docs/cli/task_submit.rst` and `docs/manual.rst` — "document isn't included in any toctree". (`05`)
- **Build hygiene:** Sphinx reuses a pickled env, so a stale cache masks new warnings — **every
  verify build must remove `docs/_build` first** (`del docs/_build`). `docs/_build` is gitignored
  (`.gitignore:72`). (`05`)

## Removal surgery (R1, R2, R4, R5)

- **`docs/index.rst`:** delete the two tail toctree blocks together — **Tutorial** (lines 205–212)
  and **Project** (lines 214–219). Verified in place.
- **Delete files/dirs:** `docs/tutorial/{basic,distributed,hybrid,advanced}.rst` (+ dir);
  `docs/blog/` — all 13 files (12 posts + `index.rst`) (+ dir); `docs/roadmap.rst`.
- **Inbound refs to delete (R4) — exactly two** (verified; README.rst has none):
  - `docs/cli/index.rst:9` — whole line `See our :ref:\`tutorials <tutorial_basic>\` (COMING SOON) …`
  - `docs/alternatives.rst:518` — trailing parenthetical only; line becomes `best.` (drop
    `(A \`\`hypershell-nextflow\`\` integration is on the :ref:\`roadmap <roadmap>\`.)`).
- **Land all three change-types (page deletes + toctree edits + ref deletes) in one phase** — partial
  edits transiently spike to "undefined label" / new "not in any toctree" warnings and break R5.

## R3 redirect decision — **Both (defense in depth)** *(human-chosen 2026-07-28)*

Resolves the one cross-brief contradiction (brief `04` claimed R3 was fully in-repo via
`sphinx-reredirects`; briefs `01`/`02`/`03` + the critic showed RTD-native redirects are
dashboard/API-only and `sphinx-reredirects` only yields a *soft* meta-refresh + adds a dep). The
human chose to do **both**:

1. **In-repo — `sphinx-reredirects`** (immediate, build-verifiable coverage of `latest`/`stable`):
   - Add `sphinx-reredirects` to the `docs` dependency group in `pyproject.toml` (low floor,
     EPEL-parity spirit; a docs/dev-only tool like the existing `sphinx`/`furo`/`sphinx_sitemap` —
     **not** shipped in the wheel).
   - Add `'sphinx_reredirects'` to `extensions` in `docs/conf.py` and an **explicit** `redirects = {…}`
     map (the full table below). Explicit (non-wildcard) keys emit a meta-refresh stub **even for
     deleted source docs** — wildcards match only existing docs and would miss them (brief `04`,
     verified against sphinx-reredirects 1.1.0, `requires-python>=3.11`).
2. **Out-of-repo — RTD dashboard Page Redirects** (durable, native HTTP 30x): commit a **runbook**
   (`spec/docs-trim-migrated-sections/redirect-runbook.md`) with the same map for the maintainer to
   apply in the RTD admin — one Page Redirect per old path → the external `www.hypershell.org` URL
   (page redirects apply across versions, so they also cover old pinned builds).

**Why both:** the in-repo stubs make R3 land + verify inside this PR (executed-evidence spine);
the RTD-native rules are the superior long-term real-30x solution the docs-only PR cannot itself land.

### Redirect map (old docname → absolute target) — 18 entries

Old paths are relative to `docs/` (sphinx-reredirects keys are source docnames). Old published URL =
`https://hypershell.readthedocs.io/en/<ver>/<docname>.html`. **All 12 blog slugs + 4 tutorials
verified 1:1 against `../hypershell.org/content/`; `roadmap` is the only page with no equivalent →
`/about` fallback.**

| Old docname | Target URL |
|---|---|
| `tutorial/basic` | `https://www.hypershell.org/tutorials/basic` |
| `tutorial/distributed` | `https://www.hypershell.org/tutorials/distributed` |
| `tutorial/hybrid` | `https://www.hypershell.org/tutorials/hybrid` |
| `tutorial/advanced` | `https://www.hypershell.org/tutorials/advanced` |
| `blog/index` | `https://www.hypershell.org/blog` |
| `blog/20230329_2_2_0_release` | `https://www.hypershell.org/blog/hypershell-2-2-0` |
| `blog/20230413_2_3_0_release` | `https://www.hypershell.org/blog/hypershell-2-3-0` |
| `blog/20230602_2_4_0_release` | `https://www.hypershell.org/blog/hypershell-2-4-0` |
| `blog/20240518_2_5_0_release` | `https://www.hypershell.org/blog/hypershell-2-5-0` |
| `blog/20240706_2_5_2_release` | `https://www.hypershell.org/blog/hypershell-2-5-2` |
| `blog/20241115_2_6_0_release` | `https://www.hypershell.org/blog/hypershell-2-6-0` |
| `blog/20241115_announce_logo` | `https://www.hypershell.org/blog/new-logo-and-discord` |
| `blog/20241231_2_6_1_release` | `https://www.hypershell.org/blog/hypershell-2-6-1` |
| `blog/20250215_2_6_5_release` | `https://www.hypershell.org/blog/hypershell-2-6-5` |
| `blog/20250405_2_6_6_release` | `https://www.hypershell.org/blog/hypershell-2-6-6` |
| `blog/20250504_2_7_0_release` | `https://www.hypershell.org/blog/hypershell-2-7-0` |
| `blog/20260705_2_8_0_release` | `https://www.hypershell.org/blog/hypershell-2-8-0` |
| `roadmap` | `https://www.hypershell.org/about` |

## Build-time reconfirmations for `/hs-build` (don't trust this file blindly)

- **Tutorial route is `/tutorials/<slug>` (plural)** though the sibling content dir is
  `content/tutorial/` (singular) — a footgun flagged by brief `02`/critic. Reconfirm the live Flask
  route in `../hypershell.org/src/` (or `curl -sI`) before finalizing tutorial targets.
- Confirm each blog target slug exists in `../hypershell.org/content/blog/` (verified today: all 12
  present) and that `content/pages/about.md` backs `/about`.
- Confirm the resolved `sphinx-reredirects` version actually emits stubs for the **deleted** explicit
  keys (the P2 verify greps the stub HTML — that is the real gate; bump the floor if it doesn't).
