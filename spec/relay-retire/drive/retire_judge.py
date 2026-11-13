# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge of the relay-retire laptop drive (retire_drive.sh), from the files of one run.

    python retire_judge.py strand RUN [HOLD]   exit 0 once a task has stranded at relay A (see below)
    python retire_judge.py judge RUN MODE      check the run; exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE

RUN is the drive's scratch directory. It holds the upstream server's log (server.log) and each
relay's (relay-NAME/relay.log), all in the system log style with UTC times; the launch log
(launches.log, one line per site client launched, see retire_shim.sh); the upstream task samples
(held.jsonl, see retire_sampler.py); the site clients alive at each relay (alive.log, one line
`EPOCH A A2` a second); one line per task run (runs.log, `TASK_ID EPOCH`); and the drive's own facts
(facts.txt, `key=value` lines).

A task has stranded at relay A when it has been assigned upstream to one of A's identities, without
completing, for more than HOLD seconds (default 20) while A ran no site client: it waits for A's
eviction. That is the defect this cycle fixes, reproduced on the earlier build (mode retire-red).

The R7 bound (mode retire): A's first "Superseded" line is the mark. The tasks held under A's
identities from the last sample before the mark onwards are followed through the samples: one that
leaves the incomplete set while still A's was started there (run by a site client of A, which is
allowed to finish it); every other one must be seen unassigned or assigned to another client within
three heartbeat intervals of the mark. A run in which A held nothing unstarted and handed back
nothing proves nothing, and is INCONCLUSIVE.
"""

import os
import re
import sys
import json
from datetime import datetime, timezone

LINE_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) ')
IDENTITY = re.compile(r'Upstream identity (\S+) for zone (\S+) at site (\S+)')
EVICTED = re.compile(r'Evicting client \([^:]*: ([0-9a-f-]{36})\)')
LEFT = re.compile(r'Relay site left \(site: [^,]*, zone: [^)]*\) \([^:]*: ([0-9a-f-]{36})\)')
REQUEUED = re.compile(r'Requeued (\d+) tasks handed back by relay \(([0-9a-f-]{36})')
HANDED = re.compile(r'Handed back (\d+) tasks no site client started \(([0-9a-f-]{36})\)')
LAUNCHING = re.compile(r'Topology \S+ \(zone: [^)]*\) - launching ([1-9]\d*)|applying again, launching ([1-9]\d*)')
SUPERSEDED = 'Superseded at site'


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


def relay_lines(run, name):
    """The log lines of relay `name`."""
    return lines(os.path.join(run, f'relay-{name}', 'relay.log'))


def first(path, needle):
    """Time of the first line of `path` containing `needle` (a regex or text), or None."""
    for line in lines(path):
        if (needle.search(line) if hasattr(needle, 'search') else needle in line):
            return stamp(line)
    return None


def facts(run):
    """The drive's key=value facts."""
    found = {}
    for line in lines(os.path.join(run, 'facts.txt')):
        key, _, value = line.partition('=')
        found[key.strip()] = value.strip()
    return found


def identities(run, name):
    """The upstream identities relay `name` logged."""
    return {match.group(1) for line in lines(os.path.join(run, f'relay-{name}', 'relay.log'))
            for match in [IDENTITY.search(line)] if match}


def samples(run):
    """The upstream task samples, as (time, {task id: client id}), in order."""
    found = []
    for line in lines(os.path.join(run, 'held.jsonl')):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        found.append((data['t'], {task: client for task, client in data['rows']}))
    return found


def alive(run):
    """A's running site clients over time, as (time, count), in order."""
    found = []
    for line in lines(os.path.join(run, 'alive.log')):
        parts = line.split()
        if len(parts) >= 2:
            found.append((float(parts[0]), int(parts[1])))
    return found


