# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""One measured run of the relay throughput check, for the end-of-run tail investigation.

    python tail_run.py OUT RUN_ID [--label LABEL] [--level TRACE] [--tasks N --sleep S --clients C --threads T]
    python tail_run.py --remeasure SCRATCH RECORD...

It loads spec/relay/drive/relay_throughput.py by path (that file is the relay cycle's record and is
not edited) and runs its two runs, direct (`hs cluster`) then relay (`hs submit`, `hs server
--restart`, `hs relay`), at its defaults unless a size is given: 2000 tasks of 0.2 s on 4 clients of
8 threads, the size of the R8 budget runs, which never set a size. Every process it starts logs in
the system style, in UTC (TZ=UTC), at LEVEL (default TRACE: the scheduler's back-off, the heart
monitor's wait and the server's disconnect lines are TRACE). `HS` names the hs under test, as for
relay_throughput.py; the scratch sites go under TMPDIR and are kept.

It writes OUT/RUN_ID.json, the record:

  - the commit, label, sizes (and whether they are the R8 size), both walls, exit statuses and counts;
  - the tail of each run: from the last task's completion_time in that run's database (`T_last`) to
    the last line of the log of the process that ends the run (the upstream server, or the cluster),
    and the same to the moment the driver saw the process exit (`tail_poll`, 0.5 s poll);
  - the relay run's stamps and the nine segments between them (see STAMPS and SEGMENTS), keyed to
    the last task's id; WARNING, ERROR and CRITICAL line counts of the upstream server's and relay's logs;
  - the scratch sites, relative to TMPDIR (written `<scratch>/...`), where the full logs stay.

and OUT/RUN_ID-server.txt and OUT/RUN_ID-relay.txt: the upstream server's and the relay's log lines
from `T_last` - 10 s to the end, less the per-task lines (a task scheduled, run, completed or applied)
of every task but the last, which the window's first line counts; with this host's name, the user's
name, the home directory, the scratch root and the run's key replaced. The record says how many of
those it found in what it wrote after the replacement (`scrubbed`, 0).

With --remeasure, it runs nothing: it measures the stamps and segments of each RECORD again from the
full logs kept in its scratch sites (SCRATCH stands for `<scratch>`), rewrites the record and marks it
`remeasured` (a run measured with an earlier list of stamps). The run budget is not touched.

Exit status: 0 when both runs finished (every task succeeded, both exited 0), whatever the tail; 1
otherwise (the record is still written, with `complete` false); 64 on bad usage. Every process it
starts is stopped when it ends, on a signal too.
"""


# Standard libs
import os
import re
import sys
import json
import time
import signal
import socket
import sqlite3
import getpass
import argparse
import importlib.util
import subprocess
from pathlib import Path
from datetime import datetime, timezone


HERE = Path(__file__).resolve().parent
THROUGHPUT = HERE.parents[1] / 'relay' / 'drive' / 'relay_throughput.py'

R8_SIZE = {'tasks': 2000, 'sleep': '0.2', 'clients': 4, 'threads': 8}
WINDOW = 10.0  # Seconds before T_last that the kept log windows start.
LINE_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) ')
SAFE_ID = re.compile(r'^[A-Za-z0-9._-]+$')
PER_TASK = re.compile(r'\b(Running|Completed|Scheduled) task \(|Result applied for task \(')

# The relay run's stamps, in order: the log, the texts that mark it (any one), and what it means. Each
# is the first matching line at or after the one before (the first, T_last, comes from the database).
# A server that finishes dispatch as soon as it is signalled logs "Finishing dispatch" first.
STAMPS = (
    ('collected', 'relay', ('[hypershell.server] Completed task ({last})',),
     'the relay-local server stored the last result: its site client returned it'),
    ('returned', 'relay', ('results upstream',), 'the relay returned results upstream'),
    ('landed', 'server', ('Completed task ({last})',), 'the upstream server stored the last result'),
    ('scheduler', 'server', ('Done (scheduler)',), 'the upstream scheduler finished'),
    ('signal', 'server', ('Finishing dispatch', 'Signaling clients'), 'the upstream server signalled its clients'),
    ('heard', 'relay', ('Upstream server is shutting down',), 'the relay heard the shutdown'),
    ('site_stop', 'relay', ('[hypershell.server] Finishing dispatch', '[hypershell.server] Signaling clients'),
     'the relay-local server signalled the site'),
    ('final', 'relay', ('Upstream heartbeat - final',), 'the relay sent its final heartbeat'),
)
SEGMENTS = ('s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9')  # s9: final beat to the upstream server's last line


def stamp(line: str):
    """The time of a log line in the system style (UTC), or None."""
    match = LINE_TIME.match(line)
    if not match:
        return None
    return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=timezone.utc).timestamp()


def read_lines(path: Path) -> list:
    """The lines of a file, or none if it is missing."""
    try:
        with open(path, errors='replace') as stream:
            return stream.read().splitlines()
    except FileNotFoundError:
        return []


def last_task(site: Path):
    """The id and completion time (epoch, UTC) of the task that completed last in a run's database."""
    try:
        with sqlite3.connect(f'file:{site / "task.db"}?mode=ro', uri=True, timeout=5) as db:
            row = db.execute('SELECT id, completion_time FROM task WHERE completion_time IS NOT NULL '
                             'ORDER BY completion_time DESC LIMIT 1').fetchone()
    except sqlite3.Error:
        return None, None
    if not row:
        return None, None
    value = datetime.fromisoformat(str(row[1]))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # Every process ran with TZ=UTC.
    return row[0], value.timestamp()


