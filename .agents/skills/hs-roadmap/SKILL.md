---
name: hs-roadmap
description: >-
  Retire the deferrals whose cycles have landed, and keep ROADMAP.md true. Finds every
  issues/{slug}.md carrying status: adopted:{slug}, confirms the cycle actually reached develop, and
  deletes the seed with its ROADMAP entry after a human-gated preview. Also repairs the drift a
  shipped cycle leaves behind: discharged "Depends on:" prerequisites, dangling cross-references,
  stale counts and file:line citations in surviving seeds, and adoption markers left by abandoned
  branches. Operational sibling of hs-harness and hs-release — maintenance, NOT a lifecycle step;
  run between releases (see .agents/factory/methodology.md).
disable-model-invocation: true
argument-hint: "[--dry-run] [slug] [--all] [status|report]"
allowed-tools: Read, Write, Edit, Grep, Glob, AskUserQuestion, Bash(git status *), Bash(git branch *), Bash(git log *), Bash(git ls-tree *), Bash(git ls-files *), Bash(git rev-parse *), Bash(git fetch *), Bash(git pull *), Bash(git add *), Bash(git commit *), Bash(grep *), Bash(del *), Bash(ls *), Bash(head *)
---

# hs-roadmap — retire what shipped, keep the index true

## When to Use

Invoke `/hs-roadmap` between releases, or any time `ROADMAP.md` has stopped describing the work that
is actually left. Its main job is the one step the lifecycle never had: a cycle seeded from an
`issues/{slug}.md` ships, and nothing retires the seed, so the backlog keeps advertising work that is
already on `develop`.

This is **maintenance, not a lifecycle step.** It does not touch `src/hypershell/`, does not read or
write `GOAL/PLAN/TECH/REVIEW`, and never advances an FSM. It edits `ROADMAP.md`, deletes seeds under
`issues/`, repairs the references a deletion breaks (including under `.agents/` and in `AGENTS.md` —
Step 6), and — in the security lane only — moves an entry rather than deleting anything.

Deliberately **not** part of `/hs-publish`. Retirement writes outside `spec/`, which is precisely what
publish's staleness gate (`git diff --stat {last_reviewed_commit}..HEAD -- . ':(exclude)spec/'`) is
built to notice; folding it in would mean carving `issues/` and `ROADMAP.md` out of that gate — and a
cycle legitimately writes there, since a review pass that defers a finding files the seed and its
ROADMAP entry inside the reviewed change set. Publish names an un-retired seed in its report and
stops there.

Reference: [`methodology.md`](../../factory/methodology.md),
[`templates/ISSUE.md`](../../factory/templates/ISSUE.md) (the `status:` vocabulary), and
[`AGENTS.md`](../../../AGENTS.md) § *Where a deferral goes — five homes, one rule each*.

**Harness portability.** Runs on any harness — see [`factory/portability.md`](../../factory/portability.md).
Fallbacks: run the *Current state* commands yourself if not auto-injected; ask in plain text and STOP
if `AskUserQuestion` is unavailable. `git` and `grep` are portable shell; `del` is this environment's
reversible-trash stand-in for a blocked `rm` (see Safety Principles).

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree: !`git status --porcelain | head -n 20`
- Adopted seeds: !`grep -rl "^status: adopted:" issues .security/issues 2>/dev/null || true`
- Queued entries: !`grep -c '^\*\*Seed:\*\*' ROADMAP.md 2>/dev/null || true`
- Hidden entries: !`grep -c '^\*\*Seed:\*\*' .security/ROADMAP.md 2>/dev/null || true`

## Argument Parsing

- No argument → consider every adopted seed whose cycle has landed. The default.
- `<slug>` → restrict to the seed adopted by that cycle. This is the **cycle slug**, not a filename;
  Step 3 explains why those need not agree.
- `--all` → widen Step 6's drift sweep to seeds this retirement does not otherwise touch.
- `--dry-run` → run Steps 1–4 and Step 5's *preview only*, then STOP. No `AskUserQuestion`, no edits,
  no deletions, no commit — Step 2's loadability-repair commit and Step 4's "put the missing
  obligation there first" write are both **reported, not performed**, under `--dry-run`.
- `status` / `report` → list adopted seeds with landed/in-flight/stale for each; no work.

## Safety Principles

- **Deleting a seed destroys the only copy outside git history.** Preview every retirement and confirm
  with the human before acting. Never delete on inference alone.
- **`del`, not `rm`.** `rm` is blocked in this environment and `del` is reversible trash. The seed is
  tracked, so follow with `git add -A` to stage the deletion; that is why this skill does not need
  `git rm`.
