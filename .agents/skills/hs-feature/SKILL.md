---
name: hs-feature
description: >-
  Start a new HyperShell feature/fix/refactor from a clean develop branch. Safety-checks the tree,
  derives a {slug}, creates feature/{slug} or fix/{slug}, ingests an inline prompt or an untracked
  GOAL.md, and refines it into spec/{slug}/GOAL.md — appetite, non-goals, EARS acceptance criteria
  with stable R-IDs, resolved clarifications. Shaping only: no deep research, no big code reads. The
  first step of the spec-driven "software factory" lifecycle (see .agents/factory/methodology.md).
disable-model-invocation: true
argument-hint: "<inline feature description> | spec/<slug>/GOAL.md [fix|refactor] [appetite small|big]"
allowed-tools: Read, Write, Edit, Grep, Glob, AskUserQuestion, Bash(git status *), Bash(git branch *), Bash(git switch *), Bash(git rev-parse *), Bash(git fetch *), Bash(git add *), Bash(git commit *), Bash(git log *), Bash(git ls-files *), Bash(head *)
---

# hs-feature — shape the goal

## When to Use

Invoke `/hs-feature` on a clean `develop` to begin a new unit of work. It produces exactly one
artifact — a refined `spec/{slug}/GOAL.md` on a fresh branch — and stops for your sign-off before the
expensive `/hs-plan` step. This is **shaping**, in the Shape Up sense: make the goal coherent,
bounded, and unambiguous, but leave design freedom for the plan. Do **not** research or read a lot of
code here.

Reference (load only if needed): [`.agents/factory/methodology.md`](../../factory/methodology.md),
[`.agents/factory/ears.md`](../../factory/ears.md), the template
[`.agents/factory/templates/GOAL.md`](../../factory/templates/GOAL.md).

**Harness portability.** These skills run on any harness, not only Claude Code — see
[`factory/portability.md`](../../factory/portability.md). Here the Claude-specific affordances degrade
gracefully: if the *Current state* block below isn't auto-injected, run those commands yourself in
Step 1; if `AskUserQuestion` is unavailable, ask in plain text and STOP. Everything else (git, `uv run`)
is portable shell.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree: !`git status --porcelain | head -n 20`
- Untracked GOAL.md files: !`git ls-files --others --exclude-standard 'spec/**/GOAL.md'`

## Argument Parsing

Parse `$ARGUMENTS` case-insensitively. If self-contradictory, STOP and ask.

- A path matching `spec/<slug>/GOAL.md` → **adopt that file** as the seed; `{slug}` is taken from the
  path. (This is the "I hand-wrote a GOAL.md" flow.)
- `fix` / `bug` / `hotfix`(reject, out of scope) / `refactor` → set `kind`; otherwise infer from the
  wording, defaulting to `feature`. **Hotfixes against `master` are out of scope — STOP and say so.**
- `appetite small` / `appetite big` → set appetite; else default `small` for `kind: fix`, `big` for
  `feature`/`refactor`.
- Everything else → the inline feature description (the seed prompt).
- No arguments **and** no untracked `spec/*/GOAL.md` present → STOP and ask for a description or a
  GOAL.md path.

## Safety Principles

- **On `develop`, otherwise-clean tree.** If the injected branch is not `develop`, STOP (do not
  auto-switch or stash). The tree must be clean **except** the untracked `spec/{slug}/GOAL.md` you are
  adopting when a path was given — any *other* modified or untracked file → STOP.
- **Never overwrite a tracked GOAL.** If `spec/{slug}/` already exists **in git** or the target
  branch already exists, STOP and report a collision. (Adopting an *untracked* hand-written
  `spec/{slug}/GOAL.md` at an explicit path is the intended flow, not a collision.)
- **Branch mapping:** `kind: fix` → `fix/{slug}`; `kind: feature|refactor` → `feature/{slug}`.
- **Never guess.** On any ambiguity in scope or requirements, emit a literal `[NEEDS CLARIFICATION:
  …]` marker in GOAL.md and ask the human (AskUserQuestion). Record answers in the Clarifications
  section. Do not invent behavior.
- **Shaping only.** No research fan-out, no broad code exploration, no implementation. If you feel
  the urge to research, that is `/hs-plan`'s job.
- **Size circuit-breaker (soft).** If shaping produces **>~8–10 acceptance criteria** or several
  distinct deliverables, the appetite is probably too big — pause and offer the human a **pilot +
  follow-ups** split (record the deferred scope in Non-goals). A prompt, not a hard limit.
- **No `Co-Authored-By` trailer** on the commit (repo convention).

## Procedure

### Step 1 — Pre-flight
1. Confirm on `develop` with an otherwise-clean tree — the **only** permitted pending change is the
   untracked `spec/{slug}/GOAL.md` being adopted (path-given flow). Any other dirty/untracked file →
   STOP (commit, stash, or discard first).
