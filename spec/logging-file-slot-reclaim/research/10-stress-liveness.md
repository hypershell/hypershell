# Research 10 — Adversarial stress-test of the liveness / fd-leak recommendation

Attacks the recommended fix in [09](09-revised-design.md) §3 (= brief [06](06-liveness-design.md)
Option **B** + brief [05](05-process-model-audit.md) fork hardening + brief 07 pid-liveness reap).
Goal: break it. All line refs `src/hypershell/core/logging.py` unless noted; source is read-only.
The refuted ENOSYS theory is **not** relied on anywhere.

**Verdict up front:** the *spine* is sound — `psutil` pid+`create_time` record is the right
liveness oracle, flock-primary is right, C/D rightly rejected. But the **OVERRIDE branch** (B
step 3) and the **pid-only reaper** (07/09) as written contain **R7/R8 holes and an internal
contradiction** that must be amended before build. None is fatal to the approach; several are
required amendments.

---

## H1 — Torn-record read → OVERRIDE of a *live, still-initializing* sibling (R7 violation on a HEALTHY FS). **REQUIRED AMENDMENT.**

This is the sharpest hole. Brief 06 §Composition mandates the winner write its record **in place**:
`seek(0)+truncate()+write(record)+flush()`, **never `close()`** (close drops the flock, `:698`/`:710`).
It cannot use the atomic write-temp→`os.rename` trick the record section calls "atomically-written"
— renaming onto the sidecar makes a **new inode** and drops the flock. So the record write is
**genuinely non-atomic**, and the two claims in brief 06 (an "atomically-written JSON line" vs. an
in-place seek/truncate/write) are **mutually inconsistent**.

Now the race. Winner A must `flock`-win *before* it can know to write, so ordering is forced:
`open(non-trunc)` → `flock` OK → *(record still empty/stale here)* → write record. Between A's
flock-win and A's flush there is a window where the sidecar is **empty or half-written**. A
concurrent starter B probes slot 1, gets `EAGAIN` (A holds the kernel flock — healthy FS!), reads
the record → **empty/torn → parse-fail → "no live holder" → B takes the OVERRIDE branch (B step 3
dead/empty case) → B appends to the canonical `.log` while A also writes.** Two live writers on a
**healthy** filesystem — precisely the R7 property the recommendation claims to preserve and the
sole reason it rejects Option C. The window is small but is hit exactly under the concurrent-startup
storm autoscaling produces.

*Mitigation (required):* on `EAGAIN` with an **empty/torn/unparseable** record, **defer to flock →
advance to `-2`**, never override. Override only on a record that is **present and definitively
dead** (pid gone / `create_time` mismatch). Also make the write torn-read-resistant: single
`write()` of a fixed-width, newline-terminated, zero-padded record and have the reader retry-once on
parse failure. Verdict: **mitigable, but blocking** — ship without it and R7 breaks on ZFS/Lustre both.

## H2 — Internal contradiction: empty/legacy record ⇒ override *or* ⇒ advance? **MUST RESOLVE (toward advance).**

Brief 06 Option B **step 3** lists "dead/stale/**empty/legacy** ⇒ override," but brief 06
**Backward-compat** says "CONFLICT with a legacy sidecar (no record) ⇒ **defer to flock (advance)**."
These are opposite verdicts for the identical on-disk state (`EAGAIN` + unparseable sidecar), and
09 §2-Q2 inherits the ambiguity. H1 forces the resolution: **held-flock + empty/legacy ⇒ advance,
never override.** Cost: a legacy 0-byte orphan that is *also* a ghost-held flock will not be
reclaimed by the override path (it still gets reaped later by P2 once its owner reads as dead via a
*written* record — but legacy holders never wrote one, so those specific orphans persist until the
fleet finishes rolling to new code). Acceptable — mixed-version fleets are transient — but the plan
must state it, not paper over it.

## H3 — Concurrent OVERRIDE under a genuine ghost flock: race is NOT "confined to R5". **MITIGABLE; the doc's framing is WRONG.**

