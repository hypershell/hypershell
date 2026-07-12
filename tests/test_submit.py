# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test submit operations."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict

# Standard libs
import sys
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile, create_taskfile_echo, assert_output, UUID_PATTERN
from hypershell.submit import SubmitApp
from hypershell.data.model import Task


@mark.integration
def test_submit_usage() -> None:
    """The 'hs submit' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hs', 'submit', ])
    assert rc == exit_status.usage
    assert stdout == SubmitApp.interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_submit_help(opt: str) -> None:
    """The 'hs submit' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'submit', opt, ])
    assert rc == exit_status.success
    assert stdout == SubmitApp.interface.help_text
    assert stderr == ''

@mark.integration
def test_submit_mixed_inputs() -> None:
    """Cannot use positional arguments with -f option."""
    # NOTE: added -g0 to suppress INFO message from auto-selected group
    assert main_lines(['hs', 'submit', 'a', 'b', '-f', 'some_file', '-g0']) == (
        exit_status.bad_argument, NO_OUTPUT, [
            'CRITICAL [hypershell] Cannot specify both -f/--task-file and positional arguments',
        ]
    )


@mark.integration
@mark.parametrize('opt', ['--foo', '--bar', '--baz=7'])
def test_submit_bad_options(opt: str) -> None:
    """Error on invalid options."""
    assert main_lines(['hs', 'submit', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] unrecognized arguments: {opt}',
        ]
    )


@mark.integration
def test_submit_missing_file(temp_site: Path) -> None:
    """Error on missing file."""
    missing_file = str(temp_site / 'missing_file.txt')
    rc, stdout, stderr = main(['hs', 'submit', '-f', missing_file])
    assert rc == exit_status.runtime_error
    assert stdout == ''
    assert_output(r'CRITICAL .* FileNotFoundError: .* No such file .*', stderr, 1)


OPTION_PAIR: Final[Dict[str, str]] = {
    '--template': '--template',
    '-b': '-b/--bundlesize', '--bundlesize': '-b/--bundlesize',
    '-w': '-w/--bundlewait', '--bundlewait': '-w/--bundlewait',
    '-t': '-t/--tag', '--tag': '-t/--tag',
}


OPTION_COUNT: Final[Dict[str, str]] = {
    **{k: 'one' for k in OPTION_PAIR},
    **{k: 'at least one' for k in {'-t', '--tag'}}
}


@mark.integration
@mark.parametrize('opt', list(OPTION_PAIR))
def test_submit_missing_option_value(opt: str) -> None:
    """Error on missing option value."""
    assert main_lines(['hs', 'submit', 'echo', 'hello world', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] argument {OPTION_PAIR[opt]}: expected {OPTION_COUNT[opt]} argument',
        ]
    )


@mark.integration
def test_submit_single(temp_site: Path) -> None:
    """Submit single task to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit single input task
    rc, stdout, stderr = main(['hs', 'submit', 'echo', 'hello world'])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted single task \(explicit\)$', stderr, 1)
    assert_output(r'INFO .* Submitted task \((?P<uuid>.*)\)$', stderr, 1, groups={'uuid': UUID_PATTERN})

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['1', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain']) == (
        exit_status.success, ['echo "hello world"', ], NO_OUTPUT
    )


@mark.integration
def test_submit_basic(temp_site: Path) -> None:
    """Submit collection of tasks from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit `seq 4` inputs
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'submit', taskfile])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(implicit - not executable\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 1 tasks$', stderr, 4)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 4 tasks$', stderr, 1)

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['4', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, ['echo 0', 'echo 1', 'echo 2', 'echo 3'], NO_OUTPUT
    )


@mark.integration
def test_submit_explicit(temp_site: Path) -> None:
    """Submit collection of tasks from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit `seq 4` inputs
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'submit', '-f', taskfile])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(explicit\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 1 tasks$', stderr, 4)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 4 tasks$', stderr, 1)

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['4', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, ['echo 0', 'echo 1', 'echo 2', 'echo 3'], NO_OUTPUT
    )


@mark.integration
def test_submit_bundled(temp_site: Path) -> None:
    """Submit larger collection with bundling from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit `seq 100` inputs
    taskfile = create_taskfile_echo(temp_site, count=100)
    rc, stdout, stderr = main(['hs', 'submit', taskfile, '-b', '10'])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(implicit - not executable\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 10 tasks$', stderr, 10)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 100 tasks$', stderr, 1)

    # Now there are 100 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['100', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, [f'echo {n}' for n in range(100)], NO_OUTPUT
    )


