# 01 — Docs hosting & deploy (R3 redirect facility)

## Verdict: docs are published on **Read the Docs**

Unambiguous. Evidence:

- `/.readthedocs.yaml` (v2 config): builds `docs/conf.py` on RTD, `ubuntu-24.04`,
  python 3.12, installs via `uv sync --all-extras --group docs`.
- `docs/conf.py:68` → `html_baseurl = 'https://hypershell.readthedocs.io'`.
- **No** competing host: no `CNAME`, `netlify.toml`, `_redirects`, `vercel.json` at
  repo root; **no `gh-pages`/`docs` branch** (`git branch -a`); **no docs/pages/deploy
  job** in `.github/workflows/` (only `tests.yml`, `publish.yml`, `docker.yml`; the only
  "pages" hits are man-page wheel checks).
- `docs/Makefile` is the stock sphinx passthrough (`_build`). No custom deploy.
- `sphinx_sitemap` + `html_baseurl` ⇒ `sitemap.xml` is generated from that base URL;
  deleted pages drop out of the sitemap automatically on next build (no stale entries).

**Current base URL / old published URLs:** RTD serves at
`https://hypershell.readthedocs.io/en/latest/` (and `/en/stable/`). So the URLs R3 must
protect look like:
`…/en/latest/tutorial/{basic,distributed,hybrid,advanced}.html`,
`…/en/latest/blog/index.html`, the ~12 dated `…/en/latest/blog/<slug>.html`, and
`…/en/latest/roadmap.html` — for **both** `latest` and `stable`.

## Redirect facilities for R3 (critical)

RTD offers native **user-defined redirects** (verified against RTD docs):
- **Types:** Page Redirect (version-agnostic), Exact Redirect (version/path-specific),
  Clean-URL⇄HTML. Both Page and Exact **can target an external domain** — i.e. can
  redirect to `https://hypershell.org/...`. First matching rule wins (order matters).
- **Where configured:** **Admin → Redirects dashboard (or RTD API) only — NOT in
  `.readthedocs.yaml`.** There is no in-repo YAML redirect support.

### Consequence for the plan (important)
R3 **cannot be closed by editing `docs/` files alone.** Two viable routes:

1. **RTD dashboard redirects (native, recommended):** after the pages are deleted, add
   redirect rules on readthedocs.org → `hypershell.org` equivalents (or a sensible
   fallback there). This is an **out-of-repo human/admin action** the plan must call out
   explicitly as a checklist item; it is not verifiable by `sphinx-build`. A Page
   Redirect `tutorial/basic.html` → external, etc., covers both `latest` & `stable` in
   one rule each.
2. **In-repo alternative — `sphinx-reredirects`:** an extension that emits client-side
   meta-refresh HTML stubs at build time from a `redirects = {...}` map in `conf.py`.
   Keeps R3 entirely in-repo/reviewable and survives version pins. **Not currently a
   dependency** (would add one line to the `docs` group in `pyproject.toml` +
   `conf.py`), and its handling of *deleted* source docs needs a quick verification build
   — flag as a small risk. Note the GOAL says "no packaging change," which a new docs-dep
   arguably touches; confirm with planner if route 2 is chosen.

## Recommendation
Treat R3 as an **RTD dashboard redirect task (route 1)** — it is the platform's native,
zero-new-dependency mechanism and matches "graceful redirect to hypershell.org." The
plan should (a) do the in-repo deletions + toctree edits for R1/R2/R4/R5, and (b) add an
explicit, out-of-repo "add RTD Page Redirects → hypershell.org" checklist item for R3,
listing every removed path. Keep `sphinx-reredirects` (route 2) as the fallback only if
the maintainer wants R3 self-contained in the repo.
