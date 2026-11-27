# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay-hardening tail judge (`spec/relay-hardening/drive/tail_judge.py`) on synthetic records.

A record is one run of `tail_run.py`: the run's label and commit, its sizes, whether it completed,
and, for the direct and the relay runs, the tail (seconds from the last task's completion to the
exit of the process that ends the run); for the relay run also the nine segments of its tail. A
run inside every segment's design bound and with a tail within 5 s has no flags; each flag below
names one thing a run can show. A directory is one budget; the recorded outcome (`OUTCOME.md`, in
the directory that holds the record directories) is checked against the records.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict

# Standard libs
import json
import importlib.util
from pathlib import Path

# External libs
from pytest import mark, fixture


JUDGE = Path(__file__).parent.parent / 'spec' / 'relay-hardening' / 'drive' / 'tail_judge.py'
R8 = {'tasks': 2000, 'sleep': '0.2', 'clients': 4, 'threads': 8}
QUICK = {'s1': 1.0, 's2': 1.0, 's3': 0.1, 's4': 0.2, 's5': 1.0, 's6': 0.1, 's7': 1.0, 's8': 0.5, 's9': 0.5}


@fixture(scope='module')
def judge():
    """The judge module, loaded from its path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location('tail_judge', JUDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(run_id: str = 'laptop-01', label: str = 'diagnostic', tail: float = 4.4, complete: bool = True,
           sizes: Dict[str, Any] = None, **segments: float) -> Dict[str, Any]:
    """A run of tail_run.py, by default complete, at the R8 size, every segment within its bound."""
    return {'kind': 'relay-hardening-tail', 'run_id': run_id, 'label': label, 'commit': 'abc1234',
            'sizes': dict(R8 if sizes is None else sizes), 'complete': complete,
            'direct': {'tail': 1.5, 'status': 0, 'completed': 2000},
            'relay': {'tail': tail, 'status': 0, 'completed': 2000, 'segments': {**QUICK, **segments}}}


def write(directory: Path, *records: Dict[str, Any]) -> Path:
    """Write records as tail_run.py does, one JSON file per run."""
    directory.mkdir(parents=True, exist_ok=True)
    for item in records:
        (directory / f'{item["run_id"]}.json').write_text(json.dumps(item))
    return directory


@mark.unit
def test_a_run_inside_the_designed_chain_has_no_flags(judge) -> None:
    """A complete R8 run with every segment within its bound and a tail within 5 s is flagged nothing."""
    assert judge.flags(record()) == []
    assert judge.line(record()).endswith('ok')


@mark.unit
@mark.parametrize('change, expected', [
    (dict(tail=6.2), ['TAIL>5']),
    (dict(tail=6.2, s1=5.0, s2=5.0, s5=1.9, s7=2.9), ['TAIL>5']),  # The designed chain at its bounds: no anomaly.
    (dict(s4=12.0), ['s4-OVER']),
    (dict(tail=61.0, s8=55.0), ['TAIL>5', 's8-OVER']),
    (dict(s3=None), ['MISSING']),
    (dict(complete=False), ['INCOMPLETE']),
    (dict(sizes={**R8, 'tasks': 200}), ['SIZE']),
], ids=['tail', 'designed-chain', 'segment-over', 'long-tail', 'missing', 'incomplete', 'size'])
def test_flags_name_what_a_run_shows(judge, change: Dict[str, Any], expected: list) -> None:
    """Each flag names one thing: a tail over 5 s, a segment over its bound, a run not finished or not at R8 size."""
    assert judge.flags(record(**change)) == expected


@mark.unit
def test_a_segment_within_its_slack_is_not_over(judge) -> None:
    """A segment over its design bound by less than the slack (logging and scheduling) is not flagged."""
    assert judge.flags(record(s4=judge.BOUNDS['s4'] + judge.SLACK / 2)) == []
    assert judge.flags(record(s4=judge.BOUNDS['s4'] + judge.SLACK * 2)) == ['s4-OVER']


@mark.unit
def test_budget_counts_each_directory(judge, tmp_path: Path) -> None:
    """At most N records per directory, each a complete run at the R8 size."""
    laptop = write(tmp_path / 'laptop', *(record(f'laptop-{n:02d}') for n in range(1, 11)))
    assert judge.main(['--budget', '10', str(laptop)]) == 0
    write(laptop, record('laptop-11'))
    assert judge.main(['--budget', '10', str(laptop)]) == 1


@mark.unit
@mark.parametrize('broken', [record('laptop-02', complete=False), record('laptop-02', sizes={**R8, 'tasks': 200})],
                  ids=['incomplete', 'not-r8'])
def test_budget_refuses_a_run_that_does_not_count(judge, tmp_path: Path, broken: Dict[str, Any]) -> None:
    """A run that did not finish, or not at the R8 size, does not belong among the budget runs."""
    laptop = write(tmp_path / 'laptop', record('laptop-01'), broken)
    assert judge.main(['--budget', '10', str(laptop)]) == 1


@mark.unit
def test_no_record_fails(judge, tmp_path: Path) -> None:
    """A directory with no record proves nothing."""
    (tmp_path / 'laptop').mkdir()
    assert judge.main(['--budget', '10', str(tmp_path / 'laptop')]) == 1


@mark.unit
def test_require_none_over_by_label(judge, tmp_path: Path) -> None:
    """The tail limit applies to the runs of the label given, or to every run."""
    laptop = write(tmp_path / 'laptop', record('laptop-01', tail=7.5),
                   record('laptop-06', label='post-fix', tail=1.9), record('laptop-07', label='post-fix', tail=2.4))
    assert judge.main(['--require-none-over', '5', '--label', 'post-fix', str(laptop)]) == 0
    assert judge.main(['--require-none-over', '5', str(laptop)]) == 1
    write(laptop, record('laptop-08', label='post-fix', tail=5.3))
    assert judge.main(['--require-none-over', '5', '--label', 'post-fix', str(laptop)]) == 1


def evidence(tmp_path: Path, outcome: str) -> Path:
    """A repository with an evidence directory holding laptop records and OUTCOME.md."""
    (tmp_path / '.git').mkdir(parents=True)
    root = tmp_path / 'spec' / 'relay-hardening' / 'evidence'
    write(root / 'laptop', record('laptop-01', tail=7.5), record('laptop-06', label='post-fix', tail=1.9))
    (root / 'OUTCOME.md').write_text(outcome)
    return root


@mark.unit
def test_outcome_a_needs_post_fix_runs_within_the_limit(judge, tmp_path: Path) -> None:
    """Outcome (a) holds only with post-fix runs, each a complete R8 run with no tail over 5 s."""
    root = evidence(tmp_path, '# Outcome\n\noutcome: a\npost-fix-label: post-fix\n')
    assert judge.main(['--outcome', str(root / 'laptop')]) == 0
    write(root / 'laptop', record('laptop-07', label='post-fix', tail=5.6))
    assert judge.main(['--outcome', str(root / 'laptop')]) == 1
    root = evidence(tmp_path / 'other', 'outcome: a\npost-fix-label: fixed\n')
    assert judge.main(['--outcome', str(root / 'laptop')]) == 1


@mark.unit
def test_outcome_b_needs_a_narrowed_seed(judge, tmp_path: Path) -> None:
    """Outcome (b) holds only when it names a seed in the repository with status unshaped."""
    root = evidence(tmp_path, 'outcome: b\nseed: issues/relay-tail.md\n')
    assert judge.main(['--outcome', str(root / 'laptop')]) == 1
    (tmp_path / 'issues').mkdir()
    (tmp_path / 'issues' / 'relay-tail.md').write_text('---\nstatus: unshaped\n---\n# The tail\n')
    assert judge.main(['--outcome', str(root / 'laptop')]) == 0


@mark.unit
def test_outcome_missing_fails(judge, tmp_path: Path) -> None:
    """No OUTCOME.md, or one naming no outcome, fails the check."""
    root = evidence(tmp_path, 'nothing decided yet\n')
    assert judge.main(['--outcome', str(root / 'laptop')]) == 1
    (root / 'OUTCOME.md').unlink()
    assert judge.main(['--outcome', str(root / 'laptop')]) == 1
