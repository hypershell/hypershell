---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# An empty `logging.style` makes every command exit 3 until the file is hand-edited

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs config set logging.style ''` writes an empty style, after which every command, `hs config` included, exits 3, so the only way out is to edit the file by hand. Found by `config-set-and-unread`'s shaping.

## Direction

Either refuse an empty or unknown style in `hs config set` (it takes a fixed word), or let `hs config` itself start with a bad logging style so the user can fix it.

## Related

- `spec/config-set-and-unread/GOAL.md` (Non-goals).
