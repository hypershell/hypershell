# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge of the relay-retire drives: the laptop drive (retire_drive.sh), from the files of one run,
and the multi-node drive (retire_cluster.sh), from its records.

    python retire_judge.py strand RUN [HOLD]          exit 0 once a task has stranded at relay A (see below)
    python retire_judge.py judge RUN MODE             check a laptop run; exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE
    python retire_judge.py PATH [--require retire]    check multi-node records (see "Cluster mode")

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

Cluster mode. PATH is one record written by retire_collect.py, or a directory searched recursively
for them (`*.json` holding a `scenario` and `transitions`). Each run is printed with every check as
PASS, FAIL or INCONCLUSIVE, then its verdict; the last line is the overall verdict. The checks are the
laptop's (mode retire) over the record: the samples are rebuilt from each task's transitions, A's
running site clients from its launch records, and A's identities are its name. A record's times keep
the clock of the node that wrote them, so where a check compares times from two nodes it allows
SKEW seconds (the relay-topology harness's allowance): the R7 follow starts at the last sample at or
before the mark less SKEW, and R7's bound is three heartbeat intervals plus SKEW (the delay without
the allowance is printed beside it). Exit status: 0 if at least one run was found, every run passed
and every scenario named by --require passed in some run; 1 if a run failed or none was found; 2 if
none failed but a run was inconclusive or a required scenario did not pass.
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

LINE_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) ')
IDENTITY = re.compile(r'Upstream identity (\S+) for zone (\S+) at site (\S+)')
EVICTED = re.compile(r'Evicting client \([^:]*: ([0-9a-f-]{36})\)')
LEFT = re.compile(r'Relay site left \(site: [^,]*, zone: [^)]*\) \([^:]*: ([0-9a-f-]{36})\)')
REQUEUED = re.compile(r'Requeued (\d+) tasks handed back by relay \(([0-9a-f-]{36})')
HANDED = re.compile(r'Handed back (\d+) tasks no site client started \(([0-9a-f-]{36})\)')
LAUNCHING = re.compile(r'Topology \S+ \(zone: [^)]*\) - launching ([1-9]\d*)|applying again, launching ([1-9]\d*)')
SUPERSEDED = 'Superseded at site'
SKEW = 1.0  # seconds: tolerance between node clocks where a cluster record's times from two nodes meet
STRAND_HOLD = 20.0
SAFE = re.compile(r'^[A-Za-z0-9 ._:,+-]*$')
SCENARIOS = ('retire',)


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
    return strand_samples(samples(run), alive(run), identities(run, 'A'), hold)


def strand_samples(found, counts, ids, hold=20.0):
    """`strand` over samples (time, {task: client}), A's running site clients (time, count) and A's
    identities (or its name, in a cluster record)."""
    if not ids or not counts:
        return None
    index, zero_since, on_since = 0, None, {}
    for t, rows in found:
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
    return follow_samples(samples(run), ids, mark)


def follow_samples(found, ids, mark):
    """`follow` over samples (time, {task: client})."""
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


# --- Cluster mode -----------------------------------------------------------------------------------

def record_samples(record):
    """The upstream samples rebuilt from a record's transitions, as (time, {task: owner}): one at every
    transition and one a second between the first and last sample."""
    steps = {int(task): items for task, items in record.get('transitions', {}).items()}
    points = {t for items in steps.values() for t, _ in items}
    sampled = record.get('samples', {})
    if sampled.get('first') is not None and sampled.get('last') is not None:
        t = sampled['first']
        while t < sampled['last']:
            points.add(round(t, 3))
            t += 1.0
        points.add(sampled['last'])
    found = []
    for t in sorted(points):
        rows = {}
        for task, items in steps.items():
            now = None
            seen = False
            for when, owner in items:
                if when <= t:
                    now, seen = owner, True
            if seen and now != 'done':
                rows[task] = now
        found.append((t, rows))
    return found


def record_alive(record, name='A'):
    """A relay's running site clients over a record's run, as (time, count), from its launch records."""
    changes = []
    for item in record.get('launches', []):
        if item.get('relay') != name:
            continue
        began = item.get('t_start') if item.get('t_start') is not None else item.get('t_launch')
        if began is not None:
            changes.append((began, 1))
        if item.get('t_end') is not None:
            changes.append((item['t_end'], -1))
    counts, running = [(0.0, 0)], 0
    for t, step in sorted(changes):
        running += step
        counts.append((t, running))
    return counts