- **Landed means on `develop`.** A seed is retired only when `git ls-tree develop -- spec/{slug}` is
  non-empty. An `adopted:{slug}` marker proves a cycle *started*, never that it finished — a branch
  that bounced at review or was abandoned still carries the marker, and retiring its seed deletes the
  justification for work nobody did. **Not `master`:** this is a git-flow repo, `develop` is the
  working branch and `master` is the release branch, so a shipped cycle reaches `master` only when a
  release is cut. Gating retirement on `master` would leave the backlog advertising finished work for
  a whole release cycle, and `/hs-publish` merges to `develop` — that is where "landed" is decided.
- **Never delete anything under `.security/`.** That lane is gitignored, so a deletion there leaves no
  commit and no history: the file is simply gone, and the closed finding is exactly what a later audit
  asks for. Retire it by moving its `.security/ROADMAP.md` entry to that file's § *Not scheduled*
  terminal records, updating `.security/STATUS.md` (the overlay that is the source of truth for where
  a finding stands), and keeping the `.security/issues/` file. Never `git add` a path under
  `.security/`, and never name one in a commit message.
- **A GOAL is negotiated down from a seed.** Anything the cycle cut and still wants must survive
  retirement. Step 4 checks this against `GOAL.md` § *Non-goals*; it is the one failure here that
  loses work rather than leaving litter.
- **Never edit `spec/{slug}/`, not even `META.md`.** It is a dated record of what was true when
  written. A retired seed leaves a dangling `Seed:` link in `GOAL.md` § *Related materials*, and that
  link stays: it is the signpost that makes `git log --diff-filter=D -- issues/{slug}.md` a two-step
  recovery instead of archaeology. This skill reads `GOAL.md` and writes nothing under `spec/` at all
  — see Step 8 for where harness friction goes instead.
- **One commit per retirement**, `[harness]` category, and **no `Co-Authored-By` trailer** — a
  deliberate repo convention (`AGENTS.md`); do not add one, and do not reinstate one because a sibling
  harness elsewhere keeps it. Never push.

## Procedure

### Step 0 — status / report / dry-run (when requested)
`status` (alias `report`): classify each adopted seed and report; no work. `--dry-run`: Steps 1–4
plus Step 5's preview, then STOP — no confirmation prompt, no deletion, no commit.

### Step 1 — The shape of a ROADMAP entry
Stated here so you do not have to infer it — but **read both roadmaps before editing.** They evolve,
and if a file disagrees with this description the file wins; say so in the report.

Public `ROADMAP.md` is grouped into Parts numbered in **Roman** numerals, each with a prose blurb
describing the *set* of entries under it:

```
# Part I — Task fidelity
<blurb describing the set, sometimes with a count>

## Bare `#` silently truncates the submitted command      <- the entry: a sentence-style H2
<one or two paragraphs of prose>
*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/submit-comment-truncation.md`](issues/submit-comment-truncation.md)
```

