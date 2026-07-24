# Research 07 — Reap semantics & the reframed design tension

Scope: read-only. Builds on the **corrected ground-truth evidence** (flock *acquire* is honored
on both Lustre `/scratch` and ZFS `/home`; canonical `client-a306.log` was won cleanly at slot 1;
a sequential `client-a306-2.log` appeared; **no** PID files, **no** ~100 `.lock` pile, **no**
"Exhausted slots" warning; behavior **identical** on both filesystems). That evidence **refutes**
the committed ENOSYS/exhaustion diagnosis in `00`–`03`/`PLAN.md`/`TECH.md` — do not resurrect it.
The live root-cause hypotheses (H-conc legitimate concurrency; H-leak fd-inheritance flock leak;
H-stale network release; H-bug) and the **robust pid-based liveness signal** are owned by brief 06;
this brief consumes that signal and does **not** re-derive the cause.

All line refs are `src/hypershell/core/logging.py` unless noted.

---

## PART 1 — Reap semantics for orphaned `-N.log` slot files

### 1.0 The load-bearing correction: the reap oracle must be pid-liveness, NOT flock

The committed plan reaps by **probing the flock**: "if `_try_lock(slot + '.lock')` returns LOCKED
→ owner is gone → `os.remove` the `-N.log`" (`PLAN.md` §2(d); `TECH.md` P2). **The corrected
evidence disqualifies this probe.** flock cannot serve as the liveness oracle for reaping, for the
same reason it cannot serve for reclaim:

- **If H-leak holds** (a fork after `initialize_logging` opened the sidecar fd — e.g.
  `BaseManager.start()`'s child at `core/queue.py:385`, reached after `main()` calls
  `initialize_logging` at `__init__.py:139`; note the task-executor `Popen(shell=True)` at
  `client.py:772` uses POSIX default `close_fds=True` — nothing sets `close_fds` anywhere — so it
  does **not** inherit, which is why H-leak is weaker for the pure `client` role than for
  cluster/server): the leaked open-file-description keeps the flock **held after the owner exits**.
  A flock probe then reports the *dead* owner's slot as LOCKED (live) and **refuses to reap a
  genuine orphan** — proliferation persists forever. This is exactly the observed symptom
  ("slot 1 never reclaimed").
- **If H-stale holds**: network release-visibility lets a just-freed slot flap between
  held/free, so a flock probe reaps non-deterministically (sometimes deletes a slot whose owner
  is transiently invisible-but-alive → data loss; sometimes misses a real orphan).
- **If H-conc holds**: flock is honest *while contended*, but the `-2` orphan left behind after a
  concurrency peak subsides is only reapable once its (now-dead) owner's flock is released — which
  is precisely the case H-leak/H-stale corrupt.

Since the FS-independent evidence points away from flock semantics entirely, the reap decision
**must** use brief 06's robust signal: a **pid recorded in the sidecar** + `os.kill(pid, 0)`
(`ESRCH` ⇒ dead ⇒ orphan; success/`EPERM` ⇒ alive), gated by a host check (the pid is only
meaningful on the host baked into the filename — already guaranteed same-host by `default_file_for`,
`:654-657`, but re-assert it so a recycled pid on a different host can never trigger a delete).
This is FS-independent, matching the identical-on-both-filesystems evidence. **Reap on liveness,
never on flock.**

### 1.1 Options for the orphan's *data*

| Option | History | Convergence to 1/host | Complexity | Verdict |
|---|---|---|---|---|
| **(a) delete orphan** | loses that generation's log | yes | trivial, idempotent | acceptable *iff* owner proven dead |
| **(b) concat → canonical, then delete** | preserves (R3 spirit) | yes | high (see 1.2–1.4) | over-appetite for a `small` fix |
| **(c) leave data, stop creating new** | preserves | no (pile persists) | trivial | fails R6 ("removable") |
| **(d) hybrid** | tunable | yes | medium | **recommended** |

