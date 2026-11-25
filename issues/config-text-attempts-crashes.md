---
status: adopted:bounded-inputs
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# A text `attempts = "3"` in a file crashes every command at import

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A configuration file holding `server.attempts` as a string makes every command fail at import with a traceback instead of a configuration error. Found by `config-set-and-unread`'s plan research.

## Direction

Read typed keys from a file by the key's declared kind, as the environment layer does since #113, and refuse a value that does not convert with exit 3 naming the key and file.

## Related

- `spec/env-config-keys/` (the typed reader); `spec/config-set-and-unread/research/`.
