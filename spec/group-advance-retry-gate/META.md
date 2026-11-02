# META — Don't advance a task group past a pending retry

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Silence is the default; the bar is *was this the skill's fault?*

- **slug:** group-advance-retry-gate

## Friction findings

## F1 — Step 3 branches from local `develop`, which may be stale
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Step 1 fetches and says a `develop` that is behind is "not fatal". Step 3 then
  runs `git switch -c {branch} develop`, which bases the cycle on that stale local ref. Here local
  `develop` lagged `origin/develop` by a release merge, so the branch had to be cut from
  `origin/develop` instead of following the command as written.
- **Skill cause:** the skill notes the lag but gives no instruction to act on it, so the literal
  command produces a GOAL (and later a plan) shaped against code that is no longer current.
- **Recommended fix:** in Step 1, when `develop` is behind `origin/develop` and can fast-forward,
  say to fast-forward it, or have Step 3 branch from `origin/develop` without upstream tracking.
  When it has diverged, STOP.
- **Confidence:** high · **Effort:** small
