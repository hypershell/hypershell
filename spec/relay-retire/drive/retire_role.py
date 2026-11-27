#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Roles and helpers of the multi-node relay-retire drive (`retire_cluster.sh`).

    retire_role.py plan        write the run's plan (HSD_RUN/plan.json) from the settings
    retire_role.py status      print the run's progress as shell assignments, for the drive
    retire_role.py server      on the server's node: run `hs server --autoscaling=fixed`, sample its
                               task table, submit on cue
    retire_role.py relay NAME  on the relays' node: run `hs relay` NAME (A or A2, both at site A)

Built from the relay-topology drive's roles (`spec/relay-topology/drive/topology_role.py`, which
stays as that cycle's record). Every role reads the run's transport directory (HSD_RUN, shared by
every node) and writes only there and under its node-local directory (HSD_NODE_TMP/<run id>). A role
ends its process on the drive's flags in HSD_RUN (`submit`, `stop-server`; `drain-NAME`, `stop-NAME`;
`abort`), on SIGTERM or SIGINT, and at the run's deadline (HSD_DEADLINE, epoch seconds), whichever
comes first, and records how it ended. Standard library only; run with the python beside the `hs`
under test.

The server role samples the upstream task table (its own database, read-only) every HSD_SAMPLE
seconds into HSD_RUN/held.jsonl, one line `{"t": EPOCH, "rows": [[TASK_ID, CLIENT_ID or null], ...]}`
of every incomplete task, as the laptop drive's sampler does. Relay A's role watches its own relay
database (read-only): once A has launched its max of site clients and has held a task no site client
took for HSD_HOLD seconds running, it writes HSD_RUN/held-A, the drive's cue to start A2.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional

# Standard libs
import os
import sys
import json
import time
import shlex
import signal
import socket
import sqlite3
import threading
import subprocess
from pathlib import Path


HS = os.environ.get('HS', 'hs')
DRIVE = Path(__file__).resolve().parent
LOCAL = os.environ.get('HSD_LOCAL', '') == '1'
RELAYS = {'A': 'A', 'A2': 'A'}  # relay name: the site it advertises
ZONE = 'cpu'


def setting(name: str, default: str) -> str:
    """A drive setting from the environment."""
    return os.environ.get(name) or default


def run_dir() -> Path:
    """The run's transport directory (shared)."""
    return Path(os.environ['HSD_RUN'])


def node_dir(name: str) -> Path:
    """A node-local directory of this run."""
    path = Path(setting('HSD_NODE_TMP', os.environ.get('TMPDIR', '/tmp'))) / run_dir().name / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def flag(name: str) -> Path:
    """A transport file of the run."""
    return run_dir() / name


def publish(path: Path, text: str) -> None:
    """Write a file whole (rename on the same filesystem)."""
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(text)
    os.replace(temp, path)


def publish_json(path: Path, value: Any) -> None:
    """Write a JSON file whole."""
    publish(path, json.dumps(value, indent=1, sort_keys=True) + '\n')


def read_json(path: Path) -> Optional[Any]:
    """A JSON file, or None if it is missing or partial."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def deadline() -> float:
    """The run's deadline (epoch seconds; never if unset)."""
    return float(setting('HSD_DEADLINE', '0')) or float('inf')


def bind_address() -> str:
    """Bind address of the servers this drive starts."""
    return '127.0.0.1' if LOCAL else '0.0.0.0'


def free_port() -> int:
    """A port free on this node now."""
    with socket.socket() as sock:
        sock.bind((bind_address(), 0))
        return sock.getsockname()[1]


def base_env(home: Path) -> Dict[str, str]:
    """The environment of a HyperShell process of this run: no setting but the run's own."""
    env = {name: value for name, value in os.environ.items() if not name.startswith('HYPERSHELL_')}
    env.update(HYPERSHELL_CONFIG_FILE=str(flag('config.toml')), HYPERSHELL_SITE=str(home),
               HYPERSHELL_LOGGING_STYLE='system', HYPERSHELL_LOGGING_LEVEL='DEBUG', TZ='UTC')
    return env


# --- plan -------------------------------------------------------------------------------------------

