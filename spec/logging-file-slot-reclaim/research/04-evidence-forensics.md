# Research 04 — Evidence forensics & hypothesis disambiguation

Scope: reconcile the committed (now-**refuted**) ENOSYS/exhaustion diagnosis with the
maintainer's real on-disk evidence from Gautschi, decide which hypotheses the evidence
*excludes* vs *permits*, and hand the maintainer a short list of decisive, low-effort
diagnostics. All line refs are `src/hypershell/core/logging.py` unless noted. **The source is
read-only; this brief changes no behavior.** Do **not** resurrect the ENOSYS theory — the disk
evidence refutes it (§2).

## 0. The evidence (verbatim on-disk signature)

After a short (~few minute) autoscaling run, on **both** `/scratch` (Lustre) and `/home`
("POSIX-ish" ZFS, `~/.hypershell/log`):

```
client-a261.log      client-a261.log.lock
client-a306.log      client-a306.log.lock
client-a306-2.log    client-a306-2.log.lock
cluster-login01.log  cluster-login01.log.lock
main.log             main.log.lock
submit-a068.log      submit-a068.log.lock
```

Maintainer's five facts: (i) **no** PID-suffixed files; (ii) **no** ~100-`.lock` pile;
(iii) **no** "Exhausted … slots" warning; (iv) a **sequential** `-2` slot on a306; (v) the
process that got `client-a306.log` **won slot-1's flock** (clean canonical name, no PID);
(vi) behavior is **identical** on Lustre and ZFS. Plus a longitudinal claim: over *longer*
runs the `-N.log`/`.lock` set "proliferates" and slot 1 is "never reclaimed."

## 1. What each artifact *proves* (decode against the code)

The claim loop (`claim_file_slot`, `:701-713`) starts every process at `n=1`, calls
`_try_lock(candidate + '.lock')` (`:708`), and returns the **first candidate whose flock
succeeds**. The `.log` itself is opened lazily (`delay=True`, `:377`) **only for the returned
(won) path** — a probed-but-lost slot ever only gets a `.lock`, never a `.log` (`research/02 §1`).

Therefore, mechanically:

- **`client-a306.log` exists with a clean (no-PID) name** ⇒ some process's
  `_try_lock('client-a306.log.lock')` **returned a handle** ⇒ `fcntl.flock(LOCK_EX|LOCK_NB)`
  **succeeded** on this filesystem. flock **acquire works** on both Lustre and ZFS. (Fact v.)
- **`client-a306-2.log` exists as a real `.log` (not just a `.lock`)** ⇒ slot 2 was **won and
  written**, which only happens after `_try_lock` on slot 1 returned `None` (a real CONFLICT →
  `BlockingIOError`/`EAGAIN`, `:675`) *and then* slot 2's flock **succeeded**. So the code both
  **detects contention** and **acquires the next slot** correctly here.
- **No `client-a306-<pid>.log`** ⇒ the PID-suffix branch (`:712-713`) was **never taken** ⇒
  neither genuine 100-way exhaustion nor the `_LOCKING=False` path fired.
- **No 100-`.lock` pile** (`client-a306-2.log.lock … -100.log.lock`) ⇒ the loop **never walked
  past a couple of slots**. Every failed probe would `open(path,'w')` and thereby *create* a
  sidecar (`:671`, `research/02 §4`), so a full 1..100 walk would leave exactly 100 sidecars.
  We see 2. The loop stopped at 2 because slot 2 was **won**, not errored.
- **No "Exhausted … slots" warning** (`:712`) ⇒ the loop terminated by *winning* a slot, never
  by falling off the end.

These five deductions are mutually reinforcing and all point one way: **flock ACQUIRE and
CONFLICT-detection are functioning on both filesystems.** That single conclusion is what
demolishes the committed diagnosis.

## 2. Hypothesis matrix — consistent vs excluded

