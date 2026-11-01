---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Any bare `#` silently truncates the submitted command

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Task.split_argline` (`src/hypershell/data/model.py:429-443`) first looks for an explicit
`# HYPERSHELL:` tag comment, then falls through to a quoting-blind fallback:

```python
elif match := re.search(r'#', args):
    args = args[:match.start()]
```

Every line submitted with inline-tag parsing is therefore truncated at the first `#` anywhere in
it, inside quotes or not:

- `echo 'issue #42'` → `echo 'issue` — an unbalanced quote
- `curl 'http://h/p#frag'` → the fragment is silently dropped
- awk/sed scripts, hex colour literals, and any URL fragment corrupt the same way

There is no error at submit time. The wrong command is stored, and the wrong command runs.

This contradicts HyperShell's own founding contract — *the shell line is the contract* — and it is
the single most dangerous class of bug available to a many-task engine: silent corruption at
ingest, where every downstream record faithfully reports success for work that was never what the
user asked for. The shell already implements comment semantics correctly; we are second-guessing
it with a regex that does not understand quoting.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H4), which was a
review pass, not a fix pass. **Pre-existing** in `develop` — not introduced by any recent work.

## Outcome / vision

A submitted line reaches the client byte-for-byte as the user wrote it, except for the explicit
`# HYPERSHELL:` tag marker the feature actually documents. `#` inside a command is data. Users
whose task files have been quietly mangled by this can find out.

## Sketch of the acceptance criteria

- **R1** — WHEN a submitted line contains a `#` that is not part of an explicit `# HYPERSHELL:`
  tag marker, the submitted command SHALL be stored and executed unmodified.
- **R2** — The `# HYPERSHELL:` tag-parsing behaviour SHALL be unchanged for lines that do carry
  the marker.
- **R3** — Tests SHALL cover `#` in single quotes, in double quotes, in a URL fragment, and
  immediately adjacent to the `# HYPERSHELL:` form, in both DB and queue submit modes.

## Notes

- Open question for `/hs-plan`: delete the bare-`#` branch outright (the shell owns comment
  semantics) versus making the fallback quote-aware. Deleting is the smaller, more defensible
  change; it is a **behaviour change** for anyone who has been relying on `#` as a task-file
  comment, so the release notes need a line and the docs need to state where comments are
  legal.
- Worth a short note in the shaped GOAL on auditing existing task files for lines this has been
  mangling — a user-facing consequence that outlives the fix.
- Related: `issues/ingest-robustness.md` (same ingest path, different failure mode).
- Found by: GNU Parallel comparison review, 2026-08 (H4).
