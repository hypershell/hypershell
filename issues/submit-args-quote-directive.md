---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# A directive in a single quoted command argument is refused

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs submit` given a command as arguments joins them with `SubmitApp.quote_arg`
(`src/hypershell/submit.py:1829`, `:2031` on `develop` 7a1040bb), which wraps any argument holding
white space in double quotes (or single quotes when it holds a double quote). A whole command given
as one quoted argument, directive included, is therefore wrapped too, and the inline parser
(`Task.split_argline`, which finds the `# HYPERSHELL:` marker anywhere on the line, quotes included)
reads the closing quote as part of the last tag. Reproduced on a throwaway SQLite site
(`.agents/factory/bin/temp_site.sh`):

- `hs submit "echo e # HYPERSHELL: group:4"` exits 5: `Failed to parse inline tags (Tag value must
  only contain alphanumerics and basic symbols [+._-]: "group:4"", from: ""echo e # HYPERSHELL:
  group:4"")`.
- `hs submit "echo f # HYPERSHELL: color:red"` fails the same way; any directive given this way does.

The same directive split across arguments (`hs submit echo e '#' HYPERSHELL: group:4`) or in a task
file works. Found by `input-directive-fields`' research (2026-11-25); **pre-existing**.

## Outcome sought

A directive given inside a quoted command argument is read as it would be from a task file: the
command is the text before the marker, the tags are the text after it, and no quote added by the
joining reaches either. A command that needs its argument quoted for the shell still is.

## Notes

- `quote_arg` exists so an argument with spaces survives the join as one shell word; the fix must keep
  that for the command part (e.g. `hs submit python -c "print(1)"`).
- Shape against `input-directive-fields` (2.9.0), which rewrites how directive tokens are read.
- Related: `direct_args` and `scan_source`'s argument check (`submit.py`), `Task.split_argline`
  (`data/model.py`).