@mark.unit
def test_inline_memory_tag_parses_units_to_bytes() -> None:
    """An inline ``memory:<size>`` resource tag is parsed to integer bytes — like the ``--memory``
    flag (parse_bytes) — not stored as a raw string.

    smart_coerce (used for inline tag values) leaves unit-bearing strings like ``2GB`` untouched, so
    without explicit parsing ``Task.memory`` would be a string and client-side resource accounting
    (``memory_total < task.memory``, an int vs str comparison) crashes.
    """
    # The reported form: an inline HYPERSHELL comment carrying a unit suffix.
    task = Task.new(args='echo AAA #HYPERSHELL: cores:1 memory:2GB timeout:10')
    assert task.memory == 2147483648            # 2 * 1024**3
    assert task.cores == 1 and task.timeout == 10
    # The tag= kwarg form resolves identically.
    assert Task.new(args='echo', tag={'memory': '512MB'}).memory == 536870912
    # Unit-less, already-integer, and absent values are unaffected (no regression).
    assert Task.new(args='echo', tag={'memory': '2000'}).memory == 2000
    assert Task.new(args='echo', tag={'memory': 4096}).memory == 4096
    assert Task.new(args='echo').memory is None


# Small script run in a subprocess so the engine binds to the temp-site environment.
_STAMP_QUERY: Final[str] = """
from hypershell.data.model import Task, Source, DIRECT_SOURCE_ID, STDIN_SOURCE_ID
tasks = Task.query().all()
named = [s for s in Source.query().all() if s.path not in ('<direct>', '<stdin>')]
assert len(tasks) == 3, f'tasks={len(tasks)}'
assert len(named) == 1, f'named sources={len(named)}'
src = named[0]
assert src.task_count == 3, f'recorded count={src.task_count}'
assert src.fingerprint and len(src.fingerprint) == 32, f'md5={src.fingerprint!r}'
assert src.path.startswith('/'), f'path not absolute: {src.path}'
assert all(t.source == src.id for t in tasks), [t.source for t in tasks]
assert all(t.fingerprint for t in tasks), 'unstamped fingerprint'
assert len({t.fingerprint for t in tasks}) == 3, 'expected distinct fingerprints'
print('OK', src.task_count)
"""


@mark.integration
def test_source_stamp_named_file(temp_site: Path) -> None:
    """A named-file submit records one Source row and stamps every task (R1, R3, R4)."""

    # Real tasks: echo a/b/c. Non-tasks (excluded from the count): blank, comment-only,
    # and an inline-tag-only line (sets a global tag, produces no task).
    taskfile = create_taskfile(temp_site, [
        'echo a',
        '',
        '# just a comment',
        '# HYPERSHELL: phase:1',
        'echo b',
        'echo c',
    ])

    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile)])
    assert rc == exit_status.success, stderr
    # R18 upfront-ingest log: found N + md5, and the excluded lines are not counted.
    assert_output(r'INFO .* Found 3 tasks in .* \(md5=[0-9a-f]{32}\)$', stderr, 1)
    assert_output(r'INFO .* Submitted 3 tasks$', stderr, 1)

    rc, stdout, stderr = main([sys.executable, '-c', _STAMP_QUERY])
    assert rc == 0, stderr
    assert stdout == 'OK 3'


@mark.integration
def test_source_stamp_reserved_direct_and_stdin(temp_site: Path) -> None:
    """Single-command and stdin submissions stamp the reserved <direct>/<stdin> rows (R3)."""
    import os
    from subprocess import run, PIPE

    # <direct>: a single positional command needs no stdin.
    rc, _, stderr = main(['hs', 'submit', 'echo', 'solo'])
    assert rc == exit_status.success, stderr

    # <stdin>: pipe two commands in; `main` does not feed stdin, so drive it directly.
    proc = run(['hs', 'submit', '-f', '-'], input=b'echo pipe1\necho pipe2\n',
               stdout=PIPE, stderr=PIPE, env=os.environ)
    assert proc.returncode == exit_status.success, proc.stderr.decode()

    query = """
from hypershell.data.model import Task, Source, DIRECT_SOURCE_ID, STDIN_SOURCE_ID
by_source = {}
for t in Task.query().all():
    by_source.setdefault(t.source, []).append(t.args)
assert sorted(by_source.get(DIRECT_SOURCE_ID, [])) == ['echo solo'], by_source
assert sorted(by_source.get(STDIN_SOURCE_ID, [])) == ['echo pipe1', 'echo pipe2'], by_source
reserved = Source.query().filter(Source.id.in_([DIRECT_SOURCE_ID, STDIN_SOURCE_ID])).all()
assert {s.path for s in reserved} == {'<direct>', '<stdin>'}, reserved
print('OK')
"""
    rc, stdout, stderr = main([sys.executable, '-c', query])
    assert rc == 0, stderr
    assert stdout == 'OK'


