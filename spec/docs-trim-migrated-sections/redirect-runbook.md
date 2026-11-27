# Redirect runbook — old docs URLs → hypershell.org

The Tutorial, blog, and roadmap pages were removed from the Sphinx docs (they now live on
**hypershell.org**). This preserves their previously-published URLs two ways ("defense in depth"):

1. **In-repo (already shipped in this branch):** `sphinx-reredirects` (added to the `docs` dependency
   group + a `redirects` map in [`docs/conf.py`](../../docs/conf.py)) emits a **meta-refresh stub**
   at each old path on every build. This covers `latest`/`stable` immediately and is verified by the
   docs build. It is a *soft* redirect (HTTP 200 + client-side refresh).

2. **Out-of-repo (this runbook — a human RTD-admin action):** add native **ReadTheDocs Page
   Redirects** for real HTTP 30x. These cannot be set from the repo (`.readthedocs.yaml`/`conf.py`) —
   only via the RTD **Admin dashboard** (or API). Page Redirects apply across **all** versions, so
   they also cover old pinned builds and are the durable long-term solution.

## Facts

- **Docs host:** ReadTheDocs — `html_baseurl = https://hypershell.readthedocs.io`.
- **Old URL shape:** `https://hypershell.readthedocs.io/en/<version>/<page>.html`.
- **Target site:** `https://www.hypershell.org` (canonical `SITE_URL`; a Flask app on a separate
  origin — it cannot perform these redirects itself, it is only the target).

## How to add the RTD Page Redirects

In the RTD project admin: **Admin → Redirects → Add redirect**. For each row below create a
**Page Redirect** (type `page`) with the given *From URL* (the path after `/en/<version>`, leading
slash) and *To URL* (the absolute hypershell.org URL). A Page Redirect is version-agnostic — one rule
per row covers `latest`, `stable`, and every pinned version.

> Note: RTD "Page redirect" To-URLs may be relative by default; for an **external** target enter the
> full `https://www.hypershell.org/...` URL (RTD supports absolute external To-URLs). If the project's
> RTD plan restricts external redirects, use an "Exact redirect" per row with From
> `/en/latest/<page>.html` and `/en/stable/<page>.html` instead.

## Redirect map (18 rows)

| From (page path) | To (absolute) |
|---|---|
| `/tutorial/basic.html` | `https://www.hypershell.org/tutorials/basic` |
| `/tutorial/distributed.html` | `https://www.hypershell.org/tutorials/distributed` |
| `/tutorial/hybrid.html` | `https://www.hypershell.org/tutorials/hybrid` |
| `/tutorial/advanced.html` | `https://www.hypershell.org/tutorials/advanced` |
| `/blog/index.html` | `https://www.hypershell.org/blog` |
| `/blog/20230329_2_2_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-2-0` |
| `/blog/20230413_2_3_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-3-0` |
| `/blog/20230602_2_4_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-4-0` |
| `/blog/20240518_2_5_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-5-0` |
| `/blog/20240706_2_5_2_release.html` | `https://www.hypershell.org/blog/hypershell-2-5-2` |
| `/blog/20241115_2_6_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-6-0` |
| `/blog/20241115_announce_logo.html` | `https://www.hypershell.org/blog/new-logo-and-discord` |
| `/blog/20241231_2_6_1_release.html` | `https://www.hypershell.org/blog/hypershell-2-6-1` |
| `/blog/20250215_2_6_5_release.html` | `https://www.hypershell.org/blog/hypershell-2-6-5` |
| `/blog/20250405_2_6_6_release.html` | `https://www.hypershell.org/blog/hypershell-2-6-6` |
| `/blog/20250504_2_7_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-7-0` |
| `/blog/20260705_2_8_0_release.html` | `https://www.hypershell.org/blog/hypershell-2-8-0` |
| `/roadmap.html` | `https://www.hypershell.org/about` |

**Note on `roadmap`:** hypershell.org has no dedicated roadmap page, so it points at `/about`
(the closest project-story page). Update the target if a roadmap page is later added to the site.

The same map (as source-docname → URL) is encoded in `docs/conf.py`'s `redirects` dict; keep the two
in sync if the site's URLs change.
