# META — Trim docs sections that have moved to hypershell.org

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** docs-trim-migrated-sections

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/{slug}/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
best-guess file with **no line number** (re-derive the exact edit at apply time to avoid staleness). If
an equivalent finding already exists, append "· seen again" to its title instead of duplicating —
recurrence is signal, not bloat.

Field enums — `severity`: `high` (a safety / gate / correctness gap) `| medium | low`; `category`:
`instruction | steering | tooling | template | missing-guidance`; `status`: `open` (written by skills)
`| applied | rejected | deferred` (written by `/hs-harness`).

Schema (copy one block per finding, appending it **after** this fence — the fence is illustrative and
is skipped by the parser):

```markdown
## F1 — <one-line title of the skillset problem>
`origin=<skill>:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why this is the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the concrete change to the skill / template / script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — `kind` taxonomy has no slot for a pure docs (or ci/test) change
`origin=hs-feature:step2 severity=medium category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** This task is a pure documentation cleanup. The skill's Argument Parsing and branch
  mapping only enumerate `fix | feature | refactor`, and Step 7 sets the commit `{category}` to `fix`
  or `feature`. AGENTS.md, by contrast, calls the category set open and lists `docs` explicitly. I had
  to bend the model: set `kind: docs`, keep a `feature/` branch prefix so `/hs-publish`'s "on a
  feature/fix branch" gate still passes, and plan to use `[docs]` commits instead of the skill's `else
  → feature` default.
- **Skill cause:** The skill's `kind → category → branch` model is narrower than the repo's actual
  (open) commit-category convention, so a legitimate `docs`/`ci`/`test`-flavored change has no clean
  path and forces an ad-hoc judgment call about branch prefix and commit category.
- **Recommended fix:** Let `kind` carry any AGENTS.md category (at least add `docs`), decouple the
  branch prefix from `kind` (e.g. map non-`fix` kinds to `feature/` or introduce a `docs/` prefix that
  the downstream gates accept), and make Step 7's `{category}` follow `kind` directly rather than
  collapsing everything non-`fix` to `feature`.
- **Confidence:** high · **Effort:** small
