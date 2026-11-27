# Topic 04 — Redirect mechanism options (R3)

## Host (ground truth from this repo)
- **ReadTheDocs**, community platform. `.readthedocs.yaml` present; `docs/conf.py`
  `html_baseurl = 'https://hypershell.readthedocs.io'`; `pyproject.toml` project URL
  `Documentation = "https://hypershell.readthedocs.io"`.
- Old URLs live under **`https://hypershell.readthedocs.io/en/<ver>/<page>.html`** (e.g.
  `/en/latest/tutorial/basic.html`, `/en/latest/blog/index.html`, `/en/latest/roadmap.html`).
  These are on **readthedocs.io**, NOT hypershell.org — so any redirect must fire at the RTD
  layer (build output or RTD dashboard). The hypershell.org app cannot intercept them.
- New home is a served app at hypershell.org with routes `/tutorial/<name>`, `/blog`,
  `/blog/<slug>`, `/pages/about`, etc. (slugs differ from docs filenames — that mapping is
  Topic 03's job).

## Option scoring

| Option | Preserves old URLs? | In-repo? | Dep cost | Works on RTD? |
|---|---|---|---|---|
| **(a) sphinx-reredirects** | Yes (meta-refresh stub at old path) | **Yes** | 1 docs-group dep | **Yes** (stubs are build output) |
| (b) hand HTML via `html_extra_path` | Yes | Yes | 0 | Yes |
| (c) RTD user-defined redirects | Yes | **No — dashboard/API only** | 0 | Yes |
| (d) Netlify/GH-Pages `_redirects` | N/A | N/A | — | **No — wrong host** |
| (e) redirects in hypershell.org app | No (can't touch readthedocs.io URLs) | No | — | No |

Notes:
- **(c)** RTD redirects are configured *exclusively* via Admin > Redirects (dashboard) or API —
  confirmed at docs.readthedocs.com/platform (2026); **no `.readthedocs.yaml` support**. Purely
  out-of-repo, so it fails the "least out-of-repo dependency" test and can't be built/verified
  from this repo.
- **(d)/(e)** eliminated: host is RTD (ignores `_redirects`); and old URLs are on readthedocs.io,
  which the hypershell.org app never sees.
- **(b)** works with zero deps but needs ~17 hand-written HTML stubs laid out as a mirror tree
  (`blog/index.html`, `tutorial/basic.html`, …) under an extra dir — manual, error-prone, and
  drifts from `html_baseurl`.

## Key verification (sphinx-reredirects)
- Latest **1.1.0**, `requires_python >=3.11` (exactly HyperShell's floor). Extension name
  `sphinx_reredirects`; PyPI `sphinx-reredirects`. Cross-domain absolute targets supported.
- **Crucial for this task:** for a *non-wildcard (explicit)* redirect key it writes the stub
  **unconditionally**, with no check that the source doc still exists — exactly the deleted-page
  case here. (Wildcard keys match only `env.found_docs`, so they'd MISS deleted pages — do **not**
  use wildcards; enumerate explicit keys.) Verified against source: explicit keys bypass existence
  checks; wildcards filter `found_docs`.
- Stubs are ordinary build artifacts, so RTD serves them at the old `/en/<ver>/…​.html` paths on
  the next build. Historical pinned versions keep their real pages (not "removed" from snapshots) —
  fine. No new "not in toctree" warnings, since deleted pages leave the source set entirely (R5 safe).

## RECOMMENDATION — (a) sphinx-reredirects
Least-manual **fully in-repo** option; one sane-floor docs-group dep; Python floor matches.

`pyproject.toml` docs group: add `"sphinx-reredirects>=0.1.3"` (low floor per AGENTS.md; uv resolves
1.1.0), then regenerate `uv.lock`.

`docs/conf.py`:
```python
extensions = [
    ...,
    'sphinx_reredirects',
]

# Old (now-deleted) doc paths -> new home on hypershell.org. Explicit keys only:
# wildcards match only existing docs and would skip deleted pages.
redirects = {
    "tutorial/basic":       "https://hypershell.org/tutorial/basic",
    "tutorial/distributed": "https://hypershell.org/tutorial/distributed",
    "tutorial/hybrid":      "https://hypershell.org/tutorial/hybrid",
    "tutorial/advanced":    "https://hypershell.org/tutorial/advanced",
    "roadmap":              "https://hypershell.org/",           # no 1:1 -> sensible fallback
    "blog/index":           "https://hypershell.org/blog",
    "blog/20230329_2_2_0_release": "https://hypershell.org/blog/hypershell-2-2-0",
    # ...all ~12 dated posts, per Topic 03's docname->slug map...
}
```

## Flags for the plan
- Redirect **target map is Topic 03's deliverable**: docs blog filenames (`20230329_2_2_0_release`)
  differ from site slugs (`hypershell-2-2-0`); `roadmap` and `20241115_announce_logo` need explicit
  fallback decisions (`hypershell.org/blog/new-logo-and-discord` exists for the logo post).
- Old URLs are versioned; stubs only appear in **future** builds of `latest`/`stable`. Pre-change
  `stable`/pinned versions still serve the real pages (no 404) until re-released — acceptable, and
  the only fully in-repo outcome. R3 is satisfiable **in-repo** for RTD via (a); no dashboard action
  required. (If instant coverage of already-published `stable` is later wanted, that's the one thing
  needing the RTD dashboard — surface as optional, not required.)
