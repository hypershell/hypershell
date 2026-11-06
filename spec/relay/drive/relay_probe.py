# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The roles of the relay drive (`relay_drive.sh`), one process per role per node.

    relay_probe.py workload            write the task file (coordinator)
    relay_probe.py upstream            upstream server, sampler and failover director (node 0)
    relay_probe.py relay SITE          one site's `hs relay` and its supervisor (the site's first node)
    relay_probe.py watch SITE NODE R   sampler of one site node (every site node; R = 1 if it hosts the relay)
    relay_probe.py sweep               end any process of this run still running here (local mode)

Configured by environment only (see `relay_drive.sh`). Roles meet through files in the shared run
directory HSD_RUN (transport: the key, readiness, identities, flags, leaf ids, execution logs; never
evidence) and write their records, counts and booleans only, to HSD_OUT. The upstream key is read
from HSD_RUN/key, handed only to the upstream server and to `hs relay` in their environments, and
never written anywhere else.

Requires psutil (a HyperShell dependency): run it with the interpreter of the HyperShell under test.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

# Standard libs
import os
import re
import sys
import json
import time
import shlex
import signal
import socket
import sqlite3
import secrets
import platform
import subprocess
from pathlib import Path
from datetime import datetime
from collections import Counter

# External libs
import psutil


SCHEMA = 2
SITES = ('A', 'B')
IDENTITY = re.compile(r'Upstream identity ([0-9a-f-]{36}) for zone (\S+) at site (\S+)')
UUID = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
DISPATCHED = re.compile(r'Dispatched (\d+) tasks to client ' + UUID)
CONFIRMED = re.compile(r'Confirmed (\d+) tasks \(.*?: ' + UUID + r'\)')
EVICTING = re.compile(r'Evicting client \(.*?: ' + UUID + r'\)')
REVERTING = re.compile(r'Reverting orphaned tasks \(.*?: ' + UUID + r'\)')
HS_NAMES = ('hs', 'hyper-shell')
SITE_KEY_VAR = 'HYPERSHELL_SERVER_AUTH'  # A launched client receives its key here, never on its command line.
KEY_OPTIONS = ('-k', '--auth')


# --- Settings and shared files -----------------------------------------------------------------


def setting(name: str, default: Any = None, kind: Callable[[str], Any] = str) -> Any:
    """An HSD_* setting from the environment."""
    value = os.environ.get(name)
    return default if value in (None, '') else kind(value)


MODE = setting('HSD_MODE', 'local')
RUN_ID = setting('HSD_RUN_ID', 'relay')
VARIANT = setting('HSD_VARIANT', 'kill')
HS = setting('HS', 'hs')
SAMPLE = setting('HSD_SAMPLE', 1.0, float)
TIMEOUT = setting('HSD_TIMEOUT', 420.0, float)
WAIT = setting('HSD_WAIT', 300.0, float)
TASKS = setting('HSD_TASKS', 2000, int)
FAILING = setting('HSD_FAILING', 3, int)
GROUPS = setting('HSD_GROUPS', 3, int)
SLEEP = setting('HSD_SLEEP', 1.0, float)
ZONE = setting('HSD_ZONE', 'shared')
EVICT = setting('HSD_EVICT', 30, int)
HEARTRATE = setting('HSD_HEARTRATE', 5, int)
THREADS = setting('HSD_THREADS', 8, int)
PER_NODE = setting('HSD_CLIENTS_PER_NODE', 2, int)
KILL_AT = setting('HSD_KILL_AT', 0.4, float)
PAUSE = setting('HSD_PAUSE', 0.0, float) or EVICT + 15.0
SETTLE = setting('HSD_SETTLE', 4.0, float)
ATTEMPTS = setting('HSD_FAILOVER_ATTEMPTS', 10, int)
DRAIN_WAIT = setting('HSD_DRAIN_WAIT', 90.0, float)
SERVER_BUNDLESIZE = setting('HSD_SERVER_BUNDLESIZE', 8, int)
SERVER_POLL = setting('HSD_SERVER_POLL', 2, int)
RELAY_BUNDLESIZE = setting('HSD_RELAY_BUNDLESIZE', 4 * THREADS, int)
TLS = setting('HSD_TLS', '1') == '1'

# The relay's backlog bound, mirrored from `hypershell.relay` (`site_bundlesize`) and
# `hypershell.relay.upstream` (`BACKLOG_FACTOR`), not imported: importing HyperShell here would build its
# configuration and database engine inside the drive's own processes. tests/test_relay_judge.py checks the
# mirror against the code.
BACKLOG_FACTOR = 2


def relay_bound(connected: int) -> int:
    """The most unfinished tasks a relay holds: two site bundles per connected site client.

    The site bundle size is the relay's bundle size, never below a site client's executor threads.
    """
    return connected * max(1, RELAY_BUNDLESIZE, THREADS) * BACKLOG_FACTOR


def run_dir() -> Path:
    """The shared run directory (transport only)."""
    return Path(os.environ['HSD_RUN'])


def out_dir() -> Path:
    """The run's evidence directory."""
    return Path(os.environ['HSD_OUT'])