@mark.integration
def test_source_stamp_non_seekable_input_streams_all(temp_site: Path) -> None:
    """A non-seekable named input (piped /dev/stdin) streams every task instead of being
    drained by the upfront read; it is stamped with the reserved <stdin> source (R3, R4)."""
    import os
    from subprocess import run, PIPE
    if not os.path.exists('/dev/stdin'):
        from pytest import skip
        skip('no /dev/stdin on this platform')

    # stdin is a pipe here, so /dev/stdin is non-seekable — the upfront count MUST NOT
    # re-read (and drain) it. Before the fix this submitted 0 tasks with a bogus Source row.
    proc = run(['hs', 'submit', '-f', '/dev/stdin'], input=b'echo one\necho two\necho three\n',
               stdout=PIPE, stderr=PIPE, env=os.environ)
    assert proc.returncode == exit_status.success, proc.stderr.decode()

    query = """
from hypershell.data.model import Task, Source, STDIN_SOURCE_ID
tasks = Task.query().all()
assert len(tasks) == 3, f'expected 3 tasks, got {len(tasks)}'
assert all(t.source == STDIN_SOURCE_ID for t in tasks), [t.source for t in tasks]
named = [s for s in Source.query().all() if s.path not in ('<direct>', '<stdin>')]
assert named == [], named  # no bogus named source for the ephemeral /dev/stdin path
print('OK')
"""
    rc, stdout, stderr = main([sys.executable, '-c', query])
    assert rc == 0, stderr
    assert stdout == 'OK'


@mark.integration
def test_source_stamp_count_matches_cr_only_newlines(temp_site: Path) -> None:
    """The recorded task_count matches the tasks actually submitted regardless of newline
    convention — the count read uses the Loader's own universal-newline decoding (R1, R4)."""
    taskfile = temp_site / 'cr.in'
    with open(str(taskfile), mode='wb') as stream:
        stream.write(b'echo a\recho b\recho c\r')  # classic-Mac CR-only line endings

    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile)])
    assert rc == exit_status.success, stderr
    assert_output(r'INFO .* Found 3 tasks in .*', stderr, 1)

    query = """
from hypershell.data.model import Task, Source
tasks = Task.query().all()
named = [s for s in Source.query().all() if s.path not in ('<direct>', '<stdin>')]
assert len(named) == 1, named
assert named[0].task_count == len(tasks) == 3, (named[0].task_count, len(tasks))
print('OK')
"""
    rc, stdout, stderr = main([sys.executable, '-c', query])
    assert rc == 0, stderr
    assert stdout == 'OK'


# --- Re-submission source gate: hs submit matrix (R3 exempt, R5-R10) --------------------------------

@mark.integration
def test_gate_refuse_resubmit_identical(temp_site: Path) -> None:
    """Re-submitting an identical named file with no flag is refused, naming the prior (R5)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, _, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)

    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.bad_argument
    assert stdout == ''
    assert_output(r'CRITICAL .* was already submitted as source .*', stderr, 1)
    # Refused before any new task is written — the count is unchanged.
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)


@mark.integration
def test_gate_refuse_changed_suggests_update(temp_site: Path) -> None:
    """A changed file at a seen path with no flag is refused, suggesting --update (R6)."""
    taskfile = create_taskfile_echo(temp_site, count=2)
    rc, _, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.success, stderr

    # Same path, different content -> different source fingerprint.
    create_taskfile_echo(temp_site, count=3)  # overwrites task.in at the same path
    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* previously submitted with different content.*--update', stderr, 1)


@mark.integration
def test_gate_warns_incomplete_prior(temp_site: Path) -> None:
    """When fewer tasks landed than recorded, detection warns of an incomplete prior (R7)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, _, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.success, stderr

    # Delete two task rows so the DB count for the source drops below its recorded count.
    prune = """
from hypershell.data.model import Task
Task.delete_all(Task.query().limit(2).all())
print('OK')
"""
    rc, stdout, stderr = main([sys.executable, '-c', prune])
    assert rc == 0, stderr
    assert stdout == 'OK'

    # Re-detect: no flag still refuses (R5), and now also warns about the incomplete prior (R7).
    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.bad_argument
    assert_output(r'WARNING .* appears incomplete: 2 of 4 tasks present.*', stderr, 1)


