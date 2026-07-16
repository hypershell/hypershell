---
name: hs-plan
description: >-
  Turn a shaped spec/{slug}/GOAL.md into a design and a phased roadmap. Runs an invariant gate against
  AGENTS.md, fans out read-only research subagents (scaled to appetite; codebase-first), synthesizes
  spec/{slug}/PLAN.md, re-checks invariants, and generates spec/{slug}/TECH.md — the phased YAML-FSM
  driven by /hs-build. Second step of the software-factory lifecycle (see .agents/factory/methodology.md).
disable-model-invocation: true
argument-hint: "[appetite small|big] [skip research] [status]"
allowed-tools: Read, Write, Edit, Grep, Glob, Agent, AskUserQuestion, WebSearch, WebFetch, Bash(git status *), Bash(git branch *), Bash(git rev-parse *), Bash(git log *), Bash(git add *), Bash(git commit *), Bash(uv run *)
---

# hs-plan — research → PLAN → TECH

## When to Use

Invoke `/hs-plan` after `/hs-feature` has landed a quality `GOAL.md` on a feature/fix branch. It
produces the design (`PLAN.md`), optional backing `research/`, and the phased FSM (`TECH.md`), then
stops for your sign-off before `/hs-build` touches code. Depth scales to the GOAL's `appetite`.

Reference: [`.agents/factory/methodology.md`](../../factory/methodology.md),
[`.agents/factory/invariants.md`](../../factory/invariants.md) (the gate),
templates [`PLAN.md`](../../factory/templates/PLAN.md) / [`TECH.md`](../../factory/templates/TECH.md).

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree: !`git status --porcelain | head -n 20`
- Spec artifacts: !`ls -1 spec/*/ 2>/dev/null | head -n 40`

## Argument Parsing

- `skip research` / `no research` → collapse to a lean plan (no fan-out) regardless of appetite.
- `appetite small|big` → override the GOAL's appetite for this planning run.
- `status` / `report` → summarize what artifacts already exist for this slug and what's missing; no work.

## Safety Principles

- **On a feature/fix branch, never `develop`/`master`.** Derive `{slug}` = branch minus its
  `feature/`|`fix/` prefix. STOP if on a base branch or the tree is dirty.
- **`GOAL.md` must exist, be committed, and carry no unresolved `[NEEDS CLARIFICATION]` markers.** If
  markers remain, STOP and send the human back to `/hs-feature`.
- **Research is strictly read-only** and biased to the codebase (this is a self-contained repo); use
  the web only for genuinely external unknowns. Keep briefs minimal — only what informs `PLAN.md`.
- **Never build.** No source edits, no implementation. That's `/hs-build`.
- **The invariant gate is mandatory** (both checkpoints). Any bend gets recorded in PLAN's deviation
  table, never applied silently.

## Procedure

### Step 1 — Pre-flight & load
1. Confirm feature/fix branch + clean tree; resolve `{slug}`.
2. Read `spec/{slug}/GOAL.md` (appetite, R-IDs, non-goals). Read
   [`invariants.md`](../../factory/invariants.md).

### Step 2 — Invariant gate #1 (pre-research sanity)
Given the GOAL's intent, list the invariant sections (`invariants.md` §1–§12) this change will touch
and confirm the intent is even sane against them (e.g. a retry change must respect the CANCEL_STATUS
filter and the UNIQUE retry chain). If the GOAL is fundamentally at odds with an invariant, STOP and
escalate.

### Step 3 — Research fan-out (appetite-scaled)
- **`appetite: small` / `kind: fix` / `skip research`:** skip the fan-out. Do at most a couple of
  targeted reads yourself. Proceed to Step 4 with a lean plan; `research/` may be omitted.
- **`appetite: big`:** identify the *rabbit holes* — the scary unknowns that could blow the appetite
  (unfamiliar code paths, algorithmic choices, external tech, perf at scale, dialect differences).
  Launch **read-only research subagents in parallel** (Agent tool), one per topic, breadth-first:
  - Each gets: the topic + GOAL context + explicit scope boundaries + "produce a ≈1–2k-token brief
    and **write it to `spec/{slug}/research/NN-topic.md`**" (use `general-purpose` so it can write;
    number `01`, `02`, … with distinct paths so there are no write conflicts). Prefer
    codebase-exploration; reserve `WebSearch`/`WebFetch` for external unknowns.
  - Scale count to appetite (a few for medium features; more for large). Log what you fan out.
- Read the returned briefs and synthesize **`spec/{slug}/research/00-digest.md`** — the consolidated
  decisions that resolve any cross-brief contradictions with a single recommendation each.

