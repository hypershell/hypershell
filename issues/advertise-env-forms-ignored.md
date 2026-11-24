---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# `hs cluster` and `hs relay` ignore the `_env` and `_eval` forms of `advertise`

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`ClusterApp.advertise_host` (`src/hypershell/cluster/__init__.py:353`) reads
`config.server.get('advertise')`, and `RelayApp.advertise_host` (`src/hypershell/relay/__init__.py:1277`)
reads `config.relay.get('advertise')`. A configuration that gives `advertise_env` or
`advertise_eval` instead puts that form in the merged table in place of `advertise`, so `.get`
returns nothing. The launched clients are then told the bind-rule address, and the configured one is
silently dropped. Found at plan time of `client-host-default-from-bind` (2026-11-23), whose new
client-side reader resolves these forms, so the two sides would disagree.

## Outcome sought

Every reader of `server.advertise` and `relay.advertise` honours the `_env` and `_eval` forms the
same way. An `_env` form naming an unset variable is refused with the key named, as the client-side
reader does.

## Notes

- Use the same form lookup as `config_seconds` and the client host helper (`core/config.py`), not a
  second copy.
- Related: `spec/client-host-default-from-bind/`, `spec/cluster-launch-failures/` (#107),
  `spec/relay-advertise/` (#116).
