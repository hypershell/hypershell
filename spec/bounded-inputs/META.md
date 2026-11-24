# META — Every numeric setting has one declared range, one parser and one error

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/bounded-inputs/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** bounded-inputs

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/bounded-inputs/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** `issues/bounded-inputs.md` names two seeds it absorbs (`config-set-wide-integers`, `config-text-attempts-crashes`). The skill says to mark the promoted seed `adopted:{slug}` but says nothing about seeds another seed absorbs; they were marked `adopted:bounded-inputs` with their ROADMAP lines, following #118's precedent.
- **Skill cause:** As `spec/submit-input-checks/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there; add that a seed's "Absorbs" list is promoted with it and marked the same way.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree and `fix/bounded-inputs` were created off `develop` `03a02bd8` on the brief's instruction; Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply.
- **Skill cause:** As `spec/cluster-launch-failures/META.md` F2.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — "Shaping only" versus checking an `unshaped` seed on current `develop` · seen again
`origin=hs-feature:step4 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The seed's range list could not become a contract without an inventory of every numeric option and key and the column types behind them, and a few throwaway-site probes (`-N 0`, `-b 0`, `-c 100000` accepted). That is more code reading than "no big code reads" allows, and the probes are outside allowed-tools.
- **Skill cause:** As `spec/env-config-keys/META.md` F3 and `spec/config-set-and-unread/META.md`: the `unshaped` bullet does not ask for the seed to be checked against `develop`.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small