def plan() -> int:
    """Write HSD_RUN/plan.json: the workload, the relays' bounds and the settings."""
    num = lambda name, default: float(setting(name, default))
    whole = lambda name, default: int(setting(name, default))
    tasks, sleep = whole('HSD_TASKS', '12'), num('HSD_SLEEP', '40')
    bounds = {'A': {ZONE: {'min': 0, 'init': whole('HSD_A_MAX', '3'), 'max': whole('HSD_A_MAX', '3')}},
              'A2': {ZONE: {'min': 0, 'init': whole('HSD_A2_MAX', '2'), 'max': whole('HSD_A2_MAX', '2')}}}
    publish_json(flag('plan.json'), {
        'scenario': 'retire', 'relays': RELAYS, 'bounds': bounds, 'workload': [[0, ZONE, tasks, sleep]],
        'settings': {'period': whole('HSD_PERIOD', '1'), 'evict': whole('HSD_EVICT', '1200'),
                     'heartrate': whole('HSD_HEARTRATE', '2'), 'timeout': whole('HSD_CLIENT_TIMEOUT', '5'),
                     'relay_period': whole('HSD_RELAY_PERIOD', '1'), 'hold': num('HSD_HOLD', '2'),
                     'sample': num('HSD_SAMPLE', '0.25'), 'quiet': whole('HSD_QUIET', '5'),
                     'run_timeout': whole('HSD_TIMEOUT', '900')},
    })
    return 0


# --- status -----------------------------------------------------------------------------------------

def launch_records() -> List[Dict[str, Any]]:
    """Every client recorded so far: its start fields, and whether it has exited."""
    records = []
    for path in sorted(flag('launches').glob('*.rec')):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        if not lines or not lines[0].startswith('start '):
            continue
        fields = dict(item.split('=', 1) for item in lines[0].split()[1:] if '=' in item)
        fields['exited'] = any(line.startswith('exit ') for line in lines[1:])
        records.append(fields)
    return records


def status() -> int:
    """Print the run's progress as shell assignments (numbers only)."""
    progress = read_json(flag('progress.json')) or {}
    records = launch_records()
    values = {
        'total': progress.get('total', -1), 'remaining': progress.get('remaining', -1),
        'launches': len(records), 'open': sum(1 for r in records if not r['exited']),
        'a_launches': sum(1 for r in records if r.get('site') == 'A'),
        'a2_launches': sum(1 for r in records if r.get('site') == 'A2'),
    }
    print(' '.join(f'{name}={int(value)}' for name, value in values.items()))
    return 0


# --- roles ------------------------------------------------------------------------------------------

class Stop:
    """Records SIGTERM/SIGINT sent to a role, so the role ends its process in order."""

    def __init__(self) -> None:
        self.requested = False
        signal.signal(signal.SIGTERM, self.set)
        signal.signal(signal.SIGINT, self.set)

    def set(self, *_: Any) -> None:
        self.requested = True


def end(proc: subprocess.Popen, first: int, grace: float) -> None:
    """Send `first` to a process, then SIGKILL past `grace` seconds."""
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(first)
    except OSError:
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def read_only(db: Path) -> sqlite3.Connection:
    """A read-only connection to a SQLite database."""
    return sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=1)


def read_progress(db: Path) -> Optional[Dict[str, Any]]:
    """Task counts from the server's database (read-only)."""
    try:
        conn = read_only(db)
        try:
            total, left, bad = conn.execute('SELECT count(*), sum(completion_time IS NULL), '
                                            'sum(exit_status IS NOT NULL AND exit_status != 0) FROM task').fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return {'t': time.time(), 'total': total, 'remaining': left or 0, 'failed': bad or 0}


def sample(db: Path, out: Path, interval: float, done: threading.Event) -> None:
    """Append every incomplete task and its client id to `out`, every `interval` seconds, until `done`."""
    with open(out, mode='a') as stream:
        while not done.is_set():
            began = time.time()
            try:
                conn = read_only(db)
                try:
                    rows = [list(row) for row in conn.execute('SELECT id, client_id FROM task WHERE '
                                                              'completion_time IS NULL ORDER BY id').fetchall()]
                finally:
                    conn.close()
            except sqlite3.Error:
                rows = None  # no table yet, or a write lock held past the timeout: skipped
            if rows is not None:
                stream.write(json.dumps({'t': round(began, 3), 'rows': rows}) + '\n')
                stream.flush()
            done.wait(max(0.0, interval - (time.time() - began)))


