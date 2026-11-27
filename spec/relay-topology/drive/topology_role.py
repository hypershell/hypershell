#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Roles and helpers of the multi-node topology drive (`topology_cluster.sh`).

    topology_role.py plan SCENARIO   write the run's plan (HSD_RUN/plan.json) from the settings
    topology_role.py status          print the run's progress as shell assignments, for the drive
    topology_role.py server          on the server's node: run `hs server --autoscaling`; submit on cue
    topology_role.py relay SITE      on a relay's node: run `hs relay` for SITE (A or B)

Every role reads the run's transport directory (HSD_RUN, shared by every node) and writes only
there and under its node-local directory (HSD_NODE_TMP/<run id>). A role ends its process on the
drive's flags in HSD_RUN (`submit`, `stop-server`; `drain-SITE`, `kill-SITE`, `stop-SITE`; `abort`), on
SIGTERM or SIGINT, and at the run's deadline (HSD_DEADLINE, epoch seconds), whichever comes first.
Standard library only; run with the python beside the `hs` under test.
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
import subprocess
from pathlib import Path


HS = os.environ.get('HS', 'hs')
DRIVE = Path(__file__).resolve().parent
LOCAL = os.environ.get('HSD_LOCAL', '') == '1'
ZONES = {'A': ('cpu', 'highmem'), 'B': ('cpu', 'gpu')}


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
               HYPERSHELL_LOGGING_STYLE='system', TZ='UTC')
    return env


# --- plan -------------------------------------------------------------------------------------------