def last_stamp(found: list):
    """The stamp of the last stamped line."""
    for line in reversed(found):
        value = stamp(line)
        if value is not None:
            return value
    return None


def first_after(found: list, texts: tuple, after: float):
    """The stamp of the first line holding any of `texts` at or after `after`, or None."""
    for line in found:
        value = stamp(line)
        if value is not None and value >= after and any(text in line for text in texts):
            return value
    return None


def count(found: list, level: str) -> int:
    """Lines logged at `level`."""
    return sum(1 for line in found if f' {level} ' in line or f'{level} [' in line)


def segments(site: Path, last_id: str, t_last: float) -> tuple:
    """The relay run's stamps (epoch) and the segments between them (seconds; None where a stamp is missing)."""
    logs = {'server': read_lines(site / 'server.log'), 'relay': read_lines(site / 'relay.log')}
    stamps = {'last': t_last}
    previous = t_last
    for name, log, texts, _ in STAMPS:
        value = None if previous is None else first_after(logs[log], tuple(text.format(last=last_id) for text in texts),
                                                          previous)
        stamps[name] = value
        previous = value
    stamps['exit'] = last_stamp(logs['server'])
    order = ['last'] + [name for name, *_ in STAMPS] + ['exit']
    found = {}
    for index, name in enumerate(SEGMENTS):
        start, end = stamps[order[index]], stamps[order[index + 1]]
        found[name] = None if start is None or end is None else round(end - start, 3)
    return stamps, found


class Scrubber:
    """Replaces this host's name, the user's name, the home directory, the scratch root and the key."""

    def __init__(self, scratch: str, key: str = None, roots: tuple = ()) -> None:
        host = socket.gethostname()
        user = getpass.getuser()
        pairs = [(scratch, '<scratch>'), (os.path.normpath(scratch), '<scratch>'),
                 (os.path.realpath(scratch), '<scratch>'), *((str(root), '<scratch>') for root in roots),
                 (str(Path.home()), '<home>'), (host, '<host>'), (host.split('.')[0], '<host>'),
                 (user, '<user>')]
        if key:
            pairs.append((key, '<key>'))
        self.pairs = [(old, new) for old, new in sorted(pairs, key=lambda pair: -len(pair[0])) if old]

    def __call__(self, text: str) -> str:
        for old, new in self.pairs:
            text = text.replace(old, new)
        return text

    def residue(self, text: str) -> int:
        """How many of the replaced strings are still in `text` (0 when scrubbed)."""
        return sum(text.count(old) for old, _ in self.pairs)


