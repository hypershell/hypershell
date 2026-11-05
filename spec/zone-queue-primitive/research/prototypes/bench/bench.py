#!/usr/bin/env python3
"""Single-host queue throughput/footprint benchmark for `hs server` + N `hs client` (prototype).

Run with the target venv's python (needs psutil, a HyperShell dependency):
    VENV/bin/python bench.py --venv VENV --label base --clients 4 --tasks 10000 --repeats 3
Each run: fresh temp site + SQLite DB, pre-submit M tasks, `hs server --restart` drains the DB
to N loopback clients, then exits; clients exit on the server's disconnect sentinel.
Throughput comes from DB timestamps (first schedule_time -> last completion_time).
"""
import argparse, json, os, secrets, socket, sqlite3, statistics, subprocess, sys, tempfile, time, threading
from datetime import datetime
import psutil


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def tree(proc):
    try:
        return [proc] + proc.children(recursive=True)
    except psutil.Error:
        return [proc]


def listening(proc, port):
    for p in tree(proc):
        try:
            for c in p.net_connections(kind='tcp'):
                if c.status == psutil.CONN_LISTEN and c.laddr.port == port:
                    return True
        except psutil.Error:
            pass
    return False


class Sampler(threading.Thread):
    def __init__(self, pid, interval):
        super().__init__(daemon=True)
        self.proc, self.interval, self.stop_flag = psutil.Process(pid), interval, threading.Event()
        self.kids = set()
        self.peak = dict(rss_mb=0.0, threads=0, fds=0, children=0, tree_rss_mb=0.0, tree_threads=0, tree_fds=0)

    def run(self):
        while not self.stop_flag.is_set():
            try:
                ps = tree(self.proc)
                own = ps[0]
                vals = dict(rss_mb=own.memory_info().rss / 2**20, threads=own.num_threads(), fds=own.num_fds(),
                            children=len(ps) - 1)
                tr = dict(tree_rss_mb=0.0, tree_threads=0, tree_fds=0)
                for p in ps:
                    try:
                        tr['tree_rss_mb'] += p.memory_info().rss / 2**20
                        tr['tree_threads'] += p.num_threads()
                        tr['tree_fds'] += p.num_fds()
                    except psutil.Error:
                        pass
                vals.update(tr)
                for p in ps[1:]:
                    try: self.kids.add(' '.join(p.cmdline())[:160])
                    except psutil.Error: pass
                for k, v in vals.items():
                    self.peak[k] = max(self.peak[k], v)
            except psutil.Error:
                break
            self.stop_flag.wait(self.interval)