| Hypothesis | Verdict vs the `ls` | Why (what it would leave on disk) |
|---|---|---|
| **refuted-ENOSYS** (every flock errors → exhaust 100 → PID suffix) | **EXCLUDED** | Predicts PID-suffixed logs, a fixed **100** `.lock` sidecars, an "Exhausted" warning **per launch**, and **no** clean canonical `.log` (the canonical name is never *returned*, so its `delay=True` `.log` is never created). The disk shows the exact opposite on all four counts (facts i–iii, v). Contradicted, not merely unsupported. |
| **H-conc** (≥2 genuinely concurrent client processes on a306) | **CONSISTENT — leading** | Working flock + a live slot-1 holder ⇒ a concurrent peer gets `EAGAIN` on slot 1 and correctly wins `-2` (**R7 behaving as designed**). Produces precisely {clean canonical + one sequential `-2`, no PID, no 100-pile, no warning}. FS-independent (needs only acquire+conflict, which fact v confirms work). a261 = 1 rank, a306 = 2 ranks explains the asymmetry directly. |
| **H-leak** (an inherited flock fd outlives its owner) | **CONSISTENT on disk, but code-grounded UNLIKELY on the client** | Same disk signature as H-conc (a stale fd looks like a live sibling → next gen pushed to `-2`, slot 1 "never reclaimed"), and FS-independent. **But** the client's own code has *no fork-without-exec vector* (§3): a remote `hyper-shell client` only **connects** to the queue (`client.py:1219`; `QueueClient.connect`→`BaseManager.connect`, `core/queue.py:421-427`) — it never `.start()`s a manager, and every child it spawns **execs** (`Popen(shell=True)`, `client.py:772`, default `close_fds=True`) so the CLOEXEC sidecar fd is dropped. Kept live because a site launcher/wrapper or a not-yet-found `multiprocessing.Process` could still inherit it; diagnostic (d) settles it. |
| **H-stale** (network-FS lock release not *seen* as released) | **CONSISTENT on Lustre only; EXCLUDED as the *common* cause** | Would give the same sequential-`-N` picture, but it is intrinsically **FS-semantic** (NFS/NLM release-visibility lag; NFSv4 lock recovery — `research/01`). Fact (vi): identical behavior on a **local-POSIX ZFS**, where a dead process's flock is dropped *immediately and coherently*, means staleness cannot be the mechanism on the ZFS half. A single cause that fits both halves cannot be release-staleness. (Caveat below.) |
| **H-bug** (some other reclaim bug in our code) | **CONSISTENT (weak) — catch-all** | The reclaim loop restarts at `n=1` and returns the first lockable slot, so with working flock + a dead prior owner it *does* reclaim (proven by `test_slot_reclaimed_after_owner_dies`, `tests/test_logging.py:91-106`). No obvious defect; retained only as the residue if (a)–(e) exonerate conc/leak/stale. |

### The FS-independence argument (made explicit)

refuted-ENOSYS and H-stale are the only **filesystem-semantic** hypotheses: each depends on a
*specific* FS's flock implementation (Lustre `noflock`→ENOSYS; NFS→ENOLCK/NLM staleness). The
maintainer reports an **identical** signature on two filesystems with **different** flock
implementations — a network Lustre and a "POSIX-ish" ZFS. A common cause therefore cannot be a
property of either one's flock quirk; it must live *above* the FS layer, in the
**process/lock lifecycle** — i.e. H-conc or H-leak (or H-bug), all of which need only that
flock *acquire* + *conflict-detect* work, which fact (v) independently confirms they do.

**Caveat that gates this deduction (→ diagnostic e).** The argument assumes `/home` ZFS has
*working, local* flock. If `/home` turns out to be **NFS-exported** ZFS (as `research/01`
speculated), then it too is a network FS and H-stale re-enters for *both* halves. Confirming
the ZFS mount (local dataset vs NFS export, and its `flock`/`local_lock` options) is a
**prerequisite** for trusting FS-independence. The maintainer's own framing ("POSIX-ish ZFS"
vs "network Lustre") suggests local, but this must be verified, not assumed.

### The one datum that splits the surviving field

The snapshot alone **cannot** separate H-conc, H-leak, and H-stale — all three yield
{clean canonical + sequential `-2`, no PID, no pile, no warning}. The decisive question is
**longitudinal**: do `-N` files stay **bounded by peak concurrency** (⇒ H-conc + a cleanup
gap: working-as-designed, files merely never consolidated/reaped) or do they **grow past** any
plausible concurrency on a node that runs few clients at once (⇒ a genuine **reclaim failure** =
H-leak or H-stale)? Diagnostics (a)–(d) answer exactly this.

## 3. Why H-leak is code-grounded *unlikely on the client* (but not dismissed)

- `initialize_logging` (`:730`) runs from `main()` at `__init__.py:139`, opening the flock'd
  sidecar fd **before** any thread/subprocess exists — the necessary precondition for an
  inheritance leak.
