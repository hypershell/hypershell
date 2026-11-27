# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge of the relay-hardening laptop drive (hardening_drive.sh), from the files of one run.

    python hardening_judge.py judge RUN MODE      exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE

RUN is the drive's scratch directory. It holds the upstream servers' logs (server-N.log, one per
start) and the relay's (relay/relay.log), all in the system log style with UTC times; the launch log
(launches.log, see hardening_shim.sh); one line per task run (runs.log, `TASK_ID EPOCH ZONE`); the
drive's facts (facts.txt, `key=value` lines); and, except in mode zones-nodb, the upstream SQLite
database (task.db). Each check is printed as PASS, FAIL or INCONCLUSIVE, and the last line is the
verdict. Standard library only, so it runs with any interpreter.
"""

import os
import re
import sys
import sqlite3
from datetime import datetime, timezone

LINE_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) ')
TLS_DEADLINE = 30.0  # relay.upstream.deadline, its default
MODES = ('restart', 'tls-on', 'tls-off', 'zones', 'zones-nodb', 'pair-relay', 'pair-server')


def stamp(line):
    """The time of a log line in the system style (UTC), or None."""
    match = LINE_TIME.match(line)
    if not match:
        return None
    return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=timezone.utc).timestamp()


def lines(path):
    """The lines of a file, or none if it is missing."""
    if not os.path.exists(path):
        return []
    with open(path, errors='replace') as stream:
        return stream.read().splitlines()


def first(found, needle):
    """The stamp of the first line holding `needle`, or None."""
    for line in found:
        if needle in line:
            return stamp(line)
    return None


def facts(run):
    """The drive's facts, as strings."""
    found = {}
    for line in lines(os.path.join(run, 'facts.txt')):
        name, _, value = line.partition('=')
        found[name] = value
    return found


def task_runs(run):
    """Each task id run at the site, with the zones it ran in (one entry per run)."""
    found = {}
    for line in lines(os.path.join(run, 'runs.log')):
        parts = line.split()
        if len(parts) >= 3:
            found.setdefault(parts[0], []).append(parts[2])
    return found


def upstream(run):
    """Every task upstream: {id: (zone name or 'default', completed, exit status)}, or None without a database."""
    path = os.path.join(run, 'task.db')
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    try:
        rows = conn.execute('SELECT task.id, zone.name, task.completion_time, task.exit_status '
                            'FROM task LEFT JOIN zone ON zone.id = task.zone').fetchall()
    finally:
        conn.close()
    return {row[0]: (row[1] or 'default', row[2] is not None, row[3]) for row in rows}


class Verdict:
    """Checks and their outcome."""

    def __init__(self):
        self.failed = False
        self.unsure = False

    def check(self, name, ok):
        print(f'{"PASS" if ok else "FAIL":<12} {name}')
        self.failed = self.failed or not ok

    def inconclusive(self, name):
        print(f'{"INCONCLUSIVE":<12} {name}')
        self.unsure = True

    def note(self, text):
        print(f'{"NOTE":<12} {text}')

    def status(self):
        if self.failed:
            return 1
        return 2 if self.unsure else 0


def exits(fact, verdict, *names):
    """Each named process exited 0 by itself."""
    for name in names:
        verdict.check(f'{name} exited 0 by itself (exit {fact.get(f"{name}_exit", "?")})', fact.get(f'{name}_exit') == '0')


def no_trouble(relay, verdict):
    """The relay logged no traceback and no CRITICAL line."""
    verdict.check('the relay logged no traceback', not any('Traceback' in line for line in relay))
    verdict.check('the relay logged no CRITICAL line', not any('CRITICAL' in line for line in relay))


def completed_once(run, fact, verdict, rerun_allowed=False):
    """Every task completed upstream with exit 0, and each ran at the site (once, unless re-runs are allowed)."""
    tasks = upstream(run)
    ran = task_runs(run)
    total = int(fact.get('total', '0') or 0)
    verdict.check(f'{total} tasks submitted', total > 0 and tasks is not None and len(tasks) == total)
    if not tasks:
        return tasks, ran
    verdict.check('every task completed upstream with exit status 0',
                  all(done and status == 0 for _, done, status in tasks.values()))
    verdict.check('every task ran at the site', set(tasks) <= set(ran) and set(ran) <= set(tasks))
    again = sorted(task_id for task_id, zones in ran.items() if len(zones) > 1)
    if rerun_allowed:
        verdict.note(f'{len(again)} tasks ran more than once (an upstream restart puts back what was in flight)')
    else:
        verdict.check(f'every task ran once ({len(again)} more than once)', not again)
    return tasks, ran


