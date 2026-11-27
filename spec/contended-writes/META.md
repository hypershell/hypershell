# META — Contended writes

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** contended-writes

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL, or for a seed entry with no ROADMAP status marker · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two seeds were promoted under a slug that is neither one's stem; the second seed's `ROADMAP.md` entry had no `· *status: …*` marker (30 of 54 entries lack one), so the skill's "where that entry's **Seed:** line carries a marker" left it unmarked while the brief asked for both markers. I added the marker.
- **Skill cause:** As `spec/relay-hardening/META.md` F1 and `spec/relay-retire/META.md` F1: the skill assumes one seed whose stem is the slug, and it does not say whether a missing marker is added on promotion.
- **Recommended fix:** As recorded there, plus: on promotion, add the marker when the entry lacks one, so `/hs-roadmap` and readers see every adopted seed the same way.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree `.worktrees/contended-writes` on `fix/contended-writes` off `origin/develop` was created before this step ran; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped by the brief's instruction.
- **Skill cause:** Open in `spec/relay-hardening/META.md` F2 and earlier cycles' META files; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — High-blast-radius research exception vs a GOAL that says no fan-out is needed · seen again
`origin=hs-plan:step3 severity=low category=instruction status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The GOAL (`appetite: small`) says the change "does not need a research fan-out", while Step 3's high-blast-radius and GOAL-deferred-unknowns exceptions both require one "regardless of appetite/kind"; only the diagnostic exception says the GOAL wins a disagreement. The two questions needed live database probes with the sandbox off, so they ran in-session as two briefs plus probes, against the letter of the step.
- **Skill cause:** As `spec/server-initdb/META.md` F3 and `spec/tls-client-address-family/META.md` F1: no depth scale and no in-session path for a diagnosed small fix, and no rule for which wins when the GOAL rules on research depth.
- **Recommended fix:** As recorded there, plus: say whether a GOAL's explicit research ruling overrides the blast-radius exception (it should not drop `research/`, only the parallel fan-out).
- **Confidence:** med · **Effort:** small

## F4 — No shared red-on-base harness; this plan reuses another cycle's `drive/red_check.sh` · seen again
`origin=hs-plan:step6 severity=low category=tooling status=open target=.agents/factory/bin/`
- **What happened:** R5 requires each test to fail before the fix. The verify gates call `spec/relay-hardening/drive/red_check.sh` with `RED_BASE_REV`/`RED_LIST`, because nothing under `.agents/factory/bin/` does this, and add a `rest_green.sh` beside it.
- **Skill cause:** As `spec/relay-hardening/META.md` (the generic `red_check.sh` recommendation): Step 6 and the TECH template still give no way to prove "fails on <rev>".
- **Recommended fix:** As recorded there: move `red_check.sh` + `red_outcome.py` (and a deselect-the-red-list runner) to `.agents/factory/bin/` and reference them from Step 6.
- **Confidence:** high · **Effort:** small

## F5 — `set_phase.py` re-serializes the whole frontmatter on its first run
`origin=hs-build:P1 severity=low category=tooling status=open target=.agents/factory/bin/set_phase.py`
- **What happened:** Advancing P1 (two fields: `status`, `current_phase`) rewrote every line of the hand-written frontmatter in the dumper's style (single quotes, wrapped `title`, `- id` dedented, folded `verify:` blocks re-flowed), a ~120-line diff; parsed, only the two fields differ.
- **Skill cause:** Step 5 mandates the script ("never hand-edit YAML"), and `hs-plan` writes the frontmatter in a style the script does not keep, so the first build commit of every cycle buries its state change in reformatting.
- **Recommended fix:** Have `hs-plan` emit the frontmatter through the same dumper (or `set_phase.py --touch` at plan time), or make the dumper keep the template's style.
- **Confidence:** high · **Effort:** small

## F6 — `next_phase.py` points at an external-wait phase while a later phase is buildable · seen again
`origin=hs-build:P3 severity=low category=tooling status=open target=.agents/factory/bin/next_phase.py`
- **What happened:** With P2 (CI red on the draft PR) pending as an external wait and P3 depending only on P1, `next_phase.py` reported P2 as next; P3 was built by explicit phase selection, and Step 5's `--current {next_id_or_done}` left `current_phase` on P2, a phase no build can run.
- **Skill cause:** As `spec/hypertable-update-retry/META.md` and earlier cycles ("no way to express an external wait in a TECH phase"): the FSM has no external-wait status, so the resume pointer cannot skip a wait to the next buildable phase.
- **Seen again:** P4 and P5, each selected explicitly past the same pointer.
- **Recommended fix:** As recorded there: an `external` phase flag (or `waiting` status) that `next_phase.py` skips when choosing the next buildable phase, reporting the wait separately.
- **Confidence:** high · **Effort:** small

## F7 — The blindness rule names files, but a repo-wide search still reads the spec artifacts
`origin=hs-review:step2 severity=medium category=missing-guidance status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** The correctness reviewer, told not to read `PLAN.md`/`TECH.md`/`research/`/`META.md`, ran one repo-wide `grep` that printed matching lines from `PLAN.md` and `TECH.md`. It did not open them and graded nothing from them, but the plan's wording reached its context.
- **Skill cause:** Step 2 excludes `spec/` from the diff (`':(exclude)spec/'`) but gives no equivalent for searches of the runnable repo, which a reviewer needs; "do not read" is read as "do not open".
- **Recommended fix:** In Step 2's reviewer brief, require every search to exclude the cycle's spec artifacts (`git grep … -- . ':(exclude)spec/'`, or `grep -r --exclude-dir=spec`), naming the exception for the evidence files the brief hands over. This tightens blind-review integrity; it weakens no gate.
- **Confidence:** high · **Effort:** small

