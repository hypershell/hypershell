# META — Promote `part` from a bookkeeping tag to a first-class column

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

- **slug:** part-tag-to-column

## What worked well

- `hs-plan` Step 3 **parallel research fan-out** (4 read-only agents → briefs) materially de-risked a
  coupled-core change: it located the real `rotatedb()` mechanism, pinned the exact three R4 expressions,
  and surfaced the `columns`-dict contradiction that the digest then resolved. Keep it.

## Friction findings

## F1 — Step 7 commit-category rule (`else feature`) contradicts practice and omits `refactor` · seen again (hs-plan:step8)
`origin=hs-feature:step7 severity=low category=instruction status=applied target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Step 7 says `{category} = fix for kind:fix, else feature`, so a `kind: refactor`
  goal would be committed as `[feature] Shape …`. I used `[refactor]` instead, matching AGENTS.md's
  category list and the repo's demonstrated convention (git log: `[docs] Shape docs-trim-migrated-sections
  goal`, i.e. `[{kind}]`). **Seen again** at `hs-plan` Step 8, whose commit rule is identically
  `{category} = fix|feature` — I again committed `[refactor]`.
- **Skill cause:** the `kind → commit-category` mapping in both skills is narrower than AGENTS.md (which
  blesses `refactor`/`docs`/… and says "coin a category when it fits") and than observed shape/plan
  commits, which use `[{kind}]`.
- **Recommended fix:** make both skills' commit lines emit `[{kind}]` (fix|feature|refactor|docs|…), or
  explicitly map `refactor → refactor`, rather than collapsing everything non-`fix` to `feature`.
- **Confidence:** high · **Effort:** small

## F2 — Research-depth gate (appetite/kind) ignores blast-radius; would skip needed research
`origin=hs-plan:step3 severity=low category=missing-guidance status=applied target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** the GOAL is `appetite: small` / `kind: refactor`, so Step 3 instructs skipping the
  research fan-out. But the change touches `data/model.py` (the `invariants.md` highest-blast-radius file)
  and a subtle SQLite JSON→column rotation mechanism whose exact shape was unknown; a lean plan would have
  gotten R4 wrong. I ran the full fan-out anyway on judgment (and it paid off — see "What worked well").
- **Skill cause:** the research-depth heuristic keys only on `appetite`/`kind`. Its sole exception is
  "diagnostic fixes," not "small-appetite change to coupled-core / high-blast-radius files." Same root as
  the docs-trim feature's F2 (the gate misses signals beyond appetite/kind) — **seen again**.
- **Recommended fix:** extend the Step 3 exception so blast-radius is also a trigger — run the fan-out
  when the change is expected to touch the coupled core / `invariants.md` high-blast-radius files,
  regardless of `appetite`.
- **Confidence:** high · **Effort:** small