09 §3 and 06 §B claim the only residual race is "confined where GOAL R5 already permits bounded
interleave" (the `_LOCKING=False` / `OSError` regime). False. Consider a **real** ghost flock
(H-leak/H-stale) on slot 1 with a present-and-dead record. Two starters B1, B2 both probe slot 1 →
both `EAGAIN` (ghost holds it) → both read the same **dead** record → **both legitimately override →
both append to canonical.** Neither can take the kernel flock (the ghost owns the OFD), so there is
**no mutual exclusion among override-ers at all.** This is R7 violation in the regime "flock works
but a ghost holds it" — which is **not** R5's "locking unavailable." The kernel-closes-the-race
argument only covers the *acquire* path, never the *override* path.

*Mitigation:* the override path needs its own exclusion. Options: (a) override-ers `flock` a
**secondary** rendezvous (e.g. `<canonical>.log.reclaim.lock`) — the loser advances; (b) accept it
and re-label it R5-class interleave (honest, but then say so). Verdict: **mitigable**, but the
"confined to R5" claim must be struck.

## H4 — pid-only reap races a concurrent claimant → deletes a LIVE generation's files (inode-reuse). **REQUIRED AMENDMENT to the reap design.**

Brief 07/09 insists "reap on pid-liveness, **NEVER** on a flock probe" (because a ghost flock would
make the probe skip a genuine orphan). But a **pure-pid** reaper is blind to a *concurrent live
claimant*. Sequence: canonical-winner W scans `client-a306-2.*`, reads its record (owner dead),
decides delete. Meanwhile the dead owner's kernel flock has released, so a fresh starter N `flock`s
`-2.lock` (wins), writes its record, and (`delay=True`, `:377`) has not yet created `-2.log`. W now
`unlink`s `-2.log`/`-2.lock` out from under N. N holds a flock on an **unlinked** inode; a *third*
starter opening `-2.lock` makes a **new** inode + independent flock → two writers on `-2.log` — the
exact inode-reuse hazard Q2 warns about, now caused by the reaper deleting a `.lock` sidecar.