**Recommended (d):** *delete the orphan `-N.log` only after the robust dead-owner check
(1.0) passes.* This is the smallest change that satisfies R6 and R4 and is **fully idempotent**
(`os.remove` wrapped to swallow `FileNotFoundError`/`OSError` — a racing reaper or live writer is
harmless). It does lose a **crashed** client's own tail — but only *that* generation's lines in a
*distinct* file; the canonical host history is untouched. If the maintainer deems crashed-slot
diagnostics load-bearing, upgrade the same helper to **(b)** *for the bare `-N.log` only* (see
1.2 for why "only"); the liveness gate and call site are identical, so (a)→(b) is a localized
swap, not a redesign. Default to (a); offer (b) as the history-preserving dial.

### 1.2 Interaction with the rotation namespace and `.partial` — the hidden proliferation

An orphan slot is **not one file**. `client-a306-2.log` owns an entire private rotation lineage:
`client-a306-2.1` / `client-a306-2.20260723` / `client-a306-2.20260723-140000` (+ a compression
ext), because rotation strips the last extension via `basename_without_ext` (`:304-306`) and keys
on a **dot**-separated counter/date (`re_pattern_count` = `prefix\.([0-9]+)ext`, `:230-232`;
`:260-277`), plus possible `<name>.gz.partial` mid-compression files (`compress_file`, `:473-491`).

Two hard consequences:
1. **Reap must key on the exact `-N` *slot* shape** `^{re.escape(root)}-([0-9]+){re.escape(ext)}$`
   (dash), and **never** touch the dot-separated rotation namespace (managed under `FILE_LOCK` by
   the compression thread, §5) nor `.partial` nor the `.lock` sidecars nor the `main` role —
   mirroring `recover_interrupted_compression`'s prefix-scoping discipline (`:513-533`).
2. **Deleting/folding only the bare `-N.log` silently abandons its rotated/compressed children**,
   which are themselves unbounded under long autoscaling runs. So *whichever* data option is
   chosen, the orphan's rotation lineage is a second proliferation source. Folding that lineage
   into the canonical file (option b, done "properly") would require **decompressing** children
   and **time-order-merging two independent rotation lineages** — decisively beyond `appetite:
   small`. This is the strongest argument that **(b) is not small-appetite** and that (a)
   delete-the-whole-lineage (bare slot **and** its `client-a306-2.*` children, all gated on the
   dead-owner check) is both smaller *and* more complete than a bare-`-N.log`-only concat.

### 1.3 Out-of-time-order lines (concat only)

Concat appends the orphan's records to the *tail* of the canonical file, so the merged file is
**not monotonic in time**: an orphan created at T0 and reaped at T2 lands after canonical records
written up to T2. Downstream `grep`/`sort -k<time>` breaks. Acceptable only if consumers key on
the embedded timestamp field, not file order — an assumption to surface, not to make silently.

### 1.4 Must concat hold a lock? Crash-mid-concat idempotency

Yes — concat (option b) **must** hold the flock on the orphan sidecar for the whole copy (to
exclude a mis-classified live writer or a racing reaper), *in addition to* the canonical lock the
reaper already holds by virtue of being the n=1 winner. Worse, concat is **not idempotent**: a
crash *after* appending the orphan's bytes to canonical but *before* `unlink`ing the orphan causes
the next run to **re-append the same bytes → duplicated log lines**. The usual atomic-rename trick
does not apply (this is an append into a live file, not a whole-file replace). A `.reaping` rename
marker narrows but does not close the window (the append itself is not all-or-nothing under
`O_APPEND`, especially on Lustre). By contrast **(a) delete is perfectly idempotent** (unlink is
naturally so). This is the second decisive strike against (b) at `small` appetite.

### 1.5 Definitive answer to the user's Q2 (quotable)

> Reclaim does **not** require unlinking the `.lock` sidecar. "Reclaim" means re-acquiring the
> advisory lock on the *persistent* sidecar — opening the same `client-<host>.log.lock` inode and
> flocking it — so the sidecar is a reusable rendezvous point that *should* live for the whole
> host's logging, not a per-generation artifact to delete. Deleting sidecars is in fact harmful:
> `open(mode='w')` on a path another process still holds via flock yields a **new inode with a
> new, independent lock** (POSIX inode-reuse race) → two live writers to one log. Bounded file
> counts therefore come from *reusing* the fixed set of sidecars, never from unlinking them; only
> the `-N.log` **data** (and its dead owner) is the reap target.

