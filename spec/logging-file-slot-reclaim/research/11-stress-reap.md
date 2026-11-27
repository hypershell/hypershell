# Research 11 — Adversarial stress of the reap / architecture choice

Scope: read-only. This brief **attacks** the recommendations in
[`09-revised-design.md`](09-revised-design.md) (§3 reap = delete, KEEP-slots) and
[`07-reap-and-tension.md`](07-reap-and-tension.md) (Part 1 options, Part 2 architectures). It does
not re-derive the root cause and does **not** resurrect the refuted ENOSYS/exhaustion theory. All
line refs are `src/hypershell/core/logging.py` unless noted. Verdict is first; the kills follow.

---

## VERDICT (first)

- **Reap = DELETE, never concat.** Concat is *fatal at `small` appetite* (three independent
  strikes below). DELETE is the safest choice — but only if it (i) deletes the **whole `-N.*`
  lineage**, and (ii) is interlocked by **acquiring the `-N.lock` flock during the unlink**. Brief
  07 §1.0's "reap on pid-liveness, **never** on flock" is **too strong and unsafe**: pid-liveness
  alone races a *relaunched* same-slot writer. The correct oracle is **both** — flock-acquire is
  the safety interlock against a live/fresh writer; the pid record is only the *ghost-override* for
  the H-leak case (`pid dead` + flock unacquirable ⇒ inherited-OFD ghost that isn't writing ⇒ safe
  to delete). Neither signal alone is sufficient.
- **Architecture = KEEP-slots (A).** SINGLE-file-per-host (B/II) is not merely "big" — with
  rotation enabled it is **correctness-breaking** (cross-process rename/compress/prune corruption;
  `FILE_LOCK` is process-local, `:355`) and on Lustre risks **torn lines**. It also silently
  violates R7. Reject B.
- **Does KEEP-slots satisfy "one file per host"?** Only in the *serial* regime (where reclaim
  already delivers it). It cannot give one file during genuine concurrency without violating R7.
  That is a UX/expectation gap, not a bug — and it is the honest answer to give the maintainer.

---

## PART 1 — Kill the concat-append reap path

### Strike 1 — Non-idempotent; crash mid-concat duplicates or truncates (FATAL)
Concat is *append into a live file*, so the atomic-rename trick cannot apply (07 §1.4 concedes
this; I sharpen it). Sequence: reaper appends orphan bytes to `client-a306.log`, then `os.remove`
the orphan. A crash (SIGKILL / OOM / node eviction — the *normal* autoscaling exit) **between** the
append and the unlink means the next start finds the orphan still present and **re-appends the same
bytes → duplicated log lines**, unboundedly across restarts. A `.reaping` marker does not close the
window: the append itself is not all-or-nothing under `O_APPEND` on Lustre, so a crash *mid-append*
leaves a **truncated** partial record with no way to know how many bytes already landed. DELETE
(`os.remove` swallowing `FileNotFoundError`/`OSError`) is idempotent by construction.

### Strike 2 — Concat races the reaper's *own* writer thread and corrupts the byte counter (FATAL, missed by 07)
The reaping process is the fresh canonical (slot-1) winner. Its file writes go through a
**QueueListener background thread** (`:818-819`) driving `file_handler`. If concat runs in
`initialize_logging` (the natural site, beside `recover_interrupted_compression`, `:836-838`) it
executes on the **main thread** and does a raw `open(canonical,'a')` + `copyfileobj`. That is a
**second, unsynchronized writer** to the canonical file from the *same process* — `FILE_LOCK`
(`:355`) guards only rotation, not raw appends, so listener records and concatenated orphan bytes
interleave/tear. Worse: the concatenated bytes **bypass `update_interval`** (`:460-462`), so
`SizeRotatingFileHandler.count_bytes` never accounts for them → the size-rotation threshold
misfires (over-large files, or rotation at the wrong offset). Concat silently breaks the rotation
accounting the handler depends on. DELETE touches neither the writer thread nor the counter.

