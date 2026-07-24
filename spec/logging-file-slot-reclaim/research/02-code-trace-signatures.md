# Code Trace & Failure Signatures — per-host log-slot reclaim

All line refs are `src/hypershell/core/logging.py` unless noted.

## 1. Full call trace (process start → resolved log path)

Role is declared by the entry point, never sniffed from argv:

- `main(argv)` (`__init__.py:136`) →
  `initialize_logging(role=role_from_command(argv[0] if argv else None))` (`__init__.py:139`).
  `main_x` prepends `cluster`, so `hsx` → role `cluster`.
- `role_from_command(cmd)` (`:649`): returns `cmd` if it is in
  `DISTRIBUTED_ROLES = {server, cluster, client, submit}` (`:646`), else `DEFAULT_ROLE='main'` (`:645`).
- `initialize_logging(role)` (`:730`, guarded once by `_INIT`) computes
  `default_file = default_file_for(role)` (`:744`).
- `default_file_for(role)` (`:654`): `main` → `main.log`; any distributed role →
  `<role>-<HOSTNAME_SHORT>.log` under `default_path.log`
  (platform Logs dir, `core/platform.py:54-99`). So a launched client → `client-<host>.log`.
- If file logging is enabled, `resolve_log_path(path, role, is_default)` (`:716`, called `:757`/`:771`).
  For `role=='client'` **and** a non-default explicit path it host-decorates:
  `…-client-<host>.log` (`:718-722`); the default is already host-scoped. Then →
  `claim_file_slot(path)` (`:701`).
- `claim_file_slot` (`:701`): `os.makedirs(dir)`, then for `n` in `1..max_slots(100)`:
  candidate = `path` (n==1) else `<root>-<n><ext>`; call `_try_lock(candidate + '.lock')` (`:708`).
  First non-None handle → append to `_slot_locks` (held for process life) and return candidate.
  Loop exhausted → `warn("Exhausted 100 log-file slots … using PID suffix")` (`:712`) and return
  `<root>-<pid><ext>` (`:713`).
- `_try_lock(path)` (`:669` fcntl branch): `handle = open(path, mode='w')` (**truncates the sidecar**),
  then `fcntl.flock(fileno, LOCK_EX|LOCK_NB)`; on success return handle, on **any** `OSError`
  `handle.close(); return None` (`:675-677`). msvcrt branch is analogous; if neither imports,
  `_LOCKING=False` and `claim_file_slot` skips locking entirely, always returning `<root>-<pid><ext>`.

The returned path becomes `TimedRotatingFileHandler`/`SizeRotatingFileHandler.filename`; the actual
`.log` file is created lazily (`delay=True`, `:377`) only for the **returned** path — probed-but-not-won
slots never get a `.log`, only a `.lock`.

**Root flaw:** `except OSError:` (`:675`) cannot distinguish a genuine lock **conflict**
(`EAGAIN`/`EWOULDBLOCK`) from lock being **unsupported/broken** (`ENOSYS`/`EOPNOTSUPP`/`ENOLCK`/`EINVAL`,
common on Lustre without `-o flock`, some ZFS/NFS setups). Both map to "slot taken."

## 2. Failure-signature table (N serial generations, one host)

### Theory A — flock raises OSError on EVERY candidate (unsupported FS)
Every candidate 1..100 errors identically → loop always exhausts → PID fallback.
- Per process: creates **100 `.lock` sidecars** (`client-host.lock`, `-2.lock`…`-100.lock`), emits the
  **"Exhausted 100 log-file slots … using PID suffix" WARNING** (to stderr; file handler not yet attached),
  writes exactly one real log `client-host-<pid>.log`.
- Canonical `client-host.log` is **never created**; no sequential `-N.log` files are created (numbered
  slots only get `.lock`, never `.log`).
- Across N gens: `.lock` count stays **fixed at 100** (deterministic names, re-`open('w')`-truncated each
  time); `client-host-<pid>.log` grows by 1 per generation.
- **Signature: dominated by non-sequential `-<pid>.log` files + a constant 100 `.lock` files + a warning
  on every launch.** Real (unbounded) proliferation, but of PID-suffixed logs, not `-2/-3` logs.

### Theory B — flock succeeds, but a released lock is not seen as released (stale / spurious EAGAIN)
Gen K sees slots `1..K-1` as still "held" (stale) and acquires slot K.
- Gen 1 → `client-host.log`; Gen 2 → `client-host-2.log`; … Gen K → `client-host-<K>.log`.
- Across N gens: `.lock` files `client-host.lock`…`client-host-<N>.lock` (**N, growing**) + real logs
  `client-host.log`, `-2.log`…`-N.log` (**N, growing**). Canonical is created by gen 1 and **never reclaimed**.
  Once N>100, the warning + PID suffix also kick in.
- **Signature: dominated by SEQUENTIAL `-N.log` files + growing numbered `.lock` files, canonical present.**
  This matches the reported symptom ("canonical never reclaimed; `.lock` and `-N.log` accumulate") most literally.

