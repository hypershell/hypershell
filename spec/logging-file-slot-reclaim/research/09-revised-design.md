# Research 09 — Revised design synthesis

Consolidates briefs [04](04-evidence-forensics.md) (evidence forensics), [05](05-process-model-audit.md)
(fd-inheritance audit), [06](06-liveness-design.md) (liveness signal), [07](07-reap-and-tension.md)
(reap semantics + architecture tension), [08](08-scope-invariants.md) (re-scope + invariant gate)
against the maintainer's **corrected ground-truth evidence** from Gautschi, and supersedes the
committed [`PLAN.md`](../PLAN.md) / [`TECH.md`](../TECH.md) diagnosis. All line refs are
`src/hypershell/core/logging.py` unless noted; source is read-only. **Do not resurrect the
refuted ENOSYS/exhaustion theory** (§1).

---

## 1. Refined diagnosis

### Where the committed plan was wrong

The committed `PLAN.md §1` / `TECH.md P1` diagnosis — *"`_try_lock` swallows every `flock`
`OSError` as contention; on Lustre `noflock` every call returns `ENOSYS`, so the loop exhausts
100 slots → PID suffix + 100 `.lock` files + an Exhausted warning"* — is **refuted by
construction** (brief 04 §1–2). The on-disk evidence is the *exact opposite* of what that theory
predicts:

| ENOSYS theory predicts | Disk actually shows | Deduction |
|---|---|---|
| PID-suffixed logs (`:713`) | **no** PID files | PID branch never taken |
| a fixed **100** `.lock` pile | **2** sidecars on a306 | loop stopped at slot 2, by *winning* |
| an "Exhausted slots" warn/launch (`:712`) | **no** warning | loop never fell off the end |
| **no** clean canonical `.log` (name never *returned*, so `delay=True` never creates it) | clean `client-a306.log` **won at slot 1** | `flock(LOCK_EX\|LOCK_NB)` **succeeded** |

A clean, no-PID `client-a306.log` can only exist if `_try_lock('client-a306.log.lock')` returned
a handle → the flock **acquired**. A real `client-a306-2.log` requires a genuine CONFLICT on
slot 1 (`EAGAIN`) followed by a successful acquire on slot 2. So **flock acquire and
conflict-detection work on both `/scratch` (Lustre) and `/home` (ZFS).** The errno-conflation
defect is real *code smell* but is **not** the cause of the observed proliferation.

### The FS-independence lever

The *identical* signature on a network Lustre and a POSIX-ish ZFS — two different flock
implementations — means the cause cannot be a property of either FS's flock quirk. It lives
**above** the FS layer, in the process/lock lifecycle. This **excludes both filesystem-semantic
hypotheses** (refuted-ENOSYS; H-stale as the *common* cause — H-stale is Lustre-only at most),
and leaves H-conc, H-leak, H-bug (brief 04 §2). *Prerequisite caveat:* the argument assumes
`/home` is a **local** ZFS mount; if it is NFS-exported ZFS, H-stale re-enters for both halves —
confirm with diagnostic (e).

### Ranking

1. **H-conc (LEADING) — legitimate concurrency, working-as-designed.** ≥2 client generations
   overlap on a306, so `-2` is *correct by design* (R7 behaving). Working flock + a live slot-1
   holder ⇒ a concurrent peer gets `EAGAIN` and correctly wins `-2`. Produces exactly {clean
   canonical + one sequential `-2`, no PID, no pile, no warning}; FS-independent; a261 = 1 rank,
   a306 = 2 ranks explains the asymmetry. **If this is the truth, the real complaints are
   downstream of correctness:** files are never consolidated to one-per-host, never cleaned up,
   and stale ones are never reaped when concurrency drops — all cleanup/UX gaps, not a lock bug.