def strand(run, hold=20.0):
    """The first (task, time) at which a task has stayed on A's identities for `hold` seconds with A at no client."""
    ids = identities(run, 'A')
    counts = alive(run)
    if not ids or not counts:
        return None
    index, zero_since, on_since = 0, None, {}
    for t, rows in samples(run):
        while index + 1 < len(counts) and counts[index + 1][0] <= t:
            index += 1
        current = counts[index][1] if counts[index][0] <= t else None
        zero_since = (zero_since or t) if current == 0 else None
        on_since = {task: on_since.get(task, t) for task, client in rows.items() if client in ids}
        if zero_since is None:
            continue
        for task, since in on_since.items():
            if t - max(since, zero_since) > hold:
                return task, t
    return None


def follow(run, ids, mark):
    """The tasks held under `ids` from the last sample before `mark`, each mapped to ('started', t),
    ('available', t) or None (never seen leaving)."""
    found = samples(run)
    start = 0
    for index, (t, _) in enumerate(found):
        if t <= mark:
            start = index
    held = {}
    for t, rows in found[start:]:
        for task, client in rows.items():
            if client in ids and task not in held:
                held[task] = None
    outcome = {}
    for task in held:
        last_on = False
        outcome[task] = None
        for t, rows in found[start:]:
            if task not in rows:
                if last_on:
                    outcome[task] = ('started', t)
                    break
                continue
            if rows[task] in ids:
                last_on = True
                continue
            if last_on:
                outcome[task] = ('available', t)
                break
    return outcome


class Verdict:
    """Checks and notes, in order; PASS only if every check passed and none was inconclusive."""

    def __init__(self):
        self.rows, self.failed, self.inconclusive = [], False, False

    def check(self, name, ok):
        self.rows.append(f'{"PASS" if ok else "FAIL"} {name}')
        self.failed |= not ok

    def unsure(self, name):
        self.rows.append(f'INCONCLUSIVE {name}')
        self.inconclusive = True

    def note(self, text):
        self.rows.append(f'  {text}')

    def status(self):
        return 1 if self.failed else 2 if self.inconclusive else 0


def common(run, fact, verdict, once=True):
    """Every task completed and none failed; with `once`, each ran exactly once; no relay ended a client."""
    total = int(fact.get('total', -1))
    verdict.check(f'every task completed ({fact.get("completed")} of {total})', fact.get('completed') == str(total))
    verdict.check('no task failed', fact.get('failed') == '0')
    runs = [line.split()[0] for line in lines(os.path.join(run, 'runs.log')) if line.strip()]
    repeated = sorted({task for task in runs if runs.count(task) > 1})
    if once:
        verdict.check(f'every task ran exactly once ({len(runs)} runs of {len(set(runs))} tasks)',
                      len(runs) == total and not repeated)
    else:
        verdict.note(f'{len(runs)} runs of {len(set(runs))} tasks')
    names = [name for name in ('A', 'A2', 'B') if os.path.isdir(os.path.join(run, f'relay-{name}'))]
    ended = [name for name in names
             if any('Terminating site client' in line for line in relay_lines(run, name))]
    verdict.check('no relay terminated a site client', not ended)
    for name in names:
        verdict.note(f'{name}: hypershell from {fact.get(f"source_{name}", "?")}')
    verdict.note(f'server: hypershell from {fact.get("source_server", "?")}')


def evictions(run):
    """Identities the server evicted, with when."""
    return [(stamp(line), match.group(1)) for line in lines(os.path.join(run, 'server.log'))
            for match in [EVICTED.search(line)] if match]


