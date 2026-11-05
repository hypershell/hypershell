---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `--ssh` and autoscaling tell clients the host's own name, whatever `-H` binds

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs cluster` binds its server to `-H` and launches clients with a `-H` of their own, the address
they dial. What it tells the clients depends on the mode, on `origin/develop` (`02b292a`, 2.9.0a4):

- **Launcher and `--mpi` modes** (`RemoteCluster`) use the bind address unless it is `0.0.0.0`,
  and only then the host's own name: `target = HOSTNAME if bind[0] == '0.0.0.0' else bind[0]`
  (`src/hypershell/cluster/remote.py:309`, used at `:312`).
- **`--ssh` mode** (`SSHCluster`) always passes `HOSTNAME`
  (`src/hypershell/cluster/ssh.py:305`).
- **Autoscaling** (`AutoScalingCluster`) always passes `HOSTNAME`
  (`src/hypershell/cluster/remote.py:850`).

`HOSTNAME` is `socket.gethostname()` (`src/hypershell/core/logging.py:58`). In remote modes the
default bind `localhost` (`src/hypershell/core/config.py:130`) is widened to `0.0.0.0`
(`src/hypershell/cluster/__init__.py:300`, `:305`, `:315`, `:321`), so the common case agrees. It
breaks when a user binds one interface on purpose, such as a cluster's internal network address:
under `--ssh` or autoscaling the server listens only there, but the clients are told a name that
may resolve to another interface, or not resolve from the client hosts at all. Every client then
fails to connect, and the cluster waits with no error.

Nor is there a way to name the advertised address separately from the bind. A server bound to
`0.0.0.0` behind a name the clients must use, for instance the name on its TLS certificate, has no
option for it. That matters under TLS: with the default CA verification and no `servername`, the
client checks the certificate against the host it dialed
(`src/hypershell/core/queue.py:320-321`, `src/hypershell/core/tls.py:186-190`), so the advertised
name must be one the certificate covers.

## Why it was deferred

Found out of cycle (2026-11-05), not by a cycle's review. **Pre-existing:** the `RemoteCluster`
rule came with `893cb81` ("Host and port binding for clusters"), and it and the `HOSTNAME` in the
other modes are all in 2.8.0.

## Outcome / vision

One rule decides the address every launch mode advertises to its clients, and the user can
override it. By default it is the bind address when that names a single interface, and the host's
own name when the server binds every interface. An option (for example `--advertise ADDR`, with a
config key beside `server.bind`) sets it outright, for a name that differs from the bind address
or for a certificate's name. The same rule and option serve a future `hs relay`, which listens for
its own clients and has to tell them where to connect.

## Sketch of the acceptance criteria

- **R1** — WHEN `hs cluster` binds a specific address, every launch mode (launcher, `--mpi`,
  `--ssh`, autoscaling) SHALL pass clients that address, not the host's own name.
- **R2** — WHEN `hs cluster` binds every interface, every launch mode SHALL pass clients the
  host's own name, as today.
- **R3** — WHERE the user gives an advertised address, every launch mode SHALL pass clients that
  address, whatever the bind.
- **R4** — The option SHALL be documented beside `-H` in the cluster help and its
  `docs/_include/*.rst` snippet, with a note on TLS certificate names.

## Open questions for shaping

- **Option name and config key.** `--advertise` / `server.advertise`, or reuse `server.host`,
  which the config already describes as the address clients use (`src/hypershell/core/config.py:131`)?
- **Does the advertised address reach the task environment?** Tasks see `TASK_SERVER_HOST`
  (`docs/_include/config_task_env.rst:24`); shaping decides whether it should carry the advertised
  address.

## Notes

- Adjacent, not checked further: `hs client` takes its default `-H` from `server.bind`
  (`src/hypershell/client.py:1545`), not from `server.host`, though the config comments say `bind`
  is the server's and `host` the clients' (`src/hypershell/core/config.py:130-131`). Shaping should
  settle which key the advertised address and the client default share.
- Related: [`relay.md`](relay.md) (a relay advertises its own downstream address), and
  [`ipv6-server-listener.md`](ipv6-server-listener.md) (an IPv6 bind address would be advertised by
  the same rule).
- Found by: out-of-cycle review, 2026-11-05.
