# 02 — Website URL Map (R3 redirect map)

## Stacks & hosts (the load-bearing fact for R3)

- **Old docs host = ReadTheDocs.** `docs/conf.py` sets `html_baseurl =
  'https://hypershell.readthedocs.io'`; `.readthedocs.yaml` (v2) builds `docs/conf.py`.
  So the *old, previously-published URLs are on `hypershell.readthedocs.io`* — e.g.
  `https://hypershell.readthedocs.io/en/latest/tutorial/basic.html`.
- **New host = `hypershell.org`** — a **Flask** app (`org_hypershell`, `flask>=3.1`,
  `gunicorn`), Dockerized, deployed by **Coolify** (auto-deploy on push to `main`),
  fronted by **Cloudflare** (Full-strict TLS). Stateless; content is Markdown baked into
  the image (`content/blog/*.md`, `content/tutorial/*.md`). Canonical origin
  `https://www.hypershell.org` (`SITE_URL`).
- These are **different origins.** The website's own `DOCS_URL` default still points *out*
  to `https://hypershell.readthedocs.io`, confirming docs stay on RTD.

### CRITICAL: the Flask app CANNOT perform these redirects
The old URLs are served by RTD, an origin the `hypershell.org` container never receives
requests for. The Flask app is only the redirect **target**. R3 must be satisfied on the
**RTD side**. Two mechanisms:

1. **RTD user-defined redirects (recommended).** RTD supports redirects to **fully-qualified
   external URLs** and returns real **HTTP 30x**. Use a **Page Redirect** (applies across all
   versions, version-agnostic `From` path like `/tutorial/basic.html`) per removed page.
   Configured in the **project dashboard/API — NOT in `.readthedocs.yaml`**. Robust, but
   out-of-repo: a docs-only PR can't land it; needs a runbook + human with RTD admin. Works
   even after the `.rst` is deleted (redirect is host-level, independent of the build).
2. **`sphinx-reredirects` (in-repo alt).** A `redirects={}` dict in `conf.py` emits
   meta-refresh HTML stubs at build for each old path; target may be absolute/external and it
   emits for deleted-source paths too. But it's a soft (200 + client bounce) redirect and adds
   a docs-group dependency (brushes the "no packaging change" non-goal). Weaker than option 1.

## Slug facts
- Website tutorial/blog slug = **Markdown filename stem** (`content.py:276`,
  `path.stem`). Tutorial slugs match 1:1: `basic/distributed/hybrid/advanced`.
- Docs tutorial pages are "Under construction" stubs; website has the real content — clean
  redirect targets.

## Redirect MAP (old RTD path → hypershell.org URL)

`From` paths are version-agnostic (RTD Page Redirect strips `/en/latest/`).

| old doc page | old URL path (RTD) | hypershell.org target |
|---|---|---|
| tutorial/basic | `/tutorial/basic.html` | `https://www.hypershell.org/tutorials/basic` |
| tutorial/distributed | `/tutorial/distributed.html` | `.../tutorials/distributed` |
| tutorial/hybrid | `/tutorial/hybrid.html` | `.../tutorials/hybrid` |
| tutorial/advanced | `/tutorial/advanced.html` | `.../tutorials/advanced` |
| blog/index | `/blog/index.html` | `https://www.hypershell.org/blog` |
| blog/20230329_2_2_0_release | `/blog/20230329_2_2_0_release.html` | `.../blog/hypershell-2-2-0` |
| blog/20230413_2_3_0_release | `.../20230413_2_3_0_release.html` | `.../blog/hypershell-2-3-0` |
| blog/20230602_2_4_0_release | `.../20230602_2_4_0_release.html` | `.../blog/hypershell-2-4-0` |
| blog/20240518_2_5_0_release | `.../20240518_2_5_0_release.html` | `.../blog/hypershell-2-5-0` |
| blog/20240706_2_5_2_release | `.../20240706_2_5_2_release.html` | `.../blog/hypershell-2-5-2` |
| blog/20241115_2_6_0_release | `.../20241115_2_6_0_release.html` | `.../blog/hypershell-2-6-0` |
| blog/20241115_announce_logo | `.../20241115_announce_logo.html` | `.../blog/new-logo-and-discord` |
| blog/20241231_2_6_1_release | `.../20241231_2_6_1_release.html` | `.../blog/hypershell-2-6-1` |
| blog/20250215_2_6_5_release | `.../20250215_2_6_5_release.html` | `.../blog/hypershell-2-6-5` |
| blog/20250405_2_6_6_release | `.../20250405_2_6_6_release.html` | `.../blog/hypershell-2-6-6` |
| blog/20250504_2_7_0_release | `.../20250504_2_7_0_release.html` | `.../blog/hypershell-2-7-0` |
| blog/20260705_2_8_0_release | `.../20260705_2_8_0_release.html` | `.../blog/hypershell-2-8-0` |
| roadmap | `/roadmap.html` | **NO EQUIVALENT** → fallback `https://www.hypershell.org/about` |

**Roadmap fallback:** no roadmap page exists on the website (routes: `/`, `/blog`,
`/tutorials`, `/get-started`, `/alternatives`, `/about`, `/factory`). Roadmap content is
partly superseded (it *describes* the now-live website). Best fallback: `/about` (project
story/community); acceptable alternatives: site root `/`, or the GitHub repo.

## Recommendation
Satisfy R3 with **RTD dashboard Page Redirects** (external `To` URLs, real 30x) per the table
above — the plan should ship the map as a committed runbook (e.g. in `spec/` or a docs note)
plus a human/RTD-admin step, since it's out-of-repo. Blog index → `/blog`, tutorials 1:1,
roadmap → `/about`. Treat `sphinx-reredirects` only as a fallback if out-of-repo RTD config is
undesirable (accepting a soft meta-refresh + a docs dependency).