### Strike 3 — Mis-detected-dead owner still writing ⇒ corruption (FATAL for concat, guarded for delete)
`os.kill(pid,0)` / `psutil.pid_exists` is a TOCTOU probe. Under **H-stale** (network
release-visibility lag) or a transient stop, a live orphan owner can read as dead; concat then
copies a file *being appended to concurrently on another node* — interleaved/torn merge into the
permanent canonical history. DELETE gated on **flock-acquire** is immune: a live owner still holds
the `-N.lock` flock, the reaper fails to acquire, and skips. (This is exactly why "reap on pid,
never on flock" is wrong — see Part 3.)

### Strike 4 — Out-of-time-order lines break parsers (MITIGABLE, but a silent contract change)
Concat lands the orphan's T0..T2 records **after** canonical records already written to T2 (07
§1.3). `grep`/`sort -k<time>`/log-shippers that assume file order break. Only acceptable if every
consumer keys on the embedded `%(asctime)s` — an assumption the fix would impose silently.

### Strike 5 — An orphan is a *lineage*, not a file: unbounded concat work (FATAL at `small`)
`client-a306-2.log` owns a private rotation lineage (`-2.1`, `-2.20260723`, `.gz`, `.gz.partial`;
`:230-277`, `:473-491`). Concatenating "properly" means **decompressing** children and
**time-merging two independent rotation lineages** — decisively over `small` (07 §1.2). Concat of
the bare `-2.log` **only** silently abandons the compressed children, which are themselves the
proliferation the feature exists to stop. DELETE-the-whole-lineage is both smaller and *more
complete*.

**Concat verdict: reject.** Strikes 1, 2, 3, 5 are individually fatal at the stated appetite. R3
("reclaim ⇒ append, preserve history") is about the **canonical file's continuity across serial
generations** — which KEEP-slots reclaim already delivers (re-acquire the persistent sidecar flock,
handler opens `mode='a'`, `:377`) — **not** about resurrecting a dead concurrent slot's tail.
Losing a crashed *concurrent* generation's private tail is acceptable; corrupting the permanent
host history to save it is not.

---

## PART 2 — Kill SINGLE-file-per-host (Architecture B / brief 07 II)

### Strike A — Cross-process rotation is corrupting, not just "racy" (FATAL)
`FILE_LOCK` is a **process-local** `Lock()` (`:355`) — it cannot serialize N processes. With N live
appenders on one `client-host.log`: process A hits its size threshold, `rotate()` does
`close()`+`os.rename(log → log.1)`+queues compression (`:398-405`). Process B still holds an open
`mode='a'` fd (`:377`, `delay=True`) to the **renamed inode** → B keeps writing into `log.1` while
A creates a fresh `log` and A's compression thread **compresses `log.1` out from under B**
(`:483-491`) and prunes it (`files_eligible_for_deletion`, `:334-345`). Result: silent data loss +
compressing a file under an active writer. Every process also independently runs
`recover_interrupted_compression` on the shared prefix at startup (`:837`), racing to re-queue the
same partials (`:513-533`). Making B safe requires a **cross-process rotation election / flock-gated
rotation** the slot scheme never needed → this is the "big" the briefs name, but the framing
"smaller" undersells that B is *broken with rotation on by default*.

### Strike B — Torn lines on Lustre (FATAL on the target FS)
stdlib emits each record as a single `stream.write(msg+terminator)`, and `O_APPEND` makes that
atomic **only up to a per-FS bound** (~`PIPE_BUF` for pipes; regular-file guarantees are weaker and
implementation-defined). Multi-node concurrent `O_APPEND` on Lustre is **not** guaranteed atomic
for records exceeding a stripe/`PIPE_BUF`-class size — the exact deployment (Gautschi `/scratch`)
where the maintainer runs. Interleaved partial lines corrupt records. 07 §II bills interleave as
"line-atomic on most filesystems"; on the *actual* filesystem that is not safe.

