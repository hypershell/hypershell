# Topic 03 — Inbound refs (R4) & conf.py coupling

## Headline
Only **two** inbound `:ref:` links from RETAINED pages point into the removed
sections, and **conf.py has ZERO coupling** to the removed sections (no ablog —
`docs/blog/` is a plain hand-maintained toctree of `.rst` files). Clean removal
is straightforward.

## (1) Inbound references from RETAINED material → removed pages (R4: DELETE)

| file:line | exact text | action |
|---|---|---|
| `docs/cli/index.rst:9` | `See our :ref:`tutorials <tutorial_basic>` (COMING SOON) for use-case specific demonstrations.` | **Delete the whole line** (only ref to a `tutorial/*` label). |
| `docs/alternatives.rst:518` | `(A ``hypershell-nextflow`` integration is on the :ref:`roadmap <roadmap>`.)` | **Delete this trailing parenthetical sentence** (the surrounding paragraph stands on its own). |

`README.rst` and any root-level `.rst`: **no matches** (grep for
tutorial/roadmap/blog empty).

### Non-matches (do NOT touch — false positives)
- `docs/alternatives.rst:153`, `:723` — `--joblog` / `joblog` (GNU Parallel's
  log file, unrelated to `docs/blog/`).
- `docs/roadmap.rst:12,27` — internal to a removed page (deleted anyway).
- `docs/index.rst` tutorial/blog/roadmap hits — the toctrees themselves (see R1/R2).

### Ref labels defined in removed pages (all become dangling if left referenced)
`tutorial_basic|distributed|hybrid|advanced`, `blog`, `roadmap`, and the 12
`YYYYMMDD_*_release` / `20241115_announce_logo` labels. Confirmed: **no RETAINED
page references any individual blog-post label or any tutorial label** except the
two rows above. Blog-post labels are cross-referenced only from `blog/index.rst`.

## (2) conf.py coupling — NONE
Full read of `docs/conf.py` (145 lines):
- `extensions` = autodoc, napoleon, intersphinx, extlinks, viewcode,
  `sphinxext.opengraph`, `sphinx_sitemap`, `sphinx_inline_tabs`,
  `sphinx_copybutton`, `sphinxcontrib.details.directive`. **No `ablog`, no blog/
  tutorial extension.**
- No `blog_*` / `ablog_*` settings, no `html_sidebars`, no `html_extra_path`, no
  intersphinx entry, no nav/theme option references the removed sections.
- `exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']` — no removed path.
- `html_static_path=['_static']`, `templates_path=['_templates']` — unrelated
  (`_templates` dir doesn't even exist; pre-existing, leave it).
- `man_pages` targets `manual` only. `html_baseurl='https://hypershell.readthedocs.io'`.
- **No in-repo redirect mechanism** (no `rediraffe`/`sphinx-reredirects`/
  `html_extra_path`). => R3 redirects are NOT satisfiable inside `docs/conf.py`;
  they must be handled at the RTD dashboard or hypershell.org (out of this topic).

**Conclusion: conf.py needs NO edits for R1/R2/R4/R5.**

## (3) _static / _include / _templates
- `docs/_static/` = custom.css + two logo PNGs — no tie to removed pages.
- `docs/_include/` — no tutorial/roadmap/blog references.
- `docs/_templates/` absent. Removed pages contain no `literalinclude`/`image::`/
  `figure::`/`include::` pulling from the retained tree (grep empty), and no
  `:orphan:`.

## EXACT edit list (R1/R2/R4 + clean R5 build)

**Delete files (R1):** entire `docs/tutorial/` (basic.rst, distributed.rst,
hybrid.rst, advanced.rst).

**Delete files (R2):** entire `docs/blog/` (index.rst + 12 posts = 13 files) and
`docs/roadmap.rst`.

**Edit `docs/index.rst`:** remove both trailing toctree blocks —
- Tutorial block (lines ~205–212: `:caption: Tutorial` + 4 `tutorial/*` entries).
- Project block (lines ~214–219: `:caption: Project` + `blog/index` + `roadmap`).

**Edit inbound refs (R4):**
- `docs/cli/index.rst` — delete line 9 (the `:ref:`tutorials <tutorial_basic>``).
- `docs/alternatives.rst` — delete the `:ref:`roadmap <roadmap>`` parenthetical at line 518.

## R5 build note
The delete + toctree-removal + ref-deletion must land **together**. Leaving pages
but removing toctree entries → new "not in any toctree" warnings; deleting pages
but keeping the two `:ref:`s → "undefined label" warnings. Doing all three yields
no new warnings vs the known baseline (`manual.rst` + `docs/cli/task_submit.rst`
"not in any toctree"). Verify with `uv run sphinx-build docs docs/_build`.