def judge_restart(run, fact, verdict):
    """The upstream server restarted twice on its port: the relay carried on and finished."""
    relay = lines(os.path.join(run, 'relay', 'relay.log'))
    verdict.check(f'three upstream server starts ({fact.get("servers")})', fact.get('servers') == '3')
    total = int(fact.get('total', '0') or 0)
    for step in ('1', '2'):
        done = int(fact.get(f'restart_{step}_completed', str(total)) or total)
        verdict.check(f'restart {step} came mid-run ({done} of {total} complete)', done < total)
    exits(fact, verdict, 'relay', 'server')
    verdict.check(f'the relay never exited 5 (exit {fact.get("relay_exit")})', fact.get('relay_exit') != '5')
    no_trouble(relay, verdict)
    lost = [line for line in relay if 'Lost connection' in line]
    if lost:
        verdict.check(f'the relay lost its link and carried on ({len(lost)} lost-link warnings)', True)
    else:
        verdict.inconclusive('the relay never logged a lost link, so the restarts were not seen')
    stale = [line for line in relay if 'unknown proxy id' in line]
    verdict.note(f'{len(stale)} lost-link lines name a restarted upstream (unknown proxy id)')
    completed_once(run, fact, verdict, rerun_allowed=True)


def judge_tls(run, fact, verdict, mode):
    """A TLS mismatch: exit 5 at once, the error names the relay's own setting, nothing launched."""
    relay = lines(os.path.join(run, 'relay', 'relay.log'))
    verdict.check(f'the relay exited 5 (exit {fact.get("relay_exit")})', fact.get('relay_exit') == '5')
    try:
        elapsed = float(fact['relay_exited_at']) - float(fact['relay_started_at'])
    except (KeyError, ValueError):
        elapsed = None
    verdict.check(f'within relay.upstream.deadline ({TLS_DEADLINE:.0f} s) of its start '
                  f'({"?" if elapsed is None else f"{elapsed:.1f} s, by the drive`s 1 s poll"})',
                  elapsed is not None and elapsed < TLS_DEADLINE)
    times = [stamp(line) for line in relay if stamp(line) is not None]
    if len(times) >= 2:
        verdict.note(f'first to last relay log line: {times[-1] - times[0]:.3f} s')
    critical = [line for line in relay if 'CRITICAL' in line]
    verdict.check(f'one CRITICAL line ({len(critical)})', len(critical) == 1)
    text = critical[0] if critical else ''
    setting = '[relay.upstream.tls] enabled' if mode == 'tls-on' else '--no-tls'
    verdict.check(f'it names the relay`s own setting ({setting}) and says the two ends must agree on TLS',
                  setting in text and 'must agree on TLS' in text)
    verdict.check('the relay logged no traceback', not any('Traceback' in line for line in relay))
    verdict.check('no relay-local server started', not any('Relay-local server ready' in line for line in relay))
    launches = [line for line in lines(os.path.join(run, 'launches.log')) if line.startswith('launch ')]
    verdict.check(f'no site client launched ({len(launches)})', not launches)
    verdict.check('the upstream server was still running', fact.get('server_running') == '1')