## F8 — A CONFIRMED LOW coverage finding: "non-blocking" by the severity table, blocking and gate-triggering by the loop rules · seen again
`origin=hs-review:step4 severity=high category=instruction status=open target=.agents/factory/review-rubric.md`
- **What happened:** A mutation showed the retry mark's own UPDATE condition (the `CANCEL_STATUS` guard, in `data/model.py`) is pinned by no test, though the code is correct. The severity table calls such a finding LOW, "missing-but-non-blocking test coverage"; the verdict rule blocks on any CONFIRMED finding; the human gate fires when a CONFIRMED finding "touches" `data/model.py` or a DB-lifecycle invariant. Grading required a judgment call (applied as written: gate triggered).
- **Skill cause:** The rubric and Step 4 do not say whether a coverage-only finding (correct code, missing pin) is CONFIRMED in the blocking sense, nor what "touches" means for one.
- **Recommended fix:** State it explicitly in `review-rubric.md` (Severity, Verdict & loop, human gate) and Step 4. Any wording that exempts coverage-only findings from blocking or from the gate weakens the human gate, a non-negotiable gate, and needs the maintainer's ruling; the safe clarification is to keep both and drop "non-blocking" from the LOW row.
- **Seen again:** review cycle 2. N1 is a CONFIRMED LOW, a wording-only overstatement in an unsupported setup. Under the severity table it is minor; under the verdict rule it blocks, and it was applied as written, so the cycle runs one more loop for a wording fix. The rubric also does not say whether a §12 docs inaccuracy that is not a *missing* update (the prose and marker exist; a leftover clause overstates a risk) grades HIGH as §12 or by its impact.
- **Seen again:** review cycle 3. G1 is again a CONFIRMED LOW, wording-only finding, at the last cycle the bound allows, so the verdict rule as written ends the loop in an escalation over one clause about an unsupported setup.
- **Confidence:** med · **Effort:** small

## F9 — No route for pre-existing inaccurate prose in a passage the diff rewrites
`origin=hs-review:step3 severity=low category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** Cycle 2 marked a clause in a passage it was grading as "may be stale … not probed". The clause was "new tasks double-schedule" with two servers, false since #103. It was left out of the findings because the diff had not changed it. The N1 remediation then rewrote that sentence and kept the clause by ruling. Cycle 3 probed it, found it false, and had to block on it at the last cycle.
- **Skill cause:** The rubric defines CONFIRMED, PLAUSIBLE and dropped for findings, but it has no class for a doubt about text next to the diff that predates it. The deferral exception covers only behaviour a GOAL criterion requires keeping. So pre-existing wrong prose that a remediation re-authors has one route, which is to block, and no rule says an unprobed doubt next to a finding must be probed before the remediation is shaped.
- **Recommended fix:** In `review-rubric.md` (Refutation protocol) and Step 3, require that a doubt about prose in a passage a finding's remediation will touch is either probed in the same cycle or listed as PLAUSIBLE for triage, never left as "not a finding, not probed". The change only tightens the evidence spine; it weakens no gate.
- **Confidence:** med · **Effort:** small

**What worked well:** The executed-evidence spine held: the reviewer ran mutations from scratch copies on `PYTHONPATH` with the tree untouched, which is what exposed F2, and the red_check/ci_check scripts re-graded R5's red in minutes.

**What worked well (review cycle 2):** F7's recommended fix, applied ad hoc in the brief (every search kept off PLAN/TECH/META/REVIEW/research, with the evidence files named as the exception), held: the reviewer reports opening none of them. Handing it the shared `red_check.sh` and the cycle's `mutant_check.sh` let it re-grade F2 on three backends in minutes.

**What worked well (review cycle 3):** Asking the reviewer for an executed probe of each sentence in the remediation, at HEAD and at base, turned a carried-over doubt into a 48-run answer. Checksumming the reviewer's source exports against `git` before re-running its probe made the orchestrator's sanity pass cheap.
