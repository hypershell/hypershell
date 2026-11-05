# GOAL — The queue authentication key is never exposed to other users on client nodes

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** authkey-handling-hardening
- **kind:** fix
- **appetite:** small · a launcher-side hardening across the client-launching cluster modes, with
  regression tests and a docs revision; about two days of build and review. Builds on top of the
  zone cycles (see Clarifications).
- **lane:** security · promoted from a hidden-lane seed. The evidence stays in `.security/`
  (maintainers only); this file states the hardening outcome only.

## Problem

`hs cluster` generates a fresh, strong authentication key for every run and uses it to gate the task
queue: whoever holds the key can register as a client and exchange messages with the server. The
key's strength only protects the run if the key stays secret, and on HPC systems the nodes where
clients run are usually shared with other users. Today the key handling in the client-launching
cluster modes does not yet guarantee that, and `docs/security.rst` lists it as a known
limitation.

This is **pre-existing**: it is present in 2.8.x and in every 2.9.0 pre-release so far. It affects
only the modes where `hs cluster` starts clients as separate processes (`--ssh`, `--mpi`,
`--launcher`, and `--autoscaling`). The local mode runs its client in-process and is not affected.

The maintainers treat this as a high-priority hardening item with a small cost. The detail and the
evidence are in the hidden security lane.

## Outcome / vision

A client started by `hs cluster` on any node gets the run's key in a way that no other unprivileged
user on that node can read, for the whole life of the client. Clients still start, authenticate,
and run tasks exactly as before. The queue handshake and the wire format do not change, so mixed
versions keep working. The existing redaction of the key from log output stays in place. When the
change lands, the public security documentation describes the hardened behaviour instead of the
limitation.

## Acceptance criteria (the contract)

- **R1** — WHEN `hs cluster` starts clients as separate processes, in any of the `--ssh`, `--mpi`,
  `--launcher` or `--autoscaling` modes (both autoscaling policies), the run's authentication key
  SHALL NOT be readable by any other unprivileged user on a node where such a client runs, at any
  point during that client's life.
- **R2** — R1 SHALL hold in every client-launching mode named there. No mode may stay on the old
  handling. Each mode builds its client invocation separately, so each one is changed and tested
  on its own.
- **R3** — WHEN `hs cluster` runs in each mode named in R1, every client SHALL authenticate to the
  server and the run SHALL complete with every task's result recorded, as it does today. This holds
  with TLS on (the default) and with `--no-tls`.
- **R4** — The queue authentication handshake and wire format SHALL NOT change. A 2.9 server SHALL
  accept a 2.8.x or 2.9.0a* `hs client` that an operator starts by hand with the key, and a 2.9
  `hs client` SHALL connect to a 2.8.x or 2.9.0a* `hs server` the same way.
- **R5** — WHEN a 2.9 `hs cluster` starts clients whose installed HyperShell is 2.8.x or 2.9.0a*,
  those clients SHALL authenticate and run tasks with no change to their install or their
  configuration. IF the plan finds this cannot be met, THEN that finding SHALL go back to sign-off
  before build. It is not a build-time decision. (See Clarifications.)
- **R6** — The launchers SHALL keep redacting the key from all log output, at every level, as they
  do today.
- **R7** — Automated tests SHALL cover R1 for each launch mode in R2. Each test SHALL fail on the
  code before the fix and pass after it, and SHALL run with no special machine setup and no second
  user account.
- **R8** — In the same change, the known-limitation note about the key in `docs/security.rst` and the
  matching bullet in `AGENTS.md` § *Queue transport & security* SHALL be revised to describe the
  hardened behaviour. IF any user-facing option or help text changes, THEN the affected
  `docs/_include/*.rst` snippets and the `share/` completions SHALL change in the same commit.

## Non-goals (no-gos)

- Changing how an operator gives the key to an `hs server` or `hs client` they start by hand, or
  removing any existing option for doing so. Operator guidance for manual setups is a docs
  follow-up if the plan finds one is needed.
- Changing the queue protocol, the authentication handshake, or the key's format, length and
  character rules.
- Any other transport, TLS or key-at-rest hardening. Those items are tracked separately in the
  security lane.
