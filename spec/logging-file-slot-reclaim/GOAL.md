# GOAL — Reclaim per-host log-file slots on shared HPC filesystems

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** logging-file-slot-reclaim
- **kind:** fix
- **appetite:** small

## Problem

HyperShell's file-based logging gives long-running / distributed roles (`server`, `client`,
`cluster`, `submit`) a **per-host** log file — a client on host `a123.cluster.edu` logs to
`client-a123.log` — and enforces a single-writer invariant with an advisory `flock` on a
per-file `.lock` sidecar. When a slot is contended, the process falls through to
`client-a123-2.log`, `client-a123-3.log`, …, so the file count is meant to be bounded by *peak
concurrency on a host*, not by the *total number of processes ever launched* (which matters
under autoscaling, where a host cycles through many short-lived client generations).

In real-world use on our Gautschi HPC cluster this is not what happens. On both `/home` (ZFS)
and `/scratch` (Lustre), the canonical per-host path is **never reclaimed**: successive client
generations keep falling through to fresh `-N` slots, and the `.lock` sidecars and `-N.log`
files **proliferate without bound** — exactly the "one file per process ever launched" failure
the slot scheme was designed to prevent. The lock is either never released or never seen as
released across the network filesystem. It is not yet confirmed whether this reproduces on a
local filesystem; the code comment's assumption that "same-host advisory locks are reliable
even on shared network filesystems" is the prime suspect (Lustre commonly needs an explicit
`flock` mount option; without it `flock` may error or be node-local, and `_try_lock` treats a
lock **error** identically to a lock **conflict**, forcing every process onto a new slot).

The concrete pain: an autoscaling client fleet running for hours or days silently fills the log
directory with thousands of near-empty `client-<host>-<N>.log` and `.lock` files, on precisely
the shared filesystems (ZFS/Lustre) HPC users are told to use.

## Outcome / vision

On the filesystems HyperShell actually runs on in production — including Lustre and ZFS — the
per-host log-slot scheme behaves as designed: a freed canonical per-host path is **reclaimed**
by the next generation, so a host that runs one client at a time trends toward **a single log
file per host** rather than an ever-growing pile. The file count stays bounded by real
concurrency, not by cumulative launches. The root cause is understood and documented (not just
patched), and the artifacts that have already accumulated (orphaned `.lock` sidecars and stale
`-N.log` slots whose owning process is gone) are reaped. The single-writer safety intent is
preserved to whatever degree the underlying filesystem permits, with the trade-off made
deliberately rather than by accident.

## Acceptance criteria (the contract)

- **R1** — The investigation SHALL identify and document (in `TECH.md`) the root cause of
  non-reclamation and unbounded proliferation on Lustre and ZFS, including whether it
  reproduces on a local POSIX filesystem, before a fix is committed.
- **R2** — WHEN a client process starts on a host and no live process holds the canonical
  per-host log path, the logging subsystem SHALL reclaim that canonical path (e.g.
  `client-<host>.log`) rather than falling through to a new `-N` slot.
- **R3** — WHEN a canonical per-host path is reclaimed under R2, the logging subsystem SHALL
  **append** to the existing file, preserving prior generations' log history (subject to normal
  rotation), and SHALL NOT truncate it.
- **R4** — WHILE multiple client generations are launched serially on one host over time (the
  autoscaling case), the number of `*.log` and `*.lock` files for that host SHALL remain
  bounded by peak concurrent processes on the host, NOT by the cumulative count of processes
  ever launched.
- **R5** — IF advisory locking is unavailable or unreliable on the target filesystem, THEN the
  logging subsystem SHALL still keep the per-host file count bounded (it SHALL NOT degenerate to
  one file per process). *The specific mechanism — graceful degradation to a reused per-host
  path vs. a bounded, self-reaping distinct-file scheme — is deferred to `/hs-plan` once the
  root cause is confirmed.*
- **R6** — The fix SHALL reap orphaned artifacts: stale `.lock` sidecars and `-N.log` slots
  whose owning process is no longer alive SHALL be reclaimable/removable rather than reserved
  forever.
- **R7** — The single-writer intent SHALL be honored wherever the filesystem supports it: two
  genuinely concurrent same-host processes SHALL NOT silently corrupt each other's log via
  interleaved writes on a filesystem where locking works.
- **R8** — The change SHALL NOT regress the cross-host safety property (distinct hosts keep
  distinct files via the baked-in hostname) nor the non-distributed `main`-role default, and
  SHALL preserve the existing Windows (`msvcrt`) and no-locking (`_LOCKING = False`) fallbacks.

## Non-goals (no-gos)

- Redesigning the logging architecture beyond the per-host file-slot / lock scheme (no new log
  backends, no change to the queue-based handler, rotation policy, or log formats).
- Changing the role model (`DISTRIBUTED_ROLES`, `role_from_command`, `main`-role sharing) or the
  choice to bake the short hostname into the filename.
- Fixing HPC filesystem mount configuration (e.g. mandating a Lustre `flock` mount option) —
  HyperShell must behave acceptably regardless, but we do not own the site's mount options.
- A background reaper daemon or cross-run garbage-collection service; orphan reaping (R6) is
  expected to happen opportunistically at slot-claim time, not as a separate long-lived process.
- Distributed coordination of a shared log across hosts (each host remains independent).

## Clarifications

- **Q:** On reclaiming a freed canonical per-host path, append or truncate? — **A:** Append,
  preserving history (rotation still bounds size) (resolved 2026-07-23).
- **Q:** When advisory locking is unsupported/unreliable on the filesystem, degrade to a reused
  per-host path (accepting possible interleave) or preserve strict single-writer with bounded
  distinct files? — **A:** Require only that proliferation stays bounded (R5); defer the
  mechanism choice to `/hs-plan` after the root cause is confirmed (resolved 2026-07-23).
- **Q:** Reap already-accumulated orphaned `.lock` and `-N.log` files as part of this fix? —
  **A:** Yes, in scope (R6) (resolved 2026-07-23).

## Related materials

- Source: `src/hypershell/core/logging.py` — `claim_file_slot`, `_try_lock`, `resolve_log_path`,
  `default_file_for`, `role_from_command` (~lines 640–723); `FILE_LOCK` rotation lock (~line 355).
- Observed on: Gautschi HPC cluster — `/home` (ZFS), `/scratch` (Lustre).
- Context: autoscaling client fleets (`cluster/remote.py` `AutoScalingCluster`) cycle many
  short-lived client generations per host — the workload that makes non-reclamation visible.
