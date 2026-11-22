# 03 — Relay workload scans (R7) and the tail drive (R5)

Read-only research for `hs-plan`, 2026-11-21, worktree head `6f4e809f`. Nothing was run but
`tail_run.py --help`, a loopback socket probe and a watchdog probe.

**No `timeout` on this host** (`timeout` and `gtimeout` are both missing). Every bounded command below
uses this zsh function. It sends SIGINT, then SIGKILL 10 s later, and exits 124 on timeout. SIGINT lets
pytest run its fixture teardown (`Group.kill`), and lets `tail_run.py` run its `finally`, which kills
every process group it started (`tail_run.py:295-301`):

```zsh
bounded() { perl -e '$t=shift; $p=fork; if(!$p){setpgrp(0,0); exec @ARGV or die "exec: $!"} $SIG{ALRM}=sub{kill "INT",-$p; for(1..20){exit 124 if waitpid($p,1)>0; select(undef,undef,undef,0.5)} kill "KILL",-$p; waitpid $p,0; exit 124}; alarm $t; waitpid $p,0; exit($?>>8)' "$@"; }
export UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-rst UV_CACHE_DIR=/tmp/claude-501/uv-cache-rst
```

`/tmp/claude-501/venv314-rst` is an editable install of this worktree (its `direct_url.json` points
here) with psutil 7.0.0.

## Part A — R7: the psutil reads in `tests/test_relay_workload.py`

