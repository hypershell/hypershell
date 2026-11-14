#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Compare two builds of HyperShell on one host: final-state parity, client compatibility, throughput.

Each build is a git ref, installed into its own locked, non-editable virtual environment
(``git archive`` + ``uv sync --frozen``) under a work directory, so the code under test is frozen
and neither build touches this checkout. ``HEAD`` means the committed tip of this checkout.
Every run uses a fresh throwaway site and database, a free loopback port and a random key.

Subcommands:

    parity  --base REF --feature REF    Same grouped workload (with a retried failure) on both
                                        builds, TLS on and off: identical normalised task tables,
                                        groups run in order, clean exits, and byte-identical
                                        ``--help`` output (unless ``--skip-help``).
    skew    --new REF --old REF...      Version skew on the work-request protocol, TLS on and off:
                                        an old client against a new server exits within the bound,
                                        is logged as too old, and a concurrent new client finishes
                                        every task; a new client against an old server exits
                                        non-zero within the bound with one version error.
    gate    --base REF --feature REF    Throughput and footprint, interleaved and repeated; with
                                        ``--min-ratio R`` a config passes only if the feature's
                                        median is at least R times the base's.

Never collected by pytest (no ``test_`` prefix). Run it with the project interpreter, which has
``psutil``: ``uv run python tests/bench/bench_queue.py parity --base b819de6 --feature HEAD``.
Exits 0 on success and 1 on any failure.
"""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Any, Optional, Sequence, Tuple

# Standard libs
import io
import os
import sys
import json
import time
import shutil
import socket
import sqlite3
import secrets
import tarfile
import argparse
import tempfile
import threading
import statistics
import subprocess
from pathlib import Path
from datetime import datetime

# External libs
import psutil


REPO = Path(__file__).resolve().parents[2]
WAIT = 60.0
HELP_COMMANDS = ('server', 'client', 'submit', 'cluster')


def log(message: str) -> None:
    """Progress line on stderr."""
    print(message, file=sys.stderr, flush=True)


def resolve(ref: str) -> str:
    """Full commit id for `ref` in this repository."""
    return subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--verify', f'{ref}^{{commit}}'],
                          check=True, capture_output=True, text=True).stdout.strip()


def build(ref: str, workdir: Path) -> Path:
    """A locked, non-editable virtual environment for `ref`, built once and reused by commit id."""
    commit = resolve(ref)
    venv = workdir / f'venv-{commit[:12]}'
    if (venv / 'bin' / 'hs').exists():
        return venv
    src = workdir / f'src-{commit[:12]}'
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    log(f'building {ref} ({commit[:12]})')
    archive = subprocess.run(['git', '-C', str(REPO), 'archive', '--format=tar', commit],
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(src, filter='data')
    env = {**os.environ, 'UV_PROJECT_ENVIRONMENT': str(venv)}
    env.pop('VIRTUAL_ENV', None)
    subprocess.run(['uv', 'sync', '--quiet', '--frozen', '--no-dev', '--no-group', 'docs', '--no-editable'],
                   cwd=src, env=env, check=True)
    return venv


def free_port() -> int:
    """An unused loopback port."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def tree(proc: psutil.Process) -> List[psutil.Process]:
    """`proc` and all of its descendants."""
    try:
        return [proc, *proc.children(recursive=True)]
    except psutil.Error:
        return [proc]


def listening(pid: int, port: int) -> bool:
    """True once a process in the tree rooted at `pid` listens on `port` (no probe connection)."""
    try:
        procs = tree(psutil.Process(pid))
    except psutil.Error:
        return False
    for proc in procs:
        try:
            for conn in proc.net_connections(kind='tcp'):
                if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                    return True
        except psutil.Error:
            pass
    return False


class Sampler(threading.Thread):
    """Peak footprint of a process and of its whole tree, sampled until stopped."""

    KEYS = ('rss_mb', 'threads', 'fds', 'children', 'tree_rss_mb', 'tree_threads', 'tree_fds')

    def __init__(self, pid: int, interval: float) -> None:
        super().__init__(daemon=True)
        self.proc = psutil.Process(pid)
        self.interval = interval
        self.done = threading.Event()
        self.peak = dict.fromkeys(self.KEYS, 0.0)

    def sample(self) -> Dict[str, float]:
        procs = tree(self.proc)
        own = procs[0]
        values = dict(rss_mb=own.memory_info().rss / 2**20, threads=own.num_threads(), fds=own.num_fds(),
                      children=len(procs) - 1, tree_rss_mb=0.0, tree_threads=0, tree_fds=0)
        for proc in procs:
            try:
                values['tree_rss_mb'] += proc.memory_info().rss / 2**20
                values['tree_threads'] += proc.num_threads()
                values['tree_fds'] += proc.num_fds()
            except psutil.Error:
                pass
        return values

    def run(self) -> None:
        while not self.done.is_set():
            try:
                for key, value in self.sample().items():
                    self.peak[key] = max(self.peak[key], value)
            except psutil.Error:
                break
            self.done.wait(self.interval)


