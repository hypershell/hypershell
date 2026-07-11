---
name: hs-publish
description: >-
  Land an approved HyperShell feature branch on develop. Default: push the branch and open a squash PR
  to develop with a rich, artifact-linked body (Summary/Goal/Design/Research/Phases/Verification).
  Alternative (local): squash-merge into develop locally and delete the branch. Confirms before any
  irreversible/outward step; never targets master. Final step of the software factory.
disable-model-invocation: true
argument-hint: "[pr (default) | local] [merge]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Bash(git status *), Bash(git branch *), Bash(git log *), Bash(git diff *), Bash(git rev-parse *), Bash(git fetch *), Bash(git push *), Bash(git switch *), Bash(git checkout *), Bash(git merge *), Bash(git add *), Bash(git commit *), Bash(gh pr *), Bash(gh repo *)
---

# hs-publish — ship the branch to develop

## When to Use

Invoke `/hs-publish` once `/hs-review` has approved the branch (`TECH.md` `review.verdict: approved`).
This is the **one irreversible step** — remote pushes and PRs can't be checkpointed — so it always
confirms with you before acting. Default is a PR to `develop`; `local` does a local squash-merge.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Verdict / kind / slug (from TECH.md): !`b=$(git branch --show-current); s=${b#*/}; f="spec/$s/TECH.md"; echo "slug=$s"; awk '/^kind:/{print}' "$f" 2>/dev/null; awk '/verdict:/{print "verdict:",$2}' "$f" 2>/dev/null`
- Commits vs develop: !`git log --oneline develop..HEAD 2>/dev/null | head -n 30`
- Default remote branch: !`gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo "(gh unavailable)"`

## Argument Parsing

- `local` / `no fuss` → squash-merge into `develop` locally, delete the branch (no remote PR).
- `pr` (default) → push branch + open a PR to `develop`.
- `merge` → after opening the PR, also `gh pr merge --squash` (only with explicit confirmation).

## Safety Principles

- **Base is `develop`, never `master`.** Hotfixes to `master` are out of scope.
- **Require an approved review.** If `TECH.md` `review.verdict` is not `approved`, STOP and report —
  proceed only on explicit human override.
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
1. On a feature/fix branch, clean tree; resolve `{slug}`, `kind`, and the review `verdict` (injection).
   STOP if verdict ≠ `approved` (unless the human overrides).
2. `git fetch origin`. Confirm `develop` is reachable and note if the branch is behind `develop`
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
  - Issue: `Refs #NN` (see the auto-close note below).
  - Trailing line: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

### Step 3 — Confirm with the human
Present the mode (PR vs local), the title, and the body via AskUserQuestion. Do not proceed without a
choice.

### Step 4a — PR (default)
```
git push -u origin {branch}
gh pr create --base develop --head {branch} --title "{title}" --body-file <(...)
```
Report the PR URL. If `merge` was requested and confirmed: `gh pr merge --squash --delete-branch`.

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
