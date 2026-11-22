---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# The cluster classes' `max_retries` defaults disagree with the server's

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The `LocalCluster`/`RemoteCluster`/`SSHCluster` constructors default `max_retries` differently from `ServerThread`, so the Python API and the commands can retry differently for the same configuration. Found by `config-set-and-unread`'s plan research.

## Direction

Use one default, derived from `server.attempts`, everywhere; pin it with a test of each constructor.

## Related

- `spec/config-set-and-unread/research/`.
