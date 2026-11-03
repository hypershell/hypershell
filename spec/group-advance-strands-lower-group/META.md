# META — Don't strand a task group whose tasks arrive late

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Silence is the default; the bar is *was this the skill's fault?*

- **slug:** group-advance-strands-lower-group

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — Step 3 branches from local `develop`, which may be stale · seen again
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/group-advance-retry-gate/META.md` F1, also seen in
  `spec/schema-upgrade-step/` and `spec/timescale/`. Local `develop` was stale, and the main
  checkout held untracked files that belong to other work, so Step 1's "on `develop`, otherwise
  clean" check could not pass there. The cycle was opened with `git worktree add -b {branch}
  .worktrees/{slug} origin/develop`, which also set an upstream that had to be removed with
  `git branch --unset-upstream`.
- **Skill cause:** Unchanged from the original finding. The skill still assumes a single checkout
  with `develop` current, while parallel cycles run in worktrees.
- **Recommended fix:** As in the original finding: branch with `--no-track` from `origin/develop`
  after the fetch, and accept a fresh worktree at `origin/develop` as passing the pre-flight.
- **Confidence:** high · **Effort:** small

## F2 — Step 7 does not cover seeds written while shaping
`origin=hs-feature:step7 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Non-goals that are real deferred work had to be seeded as `issues/{slug}.md`
  plus a `ROADMAP.md` entry under `AGENTS.md`'s "whoever defers it" rule. Step 4 and the size
  circuit-breaker say only to "record the deferred scope in Non-goals", and Step 7's commit lists
  only the GOAL, META and a promoted seed's status flip, so where new seeds go and how they are
  committed was inferred from `AGENTS.md`.
- **Skill cause:** The skill names Non-goals as the home for deferred scope but never points at the
  `AGENTS.md` deferral table, which says deferred code work also needs a seed and an index entry.
- **Recommended fix:** In Step 4, add one line: a non-goal that is deferred work, rather than a
  deliberate exclusion, is seeded from `templates/ISSUE.md` with a `ROADMAP.md` entry and linked
  from the non-goal. In Step 7, add the `git add issues/{new}.md ROADMAP.md` case.
- **Confidence:** med · **Effort:** small
