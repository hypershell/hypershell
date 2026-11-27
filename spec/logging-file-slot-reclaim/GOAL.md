# GOAL — Ephemeral log-lock sidecars + hardening (per-host log-file hygiene)

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** logging-file-slot-reclaim
- **kind:** fix
- **appetite:** small (→ small-medium)

> **Re-scoped 2026-07-23 after investigation.** The original premise — "the per-host log-slot
> scheme never reclaims the canonical path on Lustre/ZFS and proliferates files without bound" —
> was **refuted by ground-truth evidence**. `flock` acquire, conflict-detection, and
> **reclaim+append all work** on both Lustre and ZFS; the `client-<host>-N.log` files the
> maintainer saw were **legitimate concurrent clients** (one per rank under
> `--launcher=srun`, e.g. one client per GPU), plus a `SIGINT`/`SIGTERM` quirk in an external
> management script that was unknowingly overlapping client generations. See
> [`research/09-revised-design.md`](research/09-revised-design.md) (supersedes
> [`research/00-digest.md`](research/00-digest.md)). This GOAL now targets the *real* residual
> issues that the investigation surfaced.

## Problem

Per-host file logging works as designed: distributed roles get `<role>-<host>.log`, a single
writer is enforced by an advisory `flock` on a `<path>.lock` sidecar, and concurrent same-host
clients correctly fall through to `-2.log`, `-3.log`, … (bounded by peak concurrency). Three real
issues remain, none of which is a reclaim bug:

1. **The `.lock` sidecars are permanent and alarming.** They are 0-byte, carry no owner
   information, and are never removed — so a log directory accumulates `.lock` files that look
   like something is wrong and give no way to tell whether a *live* process actually holds a
   slot. Operators expect a lock file to be ephemeral: present while the owner runs, gone when it
   stops.
2. **A latent fd-inheritance lock leak for `server`/`cluster` roles.** `initialize_logging()`
   runs before any process creation, so the flock'd sidecar descriptor is open when
   `BaseManager.start()` (`core/queue.py`) forks the queue-manager child on Linux's default
   `fork` start method. The child inherits and *shares* that lock's open-file-description, so a
   `SIGKILL`/OOM'd parent can leave the slot **ghost-locked** until the child also dies. (Clients
   never fork a manager, so client slots are unaffected — consistent with the evidence.)
3. **A latent proliferation bug on a genuinely lockless filesystem.** `_try_lock` treats *every*
   `OSError` as contention, and both the no-locking and exhausted-slots branches fall back to a
   per-PID path (`<root>-<pid>.log`) — i.e. one file per process. On a filesystem that truly does
   not support `flock` (e.g. Lustre actually mounted `noflock`), this would proliferate. Not the
   maintainer's current symptom, but a real footgun to close while we are here.

## Outcome / vision

Lock sidecars behave like well-mannered PID/socket files: created while a client runs, carrying
the owner's identity, and removed when the client stops (best-effort on clean exit; swept on the
next start for crashes). No accumulation across clean restarts, and an operator can always tell
whether a real live process holds a slot. The `server`/`cluster` fd-leak is closed so a killed
parent never ghost-locks its slot. On a lockless filesystem the file count stays bounded instead
of growing per-process. Throughout, the behaviors that already work — reclaim+append on a healthy
FS, distinct files for genuinely-concurrent same-host clients, cross-host isolation — are
**preserved and regression-tested**, and legitimate `-N.log` data is **never** deleted or merged.

## Acceptance criteria (the contract)

- **R1** — The investigation finding SHALL be recorded (retained `research/`): the observed
  proliferation was legitimate same-host concurrency, not a reclaim/lock failure, and reclaim+
  append already works on Lustre and ZFS. Regression tests SHALL codify that correct behavior.
- **R2** — Each per-host lock sidecar SHALL carry its owner's identity (process id + process
  start-time + short hostname + app instance), written by the owner **while holding the advisory
  lock**, so any later process can determine whether a *live* process holds the slot (start-time
  defeats PID reuse; the baked-in hostname guarantees the PID is local and checkable).