def submit(spec: Dict[str, Any], home: Path, env: Dict[str, str]) -> bool:
    """Submit the workload; each task leaves one file named for its TASK_ID in HSD_RUN/runs when it starts."""
    runs = flag('runs')
    runs.mkdir(exist_ok=True)
    for group, zone, count, sleep in spec['workload']:
        source = home / f'tasks-{group}-{zone}.in'
        prefix = shlex.quote(f'{runs}/')
        source.write_text(''.join(f'mktemp {prefix}"$TASK_ID".XXXXXX > /dev/null; sleep {sleep:g}\n'
                                  for _ in range(count)))
        done = subprocess.run([HS, 'submit', '-g', str(group), '--zone', zone, '-f', str(source)],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if done.returncode != 0:
            print(done.stderr, file=sys.stderr)
            return False
    return True


def server() -> int:
    """Run the server and its sampler; submit the workload on the drive's flag (once relay A is
    registered); stop when told to; then export the tasks."""
    stop = Stop()
    spec = read_json(flag('plan.json'))
    settings = spec['settings']
    home = node_dir('server')
    db = home / 'task.db'
    env = dict(base_env(home), HYPERSHELL_DATABASE_FILE=str(db))
    port = free_port()
    server_env = dict(env, HYPERSHELL_SERVER_AUTH=flag('key').read_text().strip(),
                      HYPERSHELL_SERVER_EVICT=str(settings['evict']))
    argv = [HS, 'server', '--autoscaling=fixed', '-P', str(settings['period']),
            '--no-tls', '-H', bind_address(), '-p', str(port)]
    with open(flag('server.log'), 'ab') as stream:
        proc = subprocess.Popen(argv, env=server_env, stdout=stream, stderr=stream, start_new_session=True)
    host = '127.0.0.1' if LOCAL else os.environ['HSD_SERVER_NODE']
    listening = False
    while not listening and proc.poll() is None and time.time() < deadline() and not stop.requested:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                listening = True
        except OSError:
            time.sleep(0.5)
    if listening:
        publish_json(flag('server.ready'), {'host': host, 'port': port})
    else:
        publish(flag('abort'), 'server did not start\n')

    sampled = threading.Event()
    sampler = threading.Thread(target=sample, args=(db, flag('held.jsonl'), settings['sample'], sampled),
                               daemon=True)
    sampler.start()
    while proc.poll() is None:
        if (stop.requested or flag('stop-server').exists() or flag('abort').exists()
                or time.time() >= deadline()):
            end(proc, signal.SIGINT, 30)
            break
        if flag('submit').exists() and not flag('submitted').exists():
            if not submit(spec, home, env):
                publish(flag('abort'), 'submit failed\n')
                continue
            publish(flag('submitted'), f'{time.time()}\n')
        progress = read_progress(db)
        if progress is not None:
            publish_json(flag('progress.json'), progress)
        time.sleep(1)
    rc = proc.wait()
    sampled.set()
    sampler.join(timeout=10)

    fields = ['id', 'group', 'zone', 'exit_status', 'schedule_time', 'start_time', 'completion_time', 'attempt']
    export = subprocess.run([HS, 'list', *fields, '--json', '--all'], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if export.returncode == 0:
        publish(flag('tasks.json'), export.stdout)
    else:
        print(export.stderr, file=sys.stderr)
    publish_json(flag('server.exit'), {'rc': rc, 'exported': export.returncode == 0})
    return 0


def undelivered(db: Path) -> Optional[int]:
    """Tasks held at a relay, incomplete, that no site client has taken (its database, read-only)."""
    try:
        conn = read_only(db)
        try:
            return conn.execute('SELECT count(*) FROM task WHERE completion_time IS NULL '
                                'AND client_id IS NULL').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


class Trigger:
    """Relay A's cue for A2: A has launched its max of site clients and has held a task no site
    client took for `hold` seconds running (the laptop drive's calibrated trigger)."""

    def __init__(self, db: Path, launched: int, hold: float) -> None:
        self.db, self.launched, self.hold = db, launched, hold
        self.full = False
        self.since: Optional[float] = None
        self.fired = False

    def check(self) -> None:
        if self.fired:
            return
        now = time.time()
        if not self.full:
            self.full = sum(1 for r in launch_records() if r.get('site') == 'A') >= self.launched
        held = undelivered(self.db)
        with open(flag('trigger-A.log'), 'a') as stream:
            stream.write(f'{now:.3f} full={int(self.full)} undelivered={-1 if held is None else held}\n')
        if self.full and held:
            self.since = self.since or now
            if now - self.since >= self.hold:
                publish_json(flag('held-A'), {'t': now, 'undelivered': held})
                self.fired = True
        else:
            self.since = None


def relay(name: str) -> int:
    """Run relay `name` until it exits by itself, or is drained or stopped; record how it ended."""
    stop = Stop()
    spec = read_json(flag('plan.json'))
    settings = spec['settings']
    site = RELAYS[name]
    home = node_dir(f'relay-{name}')
    ready = None
    while ready is None:
        if stop.requested or flag('abort').exists() or time.time() >= deadline():
            publish_json(flag(f'relay-{name}.exit'), {'rc': None, 'started': False})
            return 1
        ready = read_json(flag('server.ready'))
        time.sleep(0.5)

    launcher = f'sh {shlex.quote(str(DRIVE / "retire_launch.sh"))} {{zone}}'
    lines = ['[relay]', f'site = "{site}"', f'timeout = {settings["timeout"]}', f'launcher = {json.dumps(launcher)}']
    for zone, bounds in spec['bounds'][name].items():
        lines += [f'[relay.zone.{zone}]', f'min = {bounds["min"]}', f'init = {bounds["init"]}',
                  f'max = {bounds["max"]}']
    config = home / 'relay.toml'
    config.write_text('\n'.join(lines) + '\n')
    db = home / 'relay.db'
    env = dict(base_env(home),
               HYPERSHELL_CONFIG_FILE=str(config),
               HYPERSHELL_RELAY_DATABASE=str(db),
               HYPERSHELL_RELAY_UPSTREAM_AUTH=flag('key').read_text().strip(),
               HYPERSHELL_RELAY_UPSTREAM_HEARTRATE=str(settings['heartrate']),
               HYPERSHELL_SERVER_TLS_ENABLED='false',
               HYPERSHELL_AUTOSCALE_PERIOD=str(settings['relay_period']),
               HSD_SITE=name, HSD_LAUNCH_STATE=str(home / 'launch'), HSD_DRIVE=str(DRIVE),
               HSD_CLIENT_NODES=os.environ['HSD_CLIENT_NODES'])
    argv = [HS, 'relay', '-H', ready['host'], '-p', str(ready['port']), '--no-tls', '--zone', ZONE,
            '--bind', bind_address(), '--port', str(free_port())]
    with open(flag(f'relay-{name}.log'), 'ab') as stream:
        proc = subprocess.Popen(argv, env=env, stdout=stream, stderr=stream, start_new_session=True)
    record: Dict[str, Any] = {'started': True, 'started_at': time.time(), 'drained_at': None,
                              'stopped': False, 'stopped_at': None, 'exited_at': None}
    trigger = Trigger(db, spec['bounds'][name][ZONE]['max'], settings['hold']) if name == 'A' else None

    while proc.poll() is None:
        if trigger:
            trigger.check()
        if flag(f'drain-{name}').exists() and record['drained_at'] is None:
            proc.send_signal(signal.SIGUSR1)
            record['drained_at'] = time.time()
        if (stop.requested or flag(f'stop-{name}').exists() or flag('abort').exists()
                or time.time() >= deadline()):
            record['stopped'], record['stopped_at'] = True, time.time()
            end(proc, signal.SIGTERM, 15)
            break
        time.sleep(0.5)
    record['rc'] = proc.wait()
    record['exited_at'] = time.time()
    record['self'] = record['drained_at'] is None and not record['stopped']
    publish_json(flag(f'relay-{name}.exit'), record)
    return 0


def main(argv: List[str]) -> int:
    """Dispatch a role."""
    if argv == ['plan']:
        return plan()
    if argv == ['status']:
        return status()
    if argv == ['server']:
        return server()
    if argv[:1] == ['relay'] and len(argv) == 2 and argv[1] in RELAYS:
        return relay(argv[1])
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
