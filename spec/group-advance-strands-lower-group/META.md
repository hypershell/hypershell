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

## F3 — `verify:` guidance has no guard against a command that never returns
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** This fix is for a hang. On the base commit its CLI drives and its integration test can run
  forever, the integration suite has no per-test timeout, and `timeout(1)` is not installed on the host. Every
  `verify:` therefore defines its own one-line Python kill timer (new session, `os.killpg` after N seconds),
  which makes the frontmatter long and repetitive. The port-trial script repeats it a sixth time.
- **Skill cause:** Steps 4 and 6 steer verification toward driving `hs`/`hsx` and running integration tests, but
  they say nothing about bounding those commands. The factory has no helper for doing so, so each plan reinvents one
  and a plan that forgets hangs `hs-build`.
- **Recommended fix:** Add `.agents/factory/bin/kt` (or `with_timeout.py`): it runs a command in its own process
  group and kills the group on expiry with exit 124. Add one line to Step 6 and the TECH template: every `verify:`
  that runs pytest integration tests or `hs`/`hsx` goes through it.
- **Confidence:** high · **Effort:** small

## F4 — The high-blast-radius exception calls for parallel subagents even at `appetite: small`
`origin=hs-plan:step3 severity=low category=steering status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The change touches `data/model.py` and `server.py`, so Step 3 requires the full fan-out. Step
  3 describes that fan-out as parallel subagents and allows the planner to write the briefs one after another only
  when subagents are unavailable. For a small fix, the foreman directed the proportionate form: three briefs the
  planner writes itself, in sequence, as #68 did. That form departs from the skill's text.
- **Skill cause:** The exception sets *whether* research runs but not *how much*. It borrows the `appetite: big`
  mechanics, so the practice the factory actually follows reads as non-compliance.
- **Recommended fix:** In the high-blast-radius exception, add: at `appetite: small`, the planner may write the
  briefs itself, in sequence (same paths, same digest), and should prefer that unless a topic needs an
  independent read.
- **Confidence:** med · **Effort:** small

## F5 — No pattern for proving a fix ports to a patch branch
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.agents/factory/templates/TECH.md`
- **What happened:** R9 requires this cycle's `src/` and `tests/` diff to port to 2.8.3 after #68. The skill and
  the template have no phase shape for that. The plan invented a trial: a scripted detached `origin/master`
  worktree, #68 applied with one scripted conflict resolution, then this cycle applied with `git apply -3`, then
  the group tests and removal of the worktree. The script is committed under `spec/` because `verify:` needs a
  file to call. The plan also had to tell the builder where edits may sit so the 3-way apply stays clean.
- **Skill cause:** The lifecycle assumes a cycle lands only on `develop`. Patch ports reach `/hs-release` with no
  earlier proof, though two consecutive fixes (#68 and this one) needed one.
- **Recommended fix:** Add an optional "port trial" phase pattern to the TECH template and Step 6: a
  `.agents/factory/bin/port_trial.sh <target-ref> <prereq-commit>…` that builds a detached worktree, applies the
  diffs path-limited to `src/` and `tests/`, runs named tests under a kill timer, and always removes the worktree.
  Add a note on keeping edits clear of lines that differ on the target.
- **Confidence:** med · **Effort:** medium

## F6 — Step 7 stages with `git add -A`, against the explicit-path rule
`origin=hs-build:P1 severity=medium category=instruction status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** Step 7's commit block is `git add -A`, and Step 6 says the meta-note "rides in this
  phase's atomic `git add -A` commit". This cycle's TECH conventions and the foreman both require staging by
  explicit path. Every phase was staged by path instead. The main checkout and sibling worktrees routinely hold
  untracked files that belong to other work, so `-A` is the riskier default.
- **Skill cause:** The skill hard-codes `-A` and never mentions staging by path, so a builder following it
  literally contradicts the plan's own convention.
- **Recommended fix:** Change Step 7 to `git add <paths the phase touched> spec/{slug}/TECH.md` (plus
  `META.md` when written), naming the phase's `Touches:` list as the source, and drop `-A` from Step 6.
- **Confidence:** high · **Effort:** small

## F7 — `set_phase.py` rewraps every `verify:` on its first write
`origin=hs-build:P1 severity=low category=tooling status=open target=.agents/factory/bin/set_phase.py`
- **What happened:** The first status change (P1 to done) produced a 90-line diff of `TECH.md`, because the
  YAML dump re-folded every phase's long `verify:` scalar at a different width. The strings were unchanged, but
  that had to be confirmed by parsing old and new frontmatter. The noise lands in the phase commit and hides the
  real two-field change.
- **Skill cause:** The script regenerates the frontmatter with the dumper's default width, while `hs-plan`
  wrote it at another. Nothing keeps the two consistent.
- **Recommended fix:** Dump with a fixed, very large `width` (no folding), in both `set_phase.py` and whatever
  `hs-plan` uses to write the frontmatter, so a state change touches only its own lines.
- **Confidence:** high · **Effort:** small

## F8 — The port trial's `uv sync --frozen` also needs the docs group
`origin=hs-build:P5 severity=low category=tooling status=open target=.agents/factory/templates/TECH.md`
- **What happened:** On a host with no network, `uv sync --frozen` for 2.8.3 failed on `jinja2` (pulled in by
  the default `docs` group's Sphinx), which was not in the offline cache. The trial needs only the dev group.
  It ran with `UV_NO_GROUP=docs` set in the environment, with no script edit. Relates to F5.
- **Skill cause:** The trial pattern syncs every default group, so it depends on documentation tooling it never
  runs.
- **Recommended fix:** In the port-trial pattern proposed in F5, sync with `--no-group docs` (and run with the
  same flag), so the trial needs only what the tests import.
- **Confidence:** med · **Effort:** small

## F9 — The review's base resolves to local `develop`, which can grade the wrong diff · seen again
`origin=hs-review:step1 severity=medium category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** In this checkout, local `develop` (222075b) is stale, and the cycle's base
  b31c66e is not an ancestor of it. The skill gets its base from `TECH.md` (`base: develop`) and
  builds both the injected diffstat and the blind reviewer's diff with `develop...HEAD`, which spans
  87 files instead of the cycle's 12. The review graded the right diff only because the foreman
  pinned `origin/develop` b31c66e. Related to F1, which is the same stale ref in `hs-feature`.
- **Skill cause:** Step 1 and Step 2 name a branch, not a pinned commit, and nothing checks that
  the base is an ancestor of HEAD. A stale ref silently hands the blind reviewer unrelated
  commits.
- **Recommended fix:** Resolve `{base}` to `origin/develop` after a fetch, or to the branch's
  recorded fork point. In the Step 1 pre-flight, add `git merge-base --is-ancestor {base} HEAD`,
  and stop if `git diff --stat {base}...HEAD` touches commits outside `{base}..HEAD`. Inject the
  diffstat against that same ref. This tightens the blind-review input and weakens no gate.
- **Confidence:** high · **Effort:** small