class Site:
    """A throwaway HyperShell site with its own database, port and key (in every process's environment)."""

    def __init__(self, workdir: Path, label: str, tls: bool, log_level: str = 'INFO') -> None:
        self.path = Path(tempfile.mkdtemp(prefix=f'site-{label}-', dir=workdir))
        self.port = free_port()
        self.key = secrets.token_hex(16)
        self.tls = tls
        self.database = self.path / 'task.db'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('HYPERSHELL_')}
        self.env.pop('VIRTUAL_ENV', None)
        self.env.update(HYPERSHELL_CONFIG_FILE='', HYPERSHELL_SITE=str(self.path),
                        HYPERSHELL_DATABASE_FILE=str(self.database), HYPERSHELL_LOGGING_LEVEL=log_level,
                        HYPERSHELL_SERVER_AUTH=self.key)

    def tls_args(self) -> List[str]:
        return [] if self.tls else ['--no-tls']

    def run(self, venv: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(venv / 'bin' / 'hs'), *args], cwd=self.path, env=self.env,
                              capture_output=True)

    def spawn(self, venv: Path, name: str, *args: str) -> subprocess.Popen:
        with open(self.path / f'{name}.log', 'wb') as stream:
            return subprocess.Popen([str(venv / 'bin' / 'hs'), *args], cwd=self.path, env=self.env,
                                    stdout=subprocess.DEVNULL, stderr=stream)

    def rows(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.database) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM task')]


def wait_all(server: subprocess.Popen, clients: Sequence[subprocess.Popen],
             server_timeout: float, client_timeout: float) -> Tuple[Any, List[Any], float]:
    """Exit codes of the server and its clients, each bounded; a process over its bound is killed."""
    start = time.monotonic()
    try:
        server_rc = server.wait(timeout=server_timeout)
    except subprocess.TimeoutExpired:
        server.kill()
        server_rc = 'timeout'
    server_exit = time.monotonic()
    client_rcs = []
    for client in clients:
        try:
            client_rcs.append(client.wait(timeout=max(1.0, client_timeout - (time.monotonic() - server_exit))))
        except subprocess.TimeoutExpired:
            client.kill()
            client_rcs.append('timeout')
    return server_rc, client_rcs, server_exit - start


def serve(site: Site, server_venv: Path, client_venv: Path, server_args: Sequence[str],
          client_args: Sequence[str], clients: int, server_timeout: float = WAIT,
          client_timeout: float = WAIT, sampler_interval: Optional[float] = None) -> Dict[str, Any]:
    """Run `hs server --restart` over the site's database with `clients` loopback clients."""
    start = time.monotonic()
    server = site.spawn(server_venv, 'server', 'server', '--restart', '-H', 'localhost', '-p', str(site.port),
                        *server_args, *site.tls_args())
    sampler = None
    if sampler_interval:
        sampler = Sampler(server.pid, sampler_interval)
        sampler.start()
    deadline = time.monotonic() + WAIT
    while not listening(server.pid, site.port):
        if server.poll() is not None or time.monotonic() > deadline:
            server.kill()
            raise RuntimeError(f'server did not listen; see {site.path}/server.log')
        time.sleep(0.05)
    ready = time.monotonic()
    procs = [site.spawn(client_venv, f'client-{index}', 'client', '-H', 'localhost', '-p', str(site.port),
                        '-T', '60', *client_args, *site.tls_args())
             for index in range(clients)]
    server_rc, client_rcs, _ = wait_all(server, procs, server_timeout, client_timeout)
    end = time.monotonic()
    if sampler:
        sampler.done.set()
        sampler.join()
    return dict(server_rc=server_rc, client_rcs=client_rcs, startup_s=ready - start, wall_s=end - start,
                peak=({k: round(v, 1) for k, v in sampler.peak.items()} if sampler else None))