def plan(scenario: str) -> int:
    """Write HSD_RUN/plan.json: the scenario's workload, bounds and settings."""
    num = lambda name, default: float(setting(name, default))
    whole = lambda name, default: int(setting(name, default))
    sleep = num('HSD_SLEEP', '2')
    tasks = whole('HSD_TASKS', '600')
    side = whole('HSD_SIDE_TASKS', '16')
    cpu_max = whole('HSD_CPU_MAX', '6')
    side_max = whole('HSD_SIDE_MAX', '2')
    bounds = {'A': {'cpu': {'min': 0, 'max': cpu_max}, 'highmem': {'min': 0, 'max': side_max}},
              'B': {'cpu': {'min': 0, 'max': cpu_max}, 'gpu': {'min': 0, 'max': side_max}}}
    if scenario == 'spread-kill':
        # Group 0 is the zones only one site serves; group 1 is cpu, which both serve, and in which
        # relay B is killed. Every gpu task is done before the kill, so none is left with no site.
        workload = [[0, 'highmem', side, sleep], [0, 'gpu', side, sleep], [1, 'cpu', tasks, sleep]]
        distribution, sites = 'spread', []
    elif scenario == 'fill':
        # B registers first, so the listed order (A first) is what makes A the preferred site.
        fill_max = whole('HSD_FILL_MAX', '4')
        bounds['A']['cpu']['max'] = bounds['B']['cpu']['max'] = fill_max
        workload = [[0, 'cpu', whole('HSD_FILL_TASKS', '300'), sleep],
                    [1, 'highmem', side // 2, sleep], [1, 'gpu', side // 2, sleep]]
        distribution, sites = 'fill', ['A', 'B']
    else:
        print(f'topology_role: unknown scenario {scenario}', file=sys.stderr)
        return 64
    publish_json(flag('plan.json'), {
        'scenario': scenario, 'distribution': distribution, 'sites': sites, 'bounds': bounds,
        'workload': workload,
        'settings': {'period': whole('HSD_PERIOD', '5'), 'factor': num('HSD_FACTOR', '1'),
                     'evict': whole('HSD_EVICT', '20'), 'heartrate': whole('HSD_HEARTRATE', '2'),
                     'timeout': whole('HSD_CLIENT_TIMEOUT', '10'), 'threads': whole('HSD_THREADS', '2'),
                     'bundlewait': whole('HSD_BUNDLEWAIT', '2'), 'relay_period': whole('HSD_RELAY_PERIOD', '2'),
                     'kill_at_clients': whole('HSD_KILL_AT_CLIENTS', '2'),
                     'quiet': whole('HSD_QUIET', '20')},
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
    groups = progress.get('groups') or {}
    first = min(groups, key=int) if groups else None
    records = launch_records()
    count = lambda site, zone: sum(1 for r in records if r.get('site') == site and r.get('word') == zone)
    values = {
        'total': progress.get('total', -1), 'remaining': progress.get('remaining', -1),
        'first_group_left': groups[first]['remaining'] if first is not None else -1,
        'launches': len(records), 'open': sum(1 for r in records if not r['exited']),
        'a_cpu': count('A', 'cpu'), 'b_cpu': count('B', 'cpu'),
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


def read_progress(db: Path) -> Optional[Dict[str, Any]]:
    """Task counts per group, from the server's database (read-only)."""
    try:
        conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
        try:
            rows = conn.execute('SELECT "group", count(*), sum(completion_time IS NULL), '
                                'sum(exit_status IS NOT NULL AND exit_status != 0) FROM task GROUP BY "group"').fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    groups = {str(g): {'total': n, 'remaining': left or 0, 'failed': bad or 0} for g, n, left, bad in rows}
    return {'t': time.time(), 'total': sum(g['total'] for g in groups.values()),
            'remaining': sum(g['remaining'] for g in groups.values()),
            'failed': sum(g['failed'] for g in groups.values()), 'groups': groups}


def submit(spec: Dict[str, Any], home: Path, env: Dict[str, str]) -> bool:
    """Submit the workload (one source per group and zone); False if a submission failed."""
    for group, zone, count, sleep in spec['workload']:
        source = home / f'tasks-{group}-{zone}.in'
        source.write_text(''.join(f'sleep {sleep}\n' for _ in range(count)))
        done = subprocess.run([HS, 'submit', '-g', str(group), '--zone', zone, '-f', str(source)],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if done.returncode != 0:
            print(done.stderr, file=sys.stderr)
            return False
    return True


def server() -> int:
    """Run the server; submit the workload on the drive's flag (once both relays are registered, so
    that the first plan with work sees both sites); stop when told to; then export the tasks."""
    stop = Stop()
    spec = read_json(flag('plan.json'))
    settings = spec['settings']
    home = node_dir('server')
    db = home / 'task.db'
    env = dict(base_env(home), HYPERSHELL_DATABASE_FILE=str(db))
    port = free_port()
    server_env = dict(env, HYPERSHELL_SERVER_AUTH=flag('key').read_text().strip(),
                      HYPERSHELL_SERVER_EVICT=str(settings['evict']), HYPERSHELL_LOGGING_LEVEL='INFO',
                      HYPERSHELL_AUTOSCALE_DISTRIBUTION=spec['distribution'],
                      HYPERSHELL_AUTOSCALE_SITES=','.join(spec['sites']))
    argv = [HS, 'server', '--autoscaling=dynamic', '-P', str(settings['period']), '-F', str(settings['factor']),
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

    fields = ['id', 'group', 'zone', 'exit_status', 'schedule_time', 'start_time', 'completion_time', 'attempt']
    export = subprocess.run([HS, 'list', *fields, '--json', '--all'], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if export.returncode == 0:
        publish(flag('tasks.json'), export.stdout)
    else:
        print(export.stderr, file=sys.stderr)
    publish_json(flag('server.exit'), {'rc': rc, 'exported': export.returncode == 0})
    return 0


def relay(site: str) -> int:
    """Run the relay of `site` until it is drained, killed or stopped; record how it ended."""
    stop = Stop()
    spec = read_json(flag('plan.json'))
    settings = spec['settings']
    home = node_dir(f'relay-{site}')
    ready = None
    while ready is None:
        if stop.requested or flag('abort').exists() or time.time() >= deadline():
            publish_json(flag(f'relay-{site}.exit'), {'rc': None, 'started': False})
            return 1
        ready = read_json(flag('server.ready'))
        time.sleep(0.5)

    launcher = f'sh {shlex.quote(str(DRIVE / "topology_launch.sh"))} {{zone}}'
    lines = ['[relay]', f'site = "{site}"', f'timeout = {settings["timeout"]}',
             f'num_threads = {settings["threads"]}', f'bundlewait = {settings["bundlewait"]}',
             f'launcher = {json.dumps(launcher)}']
    for zone, bounds in spec['bounds'][site].items():
        lines += [f'[relay.zone.{zone}]', f'min = {bounds["min"]}', f'max = {bounds["max"]}']
    config = home / 'relay.toml'
    config.write_text('\n'.join(lines) + '\n')
    env = dict(base_env(home),
               HYPERSHELL_CONFIG_FILE=str(config),
               HYPERSHELL_RELAY_DATABASE=str(home / 'relay.db'),
               HYPERSHELL_RELAY_UPSTREAM_AUTH=flag('key').read_text().strip(),
               HYPERSHELL_RELAY_UPSTREAM_HEARTRATE=str(settings['heartrate']),
               HYPERSHELL_SERVER_TLS_ENABLED='false',
               HYPERSHELL_AUTOSCALE_PERIOD=str(settings['relay_period']),
               HYPERSHELL_LOGGING_LEVEL='DEBUG',
               HSD_SITE=site, HSD_LAUNCH_STATE=str(home / 'launch'), HSD_DRIVE=str(DRIVE),
               HSD_CLIENT_NODES=os.environ[f'HSD_CLIENT_NODES_{site}'])
    argv = [HS, 'relay', '-H', ready['host'], '-p', str(ready['port']), '--no-tls',
            *[arg for zone in ZONES[site] for arg in ('--zone', zone)],
            '--bind', bind_address(), '--port', str(free_port())]
    with open(flag(f'relay-{site}.log'), 'ab') as stream:
        proc = subprocess.Popen(argv, env=env, stdout=stream, stderr=stream, start_new_session=True)

    record: Dict[str, Any] = {'started': True, 'killed_at': None, 'drained_at': None, 'stopped': False}
    while proc.poll() is None:
        if flag(f'kill-{site}').exists():
            proc.kill()
            record['killed_at'] = time.time()
            break
        if flag(f'drain-{site}').exists() and record['drained_at'] is None:
            proc.send_signal(signal.SIGUSR1)
            record['drained_at'] = time.time()
        if (stop.requested or flag(f'stop-{site}').exists() or flag('abort').exists()
                or time.time() >= deadline()):
            record['stopped'] = True
            end(proc, signal.SIGTERM, 15)
            break
        time.sleep(0.5)
    record['rc'] = proc.wait()
    publish_json(flag(f'relay-{site}.exit'), record)
    return 0


def main(argv: List[str]) -> int:
    """Dispatch a role."""
    if argv[:1] == ['plan'] and len(argv) == 2:
        return plan(argv[1])
    if argv == ['status']:
        return status()
    if argv == ['server']:
        return server()
    if argv[:1] == ['relay'] and len(argv) == 2 and argv[1] in ZONES:
        return relay(argv[1])
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