def judge_zones(run, fact, verdict):
    """A zone with no site client takes what is staged for it at the shutdown, and runs it once a client comes."""
    relay = lines(os.path.join(run, 'relay', 'relay.log'))
    launch_log = lines(os.path.join(run, 'launches.log'))
    exits(fact, verdict, 'relay', 'server')
    no_trouble(relay, verdict)
    tasks, ran = completed_once(run, fact, verdict)
    if tasks:
        verdict.check('every task ran in its own zone',
                      all(zones == [tasks[task_id][0]] for task_id, zones in ran.items() if task_id in tasks))
    cpu_heard = first(relay, 'no more requests for zone cpu')
    held = first(relay, 'for zone gpu, which has no site client')
    verdict.check('the cpu zone heard the shutdown', cpu_heard is not None)
    verdict.check('the gpu zone, with no site client, took a staged task at the shutdown', held is not None)
    holds = [line for line in launch_log if line.startswith('hold ') and ' word=gpu ' in line]
    verdict.check(f'the gpu client was held after its first one exited ({len(holds)} held launch)', len(holds) == 1)
    try:
        released = float(fact['released_at'])
    except (KeyError, ValueError):
        released = None
    if cpu_heard is not None and held is not None and released is not None:
        verdict.check(f'in order: cpu heard the shutdown, gpu held a task (+{held - cpu_heard:.3f} s), '
                      f'then the gpu client came back (+{released - held:.3f} s)',
                      cpu_heard <= held <= released)
    gpu = [task_id for task_id, (zone, _, _) in (tasks or {}).items() if zone == 'gpu']
    late = [line.split() for line in lines(os.path.join(run, 'runs.log'))]
    late = [parts for parts in late if len(parts) >= 3 and parts[2] == 'gpu' and released is not None
            and float(parts[1]) >= int(released)]
    verdict.note(f'{len(gpu)} gpu tasks in all, {len(late)} of them run at the site once the gpu client came back')


def judge_zones_nodb(run, fact, verdict):
    """Without an upstream database: the zone with nothing staged and no client ends with the others."""
    relay = lines(os.path.join(run, 'relay', 'relay.log'))
    exits(fact, verdict, 'relay', 'server')
    no_trouble(relay, verdict)
    ran = task_runs(run)
    total = int(fact.get('total', '0') or 0)
    verdict.check(f'{total} tasks ran at the site ({len(ran)})', total > 0 and len(ran) == total)
    verdict.check('each ran once', all(len(zones) == 1 for zones in ran.values()))
    verdict.check('none ran in the gpu zone', not any('gpu' in zones for zones in ran.values()))
    default_heard = first(relay, 'no more requests for zone default')
    final = first(relay, 'Upstream server is shutting down - no more upstream requests')
    verdict.check('the default zone heard the shutdown, then the relay ended pulling for every zone',
                  default_heard is not None and final is not None and default_heard <= final)
    verdict.check('the gpu zone held nothing', not any('for zone gpu, which has no site client' in line for line in relay))
    released = [line for line in lines(os.path.join(run, 'launches.log')) if line.startswith('release ')]
    verdict.note(f'gpu launches held for the whole run; {len(released)} released (at the relay`s end)')


def on_base(fact, role):
    """Whether `role` ran from the earlier build's source the drive recorded (exported, or DRIVE_BASE_SRC)."""
    base, source = fact.get('base_src', ''), fact.get(f'source_{role}', '')
    if not base or not source:
        return False
    return os.path.realpath(source).startswith(os.path.realpath(base) + os.sep)


def judge_pair(run, fact, verdict, mode):
    """A relay and a server of different builds: a plain run completes as today."""
    relay = lines(os.path.join(run, 'relay', 'relay.log'))
    role = 'relay' if mode == 'pair-relay' else 'server'
    other = 'server' if role == 'relay' else 'relay'
    verdict.check(f'the {role} ran the earlier build', on_base(fact, role))
    verdict.check(f'the {other} ran this build', bool(fact.get(f'source_{other}')) and not on_base(fact, other))
    exits(fact, verdict, 'relay', 'server')
    no_trouble(relay, verdict)
    completed_once(run, fact, verdict)


def main():
    """Judge one run."""
    if len(sys.argv) != 4 or sys.argv[1] != 'judge' or sys.argv[3] not in MODES:
        print(f'usage: hardening_judge.py judge RUN {"|".join(MODES)}', file=sys.stderr)
        return 64
    run, mode = sys.argv[2], sys.argv[3]
    fact = facts(run)
    verdict = Verdict()
    if mode == 'restart':
        judge_restart(run, fact, verdict)
    elif mode in ('tls-on', 'tls-off'):
        judge_tls(run, fact, verdict, mode)
    elif mode == 'zones':
        judge_zones(run, fact, verdict)
    elif mode == 'zones-nodb':
        judge_zones_nodb(run, fact, verdict)
    else:
        judge_pair(run, fact, verdict, mode)
    status = verdict.status()
    print({0: 'PASS', 1: 'FAIL', 2: 'INCONCLUSIVE'}[status])
    return status


if __name__ == '__main__':
    sys.exit(main())
