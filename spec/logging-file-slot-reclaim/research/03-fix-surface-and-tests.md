# Research 03 — Fix surface, reap/reclaim algorithm, and test approach

Scope: `src/hypershell/core/logging.py` slot machinery + `tests/test_logging.py`. Read-only survey for `/hs-plan`.

## 1. The fix surface (exact mechanics)

Names, as the code actually builds them (path = `client-a123.log`, `root='client-a123'`, `ext='.log'`):

- **Canonical slot (n=1):** `client-a123.log`; lock sidecar `client-a123.log.lock`.
- **Fallthrough slots (n≥2):** `client-a123-2.log`, … ; sidecar `client-a123-2.log.lock` (`.lock` appended to the *full* name incl. `.log`).
- **Rotated files** strip the last ext (`basename_without_ext`) → `client-a123.1` / `.20260723` / `.20260723-140000` (+ compression ext). Each slot has its **own** rotation namespace (`client-a123-2.1`).

**`claim_file_slot` (L701-713)** loops n=1..max_slots, calls `_try_lock(candidate + '.lock')` (L671/684: `open(mode='w')` on the *sidecar* — never touches the `.log`; keeps the handle in module-global `_slot_locks` for process life so the OS releases on crash). First lockable candidate wins.

**Key finding — R2/R3 already work on POSIX.** The FileHandler opens `mode='a', delay=True` (L377). When a previous owner dies the OS drops its `flock`, so the next process's `_try_lock('client-a123.log.lock')` succeeds → returns the canonical path → appended to. The existing integration test `test_slot_reclaimed_after_owner_dies` proves this. So R2/R3 need **no new mechanism on filesystems where `flock` works** — the bug is elsewhere.

**Root cause (R1).** `_try_lock` catches **all** `OSError` and returns `None` — it conflates a *conflict* (live owner) with an *unsupported/errored* lock (Lustre mounted without `-o flock`, which raises `EOPNOTSUPP`/`ENOSYS`/`EINVAL`). Every generation then errors on n=1, falls through to a fresh `-N` slot → unbounded proliferation, plus a matching pile of `.lock` sidecars. Note: Lustre `-o localflock` (node-local locks) is actually *sufficient* for us — hostname is baked in, so we only ever need same-host locks. The failure is the *no-flock* case.

**Second proliferation source (R5/R8).** Both the `_LOCKING = False` branch (L694→713) and the "exhausted max_slots" branch (L712-713) return `f'{root}-{os.getpid()}{ext}'` — **one file per PID**. The no-locking fallback is itself a proliferation bug the fix must close.

**Collision check (safe):** `client-a123.log.lock` is never numeric/date, so it cannot match `re_pattern_count/date/datetime` — `search_files(canonical)` ignores it. Slot `client-a123-2.log` (`-2.log`) also never matches `client-a123\.([0-9]+)` (needs a literal `.` then digits), so canonical rotation never sees sibling slots. Reap logic must therefore key off the distinct **`-N` slot** shape, not the rotation regex.

## 2. Recommended reap/reclaim algorithm shape

1. **Discriminate errno in `_try_lock`** — return three outcomes: LOCKED (handle) / CONFLICT / UNSUPPORTED. Treat `{EAGAIN, EWOULDBLOCK}` (and EACCES) as CONFLICT (live owner → keep the fallthrough → **R7**); everything else (`EOPNOTSUPP, ENOTSUP, ENOSYS, EINVAL, ENOLCK, EROFS`) as UNSUPPORTED. `msvcrt` branch: keep as conflict-only.
2. **On UNSUPPORTED (or `_LOCKING=False`): degrade to the canonical per-host path** (reuse + append). Bounded to one file/host; accepts possible interleave — exactly the GOAL's resolved R5 choice. Replaces both PID-suffix returns.
3. **Reclaim canonical (R2/R3):** unchanged loop — already correct on working `flock`; degrade extends it to Lustre.
4. **Opportunistic reap (R6), performed only by the process that just acquired the *canonical* lock** (natural single-reaper-per-host serialization). For each sibling matching the exact `-N` slot shape: `_try_lock(slot + '.lock')` → if LOCKED (orphan) remove the orphaned `-N.log`; if CONFLICT/UNSUPPORTED, skip. **Never** touch rotated forms (`prefix.N`/`prefix.YYYYMMDD`) or the `main` role — mirror `recover_interrupted_compression`'s `prefix = basename_without_ext + '.'` prefix-scoping discipline (L513-533).