| Site | Read | Skips today | Caller's count / retry |
|------|------|-------------|------------------------|
| `site_processes` :248-253 | `Process(relay.pid).children(recursive=True)` | `NoSuchProcess` → `[]` | (the listing every scan uses) |
| `running_tasks` :256-272 | `cmdline()` :268 | `psutil.Error` :270 (already covers `AccessDenied`) | used inside `until(...)` everywhere |
| **`scan_site` :275-296** | `cmdline(), environ()` :284 | `NoSuchProcess, ZombieProcess` :285. **`AccessDenied` propagates.** Empty environ skipped :287 | one call per relay, :381, after `until(running_tasks)`; asserts `clients>=2, tasks>=1, hits==0`, the names rules :382-385. **No retry.** |
| `alive` :298-303 | `status()` | `NoSuchProcess` | not a cmdline/environ read; outside R7 |
| **`scan_site_key` :501-523** | `cmdline(), environ()` :511 | `NoSuchProcess, ZombieProcess` :512. **`AccessDenied` propagates** (the CI failure, #107). Empty environ dropped :514 | in `until(... tasks>=1 and clients>=2 ...)` :539, so a skipped process is retried |
| `site_clients` :571-580 | `cmdline()` :576 | `psutil.Error` :578 | pids only, at :722, :766, :855, :1011 |
| **:1058** in `test_site_clients_keep_the_relay_config_file_without_relay_settings` | `proc.environ()` for each `site_clients(relay)` | **nothing** | one shot after `until(running_tasks)` :1057; asserts `environ and all(... == str(copy))`. The relay has `-N 1`, so one client. |

`ZombieProcess` is a subclass of `NoSuchProcess`, and both of those and `AccessDenied` subclass
`psutil.Error`. `tests/test_relay_outage.py:37` imports `until`, `running_tasks`, `site_processes` and
others from this module, so keep those names and their signatures.

**Smallest refactor (test-only).** Add one reader and route the three bold sites through it:

```python
UNREADABLE = (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess)

def read_process(proc) -> Optional[Tuple[List[str], Dict[str, str]]]:
    """A process's command line and environment, or None when they cannot be read (it exited, or is exiting and refuses)."""
    try:
        return proc.cmdline(), proc.environ()
    except UNREADABLE:
        return None
```

- `scan_site`: `for read in map(read_process, site_processes(relay)): if read is None: continue`.
  The rest is unchanged, and the `if not environ` skip stays.
- `scan_site_key`: `procs = [read for read in map(read_process, site_processes(relay)) if read]`. The
  existing empty-environment filter (:514) stays as it is.
- `client_environments(relay)`: the new function returns
  `[env for cmd, env in filter(None, map(read_process, site_processes(relay))) if 'client' in cmd]`.
  It keeps an empty `{}`, so a withheld environment still fails the assertion as it does today. At
  :1058, replace the read with
  `environ = until(lambda: client_environments(relay), time.monotonic() + 60, 'a readable site client environment')`.
  The `assert environ and all(...)` on the next line stays word for word. The `until` matters: with
  one client, a skip without a retry would turn into an `environ == []` failure.
- Counts and the "no key seen" checks are unchanged over the processes that could be read. Leave
  `running_tasks` and `site_clients` alone, since they already tolerate the error. `Optional` and
  `Tuple` need adding to the `typing` import (:35).

**Unit test.** Put it in `tests/test_relay_workload.py`, after `scan_site_key`. It needs no relay,
server or sockets.

```python
class SiteProcess:
    """A stand-in for a site process; `refuse` is raised by environ(), as an exiting process can."""
    def __init__(self, cmdline, environ, refuse=None): self._cmd, self._env, self._refuse = cmdline, environ, refuse
    def cmdline(self): return self._cmd
    def environ(self):
        if self._refuse: raise self._refuse
        return self._env

@mark.unit
@mark.parametrize('refuse', [psutil.AccessDenied(pid=1), psutil.NoSuchProcess(pid=1), psutil.ZombieProcess(pid=1)],
                  ids=['AccessDenied', 'NoSuchProcess', 'ZombieProcess'])
def test_scans_skip_a_process_whose_read_is_refused(monkeypatch, tmp_path, refuse) -> None:
    barrier, site_key = tmp_path / 'barrier', 'k' * 128
    procs = [SiteProcess(['hyper-shell', 'client'], {AUTH_ENV: site_key, 'SITE_MARK': 'a'}),
             SiteProcess(['hyper-shell', 'client'], {}, refuse=refuse),   # refused mid-list
             SiteProcess(['python', 'waiter.py', str(barrier)], {'SITE_MARK': 'a'})]
    monkeypatch.setattr(sys.modules[__name__], 'site_processes', lambda relay: procs)
    found = scan_site(None, 'upstream-key', barrier)
    assert (found['read'], found['clients'], found['tasks'], found['hits']) == (2, 1, 1, 0)
    assert scan_site_key(None, barrier) == {'clients': 1, 'keyed_clients': 1, 'keys': {site_key},
                                            'tasks': 1, 'argv': 0, 'task_hits': 0}
    assert client_environments(None) == [{AUTH_ENV: site_key, 'SITE_MARK': 'a'}]
```

On `f3fb4dd2`, the `[AccessDenied]` case fails inside `scan_site` because `AccessDenied` propagates
(:284). That is the genuine red. The other two ids pass there for both scans and fail only at the end,
where `client_environments` is missing. If the plan wants a red-only leg, use the AccessDenied id.
Node ids (quote them in zsh):
`'tests/test_relay_workload.py::test_scans_skip_a_process_whose_read_is_refused[AccessDenied]'` (and
`[NoSuchProcess]`, `[ZombieProcess]`).

**Running.** Everything else in the file is `@mark.integration`: 16 functions, 18 items with the two
parametrized ones. Each test launches real `hs server`/`hs submit`/`hs relay` processes in their own
sessions over loopback, with `hs` taken from `PATH`. `uv run` puts the venv's `bin` first, and
`Group.start` is at `test_dispatch_workload.py:45`. Each test caps itself at `DEADLINE = 240` s. The
only skip is SIGINT being ignored (:979). Loopback bind and connect work **inside the sandbox**
(probed), and the tests inspect only processes they started. I did not measure the file's wall time;
expect minutes, not seconds. Run serially (no `-n`):

```zsh
bounded 120  uv run pytest -p no:cacheprovider -q -m unit tests/test_relay_workload.py
bounded 1500 uv run pytest -p no:cacheprovider -q tests/test_relay_workload.py
bounded 900  uv run pytest -p no:cacheprovider -q tests/test_relay_outage.py   # imports the helpers
```

## Part B — R5: the drive

**`tail_run.py` CLI** (`--help`, and :204-223):
`tail_run.py OUT RUN_ID [--label L] [--level TRACE|DEBUG] [--tasks N] [--sleep S] [--clients C] [--threads T]`.
The defaults are the R8 size (2000, `0.2`, 4, 8; :63). For 2004 tasks pass `--tasks 2004`.
`RUN_ID` and `LABEL` must match `[A-Za-z0-9._-]+`. A run id is used once, and an existing
`OUT/RUN_ID.json` gives exit 64 (:226-228). Exit codes: 0 when both runs complete, 1 otherwise (the
record is still written), 64 on bad usage.

- **What one run is.** A direct `hs cluster -N 32` run, then the relay run: `hs submit`,
  `hs server --restart -H localhost -p <free>`, and
  `hs relay -H localhost -p <port> --bind 127.0.0.1 --port <free> -N 4`, with a launcher that rewrites
  `-H` to 127.0.0.1 (`relay_throughput.py:148-187`). `[relay] num_threads` is in a site config. Ports
  come from `free_port()` on 127.0.0.1. **Auth goes only through the environment:**
  `HYPERSHELL_SERVER_AUTH` and `HYPERSHELL_RELAY_UPSTREAM_AUTH` (:170-174), never `-k`. Every
  process runs at TRACE, system style, `TZ=UTC`.
- **Where it writes.** `OUT/RUN_ID.json`, `OUT/RUN_ID-server.txt` and `OUT/RUN_ID-relay.txt`: windows
  from `T_last` − 10 s, with host, user, home, scratch and key scrubbed (:306-349). Sites go under
  `$TMPDIR` and are kept. Point `HS` at the hs under test; `relay_throughput.py:87` puts its `bin`
  first on `PATH` for every child.
- **Bounds of its own.** `timeout=1800` per run, `live_wait=120`, and 60 s for the relay after the
  server (:273-274, `relay_throughput.py:179-182`). The `finally` and the SIGTERM/SIGHUP handlers kill
  every process group it started. Those internal caps are loose, so wrap each run in
  `bounded 300`.
- **Duration.** About 50 s a run: direct 20.2 s, relay 19.3–25.3 s, plus setup. The shaping records'
  mtimes put 8 runs in about 6 min.
- **Sandbox.** Loopback works inside it (probed). Turn the sandbox off only on an EPERM. No psutil
  is involved.
- **Records.** `commit` comes from `git rev-parse --short HEAD`, and `src_changed` from
  `git diff HEAD -- src` (:276-280). Run on the **committed** head so that `src_changed: false`.

**Shaping baseline.** The files are in
`<scratch>/rst/out`:
24 files, 2.5 MB, `scrubbed: 0`. I found no residue of host, user or paths. The schema is as above:
`kind: relay-hardening-tail`, `commit f3fb4dd2`, `sizes`, `complete`, and for both `direct` and
`relay` the fields `{wall, status, completed, last_task, t_last, t_exit, tail, tail_poll, site}`;
`relay` adds `{segments s1–s9, stamps, counts}`.

| Run | Label | Tasks | Tail | Direct | s2 | s4 |
|-----|-------|-------|------|--------|----|----|
| develop-01…06 | `develop` | 2000 | 1.78, 1.72, 1.71, 1.76, 1.77, 1.59 | 1.65–1.76 | 0.04–0.15 | 0.60–0.76 |
| develop-odd-01, -02 | `develop-2004` | 2004 | 6.75, 7.45 | 1.68, 1.72 | 3.06, 3.20 | 2.72, 2.59 |

Each odd relay window has 4 `Bundle wait exceeded` lines, and the 2000-task windows have none. No
script or command line survives in the scratch directory. The labels and the `<scratch>/relay-throughput-*`
sites (in `rst/sites/`) imply this form:
`HS=<venv>/bin/hs TMPDIR=…/rst/sites python tail_run.py …/rst/out develop-NN --label develop [--tasks 2004]`.

**Proposed layout.**

```
spec/relay-shutdown-tail/evidence/
  baseline/  develop-0{1..6}{.json,-relay.txt,-server.txt}, develop-odd-0{1,2}{…}   (copied verbatim)
  head/      head-2000-NN{…}, head-2004-NN{…}                                       (R5 runs)
  README.md  method, host, the table (baseline beside head), what each window shows
```

**Per-run command** (from the worktree root, on the committed head):

```zsh
S=<scratch>/rst/head-sites; mkdir -p $S
HS=/tmp/claude-501/venv314-rst/bin/hs TMPDIR=$S PYTHONDONTWRITEBYTECODE=1 bounded 300 \
  /tmp/claude-501/venv314-rst/bin/python spec/relay-hardening/drive/tail_run.py \
  spec/relay-shutdown-tail/evidence/head head-2004-01 --label head-2004 --tasks 2004
```

For a 2000-task run, use `head-2000-NN --label head-2000 --tasks 2000`. Call the venv python directly
rather than through `uv run`, so the watchdog's SIGINT reaches `tail_run.py`. Do not use
`tail_laptop.sh`: it names runs `laptop-NN`, its budget counts per directory, and it runs
`tail_judge.py`.

**Schedule.** Six planned runs, alternating sizes so that host drift falls on both:
2004, 2000, 2004, 2000, 2004, 2000. Four are spare. Spend a spare only to replace a run that did not
complete, at that run's size. Every started run counts toward the 10, so stop at 10 whatever happens.
A run over 3 s ends the drive early. Record it; it fails R5 and goes back to the plan, and it is not
re-rolled.

**Judge: write a new one.** `tail_judge.py` can be run read-only for display only.
`--require-none-over 3 evidence/head` checks the bound, and a missing tail counts as over. But it
flags every 2004 run `SIZE`, and `--budget` fails on them (:82-83, :166-175). Its `TAIL>5` and
segment bounds are the wrong gate here, and it cannot count runs per size. Proposed:
`spec/relay-shutdown-tail/drive/tail_bound.py HEAD_DIR [--baseline DIR] [--table PATH]
[--limit 3] [--min-runs 3] [--budget 10]`, standard library only. It reads the
`kind == relay-hardening-tail` records and **exits 1** if any of these hold:

- more than 10 records;
- any record not at `{sleep '0.2', clients 4, threads 8}` with `tasks` in {2000, 2004};
- fewer than 3 *complete* runs at either size;
- any relay `tail` missing or over 3.0 s;
- any `src_changed` true, or more than one `commit`.

It exits 64 on bad usage. It writes a markdown table to `--table` (default
`evidence/tails.md`, which the README includes or links) with these columns: run, label, commit,
tasks, tail, direct tail, s1–s9, relay/direct wall. Baseline rows come first, then head rows, then a
per-size min/max summary. A unit test in `tests/test_tail_bound.py` can load it by path on synthetic
records, as `tests/test_tail_judge.py:31-39` does. The R5 verify would be:
`uv run python spec/relay-shutdown-tail/drive/tail_bound.py spec/relay-shutdown-tail/evidence/head --baseline spec/relay-shutdown-tail/evidence/baseline`.
Keep in mind GOAL's note that `tail_run.py` puts the 2004-run hold in s2 and s4, not s1, so the judge
should gate on `tail` only, not on any segment.