def remeasure(scratch: str, paths: list) -> int:
    """Measure the stamps and segments of each record again from its kept scratch site; rewrite it."""
    status = 0
    for path in map(Path, paths):
        record = json.loads(path.read_text())
        relay = record.get('relay') or {}
        site = Path(str(relay.get('site', '')).replace('<scratch>', scratch.rstrip('/')))
        if not (site / 'relay.log').exists() or relay.get('last_task') is None or relay.get('t_last') is None:
            print(f'tail_run: {path}: no kept site or last task to measure from ({site})', file=sys.stderr)
            status = 1
            continue
        relay['stamps'], relay['segments'] = segments(site, relay['last_task'], relay['t_last'])
        record['remeasured'] = True
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        print(f'tail_run: {path.name}: segments {relay["segments"]}')
    return status


def main() -> int:
    """Run once and write the record (or measure kept records again)."""
    if len(sys.argv) > 1 and sys.argv[1] == '--remeasure':
        if len(sys.argv) < 4:
            print('usage: tail_run.py --remeasure SCRATCH RECORD...', file=sys.stderr)
            return 64
        return remeasure(sys.argv[2], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('out', type=Path)
    parser.add_argument('run_id')
    parser.add_argument('--label', default='run')
    parser.add_argument('--level', default='TRACE', choices=['TRACE', 'DEBUG'])
    parser.add_argument('--tasks', type=int, default=R8_SIZE['tasks'])
    parser.add_argument('--sleep', default=R8_SIZE['sleep'])
    parser.add_argument('--clients', type=int, default=R8_SIZE['clients'])
    parser.add_argument('--threads', type=int, default=R8_SIZE['threads'])
    given = parser.parse_args()
    if not SAFE_ID.match(given.run_id) or not SAFE_ID.match(given.label):
        print('tail_run: RUN_ID and LABEL must match [A-Za-z0-9._-]+', file=sys.stderr)
        return 64
    given.out.mkdir(parents=True, exist_ok=True)
    record_path = given.out / f'{given.run_id}.json'
    if record_path.exists():
        print(f'tail_run: {record_path} exists; a run id is used once', file=sys.stderr)
        return 64

    os.environ['TZ'] = 'UTC'
    time.tzset()
    spec = importlib.util.spec_from_file_location('relay_throughput', THROUGHPUT)
    throughput = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(throughput)

    sites, exits, started, keys = {}, {}, [], []

    original_prepare, original_watch, original_start = throughput.prepare, throughput.watch, throughput.start

    def prepare(name, args):
        site, env, tasks = original_prepare(name, args)
        env.update(HYPERSHELL_LOGGING_LEVEL=given.level, HYPERSHELL_LOGGING_STYLE='system', TZ='UTC')
        sites[name] = site
        return site, env, tasks

    def watch(*args, **kwargs):
        status = original_watch(*args, **kwargs)
        exits[args[0]] = time.time()
        return status

    def start(*args, **kwargs):
        proc = original_start(*args, **kwargs)
        started.append(proc)
        return proc

    class Secrets:
        """The throughput check's `secrets`, keeping the key it makes so it can be scrubbed."""
        @staticmethod
        def token_hex(size):
            import secrets
            key = secrets.token_hex(size)
            keys.append(key)
            return key

    throughput.prepare, throughput.watch, throughput.start = prepare, watch, start
    throughput.secrets = Secrets

    def stop(*_):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    args = argparse.Namespace(tasks=given.tasks, sleep=given.sleep, clients=given.clients, threads=given.threads,
                              ratio=1.5, timeout=1800, live_wait=120)
    sizes = {'tasks': given.tasks, 'sleep': given.sleep, 'clients': given.clients, 'threads': given.threads}
    commit = subprocess.run(['git', '-C', str(HERE), 'rev-parse', '--short', 'HEAD'],
                            capture_output=True, text=True).stdout.strip() or None
    # Product source changed since that commit (a fix measured before it is committed with its runs).
    dirty = subprocess.run(['git', '-C', str(HERE), 'diff', '--quiet', 'HEAD', '--', '../../../src'],
                           capture_output=True).returncode == 1
    record = {'kind': 'relay-hardening-tail', 'run_id': given.run_id, 'label': given.label, 'commit': commit,
              'src_changed': dirty,
              'level': given.level, 'sizes': sizes, 'r8_size': sizes == R8_SIZE, 'complete': False}
    results = {}
    try:
        for name, run in (('direct', throughput.direct), ('relay', throughput.relay)):
            try:
                wall, status, done, site = run(args)
            except throughput.RunFailed as failure:
                record['failure'] = str(failure)
                break
            results[name] = {'wall': round(wall, 3), 'status': status, 'completed': done}
            print(f'tail_run: {given.run_id}: {name} {wall:.1f} s, exit {status}, {done} of {args.tasks}',
                  flush=True)
    finally:
        for proc in started:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    scratch = os.environ.get('TMPDIR', '/tmp').rstrip('/') or '/'
    scrub = Scrubber(scratch, keys[0] if keys else None, roots=tuple(site.parent for site in sites.values()))
    residue = 0
    for name in ('direct', 'relay'):
        site = sites.get(name)
        entry = results.setdefault(name, {})
        if site is None:
            continue
        entry['site'] = scrub(str(site))
        last_id, t_last = last_task(site)
        log = site / ('cluster.log' if name == 'direct' else 'server.log')
        t_exit = last_stamp(read_lines(log))
        entry['last_task'] = last_id
        entry['t_last'] = t_last
        entry['t_exit'] = t_exit
        entry['tail'] = None if t_last is None or t_exit is None else round(t_exit - t_last, 3)
        entry['tail_poll'] = None if t_last is None or name not in exits else round(exits[name] - t_last, 3)
        if name == 'relay':
            server_lines, relay_lines = read_lines(site / 'server.log'), read_lines(site / 'relay.log')
            entry['counts'] = {log_name: {level: count(found, level) for level in ('WARNING', 'ERROR', 'CRITICAL')}
                               for log_name, found in (('server', server_lines), ('relay', relay_lines))}
            if t_last is not None and last_id is not None:
                entry['stamps'], entry['segments'] = segments(site, last_id, t_last)
                for log_name, found in (('server', server_lines), ('relay', relay_lines)):
                    kept, dropped, inside = [], 0, False
                    for line in found:
                        value = stamp(line)
                        if value is not None:
                            inside = value >= t_last - WINDOW
                        if not inside:
                            continue
                        if PER_TASK.search(line) and last_id not in line:
                            dropped += 1
                            continue
                        kept.append(line)
                    head = (f'# {log_name}.log from T_last - {WINDOW:g} s to the end; {dropped} per-task lines '
                            f'of other tasks left out (the full log stays in the scratch site)')
                    text = scrub('\n'.join([head] + kept) + '\n')
                    residue += scrub.residue(text)
                    (given.out / f'{given.run_id}-{log_name}.txt').write_text(text)
    record.update(results)
    record['complete'] = all(results.get(name, {}).get('status') == 0
                             and results.get(name, {}).get('completed') == args.tasks for name in ('direct', 'relay'))
    text = json.dumps(record, indent=2, sort_keys=True) + '\n'
    record['scrubbed'] = residue + scrub.residue(text)
    text = json.dumps(record, indent=2, sort_keys=True) + '\n'
    record_path.write_text(text)
    relay = record.get('relay', {})
    print(f'tail_run: {given.run_id}: tail {relay.get("tail")} s (direct {record.get("direct", {}).get("tail")} s), '
          f'segments {relay.get("segments")}; record {record_path}', flush=True)
    return 0 if record['complete'] else 1


if __name__ == '__main__':
    sys.exit(main())
