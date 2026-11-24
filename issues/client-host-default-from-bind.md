---
status: adopted:client-host-default-from-bind
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# `hs client` dials `server.bind`, not `server.host`

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The configuration has two server addresses with documented, separate jobs
(`docs/_include/config_param_ref.rst`, `[server]`):

- `.bind` is the address the server listens on;
- `.host` is "the address that `client` instances and queue-direct `submit --queue` dial to reach
  the server".

`hs client` ignores `.host`. Its `-H/--host` default is `config.server.bind`
(`src/hypershell/client.py:1657`), and `run()` dials `(self.host, self.port)` (`:1747`).
`hs submit --queue` does it right (`submit.py:1614`, `config.server.host`).

The documented remote setup therefore fails. A persistent server on a login node, with `bind =
"0.0.0.0"` and `host = "login00.example.edu"` in a shared config file, and clients started from
batch jobs reading that same file: every client dials `0.0.0.0`, which is its own node, and gets
`ConnectionRefusedError`. Reproduced on `develop` (5897e5ab) by the reviewer of the RCAC guide, with
`ClientApp.host == '0.0.0.0'`. A config with only `bind` set to a real hostname works by accident,
which is probably why it went unnoticed since 2.8.

`hsx`/`hs cluster` is unaffected for the clients it launches: it passes `-H` explicitly, worked out
from `.advertise`, the bind address, or the host's own name (`cluster/launch.py:18`).

What it costs: tier 3 of the RCAC user guide (a server on login00, client jobs submitted by a helper
script) cannot run without passing `-H` by hand on every client, and the guide's `server.host`
row describes a setting the client never reads.

## Outcome / vision

A client started with no `-H` dials `server.host`, as the configuration reference says; `-H` still
overrides it.

## Sketch of the acceptance criteria

- **R1** — `hs client`'s `--host` default SHALL be `server.host`.
- **R2** — No other client-side dial path SHALL default to `server.bind`. Check
  `core/queue.py:1089` (`host: str = default.server.bind`) beside `:1107`, which uses
  `server.host`.
- **R3** — A test SHALL load a config with `bind = "0.0.0.0"` and `host` set to another name and
  assert the client's default host is that name; and one SHALL assert `-H` still wins.
- **R4** — The 2.9.0 release notes SHALL name the change, for anyone whose working setup relied on
  `bind` being dialled (a config that sets `bind` to a real hostname and leaves `host` at its
  `localhost` default would now dial localhost).

## Notes

- R4 is the compatibility question for shaping: either accept the break with a release note, or
  fall back to `bind` when `host` is unset and `bind` is one specific address (the rule `cluster`
  already uses for `advertise`).
- Found by the skeptic review of the RCAC Workflow Automation guide (RCAC-Docs PR #338),
  2026-11-23. The guide passes `-H` explicitly until this lands.