def one_run(a, label, venv, run_idx):
    site = tempfile.mkdtemp(prefix=f'hsbench-{label}-', dir=a.workdir)
    env = {k: v for k, v in os.environ.items() if not k.startswith('HYPERSHELL_')}
    env.update(HYPERSHELL_CONFIG_FILE='', HYPERSHELL_SITE=site,
               HYPERSHELL_DATABASE_FILE=os.path.join(site, 'task.db'),
               HYPERSHELL_LOGGING_LEVEL=a.log_level)
    hs = os.path.join(venv, 'bin', 'hs')
    port, key = free_port(), secrets.token_hex(16)
    tls = [] if a.tls else ['--no-tls']
    taskfile = os.path.join(site, 'tasks.in')
    with open(taskfile, 'w') as f:
        f.writelines(f'{a.command}\n' for _ in range(a.tasks))
    subprocess.run([hs, 'submit', '-f', taskfile, '-b', '5000', '-w', '1'], env=env, cwd=site, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    slog = open(os.path.join(site, 'server.log'), 'w')
    t0 = time.monotonic()
    server = subprocess.Popen([hs, 'server', '--restart', '-H', 'localhost', '-p', str(port), '-k', key,
                               '-b', str(a.bundlesize), *tls], env=env, cwd=site, stdout=slog, stderr=slog)
    sampler = Sampler(server.pid, a.interval)
    sampler.start()
    deadline = time.monotonic() + 60
    while not listening(psutil.Process(server.pid), port):
        if time.monotonic() > deadline or server.poll() is not None:
            raise RuntimeError(f'server did not listen (site={site})')
        time.sleep(0.05)
    t_listen = time.monotonic()
    clients = []
    for i in range(a.clients):
        clog = open(os.path.join(site, f'client-{i}.log'), 'w')
        clients.append(subprocess.Popen([hs, 'client', '-H', 'localhost', '-p', str(port), '-k', key,
                                         '-N', str(a.threads), '-b', str(a.client_bundlesize),
                                         '-w', str(a.client_bundlewait), '-T', '60', *tls],
                                        env=env, cwd=site, stdout=subprocess.DEVNULL, stderr=clog))
    try:
        server_rc = server.wait(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        server.kill(); server_rc = 'timeout'
    t_server_exit = time.monotonic()
    sampler.stop_flag.set(); sampler.join()
    client_rcs = []
    for c in clients:
        try:
            client_rcs.append(c.wait(timeout=30))
        except subprocess.TimeoutExpired:
            c.kill(); client_rcs.append('timeout')
    t_clients_exit = time.monotonic()
    db = sqlite3.connect(os.path.join(site, 'task.db'))
    q = lambda sql: db.execute(sql).fetchall()
    first, last = q('SELECT MIN(schedule_time), MAX(completion_time) FROM task')[0]
    done = sorted(datetime.fromisoformat(x[0]).timestamp() for x in q(
        'SELECT completion_time FROM task WHERE completion_time IS NOT NULL'))
    lo, hi = done[int(0.1 * len(done))], done[int(0.9 * len(done)) - 1]  # steady-state window
    hist = dict(q('SELECT exit_status, COUNT(*) FROM task GROUP BY exit_status'))
    (n_total, n_retried, n_incomplete), = q(
        'SELECT COUNT(*), SUM(retried), SUM(completion_time IS NULL) FROM task')
    db.close()
    span = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
    return dict(label=label, run=run_idx, tls=a.tls, clients=a.clients, threads=a.threads, tasks=a.tasks,
                bundlesize=a.bundlesize, db_span_s=round(span, 3), tasks_per_s=round(a.tasks / span, 1),
                p10_p90_tasks_per_s=round(0.8 * len(done) / (hi - lo), 1),
                startup_s=round(t_listen - t0, 3), server_wall_s=round(t_server_exit - t0, 3),
                clients_exit_after_server_s=round(t_clients_exit - t_server_exit, 3),
                server_rc=server_rc, client_rcs=client_rcs, exit_hist={str(k): v for k, v in hist.items()},
                rows=n_total, retried=n_retried, incomplete=n_incomplete,
                peak={k: round(v, 1) for k, v in sampler.peak.items()}, children=sorted(sampler.kids), site=site)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='append', required=True, metavar='LABEL=VENV',
                    help='repeatable; the first is the baseline; runs are interleaved A B A B ...')
    ap.add_argument('--clients', type=int, default=4); ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--tasks', type=int, default=10000); ap.add_argument('--repeats', type=int, default=5)
    ap.add_argument('--bundlesize', type=int, default=1)
    ap.add_argument('--client-bundlesize', type=int, default=1); ap.add_argument('--client-bundlewait', type=float, default=1)
    ap.add_argument('--no-tls', dest='tls', action='store_false')
    ap.add_argument('--command', default='true'); ap.add_argument('--interval', type=float, default=0.2)
    ap.add_argument('--timeout', type=float, default=600); ap.add_argument('--log-level', default='WARNING')
    ap.add_argument('--tolerance-floor', type=float, default=0.05, help='min tolerance as a fraction of baseline median')
    ap.add_argument('--workdir', default=os.environ.get('TMPDIR', '/tmp'))
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    builds = [b.split('=', 1) for b in a.build]
    results = {label: [] for label, _ in builds}
    for i in range(a.repeats):
        for label, venv in builds:
            r = one_run(a, label, venv, i)
            results[label].append(r)
            with open(a.out, 'a') as f:
                f.write(json.dumps(r) + '\n')
            print(json.dumps({k: r[k] for k in ('label', 'tls', 'clients', 'run', 'p10_p90_tasks_per_s', 'tasks_per_s',
                                                 'server_rc', 'incomplete', 'peak')}), flush=True)
    summary = {}
    for label, rs in results.items():
        tps = [r['p10_p90_tasks_per_s'] for r in rs]
        clean = all(r['server_rc'] == 0 and r['incomplete'] == 0 and set(r['client_rcs']) == {0} for r in rs)
        peak = {k: max(r['peak'][k] for r in rs) for k in ('children', 'tree_threads', 'tree_fds', 'tree_rss_mb')}
        summary[label] = (statistics.median(tps), max(tps) - min(tps), peak)
        print(f'# {label} tls={a.tls} N={a.clients}x{a.threads} M={a.tasks} b={a.bundlesize}: '
              f'median={summary[label][0]:.1f} min={min(tps):.1f} max={max(tps):.1f} '
              f'spread={summary[label][1]:.1f} tasks/s clean={clean} peak={peak}', flush=True)
    ok = True
    if len(builds) > 1:
        (mb, sb, pb) = summary[builds[0][0]]
        for label, _ in builds[1:]:
            mn, sn, pn = summary[label]
            tol = max(sb, sn, a.tolerance_floor * mb)
            foot = pn['children'] <= pb['children'] and pn['tree_fds'] <= pb['tree_fds']
            ok &= mn >= mb - tol and foot
            print(f'# R9 footprint {label}: children {pn["children"]} vs {pb["children"]}, '
                  f'tree_fds {pn["tree_fds"]} vs {pb["tree_fds"]} -> {"PASS" if foot else "FAIL"}', flush=True)
            print(f'# R9 {label} vs {builds[0][0]}: {mn - mb:+.1f} tasks/s, tolerance {tol:.1f} -> '
                  f'{"PASS" if mn >= mb - tol else "FAIL"}', flush=True)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