def strings(value):
    """Every string in a record, keys included."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def judge_record(record):
    """Every check of one multi-node run, as (name, result, detail); result is True, False or None
    (inconclusive)."""
    checks = []
    add = lambda name, ok, detail: checks.append((name, None if ok is None else bool(ok), detail))
    tasks, events = record.get('tasks', {}), record.get('events', {})
    settings = record.get('settings', {})
    relays, server = record.get('relays', {}), record.get('server', {})
    a, a2 = relays.get('A', {}), relays.get('A2', {})
    launches = record.get('launches', [])
    heartrate = float(settings.get('heartrate', 0) or 0)
    bound = 3 * heartrate

    add('record.schema', record.get('schema') == 1 and record.get('scenario') in SCENARIOS,
        f'schema {record.get("schema")}, scenario {record.get("scenario")}')
    ended = [name for name in ('deadline', 'stall', 'open') if name in events]
    add('run.deadline', not ended, 'finished within its deadline' if not ended else f'events: {ended}')
    add('run.eviction_longer', settings.get('evict', 0) > settings.get('run_timeout', float('inf')),
        f'eviction {settings.get("evict")} s, run deadline {settings.get("run_timeout")} s')
    total = tasks.get('total', 0)
    add('tasks.complete', total > 0 and tasks.get('rows') == total and tasks.get('completed') == total
        and tasks.get('succeeded') == total,
        f'{tasks.get("succeeded")} of {total} succeeded ({tasks.get("rows")} rows, {tasks.get("completed")} completed)')
    add('tasks.failed', tasks.get('failed') == 0, f'{tasks.get("failed")} failed')
    add('tasks.once', total > 0 and tasks.get('runs') == total and tasks.get('ran') == total
        and not tasks.get('repeated') and not tasks.get('unknown_runs'),
        f'{tasks.get("runs")} runs of {tasks.get("ran")} tasks; repeated {tasks.get("repeated")}, '
        f'{tasks.get("unknown_runs")} unknown')

    add('launch.some', launches and any(item.get('relay') == 'A' for item in launches),
        f'{len(launches)} site clients launched: ' +
        ', '.join(f'{name} {sum(1 for item in launches if item.get("relay") == name)}' for name in ('A', 'A2')))
    nodes = sorted({item.get('node') for item in launches}, key=str)
    add('launch.nodes', launches and all(isinstance(n, int) and n >= 1 for n in nodes),
        f'site clients on nodes {nodes} (node 0 is the server)')
    unnamed = [item for item in launches if not (item.get('word') == item.get('env') == item.get('client') == 'cpu')]
    add('launch.zone_named', launches and not unnamed,
        f'{len(unnamed)} launches whose {{zone}}, HYPERSHELL_ZONE and --zone differ or are not cpu')
    keyed = sum(1 for item in launches if item.get('key'))
    add('launch.key', not keyed, f'{keyed} site clients with the upstream key in their environment')
    unclean = [item for item in launches if item.get('t_end') is None or item.get('status') != 0
               or item.get('killed') or item.get('signal') is not None]
    add('exit.clean', launches and not unclean,
        f'{len(launches) - len(unclean)} site clients exited by themselves with status 0, {len(unclean)} otherwise')
    terminating = {name: relays.get(name, {}).get('terminating') for name in ('A', 'A2')}
    add('relay.no_terminate', all(v == 0 for v in terminating.values()), f'"Terminating site client" lines: {terminating}')

    registered = [entry for entry in server.get('registered', []) if entry.get('relay') == 'A2']
    add('a2.started', a2.get('started') and any(entry.get('site') == 'A' for entry in registered),
        f'A2 registered at site A at {[entry.get("t") for entry in registered]} s')
    retired = [entry for entry in server.get('retired', []) if entry.get('relay') == 'A' and entry.get('site') == 'A']
    add('server.retired', retired and server.get('duplicate_warnings', 0) >= 1,
        f'server marked A retired at {[entry.get("t") for entry in retired]} s; '
        f'{server.get("duplicate_warnings")} duplicate warnings')
    mark = a.get('mark')
    add('retire.mark', a.get('superseded') == 1 and mark is not None,
        f'A logged "Superseded" {a.get("superseded")} times, first at {mark} s')
    add('retire.exit', a.get('self') and a.get('rc') == 0,
        f'A exited by itself: {a.get("self")}, status {a.get("rc")}, at {a.get("exited_at")} s')
    evicted = [entry for entry in server.get('evicted', []) + server.get('reverted', []) if entry.get('relay') == 'A']
    add('retire.no_evict', not evicted, f'{len(evicted)} evictions or orphan reverts of A')
    finals = [entry for entry in a.get('final_heartbeats', []) if entry.get('relay') == 'A']
    left = [entry for entry in server.get('left', []) if entry.get('relay') == 'A']
    count = a.get('identities', 0)
    add('retire.final', count > 0 and len(finals) >= count and len(left) >= count,
        f'A held {count} identities: {len(finals)} final heartbeats, the server saw {len(left)} leave')
    if mark is not None:
        late = [item for item in launches if item.get('relay') == 'A' and (item.get('t_launch') or 0) > mark]
        late += [entry for entry in a.get('launching', []) if (entry.get('t') or 0) > mark]
        add('retire.no_launch_after_mark', not late, f'{len(late)} launches by A after the mark at {mark} s')
    drained = a2.get('drained_at')
    a2_gone = [entry for entry in server.get('evicted', []) if entry.get('relay') == 'A2']
    a2_early = [entry for entry in server.get('left', []) if entry.get('relay') == 'A2'
                and (drained is None or (entry.get('t') or 0) < drained - SKEW)]
    add('a2.stayed', not a2_gone and not a2_early and drained is not None and a2.get('rc') == 0,
        f'A2: {len(a2_gone)} evictions, {len(a2_early)} departures before its drain at {drained} s, '
        f'status {a2.get("rc")}')

    found = record_samples(record)
    sampled = record.get('samples', {})
    add('samples.resolution', sampled.get('count', 0) > 0 and sampled.get('max_gap') is not None
        and sampled['max_gap'] <= max(heartrate, 1.0),
        f'{sampled.get("count")} samples, largest gap {sampled.get("max_gap")} s')
    stranded = strand_samples(found, record_alive(record), {'A'}, STRAND_HOLD)
    add('retire.no_strand', stranded is None,
        'no task stayed on A > 20 s with A at no site client' if stranded is None
        else f'task {stranded[0]} stranded at A by {stranded[1]} s')
    if mark is None:
        add('R7.mark_read', False, 'A never read the mark')
        return checks
    outcome = follow_samples(found, {'A'}, mark - SKEW)
    started = sorted(task for task, result in outcome.items() if result and result[0] == 'started')
    unstarted = {task: result for task, result in outcome.items() if not (result and result[0] == 'started')}
    handed = sum(entry.get('count', 0) for entry in a.get('handed_back', []))
    requeued = [entry for entry in server.get('requeued', []) if entry.get('relay') == 'A']
    missing = sorted(task for task, result in unstarted.items() if result is None)
    add('R7.available', not missing,
        f'A held {len(outcome)} tasks from the mark ({len(started)} started at A, {len(unstarted)} unstarted); '
        f'{len(missing)} unstarted never seen available; A handed back {handed}, the server requeued '
        + (', '.join(f'{entry.get("count")} at +{entry["t"] - mark:.3f} s' for entry in requeued) or 'none'))
    if unstarted and not missing:
        delay = max(result[1] for result in unstarted.values()) - mark
        add('R7.bound', delay <= bound + SKEW,
            f'last unstarted task available at +{delay:.3f} s of the mark; bound {bound:g} s '
            f'(3 x heartrate {heartrate:g} s) + {SKEW:g} s skew allowance')
    if requeued:
        first_t = min(entry['t'] for entry in requeued)
        add('R7.requeue_logged', first_t - mark <= bound + SKEW,
            f'requeue logged at +{first_t - mark:.3f} s of the mark (bound {bound:g} s + {SKEW:g} s)')
    if not unstarted and handed == 0:
        add('R7.non_vacuous', None, 'A held nothing unstarted at the mark and handed back nothing')
    else:
        add('R7.non_vacuous', True, f'A held {len(unstarted)} unstarted at the mark, handed back {handed}')

    scrub = record.get('scrub', {})
    unsafe = [text for text in strings(record) if not SAFE.match(text)]
    add('record.scrubbed', scrub and not any(scrub.values()) and not unsafe,
        f'found: {scrub}; {len(unsafe)} strings with a path or other unsafe character')
    return checks


def records(path):
    """The multi-node records under a path."""
    files = [path] if path.is_file() else sorted(path.rglob('*.json'))
    for file in files:
        try:
            value = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and 'scenario' in value and 'transitions' in value:
            yield file, value


def cluster(argv):
    """Judge every multi-node record found; print each check and the verdict."""
    parser = argparse.ArgumentParser(prog='retire_judge.py', description='Judge multi-node relay-retire records.')
    parser.add_argument('path', type=Path)
    parser.add_argument('--require', default='', help='scenarios that must have passed (comma separated)')
    args = parser.parse_args(argv)
    required = [name for name in args.require.split(',') if name]
    unknown = [name for name in required if name not in SCENARIOS]
    if unknown:
        print(f'retire_judge: unknown scenarios: {", ".join(unknown)}', file=sys.stderr)
        return 1
    passed, failed, unsure, found = set(), 0, 0, 0
    for file, record in records(args.path):
        found += 1
        checks = judge_record(record)
        bad = any(result is False for _, result, _ in checks)
        unclear = not bad and any(result is None for _, result, _ in checks)
        verdict = 'FAIL' if bad else 'INCONCLUSIVE' if unclear else 'PASS'
        print(f'{record.get("run_id", file.stem)} ({record.get("scenario")}): {verdict}')
        for name, result, detail in checks:
            print(f'  {"INCONCLUSIVE" if result is None else "PASS" if result else "FAIL"} {name}: {detail}')
        failed += bad
        unsure += unclear
        if verdict == 'PASS':
            passed.add(record.get('scenario'))
    missing = [name for name in required if name not in passed]
    status = 1 if failed or not found else 2 if unsure or missing else 0
    detail = (f'{found} runs, {failed} failed, {unsure} inconclusive'
              + (f'; required but not passed: {", ".join(missing)}' if missing else ''))
    print(f'retire_judge: {["PASS", "FAIL", "INCONCLUSIVE"][status]} ({detail})')
    return status


def main():
    """Strand watch, laptop judgment, or the multi-node records."""
    if len(sys.argv) < 3 or sys.argv[1] not in ('strand', 'judge'):
        return cluster(sys.argv[1:])
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