---

## PART 2 — The reframed design tension

The corrected evidence — flock works yet files still pile up, identically on both filesystems, and
the maintainer's own framing ("we never reclaim … just proliferate") — suggests the **slot
abstraction itself may be the mismatch**: the maintainer appears to expect *one appending file per
host*, and the `-N` slots are unwanted machinery whose entire lifecycle (reclaim + reap +
pid-tracking) is pure cost if genuine same-host concurrency is rare. Two architectures:

### Architecture I — KEEP slots; fix reclaim + reap

Distinct files while genuinely concurrent, converging to one when serial. Reclaim and reap both
switch from the (refuted) flock oracle to brief 06's **pid-liveness** signal: at claim time, if the
canonical sidecar's recorded pid is dead, **steal** the canonical slot (append) regardless of a
leaked/stale flock; reap dead-owner `-N.log` lineages (Part 1).

- **Pros:** honors **R7 as written** (real single-writer where it matters — no interleave); keeps
  per-generation isolation; smallest *conceptual* departure from today's model.
- **Cons:** correctness now hinges on the pid signal being right across H-leak/H-stale/pid-reuse;
  retains the orphan data-vs-history question (Part 1); more moving parts (pid record, `os.kill`,
  host guard, reap helper, `_slot_locks` hygiene); the fleet still *transiently* shows many files.
- **GOAL:** satisfies **R2, R3, R4, R6, R7, R8** (and R5 via pid-based bounding even if flock is a
  no-op). Nothing violated.

### Architecture II — ABANDON slots; one shared per-host file

Every same-host client opens and **appends** to the single canonical `client-<host>.log`; a failed
or unavailable lock **never** walks to `-N` — it just appends (best-effort single-writer). Exactly
one data file per host by construction. The flock/sidecar may survive as an *advisory* hint only.

- **Pros:** **dramatically smaller** — deletes the slot loop, reclaim, reap, pid-tracking, and the
  entire orphan lifecycle; **exactly** 1/host by construction (strongest R4/R5, FS-independent, no
  reliance on flock working); directly matches the maintainer's evident mental model and GOAL Q1
  (append). Interleave is *line-atomic on most filesystems*: the stdlib emits each record in a
  single `write(msg + terminator)`, and `O_APPEND` makes that atomic up to a per-FS size limit.
- **Cons:** **contradicts R7** — concurrent same-host writers interleave, and `O_APPEND` write
  atomicity is *not* guaranteed on Lustre for records exceeding a stripe/`PIPE_BUF`-class bound
  (possible torn lines). Requires the **maintainer to explicitly relax R7**. Loses per-generation
  isolation. R6 becomes largely moot **except** the one-time reap of the *legacy* pile already on
  Gautschi (still needed via the Part-1 liveness sweep).
- **GOAL:** satisfies **R2** (always canonical), **R3** (append), **R4/R5** (1/host, lockless-safe),
  **R8** (host-scoped; `main` untouched). **Violates R7.** R6 reduces to a one-shot legacy sweep.

### Which is smaller, and the decision to put to the maintainer

**II is decisively smaller** and eliminates the flock/liveness/reap surface that the corrected
evidence just proved fragile — at the cost of a GOAL amendment (relax R7). **I is larger** and its
correctness rests entirely on the pid signal from brief 06.

**Explicit decision for the maintainer:** *Are concurrent same-host clients a real workload?* One
`ClientThread` per process is the documented model, and autoscaling typically scales a host to zero
before relaunching (serial generations) — so R7's protection may be buying little while the slot
lifecycle costs a lot. **If same-host concurrency is rare/absent → Architecture II** (relax R7 to
"best-effort single-writer," one appending file per host, keep flock only as an advisory hint, plus
a one-time legacy reap). **If it is a genuine workload → Architecture I** with brief 06's pid
liveness driving both reclaim and reap. Either way, **the flock-probe reap in the committed
`PLAN`/`TECH` must be replaced** — flock is no longer a trustworthy liveness oracle.
