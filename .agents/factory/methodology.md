# The HyperShell software factory — methodology

This is the reference an agent reads when it needs to understand the spec-driven development
lifecycle that the `hs-*` skills implement. The skills themselves are thin; this is the *why*.
When something here disagrees with a skill, **the skill body is the operating procedure** — fix
this file.

> **New to agentic engineering?** Start with [`getting-started.html`](getting-started.html) — a
> gentle, self-contained onboarding guide (for practitioners and institutional leadership) that
> introduces agents, harnesses, Shape Up, and this factory from first principles.

## The lifecycle

One feature (or fix/refactor) flows through five skills, on its own git branch, with every artifact
committed under `spec/{slug}/`:

```
develop ──/hs-feature──▶ feature|fix/{slug}     GOAL.md            (shape: what & why, locked)
             │
             ├──────────/hs-plan──────────▶     research/ PLAN.md TECH.md   (design + phased FSM)
             │
             ├──────────/hs-build──────────▶    source + docs + share/      (execute one phase/loop)
             │            ▲        │
             │            └────────┘  (loop until TECH.md is done)
             │
             ├──────────/hs-review─────────▶     REVIEW.md          (adversarial QA, clean context)
             │            │
             │      changes-requested ──▶ back to /hs-build ;  approved ──▶
             │
             └──────────/hs-publish────────▶     PR → develop   (default)  |  local squash-merge
```

The artifact spine — **`GOAL.md → PLAN.md → TECH.md`** — is the industry-standard spec-driven
skeleton (Spec Kit's `spec→plan→tasks`, Kiro's `requirements→design→tasks`). We commit these into
the repo as an immutable, dated design record ("build in the open"), not as a living source of truth
that must be maintained forever. **Code + `AGENTS.md` remain ground truth; `spec/{slug}/` is a
point-in-time record of intent and design.**

## Load-bearing principles

1. **`AGENTS.md` is the constitution.** We do not invent a `constitution.md`; the skills reference
   `AGENTS.md` and the curated [`invariants.md`](invariants.md). The `hs-plan` invariant gate and the
   `hs-review` footgun checklist both draw from it.
2. **Files + git are the durable substrate.** State lives only in the committed `TECH.md`
   frontmatter, re-read fresh each invocation (via `` !`command` `` injection and the `next_phase.py`
   script). Never rely on conversation memory to carry lifecycle state — `hs-review` runs in a
   separate context and `hs-build` may run days later on another machine.
3. **Parallelism for research, never for building.** `hs-plan` fans out read-only research
   subagents (big wins). `hs-build` is strictly single-threaded and linear — parallel builders make
   conflicting implicit decisions.
4. **Blind, externally-verified review beats self-review.** The reviewer is denied the author's
   PLAN/TECH rationale and must cite executed commands. This is enforced by spawning fresh
   subagents, not by trusting a human to `/clear`.
5. **Ceremony scales to appetite.** The single biggest anti-pattern in 2026 spec-driven tooling is
   uniform heavyweight process — 16 acceptance criteria for a one-line bug fix. `appetite: small`
   (default for `fix/`) skips the research fan-out and may collapse PLAN+TECH; a one-sentence change
   skips the lifecycle entirely.
6. **Never guess.** Ambiguity gets a `[NEEDS CLARIFICATION: …]` marker and a question to the human,
   recorded into `GOAL.md`. Mirrors AGENTS.md's "the code is ground truth."

## What we borrow from Shape Up (and what we discard)

Shape Up (37signals) is a team methodology; we take its **cognitive tools** and drop its **org
rituals**.

**Adopt:**
- **Appetite** — fixed budget, variable scope ("start with a number, end with a design"). Expressed
  as a phase/iteration cap, since calendar weeks are meaningless at machine tempo.
- **Shaping** — `GOAL.md` is *rough, solved, and bounded*: concrete enough to de-risk, abstract
  enough to leave design freedom.
- **No-gos** — explicit exclusions in `GOAL.md`.
- **Rabbit holes** — each `research/{topic}.md` investigates one scary unknown that could blow the
  appetite; PLAN records the resolutions.
- **Hill state** — `hill: uphill|crest|downhill` on each phase encodes *risk/confidence* (a
  self-honesty signal). A phase stuck uphill across builds is a raised hand.
- **Scope hammering** — nice-to-haves are cuttable; `hs-review` scope-checks against the appetite.
- **Circuit breaker** — cap build iterations / review bounce-backs; on trip, stop-and-re-shape
  rather than loop forever.

**Discard:** the betting table, six-week/two-week calendar cadence, cool-down, two-track
shaper/builder pipelining, dedicated QA roles — all exist to synchronize and shield a human team and
dissolve for a serial solo+agent pipeline.

**Critical caveat — quality is NOT negotiable here.** Shape Up assumes scope is cuttable and most
bugs can wait. That is **false** for HyperShell's load-bearing invariants (task-lifecycle
predicates, `CANCEL_STATUS` filters, auth minimums, wire-format compatibility): an engine with
silent double-run / task-resurrection failure modes cannot defer correctness. The `hammerable:
false` phase flag operationalizes this — `hs-review` must never scope-hammer a correctness/security
phase to "fit the appetite."

## Where things live

```
.agents/
  skills/hs-{feature,plan,build,review,publish}/SKILL.md   # the five lifecycle skills
  factory/
    methodology.md        # this file
    invariants.md         # curated AGENTS.md footgun checklist (plan gate + review rubric)
    ears.md               # EARS requirement templates
    review-rubric.md      # severity scale, refutation protocol, human-gate triggers
    templates/            # GOAL.md PLAN.md TECH.md REVIEW.md skeletons
    bin/                  # next_phase.py, set_phase.py, _fsm.py (stdlib+PyYAML FSM helpers)
spec/{slug}/              # per-feature artifacts (committed, retained on merge)
AGENTS.md                 # the constitution (CLAUDE.md is a symlink to it)
```

`.claude` is a symlink to `.agents`, so Claude Code discovers the skills and reads settings through
it. The skills reference bundled scripts via `${CLAUDE_SKILL_DIR}` and shared reference material by
repo-relative path.

## Traceability chain

`GOAL.md` R-IDs → `PLAN.md` requirement→design map → `TECH.md` phase `satisfies:` → commits →
`REVIEW.md` requirement→evidence matrix → PR body. Because the repo is squash-only (per-commit
history is destroyed on merge), the committed `spec/{slug}/` folder *is* the retained trace.

Provenance lives in that chain, **not in source comments**: comments explain the invariant/*why* on
their own terms and never embed R-IDs or phase ids — those restart per feature and collide across
branches, and mean nothing to a later reader of the merged tree. `git blame → commit → PR →
spec/{slug}/` recovers the requirement behind any line when you need it.
