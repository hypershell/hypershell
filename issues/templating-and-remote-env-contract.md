---
status: unshaped
kind: feature
appetite: small
lane: public
---

# Templating and remote-env contract: quoting, cwd, and a dry run

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

**Template substitution is an unquoted splice into a shell line (M12).** `{}` inserts arguments
verbatim (`core/template.py:151-153`) into a `shell=True` command (`client.py:759`): `-t 'process {}'`
with `my file.txt` word-splits, and `$`, backticks and globs are interpreted. There is no quoted
variant — no `shlex` anywhere in the template path. `{% ... %}` additionally substitutes `@`
unquoted into a per-task shell (`template.py:201`), and `{= =}`/slices are `eval`
(`template.py:193`, `:211`). The `eval` forms are powerful **by design** and stay; what is missing
is a safe option and an explicit statement of the contract.

**The remote cwd/env contract is implicit and fragile (M13).** Task cwd defaults to the *client's*
import-time `os.getcwd()` (`core/config.py:116`, used at `client.py:761`) — under SSH launch that is
typically `$HOME`, not where the user submitted. Only `HYPERSHELL_*` variables forward, opt-in
(`cluster/ssh.py:227`, `:272`, `:411`), and `compile_env` builds `KEY="value"` with **no shell
quoting** before `shlex.split` (`ssh.py:411`, `:272`) — a value containing spaces or `$` corrupts the
remote launch line. That last one is a plain bug, not a stance.

**No dry run (L3).** No way to preview template-expanded commands without writing rows — which is
exactly what would de-risk M12 for a user.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, M12 + M13 + L3) — a
review pass. All **pre-existing** in `develop`.

## Outcome / vision

A user who wants safe substitution has a quoted token and knows it exists; a user who wants the
current splice keeps it, documented as the arbitrary-code-execution surface it is. Environment
values with spaces survive an SSH launch. `--dry-run` prints what would run.

## Sketch of the acceptance criteria

- **R1** — A quoted substitution option SHALL exist (a quoted token or a submit/template flag) that
  makes an argument safe to splice into a shell line.
- **R2** — The existing unquoted `{}`, `{% %}` and `{= =}` semantics SHALL be unchanged by default,
  and the docs SHALL state plainly that they execute arbitrary code on clients.
- **R3** — `compile_env` SHALL quote values correctly so environment values containing spaces, `$`,
  or quotes survive the remote launch line.
- **R4** — A dry-run mode SHALL print the template-expanded commands that would be submitted,
  without writing task rows.

## Notes

- Deliberately **not** changing the default to quoted substitution: that would break every existing
  template that relies on splicing. Additive only.
- The `task.cwd = '<submit>'` sentinel (mirroring Parallel's `--wd .`) is the optional stretch — cut
  it first if the appetite is tight.
- R4 pairs naturally with `issues/ingest-robustness.md` (both submit-time surfaces).
- Found by: GNU Parallel comparison review, 2026-08 (M12, M13, L3).