- **BUT** the fd is non-inheritable: `open(path,'w')` (`:671`) sets `O_CLOEXEC` by default
  (PEP 446), and there is **no** `os.set_inheritable`/`close_fds`/CLOEXEC override anywhere in
  `src/hypershell/` (grep: none). So any child that **execs** loses it: task subprocesses
  (`Popen(shell=True)`, default `close_fds=True`, `client.py:772`) and the site launcher
  (`srun`/`mpirun`, exec'd) all drop it.
- The only fork-**without**-exec in the package is `QueueServer.start()` →
  `BaseManager.start()` (`core/queue.py:373-385`), which spawns a manager process (on Linux,
  `fork` start-method inherits fds *despite* CLOEXEC, since no exec occurs). **That is the
  server/cluster side** and would inherit the *server/cluster* role's fd — not a client slot.
  A remote client (`client.py:1219`) only **connects** and drives daemon **threads**
  (`.start()` at `client.py:1263-1267` are `Thread`s, not `Process`es) — **no client-side
  fork**. Hence no obvious mechanism to leak `client-a306.log.lock` into a surviving process.

Conclusion: H-leak fits the disk and the FS-independence, but the client code as written does
not supply the fd-inheritance path, so it drops below H-conc in priority. Diagnostic (d)
(`lsof`/`fuser` on the sidecar of a *dead* client) is a direct yes/no test and cheap — run it
regardless, because it is the *only* hypothesis whose confirmation would change the fix
(reclaim can't work while a zombie fd pins the lock).

## 4. Decisive, low-effort diagnostics (ordered)

Run in this order; each is a few minutes and most are one-liners. For each: what result
confirms/denies which hypothesis.

**(a) Timestamp overlap between `client-a306.log` and `client-a306-2.log`** *(most decisive)*.
Compare the **first and last** log-record timestamps inside each file (the `%(asctime)s` field;
`head -1` / `tail -1`, or `sort` the datetime column).
  - **OVERLAPPING** time ranges (both files have records in the same wall-clock window) ⇒ two
    clients were **alive simultaneously** ⇒ **H-conc / working-as-designed**; the only bug is
    non-consolidation + non-reap.
  - **DISJOINT / strictly sequential** (a306-2 begins only after a306's last record — or worse,
    a306 stops early and every later generation writes to a306-2) ⇒ **reclaim failure**
    (H-leak or H-stale), because a *serial* successor should have re-won slot 1, not slot 2.

**(b) Peak client concurrency per node the autoscaler actually launches.** Inspect the launcher
argv / autoscaler config: `srun`'s `--ntasks-per-node` / `mpirun -n` mapping, and
`AutoScalingCluster` growth (`cluster/remote.py:533` `Popen(self.launcher)`; launcher built at
`remote.py:832-836`). Cross-check against the **max `-N`** seen for any host.
  - max `-N` **==** peak concurrent ranks/generations on that node ⇒ **H-conc** (bounded,
    correct-by-design).
  - max `-N` **≫** any plausible concurrency (e.g. `-7` on a node that runs ≤2 at once) ⇒
    **reclaim failure** (H-leak/H-stale/H-bug).

**(c) Two-serial-process flock reclaim probe, on BOTH Lustre and ZFS.** A ~10-line script:
proc-1 `flock`s `probe.lock` (`LOCK_EX|LOCK_NB`), prints "held", **exits**; then proc-2
`flock`s the *same* path and prints whether it acquired. Run once under `/scratch`, once under
`/home`.
  - gen-2 **acquires** (reclaims) on both ⇒ raw flock reclaim is healthy ⇒ the failure is
    **above** the FS (H-conc or a leak/bug *in our process model*), not the FS itself.
  - gen-2 **fails/blocks** on Lustre but **succeeds** on ZFS ⇒ **H-stale** on the Lustre half,
    and the ZFS half needs a *different* explanation (H-conc/H-leak) — i.e. two causes.
  - gen-2 fails on **both** ⇒ re-open the FS-semantic angle for both (and re-check mount, (e)).

**(d) After a client exits, is its `.lock` still flock-held by a *live* pid?** On the compute
node, once a client that owned `client-a306.log` has exited, run
`lsof client-a306.log.lock` (or `fuser client-a306.log.lock`).
  - **A live pid still holds it** (especially a pid ≠ the dead client — a parent shepherd, a
    forked helper, a lingering task) ⇒ **H-leak confirmed** (an inherited OFD pins the flock;
    the kernel releases only when *all* fds to that OFD close). This directly answers the
    user's Q1 (today we track no PIDs — flock is the only signal; `lsof` is how you'd learn who
    holds it) and would justify tracking/verifying the holder in the fix.
  - **No process holds it, yet a fresh client still lands on `-2`** ⇒ **not** a leak → H-stale
    (network visibility) or H-bug.

**(e) Mount / flock options for both filesystems** *(prerequisite for the FS-independence
claim)*. `/scratch`: `mount | grep lustre` and `lctl get_param llite.*...` (look for
`flock`/`localflock`/`noflock`). `/home`: `mount | grep home`; if NFS, `nfsstat -m` (inspect
`local_lock=`), and whether it is a **local ZFS dataset** or an **NFS export**.
  - Lustre shows `flock`/`localflock` (not `noflock`) ⇒ consistent with fact (v) (acquire
    works) and definitively buries refuted-ENOSYS.
  - `/home` is a **local** ZFS mount ⇒ FS-independence (§2) holds ⇒ H-stale excluded as common
    cause. `/home` is **NFS-exported** ⇒ FS-independence weakens; keep H-stale live for both.

## 5. Local test-suite reproducibility

`tests/test_logging.py` (read in full):

- **CAN reproduce today.** `test_slot_reclaimed_after_owner_dies` (`:91-106`) Popens a
  `_HOLDER` (`:30-35`) that only `claim_file_slot`s and sleeps; it proves, on the dev FS
  (APFS/local), that flock **acquire + conflict (`-2`) + release-on-death + canonical reclaim**
  all work. `test_claim_file_slot_disambiguates_within_process` (`:80-87`) proves in-process
  conflict fallthrough. The committed PLAN P1 also plans **errno injection** — patch
  `fcntl.flock` to raise `OSError(errno.ENOSYS/ENOLCK/EAGAIN)` — which deterministically
  exercises the code's *response* to each errno class.
- **CANNOT reproduce.** (1) **Real** Lustre `noflock`/ENOSYS or NFS ENOLCK/NLM **staleness** —
  no such FS in CI; errno injection tests our *reaction*, not the FS's true behavior, so
  H-stale is not falsifiable locally. (2) **H-leak** is *not* covered: `_HOLDER` neither forks
  nor starts multiprocessing, so no fd is inherited; the existing test structurally cannot
  surface an inheritance leak. It *is* locally constructible with a **new** probe — a parent
  that claims a slot then `multiprocessing.get_context('fork').Process(...)` spawns a child that
  inherits the fd and outlives the parent; assert the slot is **not** reclaimed. (Force `fork`
  explicitly: macOS defaults to `spawn`, and Python 3.14 moves Linux toward `forkserver` — both
  re-exec and would *not* inherit, masking the very bug.) (3) The **autoscaler multi-rank-per-
  node** launch (H-conc's premise) isn't exercised; PLAN P3's serial-generation CLI count test
  approximates the *serial* half but not true same-node concurrency.

## 6. Summary (~200 words)

The committed ENOSYS/exhaustion diagnosis is **refuted by construction**: a clean, no-PID
`client-a306.log` can only exist if `flock(LOCK_EX|LOCK_NB)` on its sidecar **succeeded**, and a
real `client-a306-2.log` requires a genuine CONFLICT on slot 1 followed by a successful acquire
on slot 2. The disk shows no PID files, no 100-`.lock` pile, and no "Exhausted" warning — the
exact opposite of what an unsupported-flock FS would leave. flock **acquire and conflict-detect
work on both filesystems**. Because the *identical* signature appears on a network Lustre and a
"POSIX-ish" ZFS, the cause is **filesystem-independent** — it lives in the process/lock
lifecycle, not FS flock quirks — which excludes H-stale as the *common* cause (Lustre-only at
most) and leaves **H-conc (leading: `-2` = a legitimate concurrent rank, working-as-designed)**
and **H-leak (an inherited flock fd outliving its owner — disk-consistent but code-grounded
unlikely, since the client only *connects* and all its children *exec* a CLOEXEC fd)**. The
snapshot can't split them; the **longitudinal** question does. Run, in order: (a) timestamp
overlap between a306 and a306-2, (b) peak ranks/generations per node vs max `-N`, (c) a
two-serial-process flock reclaim probe on both FSes, (d) `lsof`/`fuser` on a dead client's
`.lock`, (e) confirm mount/`flock` options (and whether `/home` is local ZFS or NFS —
prerequisite for the FS-independence claim). Overlap + bounded `-N` ⇒ H-conc + cleanup gap;
disjoint + a live fd-holder ⇒ H-leak.
