---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# A local `hs cluster`'s in-process client ignores `client.heartrate`

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Under a local `hs cluster`, the in-process client uses the built-in heartrate rather than `client.heartrate`; the reference names only `hs client` as the key's reader. Found by `config-set-and-unread`'s shaping.

## Direction

Pass the configured heartrate to the local cluster's client, or document that it applies only to launched clients.

## Related

- `spec/config-set-and-unread/GOAL.md` (Non-goals).