2. **H-leak — inherited-OFD flock leak. DISK-CONSISTENT but code-grounded UNLIKELY for the
   `client` role** (briefs 04 §3, 05). The process-model audit (brief 05) is decisive here:
   - `initialize_logging` runs in `main()` (`__init__.py:139`) *before* any process creation, so
     the flock'd sidecar fd (`open(path,'w')`, `:671`; held in `_slot_locks`, `:698`/`:710`,
     never closed) exists before every fork/`Popen`.
   - A flock releases only when **all** fds on its OFD close. `O_CLOEXEC` (PEP 446) drops the fd
     on **exec** but **not on fork-without-exec**.
   - The **only** fork-without-exec in the tree is `SecureManager/BaseManager.start()`
     (`core/queue.py:273-281`), which on Linux's default `fork` method spawns a manager child
     that inherits and shares the role's slot OFD. **But that fires only in `server`/`cluster`
     roles** (which run a `QueueServer`), inheriting the *server/cluster* slot — not a client
     slot. The **client only `connect()`s** (`client.py:1219`), drives *threads*, and its task
     subprocesses `Popen(shell=True)` with default `close_fds=True` + exec → drop the fd.
   - **Verdict (brief 05): no fork/shared-OFD path prevents release of a *client* slot lock**, so
     H-leak does **not** explain the observed `client-<host>` proliferation. It *is* a real
     **latent** bug for `server`/`cluster` slots if the manager child is orphaned (parent
     OOM/SIGKILL). Clean `cluster-login01.log` (no `-2`) shows normal shutdowns release it.

3. **H-stale — network-FS release lag. EXCLUDED as common cause** (Lustre-only at most), by
   FS-independence — contingent on the (e) mount check.

4. **H-bug — residual catch-all.** No obvious defect; the reclaim loop restarts at `n=1` and
   `test_slot_reclaimed_after_owner_dies` (`tests/test_logging.py:91-106`) proves reclaim works on
   a healthy FS. Retained only if diagnostics exonerate the above.

**Bottom line:** the most-likely truth is **H-conc + a cleanup/consolidation gap** — the slot
scheme is working as designed, and the maintainer's pain is that it *never converges to one file
per host and never reaps*. The single decisive diagnostic (§4) settles conc-vs-stale before we
commit to more machinery than the truth warrants.

---

## 2. Definitive answers to the maintainer's three questions

**Q1 — How do we know the canonical `.lock` is held by a REAL live process? How do we track
PIDs?**
> Today we do **not** — and that is the gap. The sidecar is opened `open(mode='w')` and left
> **0-byte** (`:671`, `:669-677`); no pid, mtime, or heartbeat is ever written. The *only*
> liveness signal is the advisory flock itself, which the kernel drops when the last fd on the
> holding open-file-description closes (i.e. on process death). That signal is correct on a
> healthy local FS but is **blind to two failure modes**: an inherited-OFD ghost (H-leak) keeps
> the flock held after the true owner dies, and a network FS can lag release visibility
> (H-stale). To *track* pids we exploit the **same-host guarantee**: `default_file_for` bakes
> `HOSTNAME_SHORT` into every distributed slot name (`:654-657`), so every competitor for
> `client-a306.log` runs on a306 and a pid written into the sidecar is a **local** pid, checkable
> with `psutil.pid_exists(pid)` + `psutil.Process(pid).create_time()` (pid-reuse defense; a
> wall-clock epoch, so **no boot-id needed**). **`psutil>=7.0.0` is already a hard core
> dependency** (`pyproject.toml:37`, used in `core/resource.py:17`) — no new dependency, and the
> EPEL/stdlib-floor worry is moot. The recommended record is one atomically-written JSON line:
> `{"pid", "create_time", "host", "instance", "v"}` (brief 06), used as a liveness/reap oracle.

**Q2 — If we can't unlink `.lock` files, how do we ever reclaim?**
> Reclaim is **not** unlinking — it is **re-acquiring the flock on the persistent sidecar**:
> open the same `client-<host>.log.lock` inode, `flock` it, and the handler appends to
> `client-<host>.log` (`mode='a'`, `:377`). The sidecar is a *reusable rendezvous point* that
> should live for the whole host's logging, not a per-generation artifact. **Unlinking sidecars
> is actively harmful:** `open(mode='w')` on a path another process still holds via flock yields
> a **new inode with a new, independent lock** (POSIX inode-reuse race) → two live writers to one
> log. Bounded file counts therefore come from **reusing** the fixed set of sidecars, never from
> deleting them. On a healthy FS this reclaim *already works*
> (`test_slot_reclaimed_after_owner_dies`); the fix's job is to make it also fire when flock is
> blind, by letting the pid-record **override a ghost/stale flock** (brief 06 Option B step 3).