def submit(site: Site, venv: Path, lines: Sequence[str]) -> None:
    """Write the task file and submit it to the site's database."""
    taskfile = site.path / 'tasks.in'
    taskfile.write_text(''.join(f'{line}\n' for line in lines))
    proc = site.run(venv, 'submit', '-f', str(taskfile), '-b', '5000', '-w', '1')
    if proc.returncode != 0:
        raise RuntimeError(f'submit failed: {proc.stderr.decode()[-2000:]}')


def clean(result: Dict[str, Any]) -> bool:
    """Every process exited 0."""
    return result['server_rc'] == 0 and all(rc == 0 for rc in result['client_rcs'])


# ---------------------------------------------------------------------------------------- parity


def grouped_workload(site: Path) -> List[str]:
    """Three groups; one task in the first group fails on its first attempt only."""
    marker = site / 'retry.marker'
    return [
        'echo a0  #HYPERSHELL: group:0',
        'echo a1  #HYPERSHELL: group:0',
        'echo a2  #HYPERSHELL: group:0',
        f'test -f {marker} || {{ touch {marker}; exit 3; }}  #HYPERSHELL: group:0',
        'echo b0  #HYPERSHELL: group:1',
        'echo b1  #HYPERSHELL: group:1',
        'echo b2  #HYPERSHELL: group:1',
        'echo c0  #HYPERSHELL: group:2',
        'echo c1  #HYPERSHELL: group:2',
    ]


def normalise(site: Site, rows: List[Dict[str, Any]]) -> List[Tuple]:
    """Final task states without ids, times or site paths."""
    return sorted((row['args'].replace(str(site.path), '<site>'), row['group'], row['attempt'],
                   row['exit_status'], bool(row['retried']), row['completion_time'] is not None)
                  for row in rows)


def group_order(rows: List[Dict[str, Any]]) -> bool:
    """Every task of each group completed before any task of the next group was scheduled."""
    groups = sorted({row['group'] for row in rows})
    for earlier, later in zip(groups, groups[1:]):
        done = [row['completion_time'] for row in rows if row['group'] == earlier]
        started = [row['schedule_time'] for row in rows if row['group'] == later]
        if None in done or None in started or max(done) > min(started):
            return False
    return True


def run_parity_once(venv: Path, label: str, tls: bool, workdir: Path) -> Tuple[List[Tuple], Dict[str, Any]]:
    site = Site(workdir, label, tls)
    submit(site, venv, grouped_workload(site.path))
    result = serve(site, venv, venv, ['-r', '1'], [], clients=2)
    rows = site.rows()
    result['group_order'] = group_order(rows)
    return normalise(site, rows), result


def help_text(venv: Path, workdir: Path) -> Dict[str, bytes]:
    site = Site(workdir, 'help', True)
    return {command: site.run(venv, command, '--help').stdout for command in HELP_COMMANDS}


def cmd_parity(args: argparse.Namespace) -> bool:
    base, feature = build(args.base, args.workdir), build(args.feature, args.workdir)
    ok = True
    for tls in (True, False):
        table_base, result_base = run_parity_once(base, 'base', tls, args.workdir)
        table_feature, result_feature = run_parity_once(feature, 'feature', tls, args.workdir)
        same = table_base == table_feature
        exits = clean(result_base) and clean(result_feature)
        ordered = result_base['group_order'] and result_feature['group_order']
        ok &= same and exits and ordered and len(table_feature) == 10
        print(f'parity tls={"on" if tls else "off"}: rows {len(table_base)} vs {len(table_feature)}, '
              f'identical={same}; group order base {result_base["group_order"]} '
              f'feature {result_feature["group_order"]}; rc base {result_base["server_rc"]}/'
              f'{result_base["client_rcs"]} feature {result_feature["server_rc"]}/{result_feature["client_rcs"]}; '
              f'wall s base {result_base["wall_s"]:.1f} feature {result_feature["wall_s"]:.1f} -> '
              f'{"PASS" if same and exits and ordered else "FAIL"}')
        if not same:
            for row in sorted(set(table_base) ^ set(table_feature)):
                print(f'  differs: {row}')
    if args.skip_help:
        print('help: skipped (--skip-help)')
        return ok
    help_base, help_feature = help_text(base, args.workdir), help_text(feature, args.workdir)
    for command in HELP_COMMANDS:
        same = help_base[command] == help_feature[command] and len(help_base[command]) > 0
        ok &= same
        print(f'help hs {command}: {len(help_base[command])} bytes, identical={same} -> {"PASS" if same else "FAIL"}')
    return ok