- Changing how TLS material is located or propagated to launched clients.
- The local in-process cluster mode, apart from confirming it stays unaffected.
- Changes to `core/queue.py`. That file belongs to the zone queue-primitive cycle, and this
  hardening should not need it. IF the plan finds it does, THEN that goes back to sign-off.

## Clarifications

- **Q:** Does this overlap the zone cycles, and should it wait for them? — **A (proposed by the
  shaper, 2026-11-04):** Yes, it overlaps. The zone dispatch cycle (`feature/zone-dispatch`)
  changes how every launcher starts its clients (`cluster/remote.py`, `cluster/ssh.py`,
  `cluster/local.py`) and the `hs client` options in `client.py`, which are the same places this
  hardening changes. The zone queue-primitive cycle (`feature/zone-queue-primitive`) rewrites
  `core/queue.py`, which this hardening should not touch (Non-goals). The maintainer has ordered
  this cycle after both zone cycles. Assume both land on `develop` first. Before `/hs-plan`,
  rebase this branch onto the `develop` that contains them, and plan against the post-zone
  launchers, so that R1 to R3 also cover clients launched with `--zone`.
- **Q:** Must old clients and servers still interoperate? — **A (proposed, 2026-11-04):** Yes. The
  wire format and handshake stay the same (R4), and a new launcher must still bring up clients from
  an older install (R5). HPC sites often run a different HyperShell install on compute nodes from
  the one on the launch host. **Resolved (maintainer, 2026-11-05): split by line.** Zone dispatch (#78, in 2.9.0a4)
  makes a 2.9 server refuse any client older than its work-request protocol, so on the 2.9 line R4
  and R5 apply to 2.9.0a4-and-later peers only. On the 2.8.5 backport, the 2.8.5 launcher SHALL
  still bring up any 2.8.x client unchanged, and a 2.8.5 server and client SHALL interoperate with
  any 2.8.x peer. On both lines the authentication handshake and wire format stay unchanged.
- **Q:** Should this ship in a 2.8.x patch? — **A (proposed, 2026-11-04):** The cycle lands on
  `develop` first, as `fix/`, since hotfix branches are out of scope for `/hs-feature`. Because it is
  a pre-existing, high-ranked security item, the shaper recommends a backport to the next 2.8.x
  patch, the same way earlier 2.9 fixes were backported. That is only possible if the change does
  not depend on zone-only code. If the build is planned against the post-zone launchers, the
  backport needs its own small adaptation to the 2.8 launchers. **Resolved (maintainer, 2026-11-04): yes**, as 2.8.5. It is
  released together with the TCP_NODELAY backport (#76) and held until both are ready.
- **Q:** Appetite — **A (2026-11-04):** `small`, which fits the roughly two days of build and review
  the maintainer set. The seed was already recorded as small. If the plan finds that one launch mode
  needs a much larger mechanism than the others, the fallback is to close the exposure in the modes
  that fit the appetite and record the remaining mode as a new hidden-lane seed. It is not to
  stretch the cycle.
- **Q:** Where does the detail for planning live? — **A (2026-11-04):** In the hidden security lane
  only. `PLAN.md` and `TECH.md` describe the change as hardening and the fixed behaviour. They do
  do not characterise the behaviour before the fix beyond what `docs/security.rst` already says. The fix itself
  lands as an ordinary public commit and PR, following the `AGENTS.md` security-lane rule.

## Related materials

- Seed: the hidden security lane (`.security/issues/`, maintainers only), now marked
  `adopted:authkey-handling-hardening`. It is not published.
- `docs/security.rst` § *Authentication key* — the public statement of the current limitation.
  R8 revises it.
- `AGENTS.md` § *Queue transport & security* and § *High-risk files & footguns* (`cluster/remote.py`,
  `cluster/ssh.py`: each launch mode is built separately, and they must change together).
- Launchers: `src/hypershell/cluster/remote.py` (`RemoteCluster`, `AutoScalingCluster`),
  `src/hypershell/cluster/ssh.py` (`SSHCluster`), `src/hypershell/cluster/local.py` (unaffected,
  in-process). Client options: `src/hypershell/client.py`.
- Zone cycles that land first: `spec/zone-queue-primitive/`, `spec/zone-dispatch/` (on their
  branches until merged).