**Q3 — When we reap a `-N.log`, do we append its contents onto the canonical file?**
> **The maintainer's call, and it is genuinely balanced.** GOAL Q1/R3's *"reclaim ⇒ APPEND,
> preserve history"* argues for **concat-then-delete**. But concat is (brief 07 §1.2–1.4):
> (i) **not idempotent** — a crash after appending bytes but before `unlink` re-appends on the
> next run → **duplicated log lines** (the atomic-rename trick doesn't apply to an append into a
> live file); (ii) produces **out-of-time-order** lines (orphan's records land after newer
> canonical records — breaks `sort`/`grep` unless consumers key on the embedded timestamp);
> (iii) an orphan slot is **not one file** — `client-a306-2.log` owns a whole private rotation
> lineage (`-2.1`, `-2.20260723`, `.gz`, `.gz.partial`); folding it "properly" means
> **decompressing and time-merging two rotation lineages** — decisively over `small` appetite.
> **Recommended default: reap = delete** the dead-owner's *entire* `-N.*` lineage (bare slot +
> rotated/compressed children), gated on the pid-liveness check, wrapped to swallow
> `FileNotFoundError`/`OSError` (perfectly idempotent). Offer concat-of-the-bare-`-N.log`-only as
> a localized (a)→(b) swap *if* the maintainer deems crashed-slot tails load-bearing.

---

## 3. Recommendation

**Liveness signal (from brief 06): Option B — flock-primary + pid-record fallback.**
1. `flock(LOCK_EX|LOCK_NB)` on a **non-truncating** open (see caveat below). **Acquired** ⇒
   strict single-writer (R7); write our record into the locked sidecar. Done.
2. **CONFLICT** (`BlockingIOError`/`EAGAIN`) ⇒ consult the record: **alive** (pid up,
   `create_time` matches) ⇒ genuine sibling ⇒ advance to `-2` (R7 preserved by the kernel);
   **dead/stale/empty/legacy** ⇒ **ghost flock** (H-leak/H-stale) ⇒ **override**: use the
   canonical path and append, rewrite the record with our `instance`.
3. **UNSUPPORTED** (other `OSError`) or `_LOCKING is False` ⇒ record-only mode on the canonical
   path — exactly GOAL R5's bounded-interleave regime (replaces today's `:713` PID-suffix, itself
   a proliferation bug).

B is the only option that (i) keeps the **atomic** single-writer where flock works (R7/R8, no
regression to `test_slot_reclaimed_after_owner_dies`); (ii) **fixes H-leak/H-stale if real** by
letting the local pid-record deliberately disagree with a ghost flock; (iii) degrades to
record-only precisely in the lockless regime R5 already scopes. Reject C (pure record — regresses
R7 on healthy FS, reintroduces the two-starter race even where flock would close it) and D
(mtime/heartbeat — timer guesswork, false-reap risk, worst on the network FS we distrust).

**Two composition caveats that are load-bearing** (brief 06 §Composition): `_try_lock` opens
`mode='w'` (**TRUNCATE**, `:671`) — a *losing* probe would blank the winner's record before it
gets `EAGAIN`. Switch to a non-truncating probe (`open(p,'a+')` / `os.open(...,O_RDWR|O_CREAT)`);
only the **winner** `seek(0)+truncate()+write(record)`, never `close()` (closing drops the
flock). Reader path opens `'r'`; empty/legacy/unparseable ⇒ "no live holder."

**Reap semantics (from brief 07): reap on pid-liveness, NEVER on a flock probe.** The committed
plan's flock-probe reap (`PLAN §2(d)`, `TECH P2`) is disqualified: under H-leak a ghost flock
makes a dead owner's slot read as LOCKED, so the probe **refuses to reap a genuine orphan** —
exactly the reported symptom. Reap on `record.pid` dead (+ host guard), key on the **exact slot
shape** `^{re.escape(root)}-([0-9]+){re.escape(ext)}$` (dash), and **never** touch the
dot-separated rotation namespace, `.partial`, `.lock` sidecars, or the `main` role — mirroring
`recover_interrupted_compression`'s prefix-scoping (`:513-533`).

