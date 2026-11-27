# 05 — Removal Surgery & R5 Baseline

Mechanical plan only. **Deletes nothing.** Document says exactly *what* to delete and *how* to verify.

## R5 BASELINE (captured from a fresh, uncached build)

Command run: `uv run sphinx-build -b html docs docs/_build` (first run reused a pickled env
and only re-read 1 doc; re-ran after clearing `docs/_build` — `118 added, 0 changed`, all
sources read). Sphinx v7.4.7.

**Baseline = exactly 2 warnings, verbatim:**

```
checking consistency... /Users/.../docs/cli/task_submit.rst: WARNING: document isn't included in any toctree
/Users/.../docs/manual.rst: WARNING: document isn't included in any toctree
```

`build succeeded, 2 warnings.` — `grep -cE WARNING` = 2, zero `ERROR`. Matches AGENTS.md
("pre-existing manual.rst / task_submit.rst 'not in any toctree'"). **R5 target: still ≤ these
2 identical warnings after removal.** (Non-warning noise to ignore: `pkg_resources`
DeprecationWarning, `matplotlib not installed / social cards`, `source_suffix` conversion —
all pre-existing, not counted by Sphinx.)

`docs/_build` is gitignored: `git check-ignore docs/_build` succeeds; `.gitignore:72` =
`docs/_build/`. Artifacts won't be committed. **Always clear `docs/_build` (`del docs/_build`)
before a verify build** — a stale pickled env masks new warnings.

## docs/index.rst — toctree caption blocks to DELETE (R1, R2)

Tutorial block, **lines 205–212**:
```
.. toctree::
    :hidden:
    :caption: Tutorial

    tutorial/basic
    tutorial/distributed
    tutorial/hybrid
    tutorial/advanced
```
Project block, **lines 214–219** (219 = EOF):
```
.. toctree::
    :hidden:
    :caption: Project

    blog/index
    roadmap
```
Delete lines **205–219** (both blocks + the blank line 213 between them). Optionally also drop
the now-trailing blank line 204 so the file ends after the Reference block's `templates` + one
newline. Reference/Intro toctrees (lines 185–203) are RETAINED — do not touch.

## Files/dirs to DELETE (use `del`, not `rm`)

Tutorial (4 + dir):
- `docs/tutorial/basic.rst`, `docs/tutorial/distributed.rst`, `docs/tutorial/hybrid.rst`,
  `docs/tutorial/advanced.rst` → then `del docs/tutorial/` (empty dir).

Blog (13 files incl. index — enumerated via `ls docs/blog/`; then the dir):
- `20230329_2_2_0_release.rst`, `20230413_2_3_0_release.rst`, `20230602_2_4_0_release.rst`,
  `20240518_2_5_0_release.rst`, `20240706_2_5_2_release.rst`, `20241115_2_6_0_release.rst`,
  `20241115_announce_logo.rst`, `20241231_2_6_1_release.rst`, `20250215_2_6_5_release.rst`,
  `20250405_2_6_6_release.rst`, `20250504_2_7_0_release.rst`, `20260705_2_8_0_release.rst`,
  `index.rst` → then `del docs/blog/` (whole dir; `del docs/blog` handles it in one shot).

Roadmap (1):
- `docs/roadmap.rst`.

## PREDICTED NEW warnings — pre-empt in the plan (R4)

Exhaustive grep (`tutorial_*` / `<roadmap>` / dated blog labels / `:doc:` / `:ref:` over
`docs/` + `README.rst`, excluding the dirs being removed) finds exactly **two** live inbound
refs from RETAINED pages. Each becomes an "undefined label" WARNING if the page is deleted but
the ref survives. Delete both (R4 = remove outright, do not repoint):

1. **`docs/cli/index.rst:9`** (Reference section) — whole standalone line:
   `See our :ref:`tutorials <tutorial_basic>` (COMING SOON) for use-case specific demonstrations.`
   → delete the entire line. Line 8 (`:ref:`Python API <library>``) is retained and reads fine.

2. **`docs/alternatives.rst:518`** (Intro section) — ref is mid-sentence:
   ``best. (A ``hypershell-nextflow`` integration is on the :ref:`roadmap <roadmap>`.)``
   → delete the trailing parenthetical `(A ...roadmap>`.)` only; the sentence ends cleanly at
   `...it does best.`

No other predicted breakage: toctree entries vanish with their caption blocks (no dangling
toctree); blog's internal `:ref:` cross-links + its own `:hidden:` toctree (12 posts) are
deleted wholesale with the dir. False positives to ignore: `docs/logging.rst:305`
(`client-<host>-3.20260101.log.gz`, a filename in a code sample) and `alternatives.rst`
`--joblog` mentions (not refs). `README.rst` has zero refs to removed pages.

## Cross-topic note (not this brief's scope)

Old published URL stems for R3 redirects: `/tutorial/{basic,distributed,hybrid,advanced}.html`,
`/roadmap.html`, `/blog/index.html`, `/blog/<12 dated slugs>.html`. Redirect wiring is topic
03/04's job.

## Per-phase verify: command

```
del docs/_build; uv run sphinx-build -b html docs docs/_build 2>&1 | tee /tmp/build.log | tail -20
grep -cE "WARNING" /tmp/build.log   # MUST be 2; the 2 must be task_submit.rst + manual.rst
grep -E "WARNING|ERROR" /tmp/build.log
```