**Race to flag for the plan author (do not hand-wave):** unlinking a `.lock` sidecar is a POSIX inode-reuse footgun — process A can hold `flock` on the old inode while B does `open(mode='w')` and gets a *new* inode + a *new* independent lock → two writers. Safest: **reap the `-N.log` data file (the real disk cost) and leave/limit the 0-byte `.lock` sidecars** (their count is bounded once `-N` proliferation stops), or unlink `.lock` only while holding its `flock` and accept the residual small window. Also a scope gap: an orphan `-N.log`'s *own* rotated children (`client-a123-2.3`) are also orphaned; reaping just the bare slot is the minimum for `appetite=small`.

## 3. R5 mechanism recommendation

**Recommend option (iii) hybrid = errno-discrimination + degrade-to-canonical, with (ii) opportunistic reap layered on.** Rationale: (i) alone (degrade) fixes the ongoing Lustre bleed with minimal code and honors R7 where locking works; (ii) alone can't help when locking is genuinely broken (can't prove orphan-vs-live). The hybrid keeps distinct files + strict single-writer where `flock` works, and collapses to one reused file where it doesn't. Decision is the plan author's + human's per GOAL Q2. Flag: no new config knob is *needed* — the whole fix is internal (errno + degrade + reap), so it avoids the §12 docs/_include + `share/` completions burden. A knob (`logging.file.lock = auto|off`) is optional and would force those companion edits in-commit; recommend against for a small fix.

## 4. Test approach

**Existing coverage (`tests/test_logging.py`):** role mapping, host-scoping, `resolve_log_path` client decoration, `claim_file_slot` within-process disambiguation (unit, skip if `not _LOCKING`), and the crash-reclaim integration test (`_HOLDER` subprocess via `Popen`, reads claimed path on stdout, asserts contention→slot-2 then reclaim after `terminate()`). Tests **call `claim_file_slot` directly** already.

**Simulating a lockless/erroring FS (the key technique):**
- Unit: `monkeypatch.setattr('hypershell.core.logging._try_lock', fake)` — model an in-memory `name→owner` dict so no real fds leak; assert claim degrades to canonical vs. falls through per outcome.
- To test the *discrimination* itself: patch `fcntl.flock` to `raise OSError(errno.EOPNOTSUPP, ...)` vs `OSError(errno.EAGAIN, ...)` and assert claim returns canonical (degrade) vs `-2` (conflict fallthrough).
- No-locking branch: `monkeypatch.setattr('hypershell.core.logging._LOCKING', False)` — note the test module's top-level `from ... import _LOCKING` is a *separate binding*; patch the **module attribute**, not the imported copy; skip/guards read the copy.

**File-count assertions after N generations:** cleanest is N sequential subprocess spawns (lock auto-released at exit) asserting only `client.log` survives (POSIX reclaim). For the Lustre degrade path, either inject the errno via an env var the fake `_try_lock` reads in the child, or assert in-process with the monkeypatched fake and `glob('client-*.log')` count == 1.

**`_slot_locks` global leak — cleanup required.** `_slot_locks` (L698) accumulates handles and is **never cleared**; in-process "owner-died-then-reclaim" tests must release (close handles + clear the list) in teardown. Recommend an autouse fixture in `test_logging.py` that snapshots and closes `_slot_locks` after each test. Existing tests dodge this only because each uses a unique `tmp_path`.

**Markers:** `@mark.unit` for monkeypatched errno/degrade/reap logic; `@mark.integration` for subprocess crash-reclaim and real-CLI emission (`temp_site` + `HYPERSHELL_LOGGING_FILE=enabled`, glob for the file).

## 5. Invariants / risks

- **§5 (compression thread + `FILE_LOCK`):** reap runs at claim time (in `initialize_logging`, alongside `recover_interrupted_compression` at L837). Reap must touch only exact `-N.log`/`-N.log.lock`, never `prefix.N`/`prefix.YYYYMMDD` rotated files (managed under `FILE_LOCK` by the compression thread), and never the process's own newly-claimed files.
- **§12:** internal-only fix ⇒ no docs/completions churn. Confirm before adding any knob.
- **R8:** keep the `msvcrt` branch; `main` role stays un-host-scoped and un-reaped; degrade path is still host-scoped (`path` is already `client-<host>.log`), preserving cross-host safety.