@mark.integration
def test_gate_dedup_update_not_reported_incomplete(temp_site: Path) -> None:
    """A de-duplicated --update is not later misreported as an incomplete prior (R7 regression).

    --update records the new source's expected count as the *full* file count but stamps only the
    novel tasks onto it — the deduped ones stay under earlier same-path sources. Completeness is
    therefore measured across the lineage; a subsequent detection of the (complete) version must
    not cry wolf. Before the fix this warned a spurious 'appears incomplete: 2 of 6'.
    """
    taskfile = create_taskfile_echo(temp_site, count=4)
    assert main(['hs', 'submit', '-f', str(taskfile), '-g0'])[0] == exit_status.success
    create_taskfile_echo(temp_site, count=6)  # same path, two new lines
    assert main(['hs', 'submit', '-f', str(taskfile), '-g0', '--update'])[0] == exit_status.success

    # Re-detect the now-complete v2: no flag refuses it as a duplicate (R5) but must NOT warn.
    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0'])
    assert rc == exit_status.bad_argument, stderr
    assert_output(r'appears incomplete', stderr, 0)   # the F2 false-positive is gone
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['6'], NO_OUTPUT)


@mark.integration
def test_gate_repeat_submits_all_again(temp_site: Path) -> None:
    """--repeat ingests a new source and submits all tasks again, even on an identical match (R8)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    assert main(['hs', 'submit', '-f', str(taskfile), '-g0'])[0] == exit_status.success

    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0', '--repeat'])
    assert rc == exit_status.success, stderr
    assert_output(r'INFO .* Submitted 4 tasks$', stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['8'], NO_OUTPUT)


@mark.integration
def test_gate_update_submits_only_novel(temp_site: Path) -> None:
    """--update creates a new source but submits only task identities not already present (R9)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    assert main(['hs', 'submit', '-f', str(taskfile), '-g0'])[0] == exit_status.success

    # Extend the same file: the first four lines are byte-identical, two are new.
    create_taskfile_echo(temp_site, count=6)  # overwrites task.in at the same path
    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0', '--update'])
    assert rc == exit_status.success, stderr
    # Loader de-dup tally (R18): four already present, two submitted.
    assert_output(r'INFO .* 4 tasks already present; submitting 2 new tasks$', stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['6'], NO_OUTPUT)


@mark.integration
def test_gate_update_on_unseen_path_submits_all(temp_site: Path) -> None:
    """--update on a never-before-seen path has an empty lineage, so nothing is skipped and all
    tasks are submitted — `--update` degrades to a plain submit when there is no prior source (R9 edge)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'submit', '-f', str(taskfile), '-g0', '--update'])
    assert rc == exit_status.success, stderr
    assert_output(r'INFO .* 0 tasks already present; submitting 4 new tasks$', stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)


@mark.integration
def test_gate_update_repeat_contradictory() -> None:
    """--update and --repeat together are contradictory and rejected before any work (R10)."""
    assert main_lines(['hs', 'submit', '-f', 'x.in', '-g0', '--update', '--repeat']) == (
        exit_status.bad_argument, NO_OUTPUT, ['CRITICAL [hypershell] Cannot combine --update with --repeat']
    )


@mark.integration
def test_gate_direct_and_stdin_exempt(temp_site: Path) -> None:
    """Single-command <direct> and streamed <stdin> submissions are exempt from gating (R3)."""
    import os
    from subprocess import run, PIPE
    # <direct>: an identical single command submitted twice — both succeed, no refusal.
    for _ in range(2):
        rc, _, stderr = main(['hs', 'submit', 'echo', 'solo', '-g0'])
        assert rc == exit_status.success, stderr
    # <stdin>: identical piped input submitted twice — both succeed (main() does not feed stdin).
    for _ in range(2):
        proc = run(['hs', 'submit', '-f', '-', '-g0'], input=b'echo a\necho b\n',
                   stdout=PIPE, stderr=PIPE, env=os.environ)
        assert proc.returncode == exit_status.success, proc.stderr.decode()