**Architecture: recommend KEEP-slots (Architecture A / brief 08 A), but flag it as the
maintainer's call.** A preserves R7 as written, needs **no GOAL amendment**, touches no argv
builder or config knob (invariant §11/§12 clean), and its liveness fix credibly restores reclaim.
SINGLE-file (Architecture B) is *conceptually* what the maintainer's mental model wants (one
appending file/host) and is smaller for *counting* — but it **violates R7**, forfeits
per-generation isolation, and introduces a **cross-process rotation race** the slot scheme never
had (multiple live processes each running a `RotatingFileHandler` with a *process-local*
`FILE_LOCK`, `:355` — rotation would need to be flock-gated). That pushes B to **big** appetite
and a GOAL amendment. **Both the architecture choice and reap=delete-vs-concat are explicitly the
maintainer's decisions** — they turn on one empirical fact (§4) plus one policy call (R7).

---

## 4. Explicit decisions the maintainer must make

**D0 (DECISIVE DIAGNOSTIC — run first; from brief 04 §4a). Timestamp-overlap between
`client-a306.log` and `client-a306-2.log`.** Compare first/last `%(asctime)s` records in each.
- **OVERLAPPING wall-clock windows ⇒ H-conc / working-as-designed** — the only bug is
  non-consolidation + non-reap. Favors modest cleanup work; SINGLE-file becomes tempting.
- **DISJOINT / strictly sequential (a306-2 starts only after a306 ends) ⇒ reclaim failure**
  (H-leak/H-stale) — a serial successor should have re-won slot 1. Favors the full Option-B
  liveness override. Corroborate with (04 §4d) `lsof client-a306.log.lock` on a dead client (a
  live holder ⇒ H-leak confirmed) and (04 §4e) confirm `/home` is local ZFS vs NFS-exported.

**D1 — Architecture.** *Recommended default:* **KEEP-slots (A)** — preserves R7, no GOAL change,
smaller blast radius. Choose SINGLE-file (B) only if D0 shows same-host concurrency is
rare/absent *and* you accept relaxing R7 to "best-effort single-writer" (a GOAL amendment + a
cross-process rotation-election phase → **big** appetite).

**D2 — Reap data policy.** *Recommended default:* **delete the dead-owner's whole `-N.*`
lineage** (idempotent, complete). Upgrade to concat-of-bare-`-N.log`-only if crashed-slot tails
are load-bearing (accept non-idempotency + out-of-order lines + abandoned rotated children).

**D3 — Ship the `server`/`cluster` fd-leak hardening now?** (brief 05 §5) *Recommended: yes,
cheap.* Close inherited `_slot_locks` handles in the forked manager child via the existing
`_tls_bootstrap` initializer seam (`core/queue.py:243-281`) or `os.register_at_fork`. This is a
genuine latent leak for server/cluster slots even though it is **not** the observed client
symptom.

**D4 — Config knob or knob-free?** *Recommended: knob-free* auto behavior (flock-primary →
pid-fallback → canonical-append). A knob (`logging.file.lock=...`) is a **new config key** → forces
same-commit `docs/_include/config_param_ref.rst` + man-page + bash/zsh completion edits (invariant
§12) and enlarges scope.

---

## 5. Revised phase plan (Architecture A) + appetite

The committed 3-phase plan (errno discrimination → flock-probe reap → count) is **built on the
refuted diagnosis** and must be replaced. Revised, for KEEP-slots:

- **P0 — Confirm the cause (gate).** Maintainer runs D0 (+ corroborators). Decide D1/D2. *This
  gates whether P2/P3 below are even the right shape.* (Not code; a few minutes.)
- **P1 — pid-record liveness + non-truncating probe (Option B core).** Replace the 0-byte sidecar
  with an atomic JSON record; add a `_pid_alive`/`_record_holder_alive` seam
  (`psutil.pid_exists` + `create_time`, host-guarded, defensive on legacy/empty/`AccessDenied`);
  switch `_try_lock` to a non-truncating open with winner-only write; implement the CONFLICT→
  consult-record→override branch; degrade UNSUPPORTED/`_LOCKING=False` to canonical-append
  (kills the `:713` PID-suffix bug); fix the false comment (`:660-666`). Tests: monkeypatch the
  liveness seam + inject errnos; **autouse `_slot_locks` cleanup fixture**. **Ship-alone-able** —
  may fully restore reclaim. Appetite: **small**.