### Theory C — flock is a working node-local lock that always succeeds/releases (e.g. Lustre `localflock`)
Acquire and **release** both work on the local node. Serial gens on one host: gen1 holds `client-host.log`,
exits → OS releases → gen2 reclaims `client-host.log`.
- Only ever `client-host.log` + `client-host.lock` (both reused). **No proliferation — does NOT reproduce
  the bug.** (localflock's only hazard is cross-node contention on the *same* filename, which the host-scoped
  default filename already precludes.)

**Assessment:** the user's literal wording matches **Theory B**. Theory A also yields unbounded proliferation
but is distinguishable: PID-valued (not sequential) suffixes, a fixed 100 `.lock` files, and a per-launch
"Exhausted…" warning. Both A and B are the *same* code defect (`except OSError` conflating error with
conflict); the discriminator to confirm with the user's real `ls` is: sequential `-2/-3.log` + growing lock
count (B) vs. big-number `-<pid>.log` + exactly 100 locks + warning spam (A).

## 3. Reclaim analysis (working FS — apfs/ext4/xfs)
On a filesystem where flock is correct, reclaim works and there is **no bug**. Reasoning from code: the held
handle lives only in `_slot_locks` (`:698`, `:710`) for the life of the process; on exit (clean or crash) the
OS drops the flock. A later same-host generation restarts the loop at n=1, `_try_lock('client-host.lock')`
succeeds (the sidecar file persists but is unlocked), and the canonical slot is returned again. This is
asserted by `tests/test_logging.py::test_slot_reclaimed_after_owner_dies` (`:91-106`): while an owner lives a
peer is pushed to `client-2.log`; after the owner dies, a new claim returns `client.log`. So locally the
canonical slot **is** reclaimed — the defect only surfaces where flock errors or goes stale.

## 4. Cleanup gap
Nothing in the module ever deletes `.lock` sidecars or reclaims/removes orphaned `-N.log`/`-<pid>.log` slot
files. The only deletion path is `compress_file` → `files_eligible_for_deletion` (`:334`, `:488-491`), which
(a) only runs when compression is enabled (`COMPRESSION_MODE is not None`) and (b) only matches **rotation**
artifacts via `search_files` (`:309`) whose regex is `re_pattern_count` = `prefix\.([0-9]+)ext` (`:230`) — i.e.
`client-host.<N>` with a **dot** (rotation counter), never `client-host-<N>.log` with a **dash** (slot suffix).
Slot files and `.lock` files are therefore invisible to pruning. `recover_interrupted_compression` (`:513`)
only touches `.partial` files. **No `.lock` is ever unlinked.**

`open(path, mode='w')` truncating the sidecar each attempt (`:671`) is **harmless for data** (the `.lock`
file has no contents — it is a pure flock target), but it is the mechanism that **creates** an orphan `.lock`
for every probed slot (the file springs into existence as a side effect of the failed probe). Using
`O_CREAT` without truncate would not change accumulation; the real fix must both (a) not treat unsupported
flock as a conflict and (b) clean up / not leave sidecars for slots never won.

## 5. Process lifecycle facts (feeds invariant §11: no shared client-argv builder)
- **Many client generations per host under autoscaling.** `AutoScaler.scale()`
  (`cluster/remote.py:531-536`) runs `Popen(self.launcher)` — a full
  `<launcher(srun/mpirun/…)> … hyper-shell client …` (built once at `remote.py:832-836`) — every time
  pressure/INIT demands growth, over the whole run. Clients self-exit on idle (the autoscaler never kills
  them; `clean()` at `:542` only `poll()`s and reaps PIDs). Scale-to-zero then scale-up means the **same
  physical hosts host many serial client generations** → the exact serial-reclaim path that Theories A/B break.
- **Clean exit → OS releases flock.** Client shutdown is FSM-driven: schedulers/executors/collector/heartbeat
  each `machine.halt()` in `stop()` (`client.py:286,425,955,1060`) and the process exits normally, so on a
  working FS the flock is released and the slot reclaimable (§3). On a broken FS the *lock* mechanism is what
  fails, not the exit.
- **No log path is forwarded to launched clients.** All three argv builders omit any `--log-*`/path:
  - `RemoteCluster` (`remote.py:303-306`) and `AutoScalingCluster` (`remote.py:832-836`):
    `… client -H <HOSTNAME> -p <port> -N -b -w -t <template> -k <auth> -d -S [<client_args>]`.
  - `SSHCluster` (`ssh.py:296-306`): per-host `… <remote_exe> client -H <HOSTNAME> …`.
  - `LocalCluster` embeds threads in-process (no client argv).
  `client_args` only ever carries `--no-confirm/--capture/--monitor/-C/-M/-T/-W/-R/--no-tls` — never a log
  path. So **every launched client independently derives `client-<its-own-host>.log`** via
  `role_from_command('client')`. Cross-host names differ (host in filename); the proliferation is purely the
  **same-host serial-generation reclaim failure**. A fix that adds/forward a logging flag must be replicated
  across `local.py`/`remote.py` (×2)/`ssh.py` (§11: no shared client-argv builder), but the cleaner fix keeps
  path derivation client-side and repairs `_try_lock`/reclaim/cleanup in `core/logging.py` alone.