### Step 4 — Write `PLAN.md`
From the template: **Summary**, **Design** at architecture altitude (reference concrete
`src/hypershell/…` files), the **requirement → design map** (every R-ID covered), **rabbit holes
resolved** (link briefs), **risks/open questions**, **verification strategy** (seeds the per-phase
`verify:` commands — prefer driving `hs`/`hsx` in a `temp_site`).

### Step 5 — Invariant gate #2 (post-design)
Re-walk the touched invariant sections against the *drafted design*. Fill PLAN's **deviation
justification table** for anything that bends an invariant or adds complexity (with the simpler
alternative and why it was rejected). Empty is the goal. STOP and escalate on an unavoidable
CRITICAL-invariant conflict.

### Step 6 — Generate `TECH.md` (the FSM)
Copy the template; author phases as **vertical slices, not horizontal layers** — each independently
verifiable end-to-end, ordered **core + small + novel first**. **Size circuit-breaker (soft):** if the
roadmap needs **>~8 phases**, the scope is probably too big for one unit of work — pause and reconsider
with the human (split into a pilot + follow-ups, or trim to the appetite) before committing a mega-plan. For each phase set: `id`, `name`,
`satisfies` (R-IDs), `depends_on`, `parallel` (**false** for coupled-core files — `data/model.py`,
`server.py`, `client.py`, `core/*`; `true` only for independent `task.py`/docs/tests work),
`hammerable` (**false** for correctness/security phases), `hill: uphill`, and a real `verify:`
command. Foundational schema/model changes must land and pass **before** dependent server/client
phases. Set top `status: in_progress`, `current_phase` to the first phase, `last_updated` today.
Then **validate**: `uv run python .agents/factory/bin/next_phase.py spec/{slug}/TECH.md` must exit 0
and report the first phase.

### Step 7 — Meta-note (self-improvement loop · silence by default)
Before committing, reflect on the **skillset itself** — not the task, not the code. Write nothing
unless the bar is met.

**The bar (one test):** *was this the skill's fault — not mine, not the task's?* **Qualifies:** you
hand-fixed a command this skill gave (wrong flag/path, unquoted `verify:` YAML); a genuinely ambiguous
instruction; a `[NEEDS CLARIFICATION]` that better guidance could have pre-empted; an
allowed-tools/step mismatch; a gate that passed or failed misleadingly. **Stay silent for:** a merely
hard task; your own error against clear guidance; a one-off content/code issue (→ `PLAN.md`/`GOAL.md`,
not here); a vague preference.

If (and only if) the bar is met, record it in `spec/{slug}/META.md` (create from
[`templates/META.md`](../../factory/templates/META.md) if absent, else append). You may also add a
one-line **What worked well** note when a part of this skill materially helped. Caps: **≤3 findings**,
terse; if an equivalent finding already exists, append "· seen again" rather than duplicating; a fix
that would weaken a non-negotiable gate (the invariant gate, the `verify:` design, an `invariants.md`
item) is `severity=high` and must say so. **Records only** — `/hs-harness` applies fixes later,
human-reviewed. Use the next unused `F#`; always write `status=open`; append the finding as a section
**outside** any code fence:
```markdown
## F<n> — <one-line title>
`origin=hs-plan:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why it's the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the change to the skill/template/script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```
Likely sources here: the research fan-out mechanics (Step 3), the invariant-gate steps, or
`TECH.md`/YAML authoring.

### Step 8 — Commit
```
git add -A spec/{slug}      # PLAN.md + TECH.md, plus research/ and META.md when present (research/ is omitted on the lean path)
git commit -m "[{category}] Plan {slug}: design + phased roadmap"
```
`{category}` = `fix`|`feature`. **No `Co-Authored-By` trailer.** Do not push.

### Step 9 — Report & hand off
Report the design summary, the phase list (id · name · satisfies · verify), any deviations recorded,
and open risks. Sign-off gate: the human reviews `PLAN.md`/`TECH.md`; then `/hs-build` executes phase
one. Stop.

## Examples

- `/hs-plan` — full appetite-scaled run for the current branch's slug.
- `/hs-plan skip research` — lean plan for a small change (no fan-out).
- `/hs-plan status` — list existing GOAL/research/PLAN/TECH for this slug and what's missing.

## Notes

- For a very large fan-out you may ask the human whether to use the `Workflow` tool instead of ad-hoc
  `Agent` calls — but default to `Agent` fan-out here.
- Keep `research/` lean: reviewers and future readers pay a tax for sprawl. Only keep what informs the
  design.
- `TECH.md` is the resume ground-truth for `/hs-build`; if `next_phase.py` reports it invalid, fix it
  before committing.