- **R3** — WHEN a process shuts down cleanly, it SHALL remove its own lock sidecar(s) on a
  best-effort basis — after its file logging has stopped and **while still holding the lock** —
  so sidecars do not accumulate across clean restarts.
- **R4** — WHEN a process starts and resolves its log slot, it SHALL prune stale sibling lock
  sidecars (those with no live holder), **acquiring each sidecar's lock before removing it**, and
  SHALL NOT remove a sidecar that a live process holds.
- **R5** — The system SHALL NEVER delete, truncate, or rewrite `-N.log` data files or their
  rotated/compressed lineage. Concurrent-rank logs are legitimate output.
- **R6** — IF a distributed role forks a queue-manager child (`server`/`cluster`), THEN the
  inherited log-slot lock descriptor SHALL be closed in the child, so the lock releases when the
  true owner exits (no ghost lock from a killed parent).
- **R7** — WHEN acquiring a slot lock fails, the system SHALL advance to the next `-N` slot ONLY
  on genuine contention (`EAGAIN`/`EWOULDBLOCK`); IF advisory locking is unavailable (any other
  lock error, or no locking support), THEN it SHALL reuse the canonical per-host path (append)
  rather than a per-PID path, keeping the file count bounded.
- **R8** — The change SHALL preserve existing correct behavior: canonical reclaim+append on a
  healthy filesystem, cross-host file isolation (hostname in filename), the non-distributed
  `main` role, and the Windows (`msvcrt`) and no-locking fallbacks. The safety invariant **"only
  unlink a sidecar while holding its lock"** SHALL hold everywhere (no inode-reuse races).
- **R9** — The file-based-logging documentation SHALL note that (a) per-host `-N.log` files are
  expected under legitimate same-host concurrency, (b) sidecars are ephemeral and carry owner
  identity, and (c) revisiting a host at lower concurrency can leave earlier ranks' rotated
  leaves dangling — which is legitimate.

## Non-goals (no-gos)

- **Deleting, merging, or concatenating `-N.log` data** (explicitly rejected — legitimate logs).
- **Single-file-per-host** logging (one shared appending file for all same-host clients):
  correctness-breaking with rotation (`FILE_LOCK` is process-local → compress-under-writer data
  loss), risks torn lines on Lustre, and violates single-writer.
- **A PID-record "override a held `flock` to force reclaim" mechanism** — unnecessary; reclaim
  already works. The PID record is for liveness/diagnostics and safe pruning only.
- Fixing the external box-manager `SIGINT`/`SIGTERM` behavior that overlaps client generations
  (separate work the maintainer will do later).
- Cross-process rotation coordination; a new config knob; changing the role model or the
  hostname-in-filename scheme.

## Clarifications

- **Q:** Is the observed `-N.log` proliferation a reclaim bug? — **A:** No; it is legitimate
  same-host concurrency (confirmed by log-timestamp forensics + a ZFS restart-reclaim test);
  reclaim+append works on both filesystems (resolved 2026-07-23).
- **Q:** Delete `-N.log` orphans on reap? — **A:** No — never delete legitimate rank logs; the
  reap target is the `.lock` sidecar only (resolved 2026-07-23).
- **Q:** How to handle leftover `.lock` sidecars? — **A:** Ephemeral lifecycle: best-effort
  unlink at clean shutdown + flock-guarded prune at startup, with a PID+timestamp record
  (resolved 2026-07-23).
- **Q:** Fold in the latent errno/lockless-FS cleanup now? — **A:** Yes (R7) (resolved
  2026-07-23).

## Related materials

- Source: `src/hypershell/core/logging.py` (`_try_lock`, `claim_file_slot`, `resolve_log_path`,
  `_slot_locks`, `initialize_logging`, rotation/compression machinery) and `src/hypershell/core/
  queue.py` (`SecureManager`/`BaseManager.start()` fork seam, ~`:243-281`,`:385`).
- Observed on: Gautschi HPC — `/scratch` (Lustre), `/home` (ZFS); autoscaling + `--launcher=srun`
  one-client-per-GPU.
- Investigation: [`research/04`](research/04-evidence-forensics.md)–[`research/11`](research/11-stress-reap.md);
  synthesis [`research/09-revised-design.md`](research/09-revised-design.md).