def node_tmp() -> Path:
    """This node's private directory for the run (node-local disk)."""
    base = os.environ.get('HSD_NODE_TMP') or os.environ.get('TMPDIR') or '/tmp'
    return Path(base) / f'hsd-relay-{RUN_ID}'


def site_nodes(site: str) -> List[str]:
    """The node names of `site`."""
    return os.environ.get(f'HSD_SITE_{site}_NODES', '').split()


def upstream_host() -> str:
    """The name relays dial for the upstream server."""
    if MODE == 'slurm':
        return os.environ.get('HSD_UPSTREAM_NODE') or 'localhost'
    return 'localhost'


def bind_address() -> str:
    """Bind address of the servers this drive starts."""
    return '0.0.0.0' if MODE == 'slurm' else '127.0.0.1'


def publish(path: Path, text: str) -> None:
    """Write a file whole (atomic rename on the same filesystem)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(text)
    os.replace(temp, path)


def publish_json(path: Path, record: Dict[str, Any]) -> None:
    """Write a JSON record whole."""
    publish(path, json.dumps(record, indent=1, sort_keys=True) + '\n')


def flag(name: str) -> Path:
    """A transport file in the run directory."""
    return run_dir() / name


def wait_for(condition: Callable[[], Any], seconds: float, interval: float = 0.25) -> Any:
    """Poll `condition` until it returns something true or `seconds` pass (then None)."""
    deadline = time.monotonic() + seconds
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def read_key() -> str:
    """The upstream key (transport file, owner-only)."""
    return flag('key').read_text().strip()


def read_token() -> str:
    """The token every process of this run's sites carries in its environment."""
    return flag('token').read_text().strip()


def free_port(bind: str) -> int:
    """A port free on `bind` now."""
    with socket.socket() as sock:
        sock.bind((bind, 0))
        return sock.getsockname()[1]


def readonly(path: Path) -> sqlite3.Connection:
    """A read-only connection to a SQLite file that another process writes."""
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1)


def short(host: Optional[str]) -> str:
    """A host name without its domain."""
    return (host or '').split('.', 1)[0].lower()


def addresses(host: str) -> Set[str]:
    """The addresses `host` resolves to here."""
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


def own(proc: psutil.Process) -> bool:
    """True if `proc` belongs to this user."""
    try:
        return proc.uids().real == os.getuid()
    except psutil.Error:
        return False


def hs_command(cmdline: List[str]) -> Optional[str]:
    """The HyperShell subcommand a command line runs (`client`, `relay`, ...), if any."""
    for i, arg in enumerate(cmdline[:-1]):
        if os.path.basename(arg) in HS_NAMES:
            return cmdline[i + 1]
    return None


def established_to(proc: psutil.Process, port: int, hosts: Set[str]) -> int:
    """Number of TCP connections `proc` has open to `port` on any of `hosts`."""
    count = 0
    for conn in proc.net_connections(kind='tcp'):
        if (conn.status == psutil.CONN_ESTABLISHED and conn.raddr
                and conn.raddr.port == port and conn.raddr.ip in hosts):
            count += 1
    return count


def end(proc: psutil.Process, grace: float = 10.0) -> None:
    """SIGTERM a process (continuing it if stopped), then SIGKILL after `grace`."""
    try:
        proc.send_signal(signal.SIGCONT)
        proc.terminate()
        proc.wait(grace)
    except psutil.TimeoutExpired:
        try:
            proc.kill()
        except psutil.Error:
            pass
    except psutil.Error:
        pass


def deadline_from_start() -> float:
    """The run's deadline on this node's monotonic clock."""
    return time.monotonic() + TIMEOUT


# --- Workload ----------------------------------------------------------------------------------


