# META — De-flake `test_group_failed_task_with_retries`

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Silence is the default; the bar is *was this the skill's fault?*

- **slug:** deflake-group-retry-test

## What worked well

- `hs-plan:3` — the parallel read-only research fan-out (three briefs: output-path / test-harness /
  design-intent) converged cleanly and independently on the same "no product defect" verdict, which
  is exactly the confidence a diagnosis-gated fix needs. The three-way split (product code / test
  harness / docs+history) had no overlap and left no gap.

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — `kind: fix` research-skip rule misfires for diagnostic fixes
`origin=hs-plan:3 severity=medium category=steering status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** Step 3 says for `kind: fix` (and `appetite: small`) to *skip the research
  fan-out* and "do at most a couple of targeted reads yourself." This fix's `GOAL.md` explicitly
  demanded first-principles root-cause diagnosis of an unknown failure (test-fragility vs. a real
  product output-loss bug the human had opted into fixing). Following the rule literally would have
  produced a test-only guess without ever confirming there was no product defect — the opposite of
  what the GOAL required. I had to override the rule and run a full fan-out.
- **Skill cause:** the research gate keys on `kind`/`appetite`, but those are proxies for the real
  question — *is the root cause known?* A `kind: fix` can be a "diagnostic fix" whose whole value is
  the investigation; the skill offers no carve-out for it (and the GOAL's non-canonical
  `appetite: medium — diagnosis-gated` had no clean mapping either).
- **Recommended fix:** in Step 3, add a branch: *if the GOAL's root cause is unknown or it explicitly
  requests diagnosis, run the fan-out regardless of `kind`/`appetite`.* Optionally let `hs-feature`
  emit a `diagnosis: required` hint (or accept `appetite: medium`) so the gate is explicit rather
  than relying on the planner to notice.
- **Confidence:** high · **Effort:** small
