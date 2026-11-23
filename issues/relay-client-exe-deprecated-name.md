---
status: unshaped
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# The relay starts its clients as the deprecated `hyper-shell`

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A relay launches every site client as the literal command `hyper-shell client …`:
`relay/site.py` (`zone_launch`, line 181, which `site_launch` wraps) calls
`client_launch(DEFAULT_REMOTE_EXE, …)`, and `DEFAULT_REMOTE_EXE` is `'hyper-shell'`
(`cluster/remote.py:54`). The relay has no `--remote-exe` option and no configuration key to
change it.

`hyper-shell` is the deprecated entry point. Installations that expose only `hs` and `hsx` (RCAC's
modules link `bin/hs` and `bin/hsx` to `libexec/bin/`, and put only `bin/` on `PATH`) have no
`hyper-shell` on `PATH`, so every relay launch fails on the compute node with command-not-found.
Checked 2026-11-23 on Gautschi: `module load hypershell/develop` puts `…/develop/bin` on `PATH`,
which holds `hs` and `hsx`; `command -v hyper-shell` finds nothing; `libexec/bin/hyper-shell`
exists but is not reachable by name.

`hs cluster` (`hsx`) does not have this problem: its `--remote-exe` defaults to
`sys.argv[0].rstrip('x')` (`cluster/__init__.py:212`), the path of the `hs` the user ran, so a
symlinked `bin/hs` works. The relay is newer code (2.9.0a6) and took the constant instead.

What it costs: tier 4 of the RCAC user guide (a server on Geddes with relays on each cluster's
login00) cannot run on RCAC's module layout until this is fixed, and the guide's merge is held on
it.

## Outcome / vision

A relay starts its clients the same way `hs cluster` does: with the `hs` of the installation that
runs the relay, by its full path, and never by the deprecated name; with a way to override it for
sites where clients must run a different installation.

## Sketch of the acceptance criteria

- **R1** — By default, a relay SHALL launch site clients as `<the relay's own hs> client …`,
  resolved the way `hs cluster` resolves `--remote-exe` (the path of the running program), not as
  `hyper-shell`.
- **R2** — `hs relay` SHALL accept `--remote-exe PATH` and a `relay.remote_exe` configuration key
  that override the default, with the CLI over the key.
- **R3** — No launch path in HyperShell SHALL default to the name `hyper-shell`;
  `DEFAULT_REMOTE_EXE` is retired or changed to match R1.
- **R4** — A test SHALL start a relay from a symlinked `bin/hs` whose directory holds no
  `hyper-shell`, and assert that its launch command names that `hs` and that a client starts.

## Notes

- Check every caller of `client_launch` and `DEFAULT_REMOTE_EXE` (`cluster/remote.py` SSH and
  autoscaling paths as well as `relay/site.py`), so the fix lands once.
- There are two constants, not one: `cluster/ssh.py:42` defines its own `DEFAULT_REMOTE_EXE =
  'hyper-shell'`, exported from `hypershell.cluster`. The Python API's `SSHCluster` and the remote
  and autoscaling cluster classes default `remote_exe` to them (`ssh.py:248`, `remote.py:259`,
  `:964`), so changing the default changes the Python API as well as the relay. Shaping decides
  whether the API keeps the name.
- With `--ssh`, the remote host may lay out the installation differently; keep `--remote-exe` as
  the escape hatch there, as today.
- Found while drafting the RCAC Workflow Automation guide's tier 4 (RCAC-Docs PR #338), 2026-11-23.
  Related: `issues/autoscaling-cluster-ignores-usr1.md` (found in the same work).