# ------------------------------------------------------------------------------------------ skew


def spawn_group(site: Site, venv: Path, name: str, *args: str) -> subprocess.Popen:
    """Start `hs` from `venv` in its own process group, logging to the site."""
    with open(site.path / f'{name}.log', 'wb') as stream:
        return subprocess.Popen([str(venv / 'bin' / 'hs'), *args], cwd=site.path, env=site.env,
                                stdout=subprocess.DEVNULL, stderr=stream, start_new_session=True)


def kill_group(proc: subprocess.Popen) -> None:
    """Kill the process group of `proc` if it is still running, and reap it."""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
    proc.wait()


def wait_bounded(proc: subprocess.Popen, bound: float) -> Tuple[Any, float]:
    """Exit code of `proc` (or 'timeout') and the seconds it took, killing its group past `bound`."""
    start = time.monotonic()
    try:
        rc = proc.wait(timeout=bound)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        rc = 'timeout'
    return rc, time.monotonic() - start


def start_server(site: Site, venv: Path, name: str = 'server') -> subprocess.Popen:
    """`hs server --restart` from `venv` on the site, once it listens."""
    server = spawn_group(site, venv, name, 'server', '--restart', '-H', 'localhost', '-p', str(site.port),
                         *site.tls_args())
    deadline = time.monotonic() + WAIT
    while not listening(server.pid, site.port):
        if server.poll() is not None or time.monotonic() > deadline:
            kill_group(server)
            raise RuntimeError(f'server did not listen; see {site.path}/{name}.log')
        time.sleep(0.05)
    return server


def client_args(site: Site) -> List[str]:
    """Connection arguments of a client for the site (no -T: an old client must exit on its own)."""
    return ['client', '-H', 'localhost', '-p', str(site.port), *site.tls_args()]


def old_client_new_server(new: Path, old: Path, label: str, tls: bool, args: argparse.Namespace) -> bool:
    """An old client is turned away in bounded time while a new client finishes every task."""
    site = Site(args.workdir, f'skew-old-client-{label}', tls)
    submit(site, new, [f'echo {i}' for i in range(args.tasks)])
    server = start_server(site, new)
    procs = [server]
    try:
        old_client = spawn_group(site, old, 'old-client', *client_args(site))
        procs.append(old_client)
        new_client = spawn_group(site, new, 'new-client', *client_args(site), '-T', '30')
        procs.append(new_client)
        old_rc, old_s = wait_bounded(old_client, args.bound)
        new_rc, _ = wait_bounded(new_client, WAIT)
        server_rc, _ = wait_bounded(server, WAIT)
    finally:
        for proc in procs:
            kill_group(proc)
    log_text = (site.path / 'server.log').read_text(errors='replace')
    refused = log_text.count('Client too old')
    rows = site.rows()
    finished = len(rows) == args.tasks and all(r['exit_status'] == 0 and r['completion_time'] for r in rows)
    passed = old_rc not in ('timeout', 0) and refused >= 1 and new_rc == 0 and server_rc == 0 and finished
    print(f'skew old client {label} -> new server, tls={"on" if tls else "off"}: old client rc {old_rc} '
          f'in {old_s:.1f} s (bound {args.bound:g}), "too old" logged {refused}x, new client rc {new_rc}, '
          f'server rc {server_rc}, {sum(r["exit_status"] == 0 for r in rows)}/{args.tasks} tasks done '
          f'-> {"PASS" if passed else "FAIL"}')
    return passed


def new_client_old_server(new: Path, old: Path, label: str, tls: bool, args: argparse.Namespace) -> bool:
    """A new client against an old server exits non-zero in bounded time with one version error."""
    site = Site(args.workdir, f'skew-old-server-{label}', tls)
    submit(site, old, [f'echo {i}' for i in range(args.tasks)])
    server = start_server(site, old)
    procs = [server]
    try:
        client = spawn_group(site, new, 'new-client', *client_args(site))
        procs.append(client)
        rc, seconds = wait_bounded(client, args.bound)
    finally:
        for proc in procs:
            kill_group(proc)
    lines = (site.path / 'new-client.log').read_text(errors='replace').splitlines()
    critical = [line for line in lines if line.startswith('CRITICAL')]
    named = len(critical) == 1 and 'work-request protocol' in critical[0]
    passed = rc not in ('timeout', 0) and named
    print(f'skew new client -> old server {label}, tls={"on" if tls else "off"}: rc {rc} in {seconds:.1f} s '
          f'(bound {args.bound:g}), critical lines {len(critical)}'
          f'{": " + critical[0][:160] if critical else ""} -> {"PASS" if passed else "FAIL"}')
    return passed