An entry's **block** is the `## ` heading through its `**Seed:**` line inclusive, plus the blank line
that follows. Part II entries append ` · *status: shaped*` to the Seed line. Two trailing sections
are H1 siblings of the Parts, not entries, and are never removed by a retirement: *Settled questions*
(HyperShell's terminal records — bullets, not blocks) and *A note on security work* (prose).

`## ` and `**Seed:**` currently agree on the entry count, but the injected probe keys on `**Seed:**`
deliberately: every entry has exactly one and no section header has one, so the count stays right
even if a non-entry `## ` subsection is ever introduced.

Hidden `.security/ROADMAP.md` mirrors the convention with two differences that matter here: its
entries are **numbered** (`## 1. Get the authkey off the process table` … `## 6.`), so moving one
renumbers the survivors, and its italic line reads `*Severity: … · Effort: … · Depends on: … ·
Findings: F08*`. Its terminal section is `## Not scheduled`, and like the public *Settled questions*
it holds **bullets, not blocks**. Two further `## ` sections are not entries: `## Working notes`
(prose) and `## Standing at the 2026-08 re-assessment` — a severity **count table** that closing a
finding falsifies, so Step 5 updates it by hand; no `{slug}`-keyed sweep will find it.

### Step 2 — Pre-flight
Clean tree; non-empty → STOP. Confirm you are on `develop`; this skill does not run on a
`feature/`|`fix/`|`harness/` branch, because a seed retired on a branch that never merges takes the
backlog entry with it.

`git fetch origin || true`. `/hs-publish` lands cycles by squash PR, so a merge can exist on
`origin/develop` before local `develop` has it: if local `develop` is behind, `git pull --ff-only`,
or classify against `origin/develop` and say which ref you used in the report.

One exception to the clean-tree STOP, because the alternative is circular: when the tree is dirty
*only* with the repair that made this skill loadable, commit that as its own `[harness]` commit and
continue. The STOP is there to keep a retirement from being tangled with unrelated work; a fix to the
factory is not unrelated work, it is the reason the sweep can run at all. Anything else in the tree
still STOPs, and the repair is never folded into a retirement commit.

### Step 3 — Find the adopted seeds and classify each
```
grep -rl "^status: adopted:" issues .security/issues 2>/dev/null || true
git ls-files 'issues/*.md'          # cross-check: an untracked seed is not yet part of the index
```
Plain `grep`, not `git grep`: `.security/` is gitignored, and `git grep` searches tracked files only,
so it would skip that lane while appearing to work. The `|| true` is load-bearing — with `.security/`
absent `grep` exits 2 while still printing its matches, so anything branching on the exit status reads
"no adopted seeds" off a list of them.

**Match on the frontmatter, never on the filename.** Today `/hs-feature` takes `{slug}` from the file
stem in both lanes, so the two normally agree — but the marker, not the filename, is the record: a
hand-renamed seed, a GOAL adopted at an explicit `spec/{slug}/GOAL.md` path, or a future re-phrasing
of a hidden-lane slug all break the coincidence. A filename guess deletes nothing, or deletes the
wrong thing.

Read the `{slug}` out of each `status: adopted:{slug}` value and classify:

| `git ls-tree develop -- spec/{slug}` | Meaning | Action |
|---|---|---|
| non-empty | the cycle landed | retire (Steps 4–5) |
| empty, branch exists | in flight | leave alone |
| empty, no branch | abandoned; the marker is stale | offer to reset `status:` to `shaped` or `unshaped`, never delete |

"Branch exists" means local **or** remote — after a `git fetch` it may be only the latter:
```
git rev-parse --verify --quiet feature/{slug} || git rev-parse --verify --quiet fix/{slug} \
  || git rev-parse --verify --quiet origin/feature/{slug} || git rev-parse --verify --quiet origin/fix/{slug}
```
(`/hs-feature`'s branch mapping is `kind: fix` → `fix/{slug}`, every other kind → `feature/{slug}`.)

The stale case matters because `/hs-feature` STOPs on an adoption marker as already-promoted. Left
alone, an abandoned cycle leaves a seed that cannot be re-shaped under a new slug, and a ROADMAP entry
that looks worked when it is not.

### Step 4 — Check the seed shipped whole
Read `spec/{slug}/GOAL.md` § *Non-goals* against the seed's problem statement and its sketch of the
acceptance criteria. Non-goals are the written record of what the promotion negotiated away.

Anything cut and still wanted does not die with the seed. Either rewrite the seed down to the
remainder and reset `status:` to `unshaped`, re-wording its ROADMAP entry to match, or file a fresh
seed for it from [`templates/ISSUE.md`](../../factory/templates/ISSUE.md). Only a seed with no live
remainder is deleted.

**A non-goal that discharges itself by pointing elsewhere is conditional, and the condition is what
you verify.** "Record it there", "the harness cycle must cover this", "that is a seed for `issues/`" —
each of those is the reason the cycle was allowed to ship without the work. Open the file it names and
confirm the obligation is *in* it: `issues/{slug}.md` plus a `ROADMAP.md` entry for code work,
`.security/` for anything unremediated, `spec/{slug}/META.md` for harness friction — the five homes
in `AGENTS.md`, one rule each. An intention stated in `GOAL.md`, in `PLAN.md`, or in the ROADMAP entry
is not the record; the named destination is. Where it is missing, put it there first, in the
retirement's own commit.

Check this before deleting, not after. The obligation is frequently written in exactly one place — the
roadmap entry the retirement removes — so the deletion is what makes the loss irreversible, and this
is the one failure in the sweep that costs work rather than leaving litter.

### Step 5 — Preview, confirm, retire
Present per seed: the file to delete, the ROADMAP entry to remove, any remainder being preserved, and
the cross-references Step 6 will repair. Confirm with `AskUserQuestion`. On `--dry-run`, stop here.

Then, for each confirmed public-lane seed:
```
del {seed-path}     # the path Step 3 printed, never a name reconstructed from {slug}
```
Remove its block from `ROADMAP.md` — the `## ` heading through the `**Seed:**` line inclusive, plus
the trailing blank line. Leave the Part heading and its blurb standing.

**Removing the last entry of a Part is a special case: remove the Part too** — its
`# Part {I,II,…} — …` heading (Roman, H1 — not `##`), its blurb, and the `---` rule that separated it.
An emptied Part is an index section indexing nothing, and its blurb states a fact about a set ("Four
shaped GOALs…", "Findings from a comparative review…") that is false once the set is empty. Because
the Parts are *ordinally numbered*, this is the one edit in the sweep that renumbers something:
renumber the surviving Parts in Roman order and sweep for prose that names them —
```
grep -rnE 'Part [IVX]+' ROADMAP.md .security/ROADMAP.md issues/ .agents/ --include='*.md'
```
Never fold this into a seed's retirement silently — raise it in the preview as its own confirmation.

Security-lane seeds are **moved, not deleted**, per the safety rule above: rewrite the
`.security/ROADMAP.md` entry as a **bullet** under § *Not scheduled* (that section holds bullets, not
`## N.` blocks) with a one-line note of what closed it, renumber the surviving entries, correct the
severity counts in § *Standing at the 2026-08 re-assessment* — a closed finding falsifies that table
and no sweep in Step 6 will catch it — record the remediation in `.security/STATUS.md`, and keep the
`.security/issues/` file. Do not edit
`findings/F*.md` — dated evidence, under the same rule as `spec/`. None of this is committable, so the
run report is the only place these edits are visible; name them there.

### Step 6 — Repair what the removal broke
Find the references; do not recall them. Both strings matter, and Step 3 already says they need not be
the same:

```
grep -rnE '{seed-filename}|{slug}' \
    --include='*.md' --include='*.rst' --include='*.py' --include='*.toml' . \
    | grep -vE '(^|/)(\.git|\.venv|docs/_build)/'

# `.security/` is gitignored, and a `grep` that honours ignore files during recursion — this
# environment's does — returns zero hits from that lane for a `.`-rooted sweep. Name it as an
# explicit path argument, the way Step 3 does, or the cross-lane row of the table below is dead:
grep -rnE '{seed-filename}|{slug}' --include='*.md' .security 2>/dev/null || true
```

Short generic slugs match ordinary prose — `zone` alone returns well over a hundred lines in this
tree. When that happens, split the sweep: run the *filename* pattern (`issues/{seed-filename}`)
unfiltered, since that is the authoritative set of index references, and run the bare slug only inside
the files the filename pass already flagged, plus `ROADMAP.md`, `.security/ROADMAP.md`, and `issues/`.

Triage every hit by where it lands, because three of these destinations are deliberately left alone:

| Where the hit is | Action |
|---|---|
| `ROADMAP.md`, other `issues/*.md` | Repair. This is the work described below. |
| `.security/ROADMAP.md`, `.security/issues/*.md`, `.security/STATUS.md` | Repair — the cross-lane references are real (the hidden lane cites public seeds by path). Never stage it; name the edit in the report instead, since no commit will show it. |
| `.agents/` skills, templates, factory docs | Repair — with one carve-out: a slug used as an **illustrative example** (this skill's Step 1 sample block and Examples, `/hs-feature`'s Examples) is a placeholder, not a cite, and stays. Repair a real cite; repair an instruction the promotion falsified. |
| `AGENTS.md`, `docs/**`, `README.rst` | **Repair, but conservatively.** These are ground truth about the code, not an index of pending work: a genuine cite of a deleted seed is drift, while a description of behavior the cycle shipped is now just documentation and stays. Flag anything ambiguous in the report. |
| `spec/**` | **Leave.** Never edit `spec/{slug}/`. The dangling `Seed:` link is the signpost that makes recovery two steps instead of archaeology. |
| `.agents/factory/harness-log.md` | **Leave.** A dated record of what was decided, not an index of what exists. |
| `src/hypershell/**` | **Report, do not edit.** Source is never supposed to cite a seed or a feature-scoped id (`AGENTS.md`); a hit here is a finding for the human, not a fix for this sweep. |

Public entries carry no numbers, so removing one renumbers nothing (the exceptions are a whole Part,
Step 5, and the numbered hidden lane, Step 5). What still breaks is prose:

- **A discharged `Depends on:`.** HyperShell's entries name prerequisites in the italic line and in
  the `Refs:` clause — "Depends on: zone (strictly)", "prerequisite for relay (R5/R6 inherit the
  return path)", "relay R9 is verbatim this machinery". Retiring an entry discharges those edges, so
  find them:
  ```
  # `{keyword}` is required, not optional: entries name prerequisites in prose, rarely by slug.
  # Derive it from the retired entry's own `## ` heading — retiring submit-comment-truncation needs
  # `truncation` to reach "Depends on: sequence after the `#`-truncation fix", which the slug misses.
  grep -n 'Depends on:\|Refs:' ROADMAP.md .security/ROADMAP.md | grep -i '{slug}\|{keyword}'
  ```
  Say the constraint cleared rather than deleting the clause — a reader needs to know the ordering
  existed and is discharged. Where surviving text cites the retired seed's R-IDs, repoint it at the
  shipped thing by name: R-IDs are renegotiated at promotion, so `relay R9` in a seed and `R9` in
  `spec/relay/GOAL.md` are not guaranteed to be the same requirement.
- A count that the retirement makes wrong — in `ROADMAP.md` and inside surviving seeds. The Part
  blurbs are where these hide ("Four shaped GOALs", "two were judged not to be defects at all"), so
  re-read the blurb of every Part you edited — **and the file's opening framing above Part I**, which
  states facts about the set of Parts and is not itself a Part blurb.
- A figure the shipped cycle falsified — a count, a `file:line` citation, a quoted output — anywhere
  in a file this retirement already edits. Read those whole: a stale figure standing beside a freshly
  repaired link is worse than one in a file nobody opened, because the repair is what tells a later
  reader the file was reviewed.
- With `--all`: the same figures in seeds this retirement never touches — a line count, an occurrence
  table, a file inventory. A stale baseline in a seed whose own acceptance criterion is a line-count
  guard is the one number in it that has to be right.

Do not rewrite a `Found by:` line. Those ordinals are provenance ("GNU Parallel comparison review,
2026-08 (H6 + H7)"), not queue position.

### Step 7 — Commit
```
git add -A
git commit -m "[harness] Retire the {slug} seed and its roadmap entry"
```
One commit per retirement; fold Step 6's repairs into the commit that caused them. `git add -A` stages
the `del` as a deletion — that is why no `git rm` is needed — and `.security/` is gitignored so `-A`
cannot reach it; still check `git status --porcelain` before committing that no `.security/` path
appears and that the message names none. `[harness]` is the category because the sweep maintains the
factory's bookkeeping, never the product; do not coin a new one. **No `Co-Authored-By` trailer.** Do
not push.

For a stale marker reset with no deletion: `[harness] Reset the stale adoption marker on {slug}`. For
a retirement that also empties a Part: `[harness] Retire the {slug} seed and fold Part {N}`.

### Step 8 — Report
Seeds retired, seeds left in flight, stale markers found and what was done about them, remainders
preserved, cross-references repaired, and every uncommittable `.security/` edit (the report is their
only record). Name anything you chose not to touch, and say which ref you classified against if it was
not local `develop`.

Name any **harness friction** this sweep exposed — a skill instruction that was wrong or ambiguous, a
command that had to be hand-fixed. **Operational, not meta:** like `/hs-harness` and `/hs-release`,
this skill writes no `META.md` findings and never recurses. `/hs-harness` reads only `spec/*/META.md`,
so a finding named only here dies with the session — say so plainly in the report and let the human
decide whether to carry it into a `META.md` themselves.

## Examples

- `/hs-roadmap` — retire every landed seed, one commit each.
- `/hs-roadmap --dry-run` — preview the whole sweep; change nothing.
- `/hs-roadmap submit-comment-truncation` — retire just that cycle's seed.
- `/hs-roadmap status` — classify every adopted seed; no work.

## Notes

- `/hs-publish` names an un-retired seed in its final report. It does not act: this skill is where
  deletion lives, so publish keeps neither an `Edit` tool nor a deletion verb.
- A **public** seed that shipped leaves no terminal record. `ROADMAP.md` § *Settled questions* is for
  deferrals closed **without** shipping — the `declined` and `accepted-behaviour` stances it points
  at — where nothing else in the repository shows the question was asked. For shipped public work the
  refutation is free: someone re-filing it greps the code and finds it already done, and
  `spec/{slug}/` holds the account. **The hidden lane is the exception:** `.security/ROADMAP.md`
  § *Not scheduled* holds those two stances *and* remediated findings (Step 5), because a gitignored
  lane has no git history to refute a re-filing with.
- This skill never touches `src/hypershell/`, never advances an FSM, never tags or cuts a release
  (`/hs-release` does that), and never ships to `master`.
