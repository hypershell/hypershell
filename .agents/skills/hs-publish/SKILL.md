---
name: hs-publish
description: >-
  Land an approved HyperShell feature branch on develop. Default: push the branch and open a squash PR
  to develop with a rich, artifact-linked body (Summary/Goal/Design/Research/Phases/Verification).
  Alternative (local): squash-merge into develop locally and delete the branch. Confirms before any
  irreversible/outward step; never targets master. Final step of the software factory.
disable-model-invocation: true
argument-hint: "[pr (default) | local] [merge]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Bash(uv run *), Bash(git status *), Bash(git branch *), Bash(git log *), Bash(git diff *), Bash(git rev-parse *), Bash(git fetch *), Bash(git push *), Bash(git switch *), Bash(git checkout *), Bash(git merge *), Bash(git add *), Bash(git commit *), Bash(gh pr *), Bash(gh repo *), Bash(head *)
---

# hs-publish — ship the branch to develop

## When to Use

Invoke `/hs-publish` once `/hs-review` has approved the branch (`TECH.md` `review.verdict: approved`).
This is the **one irreversible step** — remote pushes and PRs can't be checkpointed — so it always
confirms with you before acting. Default is a PR to `develop`; `local` does a local squash-merge.

**Harness portability.** Runs on any harness — see [`factory/portability.md`](../../factory/portability.md).
Fallbacks: run the *Current state* commands yourself if not auto-injected; ask in plain text and STOP if
`AskUserQuestion` is unavailable. `gh`/`git` are portable shell.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Verdict / kind / slug: resolved in **Step 1** from `spec/{slug}/TECH.md` (a load-time injection can't strip the branch prefix to form `{slug}`).
- Commits vs develop: !`git log --oneline develop..HEAD 2>/dev/null | head -n 30`
- Default remote branch: !`gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo "(gh unavailable)"`

## Argument Parsing

- `local` / `no fuss` → squash-merge into `develop` locally, delete the branch (no remote PR).
- `pr` (default) → push branch + open a PR to `develop`.
- `merge` → after opening the PR, also `gh pr merge --squash` (only with explicit confirmation).

## Safety Principles

- **Base is `develop`, never `master`.** Hotfixes to `master` are out of scope.
- **Require a *current* approved review.** If `TECH.md` `review.verdict` is not `approved`, STOP and
  report — proceed only on explicit human override. Approval is pinned to
  `review.last_reviewed_commit`: any later commit touching anything **outside `spec/`** invalidates
  it (the review's own artifact commit and meta-notes do not) — the Step 1 staleness gate checks this
  mechanically.
- **Confirm before irreversible/outward actions.** Always confirm the mode, PR title, and body with
  the human (AskUserQuestion) before `git push` / `gh pr create` / local merge.
- **Squash-only repo.** Do not create merge commits or rebase-merge. The PR title becomes the squash
  commit subject on `develop`, so the **PR title is `[category] Imperative summary`** (category =
  `TECH.md` `kind`: feature|fix|refactor) — **never Conventional Commits (`feat:`/`fix:`)**.
- **Link, don't quote.** The PR body references artifacts via SHA-pinned blob permalinks, not pasted
  copies.
- **No `Co-Authored-By`** on any commit. PR **bodies** end with the Claude Code generation line.

## Procedure

### Step 0 — status (when requested)
Report the verdict, commits vs `develop`, and whether a PR already exists (`gh pr status`). Stop.

### Step 1 — Pre-flight
1. On a feature/fix branch, clean tree; resolve `{slug}` from the branch, then read `kind`, the
   review `verdict`, and `review.last_reviewed_commit` from `spec/{slug}/TECH.md`. STOP if verdict ≠
   `approved` (unless the human overrides).
2. **Staleness gate:** `git diff --stat {last_reviewed_commit}..HEAD -- . ':(exclude)spec/'` must be
   empty. Non-empty means code changed after the approved review — STOP and send back to
   `/hs-review`; proceed only on an explicit human override, recorded in the PR body.
3. `git fetch origin`. Confirm `develop` is reachable and note if the branch is behind `develop`
   (squash-merge tolerates it, but flag a large drift).

### Step 2 — Compose the PR title + body
- **Title:** `[{kind}] {imperative summary}` synthesized from `GOAL.md` (not a copy of it).
- **Body** (sectioned; link artifacts as `https://github.com/hypershell/hypershell/blob/{head_sha}/spec/{slug}/<file>`
  using the current `git rev-parse HEAD`):
  - **Summary** — a high-level description of the whole change (not a paste of GOAL).
  - **Goal** → link `GOAL.md`.
  - **Design** → link `PLAN.md`.
  - **Research** → links to `research/*.md` (if present).
  - **Phases completed** → rendered from the `TECH.md` FSM (id · name · satisfies).
  - **Verification** → the CLI flows / tests actually run (from `REVIEW.md`).
  - **Docs & completions** → confirm the same-commit rule was honored (`docs/_include/*.rst`, `share/`).
  - **🔧 Harness feedback** — surface the self-improvement loop *only when substantial* (see the
    surfacing rule below); omit the section entirely otherwise.
  - Issue: `Refs #NN` (see the auto-close note below).
  - Trailing line: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

**Harness-feedback surfacing rule.** Before finalizing the body, read this feature's harness notes:
```
uv run python .agents/factory/bin/meta_status.py spec/{slug}/META.md --status open
```
Add a terse, factual, **toolchain-only** "🔧 Harness feedback" section when there is something
substantial: `counts.open > 0` (list each open finding as a one-liner `F# · {severity} · {title}`) **or**
`spec/{slug}/META.md` has a non-empty "What worked well" section (read the file directly — the parser
only enumerates `F#` findings; list those bullets). Keep it short and process-focused; it is reviewed
alongside the code, so it must not editorialize about the feature. If there are no open findings and
nothing worked-well of note — or the file is absent (`exists: false`) — **omit the section**.
`hs-publish` never *writes* `META.md` findings; it is the loop's **surfacer**, and `/hs-harness` is
where fixes get applied.

### Step 3 — Confirm with the human
Present the mode (PR vs local), the title, and the body via AskUserQuestion. Do not proceed without a
choice.

Once confirmed, stamp the FSM terminal so the retained record does not read `in_review` forever:
```
uv run python .agents/factory/bin/set_phase.py spec/{slug}/TECH.md --top-status done --touch
git add spec/{slug}/TECH.md && git commit -m "[{category}] Mark {slug} roadmap done"
```
(A spec-only commit — the Step 1 staleness gate ignores it by design.)

### Step 4a — PR (default)
```
git push -u origin {branch}
gh pr create --base develop --head {branch} --title "{title}" --body-file <(...)
```
Report the PR URL. If `merge` was requested and confirmed, squash with an **explicit** subject/body so
none of the intermediate branch commit subjects leak into the `develop` commit — a bare
`gh pr merge --squash` concatenates every branch commit message into the squash body:
```
gh pr merge {N} --squash --subject "{title}" --body "{one-line summary, or empty}" --delete-branch
```
The `{title}` is the PR title (`[{category}] …`), which becomes the sole squash-commit subject on `develop`.

### Step 4b — local (`local`)
```
git switch develop && git pull --ff-only
git merge --squash {branch}
git commit -m "{title}"     # no co-author trailer
git branch -D {branch}
```
Do **not** push `develop` unless the human explicitly asks.

### Step 5 — Report
The PR URL (or local merge result), the squash subject that landed, retained `spec/{slug}/` artifacts,
and the issue-close caveat below.

## Notes

- **`Refs #NN` will not auto-close the issue:** GitHub auto-closes only on merge into the *default*
  branch, and this PR targets `develop` (the injected default branch tells you what it is). Use `Refs`
  (not `Closes`) and close the issue manually when it actually ships to `master`, or note this to the
  human.
- `spec/{slug}/` is **retained** on merge — the immutable dated design record ("build in the open").
  The PR body may label PLAN/research as historical.
- This skill never ships to `master` and never force-pushes. Version bumps / `master` releases are a
  separate concern (git-flow release), out of scope here.
