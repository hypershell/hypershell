---
status: unshaped
kind: feature
appetite: big
lane: public
---

# The server cannot listen on IPv6

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`; appetite,
> non-goals and R-IDs are negotiated there. See the status vocabulary in
> [`templates/ISSUE.md`](../.agents/factory/templates/ISSUE.md).

## Problem

Both transports listen on IPv4 only. The TLS listener (`SecureListener`,
`src/hypershell/core/queue.py:139-154`) refuses any family other than `AF_INET` and opens an
`AF_INET` socket. The cleartext listener is the stock `multiprocessing` one, which treats a
`(host, port)` tuple as `AF_INET`. `find_available_ports` (`src/hypershell/core/config.py:340-351`)
also probes with `AF_INET`. A server cannot be reached on an IPv6-only host or network, and an IPv6
address given as the bind address fails.

## Why it was deferred

Named as a non-goal by `tls-client-address-family`, which brings the TLS client in line with the
IPv4-only listener rather than widening the listener. Dual-stack serving touches both transports,
port probing and the bind-address configuration, which is more than that fix's small appetite, and
it is a feature rather than a defect. **Pre-existing:** the server has been IPv4-only since the TLS
transport rewrite (`589f6fe`, 2.8.0) and, for cleartext, before it.

## Outcome / vision

A server can be bound to an IPv6 address, or dual-stack, and clients on either transport reach it by
address or by a host name that resolves over IPv6. The IPv4 default stays as it is.

## Sketch of the acceptance criteria

- **R1** — WHEN the server is given an IPv6 bind address, it SHALL listen on it over both TLS and
  cleartext.
- **R2** — WHEN a client is given a host that resolves only to IPv6, it SHALL reach a server listening
  there, on either transport.
- **R3** — The default bind and dial behaviour for IPv4 SHALL be unchanged.

## Notes

- Related: `spec/tls-client-address-family/` (the client-side alignment this would have to revisit:
  once the server can listen on IPv6, the client's IPv4-only dial needs to follow the listener's
  family).
- Found by: `tls-client-address-family` shaping (2026-11-03).
