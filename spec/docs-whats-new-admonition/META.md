# META — Replace the docs banner and What's new page with an index admonition

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/docs-whats-new-admonition/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** docs-whats-new-admonition

## What worked well

- `hs-build` Step 1/3: `set_phase.py --verify` retuned a gate script-safe mid-build, with no hand-edited YAML.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/docs-whats-new-admonition/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — A negative-grep verify gate was never dry-run against the base tree
`origin=hs-build:P1 severity=medium category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P1's gate grepped `docs/conf.py` for the bare token `_ext`, which also matches the stock Sphinx comment `html_extra_path`, a line the phase never touches. The gate could not pass on a correct build; it cost a red attempt and a retune to `'_ext'|_ext/`.
- **Skill cause:** `hs-plan` asks for mechanical verify gates but not that a "must not match" grep be run against the current tree first, so pre-existing false matches go unseen until build.
- **Recommended fix:** in `hs-plan`, require each negative grep in a `verify:` to be run against the base tree before the plan commits, and its unexpected matches resolved by tightening the pattern.
- **Confidence:** high · **Effort:** small

## F2 — Step 4 does not separate a defective gate from a failed phase
`origin=hs-build:P1 severity=low category=instruction status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** the verify went red because of the gate, not the work. Step 4 says red means STOP, and Step 1's remediation mode is the only place that mentions retuning a gate with `--verify`, so whether a builder may correct a gate and re-run in the same build was left to judgment.
- **Skill cause:** Step 4 treats every red as an implementation failure, with no rule for a gate that cannot pass on correct work.
- **Recommended fix:** add to Step 4: when a gate is shown to be defective (it fails on a known-correct tree), record the attempt, retune it with `set_phase.py --verify` without weakening what it asserts, name the change in the commit body, and re-run. Otherwise STOP.
- **Confidence:** med · **Effort:** small