### Strike C — Silently violates R7 (FATAL vs GOAL as written)
R7 requires distinct files for genuinely-concurrent same-host writers where locking works. B
abolishes that by construction. It needs an explicit **GOAL amendment** (relax R7 to "best-effort
single-writer"), so choosing it silently would ship a spec violation.

**SINGLE-file verdict: reject** unless the maintainer *both* amends R7 *and* accepts a cross-process
rotation-election phase (big). Its only genuinely-needed piece — a **one-shot legacy sweep** of the
pile already on Gautschi — is deliverable inside Architecture A anyway.

---

## PART 3 — Correct the reap oracle; confirm DELETE is safest

Brief 07 §1.0 disqualifies flock-probe reap because H-leak's ghost flock makes a dead owner's slot
read LOCKED. True — but the proposed replacement, **reap on pid-liveness alone**, opens a *new*
hole: between reading the (dead) pid record and unlinking, a **relaunched** process can legitimately
claim `-2` (the slot is genuinely free), rewrite the record with its live pid, open `-2.log`
`mode='a'`, and start writing — the reaper then **deletes a live writer's file**. pid-liveness is
not a mutual-exclusion primitive; only the flock is.

**Correct interlock (needs BOTH signals):** to reap slot `-N`, attempt `flock(-N.lock, LOCK_EX|
LOCK_NB)`.
- **Acquired** ⇒ no live writer holds it (original dead, none relaunched) ⇒ delete the whole `-N.*`
  lineage **while holding the flock**, then release. (This is safe even though it *uses* flock —
  the H-leak objection is about flock giving a false *LOCKED*, never a false *FREE*.)
- **Not acquired** ⇒ consult the pid record: **alive** ⇒ genuine sibling/relaunch ⇒ skip.
  **dead** ⇒ inherited-OFD **ghost** (H-leak): the flock is held by a live *inheritor that never
  writes* `-N.log`, and no fresh claimant can exist (they'd fail the same flock), so deleting the
  data is safe — this is the *only* place the pid record overrides flock.

So: **flock-acquire is the interlock, the pid record is the ghost-override.** "Never on flock" is
wrong; "never on flock *alone* / never trust flock's LOCKED verdict without the pid record" is
right. DELETE composes cleanly with this (idempotent unlink under the held lock); concat cannot
(Strikes 1–2 persist even under the lock). **DELETE is the safest choice**, confirming R3 is about
canonical continuity, not dead-slot resurrection.

### Residual bounded-work note (ACCEPTABLE)
Reap runs one-shot at startup beside `recover_interrupted_compression` (`:836-838`), before the
compression thread does real work (invariant §5 respected). Cost is O(files in log dir) `stat`/
`flock`/`unlink` — bounded by current disk state, idempotent, and self-reducing across restarts.
Not unbounded per-record. Acceptable even with hundreds of legacy orphans.

---

## Scorecard

| Target | Strike | Severity |
|---|---|---|
| Concat | crash mid-concat dup/truncate (non-idempotent) | **fatal** |
| Concat | races own QueueListener thread + corrupts `count_bytes` | **fatal** |
| Concat | mis-detected-live owner corruption (no flock interlock) | **fatal** |
| Concat | out-of-time-order lines | mitigable |
| Concat | abandons/over-merges `-N.*` rotation lineage | **fatal @ small** |
| SINGLE-file | cross-process rename/compress/prune corruption (`FILE_LOCK` local) | **fatal** |
| SINGLE-file | Lustre `O_APPEND` torn lines | **fatal on target FS** |
| SINGLE-file | silent R7 violation | **fatal vs GOAL** |
| DELETE (recommended) | needs flock-interlock + whole-lineage + host guard | mitigable (design it in) |
| KEEP-slots (recommended) | "one file/host" only in serial regime | acceptable (expectation gap) |

**Final recommendation:** reap = **DELETE whole `-N.*` lineage, interlocked by flock-acquire with
the pid record as ghost-override**; architecture = **KEEP-slots (A)**. Reject concat and reject
SINGLE-file.