def judge_retire(run, fact, verdict):
    """The RV5 scenario on this build: A retires, hands back, finishes, exits 0 by itself; R7 timed."""
    a_ids, a2_ids = identities(run, 'A'), identities(run, 'A2')
    a_log, server_log = os.path.join(run, 'relay-A', 'relay.log'), os.path.join(run, 'server.log')
    heartrate = float(fact.get('heartrate', 1))
    common(run, fact, verdict)
    verdict.check('A2 started at site A', bool(a2_ids))
    mark = first(a_log, SUPERSEDED)
    marks = sum(SUPERSEDED in line for line in lines(a_log))
    verdict.check(f'A logged once that it was superseded ({marks})', marks == 1 and mark is not None)
    verdict.check('A exited by itself', fact.get('a_self') == '1')
    verdict.check(f'A exited with status 0 ({fact.get("a_exit")})', fact.get('a_exit') == '0')
    evicted = [identity for _, identity in evictions(run) if identity in a_ids]
    verdict.check("the server evicted none of A's identities", not evicted)
    final = any('Upstream heartbeat - final' in line for line in lines(a_log))
    left = {match.group(1) for line in lines(server_log) for match in [LEFT.search(line)] if match}
    verdict.check("A sent its final heartbeat, and the server saw each of A's identities leave",
                  final and a_ids <= left)
    if mark is not None:
        late = [line for line in lines(os.path.join(run, 'launches.log'))
                if ' site=A ' in line and int(line.split()[1].split('=')[1]) > mark]
        late += [line for line in lines(a_log)
                 if LAUNCHING.search(line) and (stamp(line) or 0) > mark]
        verdict.check('A launched no site client after the mark', not late)
    a2_gone = [identity for _, identity in evictions(run) if identity in a2_ids]
    a2_left = [line for line in lines(server_log)
               if (match := LEFT.search(line)) and match.group(1) in a2_ids
               and (stamp(line) or 0) < float(fact.get('drained_at', 0))]
    verdict.check('A2 stayed connected until drained', fact.get('a2_running') == '1' and not a2_gone and not a2_left)
    found = strand(run)
    verdict.check('no task stranded at A' + (f' ({found[0]} at {found[1]:.1f})' if found else ''), found is None)
    if mark is None:
        verdict.check('R7: the mark was read', False)
        return
    outcome = follow(run, a_ids, mark)
    started = sorted(task for task, result in outcome.items() if result and result[0] == 'started')
    unstarted = {task: result for task, result in outcome.items() if not (result and result[0] == 'started')}
    handed = sum(int(match.group(1)) for line in lines(a_log) for match in [HANDED.search(line)] if match)
    requeued = [(stamp(line), int(match.group(1))) for line in lines(server_log)
                for match in [REQUEUED.search(line)] if match and match.group(2) in a_ids]
    verdict.note(f'mark at {mark:.3f}; A held {len(outcome)} tasks under its identities from then: '
                 f'{len(started)} started at A, {len(unstarted)} unstarted')
    verdict.note(f'A handed back {handed}; the server requeued ' +
                 (', '.join(f'{count} at +{t - mark:.3f}s' for t, count in requeued) or 'none'))
    bound = 3 * heartrate
    missing = sorted(task for task, result in unstarted.items() if result is None)
    verdict.check(f'R7: every unstarted task of A was seen available upstream ({len(missing)} never)', not missing)
    if unstarted and not missing:
        delay = max(result[1] for result in unstarted.values()) - mark
        verdict.note(f'R7: last unstarted task available at +{delay:.3f}s (bound {bound:g}s = 3 x heartrate)')
        verdict.check(f'R7: unstarted tasks available within {bound:g}s of the mark (+{delay:.3f}s)', delay <= bound)
    if requeued:
        verdict.check(f'R7 (log cross-check): requeue logged within {bound:g}s of the mark '
                      f'(+{requeued[0][0] - mark:.3f}s)', requeued[0][0] - mark <= bound)
    if not unstarted and handed == 0:
        verdict.unsure('non-vacuity: A held nothing unstarted at the mark and handed back nothing')
    else:
        verdict.check(f'non-vacuity: A held unstarted tasks at the mark ({len(unstarted)}) or handed back ({handed})',
                      True)


def judge_red(run, fact, verdict):
    """The RV5 scenario on the earlier build: passes only when the stranding is reproduced."""
    found = strand(run)
    verdict.note(f'roles: server {fact.get("source_server")}, A {fact.get("source_A")}, A2 {fact.get("source_A2")}')
    verdict.check('A2 started at site A', bool(identities(run, 'A2')))
    verdict.check('red reproduced: a task stayed on A\'s identity > 20 s with A at zero clients' +
                  (f' ({found[0]}, at {found[1]:.1f})' if found else ''), found is not None)
    verdict.check('A did not retire (no "Superseded" line)',
                  not any(SUPERSEDED in line for line in lines(os.path.join(run, 'relay-A', 'relay.log'))))


