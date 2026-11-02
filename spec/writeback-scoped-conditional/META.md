# META — Scoped, conditional result writeback

> **Harness feedback log** for this feature: the producer artifact of the factory's self-improvement
> loop. See [`.agents/factory/templates/META.md`](../../.agents/factory/templates/META.md) for the
> schema and the bar. Silence is the default.

- **slug:** writeback-scoped-conditional

## Friction findings

## F1 — No guidance for a sibling seed that the promoted GOAL subsumes
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** This GOAL absorbs a second, unshaped seed (`late-result-resets-retried`) whose own notes say "retire this one with it". Step 4 says what to do with the *promoted* seed's `status:`, but nothing about a subsumed sibling. I had to choose its status (`adopted:writeback-scoped-conditional`) and decide whether its ROADMAP entry needs a marker without any guidance.
- **Skill cause:** "Promoting an issue" assumes one seed per GOAL. A subsumption decision is a normal shaping outcome, and the ROADMAP `/hs-roadmap` retirement flow depends on the sibling's status being set consistently.
- **Recommended fix:** Add a line to Step 4: a seed the GOAL subsumes gets `status: adopted:{slug}` too, the GOAL names it under Related materials and carries its testable outcomes as R-IDs, and `/hs-roadmap` retires it with the cycle.
- **Confidence:** med · **Effort:** small
