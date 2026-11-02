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

## F2 — `verify:` guidance assumes `temp_site.sh`, whose cleanup some harnesses forbid · seen again
`origin=hs-plan:step6 severity=low category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** Step 4/6 and the TECH template steer every CLI drive through `.agents/factory/bin/temp_site.sh`. This run's harness forbids that script, because its exit trap removes the site recursively and the shell blocks that. The plan had to hand-roll the same isolation (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`, empty `HYPERSHELL_CONFIG_FILE`, `UV_PROJECT`, a `mktemp -d` site left under `$TMPDIR`) inline in a long `verify:` string.
- **Skill cause:** The wrapper is the only sanctioned isolation route, and it couples isolation with deletion. The skill offers no fallback for a harness that permits the isolation but not the cleanup.
- **Recommended fix:** Give `temp_site.sh` a keep-site mode (for example `TEMP_SITE_KEEP=1` skips the trap and prints the path), and mention it in Step 4 and the TECH template as the portable form.
- **Confidence:** med · **Effort:** small

## F3 — A verify gate that only CI can turn green has no place in the FSM
`origin=hs-build:P2 severity=medium category=missing-guidance status=open target=.claude/skills/hs-build/SKILL.md`
- **What happened:** The plan gate added "the PostgreSQL round-trip test passes in CI" to P2's gate. `verify:` is a local shell command and PostgreSQL is not available locally, so the local gate skips that test and exits 0. The skill says a phase is `done` only when its `verify:` passes, and gives no way to record a phase whose remaining gate is a CI job on a PR that `hs-build` may not open. I marked P2 done on the local gate, wrote the CI condition into the phase body, and left it for the foreman to check on the PR.
- **Skill cause:** The FSM has one gate per phase, and it is local. It has no field or status for an out-of-band gate (CI, a remote service, a human sign-off), so such a gate lives only in prose, where `next_phase.py` and `hs-review` cannot see it.
- **Recommended fix:** Add an optional `ci_gate:` (or `external_gate:`) phase field that `set_phase.py` can mark `pending|passed`, and have `next_phase.py` and `hs-publish` surface any pending one. Until then, Step 4 should say: a gate that needs CI goes in the phase body under **Verify** with "pending CI", and the report names it as an open item. This does not weaken the local CLI-drive gate.
- **Confidence:** med · **Effort:** small

## F4 — Step 7's `git add -A` contradicts explicit-path staging
`origin=hs-build:P1 severity=low category=instruction status=open target=.claude/skills/hs-build/SKILL.md`
- **What happened:** Step 7 prescribes `git add -A` for the atomic commit, while this cycle's TECH conventions (and the session rules) require staging by explicit path. A worktree can hold untracked files that belong to someone else, such as maintainer notes or another agent's scratch, and `git add -A` would sweep them into a phase commit. I staged by path.
- **Skill cause:** The skill's commit recipe assumes the tree holds only this phase's work. Step 1 checks for a clean tree, but untracked files that are deliberately left alone defeat that check, and the recipe then stages them.
- **Recommended fix:** Change Step 7 to `git add <paths the phase touched> spec/{slug}/TECH.md [spec/{slug}/META.md]`, taking the paths from the phase's **Touches** line, and note that `bundle` stages the union.
- **Confidence:** high · **Effort:** small

## F5 — The blind diff's base is a mutable branch name, so a stale local `develop` grades the wrong range · seen again
`origin=hs-review:step1 severity=medium category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** Step 1 resolves `{base}` from TECH's `base: develop`. The injected diffstat and the Step 2 diff command both use `develop...HEAD`. In this worktree the local `develop` is stale, so the reviewer would have been given a diff over the wrong range. The foreman had to pin the base SHA (`origin/develop` at plan time) by hand before the blind pass.
- **Skill cause:** the skill takes the base as a branch name and never checks whether that ref is current. The plan's real base, the commit the branch was cut from, is recorded nowhere in TECH, so the diff can silently include unrelated commits or miss some. That is a blind-diff integrity gap.
- **Recommended fix:** have `hs-plan`/`hs-build` record `base_sha` in TECH's frontmatter, then make Step 1 and the injected diffstat use `git merge-base origin/{base} HEAD` (or `base_sha`). Step 1 should stop and report when local `{base}` differs from `origin/{base}`. This strengthens the blind-review gate; it does not weaken it.
- **Confidence:** high · **Effort:** small

## F6 — On a later cycle, Step 2's `git log --oneline` hands the blind reviewer the prior findings
`origin=hs-review:step2 severity=medium category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** Step 2 tells the orchestrator to give the reviewer `git log --oneline {base}..HEAD` with the diff. On cycle 2 that log holds the cycle-1 review commit and the remediation commit, whose subjects name the findings and their fixes ("F1 exit -1 redelivery is a duplicate, F2 seed corrected"). Handing it over would have shown a supposedly fresh blind pass the previous cycle's findings. I withheld the log and told the reviewer not to read commit messages.
- **Skill cause:** the skill excludes `spec/` from the diff so that the reviewer does not see REVIEW.md, but it passes the commit log as safe. The factory's own commit convention (`Review {slug}: cycle {n}`, remediation subjects naming the finding) puts the same content into that log.
- **Recommended fix:** In Step 2, drop `git log` from the reviewer's inputs, or limit it to cycle 1. Tell the reviewer not to read commit messages (`git log`, `git show <sha>`, `git blame` over the branch range) in every cycle. This strengthens blind-review integrity and weakens nothing.
- **Confidence:** high · **Effort:** small