def cmd_skew(args: argparse.Namespace) -> bool:
    new = build(args.new, args.workdir)
    ok = True
    for ref in args.old:
        old = build(ref, args.workdir)
        for tls in (True, False):
            ok &= old_client_new_server(new, old, ref, tls, args)
            ok &= new_client_old_server(new, old, ref, tls, args)
    return ok


# ------------------------------------------------------------------------------------------ gate


# (name, clients, threads per client, server bundle, client bundle or None for no -b, tls, tasks).
# A client given no -b advertises no size, so the server's -b sizes its bundles (the default path).
GATE_CONFIGS: Tuple[Tuple[str, int, int, int, Optional[int], bool, int], ...] = (
    ('4x4 b=1 tls', 4, 4, 1, 1, True, 5000),
    ('4x4 b=1 no-tls', 4, 4, 1, 1, False, 5000),
    ('16x1 b=1 tls', 16, 1, 1, 1, True, 5000),
    ('16x1 b=1 no-tls', 16, 1, 1, 1, False, 5000),
    ('4x4 b=50 tls', 4, 4, 50, 50, True, 20000),
    ('32x1 b=1 tls', 32, 1, 1, 1, True, 5000),
    ('4x4 b=1 client-default tls', 4, 4, 1, None, True, 5000),
    ('16x1 b=1 client-default no-tls', 16, 1, 1, None, False, 5000),
    ('4x4 b=50 client-default tls', 4, 4, 50, None, True, 20000),
)


def context(venv: Path) -> str:
    """Python version and multiprocessing start method of a build."""
    return subprocess.run([str(venv / 'bin' / 'python'), '-c',
                           'import sys, multiprocessing as mp; '
                           'print(sys.version.split()[0], mp.get_start_method())'],
                          check=True, capture_output=True, text=True).stdout.strip()


def throughput(rows: List[Dict[str, Any]], tasks: int) -> Dict[str, Any]:
    """Steady-state (p10-p90 of completions) and full-span tasks per second, from the database."""
    done = sorted(datetime.fromisoformat(row['completion_time']).timestamp()
                  for row in rows if row['completion_time'] is not None)
    first = min(datetime.fromisoformat(row['schedule_time']).timestamp()
                for row in rows if row['schedule_time'] is not None)
    lo, hi = done[int(0.1 * len(done))], done[int(0.9 * len(done)) - 1]
    histogram: Dict[str, int] = {}
    for row in rows:
        histogram[str(row['exit_status'])] = histogram.get(str(row['exit_status']), 0) + 1
    return dict(p10_p90=round(0.8 * len(done) / (hi - lo), 1), full=round(len(done) / (done[-1] - first), 1),
                rows=len(rows), incomplete=sum(row['completion_time'] is None for row in rows),
                histogram=histogram, complete=len(rows) == tasks and len(done) == tasks)


def run_gate_once(venv: Path, label: str, config: Tuple, args: argparse.Namespace) -> Dict[str, Any]:
    name, clients, threads, bundle, client_bundle, tls, tasks = config
    site = Site(args.workdir, label, tls, log_level='WARNING')
    submit(site, venv, ['true'] * tasks)
    client_size = [] if client_bundle is None else ['-b', str(client_bundle)]
    result = serve(site, venv, venv, ['-b', str(bundle)],
                   ['-N', str(threads), *client_size, '-w', '1'], clients=clients,
                   server_timeout=args.timeout, client_timeout=WAIT, sampler_interval=args.interval)
    result.update(throughput(site.rows(), tasks))
    result.update(label=label, config=name)
    shutil.rmtree(site.path, ignore_errors=True)
    return result


