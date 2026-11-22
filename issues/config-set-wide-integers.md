---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# `hs config set` writes integers wider than 64 bits to TOML

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

An integer value wider than 64 bits is written to the configuration file, which TOML readers then refuse or misread. Found by `config-set-and-unread`'s plan research.

## Direction

Refuse an integer outside TOML's 64-bit range in `hs config set` with exit 2 naming the key.

## Related

- `spec/config-set-and-unread/research/`.
