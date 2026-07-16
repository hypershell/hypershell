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
7. **Observe cheaply, act deliberately.** The factory improves *itself* through an asymmetric loop:
   every lifecycle skill records harness friction into `spec/{slug}/META.md` for near-zero cost
   (silence by default), but fixes are applied only by the human-gated `hs-harness`, with fresh eyes
   and guardrails that forbid quietly weakening a gate. See "The self-improvement loop" below.

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
  skills/hs-harness/SKILL.md                               # meta/maintenance: apply the self-improvement loop
  factory/
    methodology.md        # this file
    invariants.md         # curated AGENTS.md footgun checklist (plan gate + review rubric)
    ears.md               # EARS requirement templates
    review-rubric.md      # severity scale, refutation protocol, human-gate triggers
    portability.md        # non-Claude / smaller-model harness compatibility contract
    templates/            # GOAL.md PLAN.md TECH.md REVIEW.md META.md skeletons
    bin/                  # next_phase.py, set_phase.py, _fsm.py (FSM); meta_status.py (META.md reader)
    harness-log.md        # hs-harness decision ledger (cross-job anti-thrash memory)
spec/{slug}/              # per-feature artifacts incl. META.md (committed, retained on merge)
AGENTS.md                 # the constitution (CLAUDE.md is a symlink to it)
```

`.claude` is a symlink to `.agents`, so Claude Code discovers the skills and reads settings through
it. The skills reference the bundled scripts and shared reference material by **repo-relative path**
(`.agents/factory/…`), which keeps them portable. They are meant to run on other harnesses too (Warp,
OpenCode, open-weight models): the Claude-Code-specific affordances (frontmatter, `` !`…` `` state
injection, `AskUserQuestion`, `Agent` fan-out, `ReportFindings`) each have a documented fallback in
[`portability.md`](portability.md), so the factory degrades gracefully rather than breaking.

## The self-improvement loop (META.md + `hs-harness`)

The five lifecycle skills improve the *product*; this loop improves the *factory* itself. Skill
friction is otherwise invisible and forgotten between sessions — so each lifecycle skill ends with a
**silence-by-default meta-note step** that appends a finding to the feature's `spec/{slug}/META.md`
**only** when the *skillset itself* cost something. The single load-bearing gate is one test: *"was
this the skill's fault — not mine, not the task's?"* (a merely-hard task, a self-inflicted error, or a
one-off content/code issue that belongs in `GOAL.md`/`REVIEW.md` is **not** a finding).

```
hs-feature ─┐
hs-plan     ├─ meta-note (silence by default) ─► spec/{slug}/META.md   (What worked well + F# findings)
hs-build    │                                          │  (kept OUT of the blind reviewer's context)
hs-review  ─┘  (orchestrator only)                     │
                                                       ▼
hs-publish ──► reads open findings (meta_status.py) ─► "🔧 Harness feedback" block in the PR
                                                       ▼
hs-harness ──► human-gated: shape → preview → apply to .agents/ ─► flip status + log harness-log.md
```

The design is deliberately **asymmetric — cheap to observe, deliberate to act** — so it cannot become a
token-sink or quietly loosen its own guardrails:

- **Producers** (`hs-feature`/`hs-plan`/`hs-build`/`hs-review`) only *record* (≤3 terse findings each),
  never fix. `hs-build` is the richest source (per-phase, across separate invocations — appending to a
  file preserves signal a context reset erases). `hs-review`'s finding is written by the *orchestrator*,
  never the blind reviewer, which must not even read `META.md` (it leaks intent). `META.md` is
  orthogonal to the `GOAL→PLAN→TECH→REVIEW` spine and retained on merge like the rest of `spec/{slug}/`.
- **The applier is separated from the observer** (fresh eyes + a human gate, mirroring blind review).
  `hs-harness` is **human-gated always** and bound by hard guardrails: it **never auto-weakens a
  non-negotiable gate** (tests, CLI-verify, an `invariants.md` item) without an explicit typed override
  — *a finding arguing to loosen a guardrail is itself a warning sign* — prefers an example over a new
  hard rule (fixes must generalize), keeps per-finding **atomic revertable** commits with **post-apply
  verification**, writes **no `META.md` findings** (no meta-on-meta, no recursion), and reads
  `harness-log.md` first so a fix that reverts a recent change or repeats a rejected one is flagged, not
  silently re-applied.

**Observe earns act.** The cheap half (meta-notes + PR surfacing) should prove it produces real signal
over the first few features before the applier is leaned on hard; a finding that recurs across features
(via `hs-harness --all` and the ledger) escalates itself. As with the FSM, the fragile parsing is owned
by a script — `meta_status.py`, stdlib-only (no PyYAML) — not the model.

## Traceability chain

`GOAL.md` R-IDs → `PLAN.md` requirement→design map → `TECH.md` phase `satisfies:` → commits →
`REVIEW.md` requirement→evidence matrix → PR body. Because the repo is squash-only (per-commit
history is destroyed on merge), the committed `spec/{slug}/` folder *is* the retained trace.

Provenance lives in that chain, **not in source comments**: comments explain the invariant/*why* on
their own terms and never embed R-IDs or phase ids — those restart per feature and collide across
branches, and mean nothing to a later reader of the merged tree. `git blame → commit → PR →
spec/{slug}/` recovers the requirement behind any line when you need it.