- **P2 — pid-liveness reap of dead-owner `-N.*` lineages (R6/R4).** Canonical-lock winner scans
  the exact dash-slot shape, reaps whole lineages whose recorded owner is dead (D2 default:
  delete), idempotent, prefix-scoped, never touching rotation namespace/sidecars/`main`. Runs
  beside `recover_interrupted_compression` (`:837`), before the compression thread does real work
  (invariant §5). Appetite: **small-medium**.
- **P3 — `server`/`cluster` fork fd-leak hardening (D3, POSIX-only, `skipif` Windows).** Close
  `_slot_locks` in the manager child (`core/queue.py:243-281` seam / `register_at_fork`). Guarded
  no-op where there is no fork. Appetite: **small**.
- **P4 — End-to-end bounded-count + regression (R4/R8).** Integration: serial **and
  overlapping** same-host generations; assert bounded count; cross-host/`main`/Windows/no-lock
  fallbacks unregressed. The fd-leak regression test (raw `os.fork()`, not `multiprocessing`) is
  the crux pinning P3. Appetite: **small**.

**Appetite: stays `small`→`small-medium` for Architecture A** (4 code phases in one module + one
queue-seam, under the circuit-breaker). **Escalate to `big` only if the maintainer picks
Architecture B** (adds cross-process rotation election + a GOAL R7 amendment + a config knob for
opt-in). Honest fork: if D0 shows pure H-conc with a healthy FS, **P1's override branch may be
unnecessary** and the work collapses toward "reap + one-shot legacy cleanup + fd-leak hardening"
— smaller still.

---

## ~350-word summary

**Diagnosis.** The committed ENOSYS/exhaustion diagnosis is refuted by construction: a clean
no-PID `client-a306.log` proves `flock` *acquired* at slot 1, and a real `client-a306-2.log`
proves genuine CONFLICT + acquire — with no PID files, no 100-`.lock` pile, no "Exhausted"
warning (brief 04). flock acquire/conflict-detect work on **both** Lustre and ZFS, so the cause
is **FS-independent** (excludes ENOSYS and H-stale-as-common-cause). Ranking: **H-conc leading**
(the `-2` is a legitimate concurrent rank — working-as-designed; the real gap is
non-consolidation + non-reap); **H-leak disk-consistent but code-grounded UNLIKELY for the
client** — brief 05 finds the client never forks a manager and its children exec with
`close_fds=True`, so no client-slot OFD leaks (though a real latent leak exists for
`server`/`cluster` via `BaseManager.start()`); H-stale Lustre-only; H-bug residual.

**Answers.** Q1: today we track no pids — flock is the only signal; add a `psutil`-checked pid+
`create_time` record in the sidecar (same-host guarantee makes the pid local; `psutil>=7` already
a dep). Q2: reclaim = re-acquiring the flock on the *persistent* sidecar and appending; never
unlink sidecars (inode-reuse race). Q3: maintainer's call — recommend **delete** the whole
dead-owner lineage (idempotent); concat is non-idempotent, reorders lines, and abandons rotated
children (over `small`).

**Recommendation.** Brief 06 Option **B** (flock-primary + pid-record override) + brief 07
pid-liveness reap (**never** flock-probe reap) + **KEEP-slots** architecture (preserves R7, no
GOAL change, no argv/knob churn). SINGLE-file is the maintainer's mental model but violates R7 and
adds a cross-process rotation race → **big**. Architecture and reap=delete-vs-concat are
explicitly the maintainer's decisions.

**Decisions-for-user.** D0 run the timestamp-overlap diagnostic (overlap ⇒ H-conc; disjoint ⇒
reclaim failure) — decisive; D1 architecture (default A); D2 reap policy (default delete); D3 ship
server/cluster fd-leak hardening (yes, cheap); D4 knob-free (yes, avoids §12).

**Phases.** P0 confirm+decide (gate) · P1 pid-record liveness + non-truncating probe
(ship-alone) · P2 pid-liveness reap · P3 fork fd-leak hardening · P4 e2e bounded-count +
regression. Appetite stays **small→small-medium** for A; **big only if B**.
