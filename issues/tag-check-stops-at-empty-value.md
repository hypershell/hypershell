---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# The tag check stops at the first tag with an empty value

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Task.ensure_valid_tag` (`src/hypershell/data/model.py:866` on `develop` 674f6d85) walks the tags in
order. When a value is an empty string, a naked tag, it `return`s rather than `continue`s, so no tag
after it is checked: neither its key nor its value. The same function backs every strict tag path:
`hs submit -t` (`SubmitApp.check_tags`), inline `# HYPERSHELL:` tags (`Task.split_argline`, through
`Task.new`), and `hs update KEY:VALUE` (`TaskUpdateApp`). A bad tag is refused when it comes first and
stored when a naked tag comes before it.

Reproduced on a throwaway SQLite site (`.agents/factory/bin/temp_site.sh`), one task per file:

- `hs submit -t other:a/b -f tasks.in` exits 2: `Tag value must only contain alphanumerics and basic
  symbols [+._-]: "other:a/b"`.
- `hs submit -t naked other:a/b -f tasks.in` exits 0 and stores `{"naked": "", "other": "a/b"}`.
- `hs submit -t naked 'bad!key:x' -f tasks.in` passes the tag check too: a bad key after a naked tag.
- A task file line `echo two # HYPERSHELL: other:a/b` is refused (exit 5, `Failed to parse inline
  tags`); `echo three # HYPERSHELL: naked other:a/b` is submitted with both tags.

`hs update naked: other:a/b` reaches the same check with `{"naked": "", "other": "a/b"}` (read from
the code, not run). Found by `update-typed-columns`' research (2026-11-24); **pre-existing**.

## Outcome sought

Every tag is checked by the same rules, whatever its position: a naked tag ends nothing, and the
first bad tag anywhere in the set is refused as it is when it comes first.

## Notes

- `update-typed-columns` works around this in `hs update` without editing `model.py`: it checks each
  tag alone (`spec/update-typed-columns/PLAN.md`, D11). Once it lands, `hs submit` is the path left
  exposed; the fix in `ensure_valid_tag` makes that workaround unnecessary.
- Refusing a tag accepted today is an interface change, which shaping weighs against the Target.
- Related: `Tag.from_cmdline` (`core/tag.py`, a token with no `:` is a naked tag),
  `read_tag_options` (`submit.py`).
