# META — TLS handshake deadline on the queue listener

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/listener-handshake-deadline/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** listener-handshake-deadline

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- hs-feature Step 4: the `kind: fix` rule (phrase the fixed behaviour, never the mechanism) and the security-lane sentence together made the public GOAL easy to keep at the outcome level.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/listener-handshake-deadline/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees off `origin/develop` · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The foreman cut `fix/listener-handshake-deadline` off `origin/develop` (`6da633d`) in a worktree under `.worktrees/` before this step ran, so Step 1's "on `develop`" check and Step 3's `git switch -c` were bypassed, and Step 2's collision check (branch must not exist) would have stopped the cycle if followed literally.
- **Skill cause:** The skill knows only the main-checkout flow; the worktree flow every cycle now uses is not described.
- **Recommended fix:** Add a worktree variant: when invoked in a worktree whose branch matches `{kind}/{slug}` and whose base is `origin/develop`, treat the branch as created and skip Step 3.
- **Confidence:** high · **Effort:** small

## F2 — No way to promote part of a seed and keep the rest alive · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The private seed bundles four related items; the maintainer scoped this cycle to one. Step 4 only knows `status: adopted:{slug}` for the whole seed, which would retire the other three with it, so the seed was left `unshaped` with a dated note naming what this cycle took.
- **Skill cause:** Promotion is all-or-nothing; the security lane's README allows partial remediation but the skill gives no form for it.
- **Recommended fix:** Add a partial-promotion form: the seed keeps its status for the remainder and gains a dated `partly adopted:{slug} (<what>)` note (and the same marker on its ROADMAP entry), in either lane.
- **Confidence:** med · **Effort:** small

## F3 — Promotion step says to commit the seed's status edit, which is impossible in the security lane · seen again
`origin=hs-feature:step4 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Steps 4 and 7 say to set the seed's status and commit it with the GOAL. `.security/` is gitignored, so the seed, finding and lane-index edits were made in the main checkout's `.security/`, left uncommitted, and reported instead.
- **Skill cause:** The promotion and commit steps assume the public `issues/` lane; the security-lane sentence covers the GOAL's content, not the bookkeeping.
- **Recommended fix:** Add a security-lane variant: edit the `.security/` seed, the finding's front-matter `status:` (`in_progress`) and the lane's index tables per `.security/README.md`, never stage them, and name each edit in the Step 8 report.
- **Confidence:** high · **Effort:** small