def workload() -> int:
    """Write the task file: TASKS tasks in GROUPS groups, FAILING of them fail on their first attempt."""
    python, task = sys.executable, Path(__file__).with_name('relay_task.py')
    for item in (python, str(task)):
        if any(c in item for c in "{}'\n"):
            sys.exit(f'relay_probe: unusable path for a task command (quote or brace): {item}')
    failing = {k * TASKS // FAILING for k in range(FAILING)} if FAILING else set()
    lines = []
    for i in range(TASKS):
        group = i * GROUPS // TASKS
        fail = ' fail' if i in failing else ''
        lines.append(f"'{python}' -I -S '{task}' {SLEEP}{fail}  # HYPERSHELL: group:{group}")
    publish(flag('tasks.in'), '\n'.join(lines) + '\n')
    return 0


# --- Upstream: server, sampler, failover director, collector -------------------------------------


class Director:
    """Freeze relay B, check that nothing is cut for it unconfirmed, take the snapshot, then act.

    A relay killed between a reply cut for it and its confirmation would leave those rows in flight
    with no client until the upstream server restarts (a documented non-goal), so the stop happens
    only while relay B is frozen and the upstream log shows every task dispatched to it confirmed.
    """

    def __init__(self, db: Path, log: Path) -> None:
        self.db, self.log = db, log
        self.attempt = 0
        self.triggered = False
        self.given_up = False
        self.snapshot: List[str] = []
        self.completed_at_trigger = 0
        self.next_try = 0.0

    def due(self, completed: int, rows: int) -> bool:
        """Whether to try now."""
        return (VARIANT in ('kill', 'pause') and not self.triggered and not self.given_up
                and completed >= KILL_AT * (TASKS + FAILING) and time.monotonic() >= self.next_try
                and flag('identity-B').exists())

    def held(self, identity: str) -> List[str]:
        """Upstream rows confirmed to `identity` and not completed."""
        with readonly(self.db) as db:
            return [row[0] for row in db.execute(
                'SELECT id FROM task WHERE client_id = ? AND completion_time IS NULL', (identity,))]

    def settled(self, identity: str) -> bool:
        """Whether every task dispatched to `identity` has been confirmed, by the upstream log."""
        dispatched = confirmed = 0
        with open(self.log, errors='replace') as stream:
            for line in stream:
                if (match := DISPATCHED.search(line)) and match.group(2) == identity:
                    dispatched += int(match.group(1))
                elif (match := CONFIRMED.search(line)) and match.group(2) == identity:
                    confirmed += int(match.group(1))
        return dispatched == confirmed

    def attempt_once(self, completed: int) -> None:
        """One freeze, check and release of relay B."""
        identity = flag('identity-B').read_text().strip()
        try:
            if not self.held(identity):
                self.next_try = time.monotonic() + 0.5
                return
        except sqlite3.Error:
            return
        self.attempt += 1
        n = self.attempt
        publish(flag(f'freeze-B.{n}'), 'freeze\n')
        if not wait_for(flag(f'frozen-B.{n}').exists, 60):
            publish(flag(f'release-B.{n}'), 'thaw\n')
            self.given_up = True
            print('relay_probe: relay B was not frozen within 60 s - no failover', file=sys.stderr)
            return
        time.sleep(SETTLE)
        try:
            held = self.held(identity)
            settled = self.settled(identity)
        except (sqlite3.Error, OSError):
            held, settled = [], False
        if held and settled:
            self.snapshot = held
            self.completed_at_trigger = completed
            publish(flag('snapshot'), '\n'.join(held) + '\n')
            publish(flag(f'release-B.{n}'), f'{VARIANT}\n')
            self.triggered = True
            print(f'relay_probe: relay B {VARIANT} at attempt {n} holding {len(held)} tasks', file=sys.stderr)
        else:
            publish(flag(f'release-B.{n}'), 'thaw\n')
            self.next_try = time.monotonic() + 1.0
            if n >= ATTEMPTS:
                self.given_up = True
                print(f'relay_probe: no settled moment to stop relay B in {n} attempts', file=sys.stderr)


def upstream() -> int:
    """Run the upstream server on this node, direct the failover, and write upstream.json."""
    started = time.monotonic()
    deadline = started + TIMEOUT
    key = read_key()
    site = node_tmp() / 'upstream'
    site.mkdir(parents=True, exist_ok=True)
    db, log = site / 'task.db', site / 'server.log'
    env = dict(os.environ, HYPERSHELL_SITE=str(site), HYPERSHELL_DATABASE_FILE=str(db))
    submit = subprocess.run([HS, 'submit', '-f', str(flag('tasks.in')), '-b', '1000', '-w', '1', '--zone', ZONE],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if submit.returncode != 0:
        print(submit.stderr, file=sys.stderr)
        publish(flag('abort'), 'submit failed\n')
        return 1

    bind = bind_address()
    port = free_port(bind)
    server_env = dict(env, HYPERSHELL_SERVER_AUTH=key, HYPERSHELL_SERVER_EVICT=str(EVICT),
                      HYPERSHELL_LOGGING_LEVEL='DEBUG')
    argv = [HS, 'server', '--restart', '-r', '1', '-b', str(SERVER_BUNDLESIZE), '-Q', str(SERVER_POLL),
            '-H', bind, '-p', str(port)]
    if not TLS:
        argv.append('--no-tls')
    with open(log, 'wb') as stream:
        server = subprocess.Popen(argv, env=server_env, stdout=stream, stderr=stream)
    proc = psutil.Process(server.pid)
    listening = wait_for(lambda: server.poll() is not None or any(
        c.status == psutil.CONN_LISTEN and c.laddr.port == port for c in proc.net_connections(kind='tcp')), WAIT)
    if not listening or server.poll() is not None:
        publish(flag('abort'), 'upstream server did not start\n')
        end(proc)
        return 1
    if TLS:
        cert = next((Path(d) / 'server.crt' for d, _, files in os.walk(site) if 'server.crt' in files), None)
        if cert is None:
            publish(flag('abort'), 'no upstream certificate\n')
            end(proc)
            return 1
        publish(flag('upstream.crt'), cert.read_text())
    publish_json(flag('upstream.ready'), {'host': upstream_host(), 'port': port})

    director = Director(db, log)
    series: List[int] = []
    peers: Set[str] = set()
    completed = False
    aborted = False
    next_sample = time.monotonic()
    while server.poll() is None:
        now = time.monotonic()
        if now >= deadline:
            aborted = True
            publish(flag('abort'), 'deadline\n')
            print('relay_probe: run deadline passed - ending the upstream server', file=sys.stderr)
            end(proc, 15)
            break
        if now >= next_sample:
            next_sample = now + SAMPLE
            try:
                conns = [c for c in proc.net_connections(kind='tcp')
                         if c.status == psutil.CONN_ESTABLISHED and c.laddr.port == port and c.raddr]
                series.append(len(conns))
                peers.update(c.raddr.ip for c in conns)
            except psutil.Error:
                pass
            try:
                with readonly(db) as conn:
                    done, open_rows = conn.execute(
                        'SELECT COALESCE(SUM(exit_status = 0 AND completion_time IS NOT NULL), 0), '
                        'COALESCE(SUM(completion_time IS NULL), 0) FROM task').fetchone()
                    finished = conn.execute('SELECT COUNT(*) FROM task WHERE completion_time IS NOT NULL').fetchone()[0]
                completed = completed or (done == TASKS and open_rows == 0)
                if director.due(finished, TASKS + FAILING):
                    director.attempt_once(finished)
            except sqlite3.Error:
                pass
        time.sleep(0.1)
    server_rc = server.wait()
    publish(flag('server.exited'), f'{server_rc}\n')

    # Every relay and watcher finishes after the server; then collect.
    relays_done = wait_for(lambda: all(flag(f'relay-{s}.finished').exists() for s in SITES),
                           max(60.0, deadline + 120 - time.monotonic()), 0.5)
    publish(flag('relays.finished'), 'ok\n' if relays_done else 'timeout\n')
    watchers = [line.split() for line in flag('watchers').read_text().split('\n') if line.strip()]
    wait_for(lambda: all(flag(f'watch-{s}-{n}.finished').exists() for s, n in watchers), 120, 0.5)
    record = collect(db, log, key, director, series, peers, server_rc,
                     completed and not aborted, time.monotonic() - started)
    publish_json(out_dir() / 'upstream.json', record)
    try:
        publish(flag('upstream.log'), log.read_text(errors='replace'))  # Transport, for diagnosis only.
    except OSError:
        pass
    return 0 if record['completed'] and server_rc == 0 else 1


def read_lines(path: Path) -> List[str]:
    """Non-blank lines of a transport file (none if it is missing)."""
    try:
        return [line.strip() for line in path.read_text().split('\n') if line.strip()]
    except OSError:
        return []


def stamp(text: Optional[str]) -> Optional[datetime]:
    """A stored time, as the wall clock it records (any offset dropped)."""
    return None if text is None else datetime.fromisoformat(str(text)).replace(tzinfo=None)


def collect(db: Path, log: Path, key: str, director: Director, series: List[int], peers: Set[str],
            server_rc: int, completed: bool, wall: float) -> Dict[str, Any]:
    """The upstream record: checks (a)-(d) computed here, counts and booleans only."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    tasks = [dict(row) for row in conn.execute('SELECT * FROM task')]
    clients = [dict(row) for row in conn.execute('SELECT * FROM client')]
    conn.close()
    by_id = {task['id']: task for task in tasks}

    identity = {s: (read_lines(flag(f'identity-{s}')) or [''])[0] for s in SITES}
    leaves = {s: set(read_lines(flag(f'relay-{s}.leaves'))) for s in SITES}
    leaf_site = {leaf: s for s in SITES for leaf in leaves[s]}
    snapshot = set(read_lines(flag('snapshot')))
    hosts = ({s: {short(n) for n in site_nodes(s)} for s in SITES} if MODE == 'slurm'
             else {s: {short(socket.gethostname())} for s in SITES})

    # Executions, from the tasks' own node-local logs.
    runs: Counter = Counter()
    ran_at: Dict[str, Set[str]] = {}
    for path in sorted(run_dir().glob('exec-*.log')):
        for line in read_lines(path):
            parts = line.split()
            if len(parts) == 2:
                runs[parts[1]] += 1
                ran_at.setdefault(parts[1], set()).add(parts[0])
    dups = {id for id, count in runs.items() if count > 1}

    # (a)
    failed = [t for t in tasks if t['attempt'] == 1 and t['exit_status'] not in (0, None) and t['retried']]
    retries = [t for t in tasks if t['attempt'] == 2]
    retry_ok = [t for t in retries if t['exit_status'] == 0 and t['previous_id'] in by_id
                and by_id[t['previous_id']]['exit_status'] == 3 and by_id[t['previous_id']]['retried']]
    groups = sorted({t['group'] for t in tasks if t['group'] is not None})
    order = True
    for earlier, later in zip(groups, groups[1:]):
        done = [stamp(t['completion_time']) for t in tasks if t['group'] == earlier]
        out = [stamp(t['schedule_time']) for t in tasks if t['group'] == later and t['schedule_time']]
        if None in done or not out or max(done) > min(out):
            order = False
    a = {'rows': len(tasks), 'incomplete': sum(1 for t in tasks if t['completion_time'] is None),
         'succeeded': sum(1 for t in tasks if t['exit_status'] == 0 and t['completion_time'] is not None),
         'failed_first': len(failed), 'retry_rows': len(retries), 'retry_rows_ok': len(retry_ok),
         'max_attempt': max((t['attempt'] for t in tasks), default=0), 'group_order': order,
         'executed_ids': len(set(runs) & set(by_id)), 'executions': sum(runs.values()),
         'never_executed': len(set(by_id) - set(runs)), 'dup_ids': len(dups),
         'dup_outside_snapshot': len(dups - snapshot)}

    # (b)
    done_rows = [t for t in tasks if t['completion_time'] is not None]
    relays = set(filter(None, identity.values()))
    sites: Dict[str, Dict[str, int]] = {}
    for s in SITES:
        listed = [c for c in clients if c['site'] == s and c['id'] != identity[s]]
        sites[s] = {'leaves_launched': len(leaves[s]),
                    'leaves_upstream': len({c['id'] for c in listed} & leaves[s]),
                    'leaves_other_relay': sum(1 for c in listed if c['relay'] != identity[s] or c['id'] not in leaves[s]),
                    'relay_rows': sum(1 for c in clients if c['id'] == identity[s] and c['site'] == s
                                      and c['relay'] == identity[s])}
    b = {'completed_not_leaf': sum(1 for t in done_rows if t['client_id'] not in leaf_site),
         'completed_by_relay': sum(1 for t in done_rows if t['client_id'] in relays),
         'completed_site_mismatch': sum(1 for t in done_rows if t['client_id'] in leaf_site
                                        and leaf_site[t['client_id']] not in ran_at.get(t['id'], set())),
         'client_host_outside_site': sum(1 for t in done_rows if t['client_id'] in leaf_site
                                         and short(t['client_host']) not in hosts[leaf_site[t['client_id']]]),
         'completed_by_site': {s: sum(1 for t in done_rows if leaf_site.get(t['client_id']) == s) for s in SITES},
         'sites': sites}

    # (c)
    relay_addrs: Set[str] = set()
    for path in run_dir().glob('watch-*.addrs'):
        info = json.loads(path.read_text())
        if info.get('hosts_relay'):
            relay_addrs.update(info.get('addresses', []))
    c = {'samples': len(series), 'conn_peak': max(series, default=0), 'conn_series': series,
         'peers_seen': len(peers), 'peers_outside_relays': len(peers - relay_addrs),
         'address_check': MODE == 'slurm'}

    # (d)
    text = log.read_text(errors='replace')
    evicted = Counter(EVICTING.findall(text))
    reverted = Counter(REVERTING.findall(text))
    d = {'triggered': director.triggered, 'attempts': director.attempt, 'snapshot': len(snapshot),
         'snapshot_completed': sum(1 for id in snapshot if id in by_id and by_id[id]['completion_time'] is not None),
         'snapshot_completed_at_A': sum(1 for id in snapshot if leaf_site.get((by_id.get(id) or {}).get('client_id')) == 'A'),
         'snapshot_completed_at_B': sum(1 for id in snapshot if leaf_site.get((by_id.get(id) or {}).get('client_id')) == 'B'),
         'evictions': {s: evicted.get(identity[s], 0) for s in SITES},
         'reverts': {s: reverted.get(identity[s], 0) for s in SITES},
         'stale': text.count('Refused stale result'), 'duplicate': text.count('Ignored duplicate result'),
         'completed_at_trigger': director.completed_at_trigger}

    import hypershell  # noqa: the version under test
    return {'schema': SCHEMA, 'role': 'upstream', 'run_id': RUN_ID, 'label': setting('HSD_LABEL', 'feature'),
            'mode': MODE, 'variant': VARIANT, 'hypershell': hypershell.__version__,
            'python': platform.python_version(), 'tls': TLS,
            'nodes': {'total': 1 + sum(len(site_nodes(s)) for s in SITES),
                      **{s: len(site_nodes(s)) for s in SITES}},
            'workload': {'tasks': TASKS, 'failing': FAILING, 'groups': GROUPS, 'sleep': SLEEP},
            'settings': {'evict': EVICT, 'heartrate': HEARTRATE, 'threads': THREADS,
                         'relay_bundlesize': RELAY_BUNDLESIZE, 'clients_per_node': PER_NODE, 'kill_at': KILL_AT},
            'server_rc': server_rc, 'completed': completed, 'wall_s': round(wall, 1),
            'a': a, 'b': b, 'c': c, 'd': d, 'key_in_server_log': text.count(key)}


# --- Relay: one site's `hs relay` and its supervisor ---------------------------------------------


def relay(site: str) -> int:
    """Start this site's relay, act on the director's flags, sample it, and write relay-<site>.json."""
    deadline = time.monotonic() + TIMEOUT + 120
    ready = wait_for(lambda: flag('upstream.ready').exists() or flag('abort').exists(), WAIT)
    if not ready or flag('abort').exists():
        return finish_relay(site, None, {}, 'none')
    up = json.loads(flag('upstream.ready').read_text())
    nodes = site_nodes(site)
    watchers = [line.split() for line in read_lines(flag('watchers'))]
    wait_for(lambda: all(flag(f'watch-{s}-{n}.ready').exists() for s, n in watchers if s == site), WAIT)

    key, token = read_key(), read_token()
    tmp = node_tmp()
    tmp.mkdir(parents=True, exist_ok=True)
    home = run_dir() / f'site-{site}'
    home.mkdir(parents=True, exist_ok=True)
    database = tmp / f'relay-{site}.db'
    port = free_port(bind_address())
    # Executor threads per site client come from a configuration file: a variable cannot name a key with
    # an underscore in it (HYPERSHELL_RELAY_NUM_THREADS would set `relay.num.threads`). The relay hands
    # its site a copy of this file without the [relay] table.
    config = home / 'relay.toml'
    config.write_text(Path(os.environ['HYPERSHELL_CONFIG_FILE']).read_text()
                      + f'\n[relay]\nnum_threads = {THREADS}\n')
    env = dict(os.environ,
               HYPERSHELL_CONFIG_FILE=str(config),
               HYPERSHELL_SITE=str(home),
               HYPERSHELL_DATABASE_FILE=str(home / 'primary-sentinel.db'),
               HYPERSHELL_LOGGING_LEVEL='INFO',
               HYPERSHELL_RELAY_DATABASE=str(database),
               HYPERSHELL_RELAY_UPSTREAM_AUTH=key,
               HYPERSHELL_RELAY_SITE=site,
               HYPERSHELL_RELAY_UPSTREAM_HEARTRATE=str(HEARTRATE),
               HYPERSHELL_RELAY_BUNDLESIZE=str(RELAY_BUNDLESIZE),
               HYPERSHELL_RELAY_BUNDLEWAIT='1',
               HSD_KEY_PROBE=f'probe:{key}',  # The key under another name, on purpose: the fence must drop it.
               HSD_SITE=site, HSD_RUN_TOKEN=token, HSD_EXEC_LOG=str(tmp / 'exec.log'),
               HSD_SITE_NODES=' '.join(nodes), HSD_LAUNCH_STATE=str(tmp / f'launch-{site}'))
    if TLS:
        env['HYPERSHELL_RELAY_UPSTREAM_TLS_CAFILE'] = str(flag('upstream.crt'))
    Path(env['HSD_LAUNCH_STATE']).mkdir(parents=True, exist_ok=True)
    launcher = f"sh {shlex.quote(str(Path(__file__).with_name('relay_launch.sh')))}"
    argv = [HS, 'relay', '-H', up['host'], '-p', str(up['port']), '--zone', ZONE,
            '-N', str(PER_NODE * len(nodes)), '--launcher', launcher,
            '--bind', bind_address(), '--port', str(port)]
    if not TLS:
        argv.append('--no-tls')
    log_path = flag(f'relay-{site}.log')
    with open(log_path, 'ab') as stream:
        child = subprocess.Popen(argv, env=env, stdout=stream, stderr=stream, start_new_session=True)
    proc = psutil.Process(child.pid)
    publish_json(flag(f'relay-{site}.info'), {'pid': child.pid, 'port': port})
    upstream_ips = addresses(up['host'])

    peak_backlog = peak_bound = samples = conns_peak = 0
    action, orphans = 'none', []
    attempt = 1
    next_sample = time.monotonic()
    server_exited_at: Optional[float] = None
    while child.poll() is None:
        now = time.monotonic()
        if not flag(f'identity-{site}').exists():
            match = IDENTITY.search(log_path.read_text(errors='replace'))
            if match:
                publish(flag(f'identity-{site}'), match.group(1) + '\n')
        if flag(f'freeze-{site}.{attempt}').exists():
            action, orphans = freeze(site, attempt, proc, action)
            attempt += 1
            continue
        if now >= next_sample:
            next_sample = now + SAMPLE
            try:
                conns_peak = max(conns_peak, established_to(proc, up['port'], upstream_ips))
            except psutil.Error:
                pass
            try:
                with readonly(database) as db:
                    backlog = db.execute('SELECT COUNT(*) FROM task WHERE completion_time IS NULL').fetchone()[0]
                    connected = db.execute('SELECT COUNT(*) FROM client WHERE disconnected_at IS NULL').fetchone()[0]
                samples += 1
                peak_backlog = max(peak_backlog, backlog)
                peak_bound = max(peak_bound, relay_bound(connected))
            except sqlite3.Error:
                pass
        if flag('abort').exists() or now >= deadline:
            end(proc, 20)
            break
        if server_exited_at is None and flag('server.exited').exists():
            server_exited_at = now
        if server_exited_at is not None and now - server_exited_at > DRAIN_WAIT:
            print(f'relay_probe: relay {site} still running {DRAIN_WAIT} s after the upstream server - ending it',
                  file=sys.stderr)
            end(proc, 20)
            break
        time.sleep(0.1)
    rc = child.wait()

    # A relay stopped by SIGKILL leaves its site clients running: end them once the run is over.
    if orphans:
        wait_for(lambda: flag('server.exited').exists() or flag('abort').exists(), max(0.0, deadline - time.monotonic()))
        for leftover in orphans:
            end(leftover)
    return finish_relay(site, rc, {'peak_backlog': peak_backlog, 'peak_bound': peak_bound,
                                   'backlog_samples': samples, 'upstream_conns_peak': conns_peak},
                        action, key=key, database=database, home=home, log_path=log_path)


def freeze(site: str, attempt: int, proc: psutil.Process, action: str) -> Tuple[str, List[psutil.Process]]:
    """Stop the relay for the director, then kill it, pause it, or let it go on, as released."""
    orphans: List[psutil.Process] = []
    try:
        proc.send_signal(signal.SIGSTOP)
    except psutil.Error:
        pass  # Already gone: the director finds nothing settled to act on, or acts on a dead relay.
    publish(flag(f'frozen-{site}.{attempt}'), 'frozen\n')
    release = wait_for(lambda: flag(f'release-{site}.{attempt}').exists(), 120, 0.1)
    decision = flag(f'release-{site}.{attempt}').read_text().strip() if release else 'thaw'
    try:
        if decision == 'kill':
            orphans = proc.children(recursive=True)
            proc.kill()
            return 'kill', orphans
        if decision == 'pause':
            time.sleep(PAUSE)
            proc.send_signal(signal.SIGCONT)
            return 'pause', orphans
        proc.send_signal(signal.SIGCONT)
    except psutil.Error:
        pass
    return action, orphans


def finish_relay(site: str, rc: Optional[int], sampled: Dict[str, int], action: str, key: str = '',
                 database: Optional[Path] = None, home: Optional[Path] = None,
                 log_path: Optional[Path] = None) -> int:
    """Read the relay database after the relay ended and write the relay record."""
    leaves: List[str] = []
    rows_left = pending = returned = None
    if database is not None and database.exists():
        try:
            with sqlite3.connect(database, timeout=10) as db:  # Read-write: rolls back a hot journal.
                leaves = [row[0] for row in db.execute('SELECT id FROM client')]
                rows_left = db.execute('SELECT COUNT(*) FROM task').fetchone()[0]
                states = dict(db.execute('SELECT state, COUNT(*) FROM relay_result GROUP BY state').fetchall())
                pending, returned = states.get('pending', 0), states.get('returned', 0)
        except sqlite3.Error as error:
            print(f'relay_probe: could not read relay {site} database ({error})', file=sys.stderr)
    publish(flag(f'relay-{site}.leaves'), '\n'.join(leaves) + '\n')
    text = log_path.read_text(errors='replace') if log_path and log_path.exists() else ''
    key_hits = text.count(key) if key else 0
    if home is not None and key:
        for path in home.rglob('*'):
            try:
                if path.is_file() and key.encode() in path.read_bytes():
                    key_hits += 1
            except OSError:
                pass
    record = {'schema': SCHEMA, 'role': 'relay', 'run_id': RUN_ID, 'site': site, 'rc': rc,
              'identities': len(IDENTITY.findall(text)), 'leaves_launched': len(leaves), 'action': action,
              'local_rows_left': rows_left, 'results_pending': pending, 'results_returned': returned,
              'peak_backlog': sampled.get('peak_backlog', 0), 'peak_bound': sampled.get('peak_bound', 0),
              'backlog_samples': sampled.get('backlog_samples', 0),
              'upstream_conns_peak': sampled.get('upstream_conns_peak', 0),
              'primary_db_created': bool(home and (home / 'primary-sentinel.db').exists()),
              'key_in_logs': key_hits}
    publish_json(out_dir() / f'relay-{site}.json', record)
    publish(flag(f'relay-{site}.finished'), f'{rc}\n')
    return 0


# --- Watch: one site node ------------------------------------------------------------------------


def node_addresses() -> List[str]:
    """Every address of this node's interfaces."""
    found = set()
    for items in psutil.net_if_addrs().values():
        for item in items:
            if item.family in (socket.AF_INET, socket.AF_INET6):
                found.add(item.address.split('%', 1)[0])
    return sorted(found)


def launcher(cmdline: List[str]) -> bool:
    """True for a process that launches a site client (the wrapper, or the step launcher it runs)."""
    step = os.path.basename(setting('HSD_SRUN', 'srun').split()[0])
    return (any(arg.endswith('relay_launch.sh') for arg in cmdline)
            or os.path.basename(cmdline[0]) in ('sh', 'bash', 'dash', 'env', 'srun', step))


def watch(site: str, node: str, hosts_relay: str) -> int:
    """Sample this node's processes of `site` until the relays finish; write leaf-<site>-<node>.json."""
    deadline = time.monotonic() + TIMEOUT + 300
    tmp = node_tmp()
    tmp.mkdir(parents=True, exist_ok=True)
    publish_json(flag(f'watch-{site}-{node}.addrs'), {'hosts_relay': hosts_relay == '1',
                                                      'addresses': node_addresses()})
    publish(flag(f'watch-{site}-{node}.ready'), 'ready\n')
    wait_for(lambda: flag('upstream.ready').exists() or flag('abort').exists(), WAIT)
    if flag('upstream.ready').exists():
        up = json.loads(flag('upstream.ready').read_text())
        upstream_port, upstream_ips = up['port'], addresses(up['host'])
    else:
        upstream_port, upstream_ips = -1, set()
    key, token, me = read_key(), read_token(), os.getpid()

    seen: Dict[str, Set[int]] = {name: set() for name in (
        'procs', 'unreadable', 'clients', 'tasks', 'launchers', 'key', 'fenced', 'probe', 'untokened',
        'site_keyed', 'site_key_argv', 'site_key_tasks')}
    site_keys: Set[str] = set()  # Site keys seen in client environments: kept in memory, never written.
    samples = conns_max = 0
    while not (flag('relays.finished').exists() or flag('abort').exists() or time.monotonic() >= deadline):
        info = flag(f'relay-{site}.info')
        relay_port = json.loads(info.read_text())['port'] if info.exists() else None
        conns = 0
        for proc in psutil.process_iter(['pid', 'cmdline']):
            if proc.pid == me or not own(proc):
                continue
            try:
                cmdline = proc.info['cmdline'] or []
                command = hs_command(cmdline)
                if command == 'relay':
                    continue  # The relay holds the key by design; only what it launched is scanned.
                try:
                    environ = proc.environ()
                except psutil.AccessDenied:
                    environ = {}
                ours = environ.get('HSD_RUN_TOKEN') == token and environ.get('HSD_SITE') == site
                is_site_client = (command == 'client' and relay_port is not None
                                  and str(relay_port) in cmdline and '-p' in cmdline)
                if not ours:
                    if is_site_client:
                        seen['unreadable' if not environ else 'untokened'].add(proc.pid)
                        if any(key in arg for arg in cmdline):
                            seen['key'].add(proc.pid)
                    continue
                seen['procs'].add(proc.pid)
                if 'relay_task.py' in ' '.join(cmdline):
                    seen['tasks'].add(proc.pid)
                    if SITE_KEY_VAR in environ or any(k in v for k in site_keys for v in environ.values()):
                        seen['site_key_tasks'].add(proc.pid)
                elif command == 'client' and not launcher(cmdline):
                    seen['clients'].add(proc.pid)
                    if environ.get(SITE_KEY_VAR):
                        seen['site_keyed'].add(proc.pid)
                        site_keys.add(environ[SITE_KEY_VAR])
                    if any(option in cmdline for option in KEY_OPTIONS):
                        seen['site_key_argv'].add(proc.pid)
                else:
                    seen['launchers'].add(proc.pid)
                if any(k in arg for k in site_keys for arg in cmdline):
                    seen['site_key_argv'].add(proc.pid)
                if any(key in arg for arg in cmdline) or any(key in n or key in v for n, v in environ.items()):
                    seen['key'].add(proc.pid)
                if any(n.startswith('HYPERSHELL_RELAY_') for n in environ):
                    seen['fenced'].add(proc.pid)
                if 'HSD_KEY_PROBE' in environ:
                    seen['probe'].add(proc.pid)
                conns += established_to(proc, upstream_port, upstream_ips)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
        samples += 1
        conns_max = max(conns_max, conns)
        time.sleep(SAMPLE)

    exec_log = tmp / 'exec.log'
    lines = read_lines(exec_log)
    publish(flag(f'exec-{site}-{node}.log'), '\n'.join(lines) + '\n')
    record = {'schema': SCHEMA, 'role': 'leaf', 'run_id': RUN_ID, 'site': site, 'node': int(node),
              'hosts_relay': hosts_relay == '1', 'samples': samples, 'procs_scanned': len(seen['procs']),
              'env_unreadable': len(seen['unreadable']), 'clients_scanned': len(seen['clients']),
              'tasks_scanned': len(seen['tasks']), 'launchers_scanned': len(seen['launchers']),
              'key_hits': len(seen['key']), 'fenced_names_seen': len(seen['fenced']),
              'key_probe_seen': len(seen['probe']), 'untokened_clients': len(seen['untokened']),
              'site_key_clients': len(seen['site_keyed']), 'site_key_argv': len(seen['site_key_argv']),
              'site_key_tasks': len(seen['site_key_tasks']),
              'upstream_conns_max': conns_max, 'executions': len(lines)}
    publish_json(out_dir() / f'leaf-{site}-{node}.json', record)
    publish(flag(f'watch-{site}-{node}.finished'), 'ok\n')
    return 0


# --- Sweep: end what is left of this run here ----------------------------------------------------


def sweep() -> int:
    """End every process of this user carrying this run's token (local mode); print how many."""
    token = read_token()
    left = []
    for proc in psutil.process_iter(['pid']):
        if proc.pid == os.getpid() or not own(proc):
            continue
        try:
            if proc.environ().get('HSD_RUN_TOKEN') == token:
                left.append(proc)
        except psutil.Error:
            continue
    for proc in left:
        end(proc, 5)
    print(len(left))
    return 0


def main(argv: List[str]) -> int:
    """Run one role."""
    if not argv:
        print(__doc__, file=sys.stderr)
        return 64
    role, args = argv[0], argv[1:]
    roles = {'workload': workload, 'upstream': upstream, 'relay': relay, 'watch': watch, 'sweep': sweep}
    if role not in roles:
        print(f'relay_probe: unknown role {role!r}', file=sys.stderr)
        return 64
    return roles[role](*args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