*Mitigation (required):* the reaper must **flock-guard each `-N` slot before deleting it** — try to
acquire that slot's own lock; reap only if acquired (⇒ no live *and* no ghost holder, owner truly
gone, safe), else skip. This is the *opposite* of "never flock-probe reap," and it is correct:
missing a ghost-held slot is **safe** (you just don't reclaim it this pass; a bounded miss), whereas
racing a live claimant is a **regression**. Ghost-held dead slots should be handled at the source by
P3 (fd-leak fix), not by deleting files under a held lock. Verdict: **mitigable, but the stated reap
rule must be inverted.**

## H5 — Windows `msvcrt` mandatory locking blocks the reader. **REQUIRED for R8.**

06 says B "keeps the `fcntl`/`msvcrt`/`_LOCKING=False` structure (R8)." But `msvcrt.locking(fd,
LK_NBLCK, 1)` (`:686`) takes a **mandatory** byte-range lock, and today it locks **byte 0** — where
the JSON record now lives. A reader (`open('r')`) on Windows can be **denied access to the locked
region**, so the liveness read fails exactly when a holder is live. `fcntl.flock` is advisory and
has no such problem; the recommendation's "keeps the msvcrt structure" is not free.

*Mitigation:* on Windows, lock a **high, reserved byte offset** (seek past the max record size, lock
1 byte there) and keep the record in the low, unlocked bytes so readers can always read it. Verdict:
**mitigable**, but R8 ("preserve the Windows fallback") is violated as specified.

## H6 — `AccessDenied` (reused pid owned by another user) ⇒ slot never reclaimed. **ACCEPTABLE; document.**

06 correctly treats `AccessDenied` as "alive, never steal." But on a **node-shared** allocation, a
dead owner's pid can be reused by *another user's* process: `psutil.pid_exists` → True,
`create_time()` → `AccessDenied` → "alive" → the canonical slot is **never reclaimed** and
proliferation continues. `host` guard doesn't help (same hostname). This is safe (never steals) but
defeats the fix on shared nodes. Verdict: **acceptable** on HPC exclusive-node norm (Gautschi jobs
are typically node-exclusive), but the plan must state the conservative failure mode.

## H7 — pid liveness across PID namespaces / containers. **ACCEPTABLE; document.**

The same-host invariant (`:654-657`) assumes all competitors share a **pid namespace**. Clients in
separate containers (separate namespaces) sharing a mounted log dir break pid-based liveness: a
recorded pid is meaningless in another namespace (`pid_exists` may hit an unrelated process).
`create_time`+`instance` mismatch mostly saves correctness (reads as dead → reclaim, which is at
worst an over-eager reclaim of a *live* container's slot → possible interleave). HyperShell's
MPI/Slurm launchers are not per-client-containerized, so out of practical scope. Verdict:
**acceptable**, note it.

## H8 — `create_time` / boot-id / reboot. **NOT A HOLE (spine is correct).**

The attack brief asked about `/proc/<pid>/stat` field-22 (since-boot jiffies, needs a boot-id).
Confirmed moot: `psutil>=7.0.0` is a **hard dep** (`pyproject.toml:37`, imported `resource.py:17`),
and `psutil.create_time()` returns a **wall-clock epoch** (Linux: `btime + starttime/HZ`; macOS
sysctl; Windows API) — it **folds boot_time in**, so a reboot changes the value and a reused pid
reads as a mismatch. **No boot-id needed** — 06's claim holds. One caveat: Linux `btime` can jitter
±1s and psutil precision changed across versions, so the `create_time` comparison **must use a
tolerance ≥ ~2s** (06 says "small tolerance" — pin it). A pid reused within ~2s with a near-identical
`create_time` is astronomically unlikely. Verdict: **not a hole**; pin the tolerance.

## H9 — Does the record actually reclaim under a *real* H-leak? **YES — but cost/benefit is misaligned.**

Mechanically the override works: the record stamps the **original owner's** pid (written by the
parent at claim time), and a lingering fork child that inherited the OFD (`queue.py` BaseManager
child, brief 05) has a **different** pid — so `pid_exists(original)=False` → dead → override. Good.
**But** brief 05 is decisive that H-leak affects **server/cluster only, never client**, and the
observed symptom is `client-<host>` proliferation whose leading cause is **H-conc**. So the OVERRIDE
branch — the machinery H1/H2/H3 show is the riskiest part — is aimed at a mode that *does not affect
the reported role*. Under the leading (H-conc) truth, the override branch is **never legitimately
exercised and is pure added race surface.** Verdict: **gate the override branch behind D0**
confirming H-leak/H-stale (09 §4 already makes D0 a gate — this reinforces it: if D0 shows H-conc,
**drop override entirely** and the fix collapses to flock-primary + flock-guarded reap + P3, which is
smaller and has none of H1/H3).

## Not-a-hole checks

- **P3 fork hardening** (close `_slot_locks` in the manager child via the `_tls_bootstrap` seam,
  `queue.py:273-281`, + a non-TLS initializer / `os.register_at_fork`): sound and cheap; `close()`
  in the post-fork child leaves the parent as sole OFD holder → flock releases on true owner exit.
  `os.set_inheritable(False)` would be useless (exec-only) — 05 is right.
- **psutil dependency**: no new dep, no EPEL floor issue — confirmed.
- **`:713` PID-suffix removal**: replacing it with canonical-append (R5 regime) genuinely removes a
  proliferation bug — correct.

---

## VERDICT

**The recommendation HOLDS in direction** (pid+`create_time` record via already-present psutil;
flock-primary; reject C/D; KEEP-slots; P3 fork hardening) **but does NOT hold as specified.**
Required amendments before build:

1. **Override only on a present-and-definitively-dead record.** Empty/torn/legacy under a held flock
   ⇒ **advance to `-2`**, never override (fixes H1 R7-break on healthy FS; resolves H2 contradiction).
2. **Make the record write torn-read-safe** (single fixed-width `write()`, reader retry-once);
   abandon the "atomic rename" language — impossible under a held flock.
3. **Invert the reap rule: flock-guard each `-N` slot before deleting it** (fixes H4 delete-vs-claim
   inode-reuse); accept bounded missed ghost-reaps, handle ghosts via P3.
4. **Windows: lock a reserved high byte offset**, keep the record in unlocked low bytes (fixes H5/R8).
5. **Gate the OVERRIDE branch behind D0**; if D0 shows H-conc (the leading hypothesis), drop override
   — it adds H1/H3 risk to fix a mode brief 05 says never hits the client (H9).
6. **Pin the `create_time` tolerance (~2s)**; document the `AccessDenied`-⇒-never-reclaim (H6) and
   pid-namespace (H7) conservative failure modes.

With 1–6 applied, Option B is safe and meets R2–R8. Without them, it can **break R7 on a healthy
filesystem** (H1/H3) and **regress R8 on Windows** (H5) — i.e. it can be *worse* than the status quo.