def judge_pair(run, fact, verdict, mode):
    """One side does not know the mark: behaviour as today (A strands until stopped and evicted)."""
    a_ids = identities(run, 'A')
    a_log, server_log = os.path.join(run, 'relay-A', 'relay.log'), os.path.join(run, 'server.log')
    common(run, fact, verdict)
    verdict.check('A2 started at site A', bool(identities(run, 'A2')))
    for name in ('A', 'A2'):
        errors = [line for line in relay_lines(run, name) if 'Topology not read' in line]
        verdict.check(f'no topology read error at {name}', not errors)
    verdict.check('A did not retire (no "Superseded" line, nothing handed back)',
                  not any(SUPERSEDED in line or 'Handed back' in line or 'Could not hand back' in line
                          for line in lines(a_log)))
    verdict.check('A did not exit by itself (as today: it stays until stopped)', fact.get('a_self') == '0')
    found = strand(run)
    verdict.check('as today: a task stayed on A\'s identity > 20 s with A at zero clients' +
                  (f' ({found[0]})' if found else ''), found is not None)
    stopped = float(fact.get('a_stopped_at', 0))
    evicted = [t for t, identity in evictions(run) if identity in a_ids and (t or 0) >= stopped]
    verdict.check("A's identities were evicted after A was stopped (its tasks put back then)", bool(evicted))
    if evicted:
        verdict.note(f'A evicted {min(evicted) - stopped:.1f}s after it was stopped')
    requeued = [line for line in lines(server_log) if REQUEUED.search(line)]
    verdict.check('no hand-back reached the server', not requeued)
    noted = [line for line in lines(server_log) if re.search(r'Relay \S+ on \S+ retired at site A', line)]
    if mode == 'pair-relay':
        verdict.check('the server (this build) marked A retired', bool(noted))
    else:
        verdict.check('the server (earlier build) marked nothing', not noted)


def judge_idle(run, fact, verdict):
    """One relay per site stays connected at zero clients, and drains cleanly."""
    server_log = os.path.join(run, 'server.log')
    common(run, fact, verdict)
    verdict.check('every site client exited by itself', fact.get('clients_left') == '1')
    verdict.check('both relays still ran with no client after the work', fact.get('relays_running') == '1')
    gone = [line for line in lines(server_log) if (EVICTED.search(line) or LEFT.search(line))
            and (stamp(line) or 0) < float(fact.get('drained_at', 0))]
    verdict.check('no relay identity left or was evicted before the drain', not gone)
    retired = [name for name in ('A', 'B')
               if any(SUPERSEDED in line for line in lines(os.path.join(run, f'relay-{name}', 'relay.log')))]
    verdict.check('no relay retired', not retired)
    verdict.check('every relay drained and exited 0', fact.get('drained') == '0')


def main():
    """Strand watch or judgment."""
    command, run = sys.argv[1], sys.argv[2]
    if command == 'strand':
        found = strand(run, float(sys.argv[3]) if len(sys.argv) > 3 else 20.0)
        if found:
            print(f'stranded {found[0]} at {found[1]:.1f}')
        return 0 if found else 1
    mode = sys.argv[3]
    fact, verdict = facts(run), Verdict()
    if mode == 'retire':
        judge_retire(run, fact, verdict)
    elif mode == 'retire-red':
        judge_red(run, fact, verdict)
    elif mode in ('pair-relay', 'pair-server'):
        judge_pair(run, fact, verdict, mode)
    elif mode == 'idle':
        judge_idle(run, fact, verdict)
    else:
        print(f'unknown mode {mode}', file=sys.stderr)
        return 2
    print('\n'.join(verdict.rows))
    print(['PASS', 'FAIL', 'INCONCLUSIVE'][verdict.status()])
    return verdict.status()


if __name__ == '__main__':
    sys.exit(main())