def cmd_gate(args: argparse.Namespace) -> bool:
    builds = [('base', build(args.base, args.workdir)), ('feature', build(args.feature, args.workdir))]
    for label, venv in builds:
        print(f'# {label}: {args.base if label == "base" else args.feature} (python, start method: {context(venv)})')
    configs = [config for config in GATE_CONFIGS if not args.only or config[0] in args.only]
    results: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for config in configs:
        for repeat in range(args.repeats):
            for label, venv in builds:
                result = run_gate_once(venv, label, config, args)
                results.setdefault((config[0], label), []).append(result)
                if args.out:
                    with open(args.out, 'a') as stream:
                        stream.write(json.dumps(result) + '\n')
                log(f'{config[0]} {label} #{repeat}: {result["p10_p90"]} tasks/s, rc {result["server_rc"]}/'
                    f'{sorted(set(map(str, result["client_rcs"])))}, peak {result["peak"]}')
    ok = True
    rule = (f'ratio >= {args.min_ratio:g}' if args.min_ratio is not None else 'tolerance')
    print(f'| config | base median [min-max] | feature median [min-max] | delta | {rule} | throughput '
          f'| tree children b/f | tree FDs b/f | tree threads b/f | own threads b/f | own FDs b/f '
          f'| tree RSS MB b/f | clean | verdict |')
    print('|' + '---|' * 14)
    for config in configs:
        name = config[0]
        base, feature = results[(name, 'base')], results[(name, 'feature')]
        tb, tf = [r['p10_p90'] for r in base], [r['p10_p90'] for r in feature]
        mb, mf = statistics.median(tb), statistics.median(tf)
        if args.min_ratio is not None:
            tolerance = mf / mb
            fast = mf >= args.min_ratio * mb
        else:
            tolerance = max(max(tb) - min(tb), max(tf) - min(tf), 0.05 * mb)
            fast = mf >= mb - tolerance
        peak = {key: (max(r['peak'][key] for r in base), max(r['peak'][key] for r in feature))
                for key in Sampler.KEYS}
        small = peak['children'][1] <= peak['children'][0] and peak['tree_fds'][1] <= peak['tree_fds'][0]
        tidy = all(clean(r) and r['complete'] and r['incomplete'] == 0 for r in base + feature)
        passed = fast and small and tidy
        ok &= passed
        pair = lambda key: f'{peak[key][0]:g}/{peak[key][1]:g}'
        print(f'| {name} | {mb:.1f} [{min(tb):.1f}-{max(tb):.1f}] | {mf:.1f} [{min(tf):.1f}-{max(tf):.1f}] '
              f'| {mf - mb:+.1f} | {tolerance:.3g} | {"PASS" if fast else "FAIL"} | {pair("children")} '
              f'| {pair("tree_fds")} | {pair("tree_threads")} | {pair("threads")} | {pair("fds")} '
              f'| {pair("tree_rss_mb")} | {tidy} | {"PASS" if passed else "FAIL"} |')
    print(f'# gate: {"PASS" if ok else "FAIL"} ({args.repeats} repeats per build, interleaved; '
          f'load average {os.getloadavg()[0]:.1f} at the end)')
    return ok


# ------------------------------------------------------------------------------------------ main


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[1], prog='bench_queue.py')
    parser.add_argument('--workdir', type=Path,
                        default=Path(os.environ.get('TMPDIR', tempfile.gettempdir())) / 'hypershell-bench')
    commands = parser.add_subparsers(dest='command', required=True)
    parity = commands.add_parser('parity')
    parity.add_argument('--base', required=True)
    parity.add_argument('--feature', required=True)
    parity.add_argument('--skip-help', action='store_true', help='do not compare --help output')
    skew = commands.add_parser('skew')
    skew.add_argument('--new', required=True)
    skew.add_argument('--old', required=True, action='append')
    skew.add_argument('--bound', type=float, default=30.0, help='seconds a refused client may take to exit')
    skew.add_argument('--tasks', type=int, default=200)
    gate = commands.add_parser('gate')
    gate.add_argument('--base', required=True)
    gate.add_argument('--feature', required=True)
    gate.add_argument('--repeats', type=int, default=5)
    gate.add_argument('--min-ratio', type=float, default=None,
                      help='pass a config only if the feature median is at least this fraction of the base median')
    gate.add_argument('--interval', type=float, default=0.2, help='footprint sampling period (seconds)')
    gate.add_argument('--timeout', type=float, default=600.0, help='bound on one server run (seconds)')
    gate.add_argument('--only', action='append', help='run only this config (repeatable)')
    gate.add_argument('--out', help='append each run as a JSON line to this file')
    args = parser.parse_args(argv)
    args.workdir.mkdir(parents=True, exist_ok=True)
    handler = {'parity': cmd_parity, 'skew': cmd_skew, 'gate': cmd_gate}[args.command]
    return 0 if handler(args) else 1


if __name__ == '__main__':
    sys.exit(main())