2. `git fetch origin || true`; if `develop` is behind, note it (not fatal).

### Step 2 — Resolve slug, kind, appetite
1. If a `spec/<slug>/GOAL.md` path was given, use it. Else derive a concise kebab `{slug}` (≤ ~5
   words) from the description; if it's not obviously good, propose it and confirm.
2. Resolve `kind` and `appetite` per Argument Parsing.
3. Check collisions: `git rev-parse --verify {branch}` must fail (branch absent), and `spec/{slug}/`
   must not be tracked. STOP on collision.

### Step 3 — Create the branch
`git switch -c {branch} develop` where `{branch}` = `fix/{slug}` or `feature/{slug}`.

### Step 4 — Write / refine `spec/{slug}/GOAL.md`
Start from the template. Fill: **Problem** (the raw need — motivate, don't design), **Outcome**,
**Acceptance criteria** as R-IDs (`R1`, `R2`, …) nudged toward EARS, **Non-goals**, **Clarifications**
(with any `[NEEDS CLARIFICATION]` markers resolved via AskUserQuestion), **Related materials** (issue
links, source paths). Record `slug`, `kind`, `appetite` in the header. If adopting a hand-written
GOAL.md, refine it **in place** — preserve the author's intent; only disambiguate, structure, and add
R-IDs/appetite/non-goals. Do not expand scope.

### Step 5 — Coherence self-check
Re-read the GOAL: is it solved, bounded to the appetite, and free of unresolved markers? Every
requirement testable and observable? If not, iterate (ask the human) before committing.

### Step 6 — Meta-note (self-improvement loop · silence by default)
Before committing, reflect on the **skillset itself** — not the task, not the code. Write nothing
unless the bar is met.

**The bar (one test):** *was this the skill's fault — not mine, not the task's?* **Qualifies:** you
hand-fixed a command this skill gave (wrong flag/path, unquoted YAML); a genuinely ambiguous
instruction; a `[NEEDS CLARIFICATION]` that better guidance could have pre-empted; an
allowed-tools/step mismatch; a gate that passed or failed misleadingly. **Stay silent for:** a merely
hard task; your own error against clear guidance; a one-off content/code issue (→ `GOAL.md`, not here);
a vague preference.

If (and only if) the bar is met, record it in `spec/{slug}/META.md` (create from
[`templates/META.md`](../../factory/templates/META.md) if absent, else append). You may also add a
one-line **What worked well** note when a part of this skill materially helped. Caps: **≤3 findings**,
terse; if an equivalent finding already exists, append "· seen again" rather than duplicating; a fix
that would weaken a non-negotiable gate (tests, the CLI-drive verify, an `invariants.md` item) is
`severity=high` and must say so. **Records only** — `/hs-harness` applies fixes later, human-reviewed.
Use the next unused `F#`; always write `status=open`; append the finding as a section **outside** any
code fence:
```markdown
## F<n> — <one-line title>
`origin=hs-feature:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why it's the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the change to the skill/template/script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```
`hs-feature` is shaping-only, so findings here are usually about ambiguous shaping guidance or the
`GOAL.md` template.

### Step 7 — Commit
```
git add spec/{slug}/GOAL.md          # add spec/{slug}/META.md too if you recorded a meta-note
git commit -m "[{category}] Shape {slug} goal"
```
`{category}` = `fix` for `kind: fix`, else `feature`. **No co-author trailer.** Do not push.

### Step 8 — Report & hand off
Report: branch, slug, kind, appetite, the R-ID list, any open clarifications. Tell the human the
sign-off gate: review `spec/{slug}/GOAL.md`, then run **`/hs-plan`** to research + design. Stop.

## Examples

- `/hs-feature add a --dry-run flag to hs submit that prints the plan without writing rows` — infer
  `feature`, derive slug `submit-dry-run`, create `feature/submit-dry-run`, shape the GOAL.
- `/hs-feature spec/cli-cluster-restart-repeat-update/GOAL.md` — adopt the hand-written GOAL, slug
  `cli-cluster-restart-repeat-update`, branch `feature/cli-cluster-restart-repeat-update`, refine in
  place.
- `/hs-feature fix heartmonitor evicts a client that re-registered within evict window` — `kind: fix`,
  appetite small, branch `fix/{slug}`.

## Notes

- This skill never researches, edits source, or pushes. That's `/hs-plan`, `/hs-build`, `/hs-publish`.
- If a requirement can't be made unambiguous with the human right now, leave the `[NEEDS
  CLARIFICATION]` marker in place and STOP — an ambiguous GOAL blocks `/hs-plan`.
- Some `git` mutations may prompt for permission depending on your `settings.local.json`; that's
  expected and safe.
