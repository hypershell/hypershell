# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test JSON task-plan submission (--from-json) and named {key} template expansion."""


# Type annotations
from __future__ import annotations
from pathlib import Path

# Standard libs
import re

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_json_taskfile, assert_output
from hypershell.core.template import Template, render_value


# ---------------------------------------------------------------------------
# Template engine unit tests (named {key} context + render_value)
# ---------------------------------------------------------------------------

@mark.unit
def test_template_named_expansion() -> None:
    """Named keys resolve from the provided context."""
    template = Template('{a}-{b}')
    assert template.expand('', context={'a': 'X', 'b': 'Y'}) == 'X-Y'


@mark.unit
def test_template_base_and_named() -> None:
    """The base args ({}) and named keys expand together."""
    template = Template('{} --region {seqid}')
    assert template.expand('process Chr1', context={'seqid': 'Chr1'}) == 'process Chr1 --region Chr1'


@mark.unit
def test_template_missing_key_raises() -> None:
    """A named key absent from context raises UnmatchedPattern."""
    template = Template('{x}')
    try:
        template.expand('', context={})
    except Template.UnmatchedPattern:
        pass
    else:
        raise AssertionError('Expected Template.UnmatchedPattern for missing key')


@mark.unit
def test_template_builtin_precedence() -> None:
    """Built-in simple patterns take precedence over same-named context keys."""
    template = Template('{/}')
    assert template.expand('/data/x/y.bam', context={'/': 'shadowed'}) == 'y.bam'


@mark.unit
def test_template_no_context_backcompat() -> None:
    """Expansion without context behaves exactly as before (positional args)."""
    assert Template('echo {}').expand('hello world') == 'echo hello world'


@mark.unit
@mark.parametrize('value,expected', [
    ('abc', 'abc'),
    (5, '5'),
    (1.5, '1.5'),
    (True, 'true'),
    (False, 'false'),
    (None, ''),
    ({'a': 1}, '{"a":1}'),
    ([1, 2], '[1,2]'),
])
def test_render_value(value, expected) -> None:
    """render_value stringifies JSON values for {key} expansion."""
    assert render_value(value) == expected


# ---------------------------------------------------------------------------
# `hs submit --from-json` integration tests
# ---------------------------------------------------------------------------

CHUNK_PLAN = {
    'metadata': {'strategy': 'adaptive'},
    'chunks': [
        {'chunk_id': 'c1', 'seqid': 'Chr1', 'start': 1, 'end': 5000000},
        {'chunk_id': 'c2', 'seqid': 'Chr2', 'start': 1, 'end': 3000000},
    ],
}


@mark.integration
def test_from_json_basic(temp_site: Path) -> None:
    """Named {key} fields expand from each JSON record."""
    plan = create_json_taskfile(temp_site, CHUNK_PLAN)
    rc, stdout, stderr = main(['hs', 'submit', f'--from-json={plan}@chunks',
                               '--template', 'process --region {seqid}:{start}-{end} --id {chunk_id}'])
    assert rc == exit_status.success
    assert_output(r'INFO .* Submitted 2 tasks$', stderr, 1)
    _, args, _ = main_lines(['hs', 'list', 'args', '-f', 'plain'])
    assert sorted(args) == [
        'process --region Chr1:1-5000000 --id c1',
        'process --region Chr2:1-3000000 --id c2',
    ]


@mark.integration
def test_from_json_top_level_array(temp_site: Path) -> None:
    """A top-level JSON array requires no @path."""
    plan = create_json_taskfile(temp_site, [{'seqid': 'Chr1'}, {'seqid': 'Chr2'}])
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}', '--template', 'echo {seqid}'])
    assert rc == exit_status.success
    _, args, _ = main_lines(['hs', 'list', 'args', '-f', 'plain'])
    assert sorted(args) == ['echo Chr1', 'echo Chr2']


@mark.integration
def test_from_json_dotted_path(temp_site: Path) -> None:
    """A dotted @path traverses nested objects to the task list."""
    plan = create_json_taskfile(temp_site, {'results': {'tasks': [{'x': 'A'}, {'x': 'B'}]}})
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}@results.tasks', '--template', 'echo {x}'])
    assert rc == exit_status.success
    _, args, _ = main_lines(['hs', 'list', 'args', '-f', 'plain'])
    assert sorted(args) == ['echo A', 'echo B']


@mark.integration
def test_from_json_args_field(temp_site: Path) -> None:
    """The 'args' key supplies the base command reachable via {}."""
    plan = create_json_taskfile(temp_site, [{'args': 'process Chr1', 'region': 'Chr1:1-10'}])
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}', '--template', '{} --region {region}'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'args', '-f', 'plain']) == (
        exit_status.success, ['process Chr1 --region Chr1:1-10'], NO_OUTPUT
    )


@mark.integration
def test_from_json_default_template_empty_command_errors(temp_site: Path) -> None:
    """Without an 'args' field, the default template ({}) yields an empty command."""
    plan = create_json_taskfile(temp_site, [{'seqid': 'Chr1'}])
    rc, stdout, stderr = main(['hs', 'submit', f'--from-json={plan}'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* record 0: expands to an empty command', stderr, 1)
    # Nothing committed
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['0'], NO_OUTPUT)


@mark.integration
def test_from_json_relaxed_tags(temp_site: Path) -> None:
    """JSON values with spaces/slashes/colons are stored as tags (validator relaxed)."""
    plan = create_json_taskfile(temp_site, [{'seqid': 'Chr1', 'note': 'a b/c:d'}])
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}', '--template', 'echo {seqid}'])
    assert rc == exit_status.success
    _, stdout, _ = main(['hs', 'list', 'tag', '-f', 'plain'])
    assert 'a b/c:d' in stdout


@mark.integration
def test_from_json_nested_value_rendered(temp_site: Path) -> None:
    """Nested JSON values render as compact JSON for {key} expansion."""
    plan = create_json_taskfile(temp_site, [{'meta': {'nested': True}}])
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}', '--template', 'echo {meta}'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'args', '-f', 'plain']) == (
        exit_status.success, ['echo {"nested":true}'], NO_OUTPUT
    )


@mark.integration
def test_from_json_bool_null_render(temp_site: Path) -> None:
    """Booleans render true/false and null renders empty in {key} expansion."""
    plan = create_json_taskfile(temp_site, [{'flag': True, 'off': False, 'empty': None, 'n': 1}])
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}',
                     '--template', 'echo f={flag} o={off} e=[{empty}] n={n}'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'args', '-f', 'plain']) == (
        exit_status.success, ['echo f=true o=false e=[] n=1'], NO_OUTPUT
    )


@mark.integration
def test_from_json_reserved_key_promotion(temp_site: Path) -> None:
    """Reserved keys (cores) promote to columns and remain available for {key}."""
    plan = create_json_taskfile(temp_site, [{'x': 'y', 'cores': 8}])
    rc, _, _ = main(['hs', 'submit', f'--from-json={plan}', '--template', 'echo {x} c={cores}'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'args', 'cores', '-f', 'plain']) == (
        exit_status.success, ['echo y c=8\t8'], NO_OUTPUT
    )


@mark.integration
def test_from_json_failfast_missing_key(temp_site: Path) -> None:
    """A record missing a referenced key aborts before committing anything."""
    plan = create_json_taskfile(temp_site, [{'seqid': 'Chr1', 'end': 10}, {'seqid': 'Chr2'}])
    rc, stdout, stderr = main(['hs', 'submit', f'--from-json={plan}', '--template', 'echo {seqid}:{end}'])
    assert rc == exit_status.bad_argument
    assert_output(r"CRITICAL .* record 1: '\{end\}'", stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['0'], NO_OUTPUT)


@mark.integration
def test_from_json_empty_list(temp_site: Path) -> None:
    """An empty task list is a clear error."""
    plan = create_json_taskfile(temp_site, {'chunks': []})
    rc, _, stderr = main(['hs', 'submit', f'--from-json={plan}@chunks', '--template', 'echo {x}'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* Task list at "chunks" .* is empty', stderr, 1)


@mark.integration
def test_from_json_not_a_list(temp_site: Path) -> None:
    """A path that resolves to a non-list is a clear error."""
    plan = create_json_taskfile(temp_site, {'chunks': {'not': 'a list'}})
    rc, _, stderr = main(['hs', 'submit', f'--from-json={plan}@chunks', '--template', 'echo {x}'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* Expected a list of task objects .* found dict', stderr, 1)


@mark.integration
def test_from_json_bad_path(temp_site: Path) -> None:
    """A missing path segment is a clear error."""
    plan = create_json_taskfile(temp_site, {'chunks': [{'x': 1}]})
    rc, _, stderr = main(['hs', 'submit', f'--from-json={plan}@does.not.exist', '--template', 'echo {x}'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* Path "does.not.exist" not found.*no key "does"', stderr, 1)


@mark.integration
def test_from_json_element_not_object(temp_site: Path) -> None:
    """A non-object list element is a clear error."""
    plan = create_json_taskfile(temp_site, [{'x': 1}, 'not-an-object'])
    rc, _, stderr = main(['hs', 'submit', f'--from-json={plan}', '--template', 'echo {x}'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* Task 1 .* is not an object.*found str', stderr, 1)


@mark.integration
def test_from_json_file_not_found(temp_site: Path) -> None:
    """A missing JSON file is a clear error."""
    missing = str(temp_site / 'missing.json')
    rc, _, stderr = main(['hs', 'submit', f'--from-json={missing}@chunks', '--template', 'echo {x}'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* File not found: ' + re.escape(missing), stderr, 1)


@mark.integration
def test_from_json_mixed_with_positional(temp_site: Path) -> None:
    """--from-json cannot be combined with positional args."""
    plan = create_json_taskfile(temp_site, [{'x': 1}])
    rc, _, stderr = main(['hs', 'submit', 'echo', 'hi', '--from-json', str(plan), '-g0'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* Cannot combine --from-json', stderr, 1)


# ---------------------------------------------------------------------------
# `hsx` / `hs cluster` --from-json end-to-end
# ---------------------------------------------------------------------------

@mark.integration
def test_cluster_from_json(temp_site: Path) -> None:
    """A local cluster expands JSON records submit-side and runs them verbatim."""
    plan = create_json_taskfile(temp_site, {'chunks': [
        {'seqid': 'Chr1', 'chunk_id': 'c1'},
        {'seqid': 'Chr2', 'chunk_id': 'c2'},
        {'seqid': 'Chr3', 'chunk_id': 'c3'},
    ]})
    rc, stdout, stderr = main(['hsx', f'--from-json={plan}@chunks',
                               '--template', 'echo {seqid} {chunk_id}', '-N', '2'])
    assert rc == exit_status.success
    assert sorted(stdout.splitlines()) == ['Chr1 c1', 'Chr2 c2', 'Chr3 c3']
    assert_output(r'\[hypershell.server\] Completed task', stderr, 3)

    # Stored args are fully expanded; commands ran once (no double expansion)
    _, args, _ = main_lines(['hs', 'list', 'args', '-f', 'plain'])
    assert sorted(args) == ['echo Chr1 c1', 'echo Chr2 c2', 'echo Chr3 c3']

    # Status all success, and tags queryable
    assert main_lines(['hs', 'list', 'exit_status', '-f', 'plain']) == (
        exit_status.success, ['0'] * 3, NO_OUTPUT
    )
    rc, stdout, _ = main(['hs', 'list', 'id', '-t', 'seqid:Chr1', '-f', 'plain'])
    assert rc == exit_status.success and len(stdout.splitlines()) == 1


# ---------------------------------------------------------------------------
# `--from-json` re-submission gate (same matrix as a plain FILE)
# ---------------------------------------------------------------------------
# A JSON source keys on abspath(FILE)[@node] + the file's content md5, exactly like a
# line file, so the shared apply_source_gate covers it unchanged. Group 0 is pinned so
# the per-task fingerprint (which includes group) is stable across the two submits.

@mark.integration
def test_from_json_gate_refuses_resubmit(temp_site: Path) -> None:
    """Submitting the same JSON plan twice with no flag is refused."""
    plan = create_json_taskfile(temp_site, {'chunks': [{'seqid': 'Chr1'}, {'seqid': 'Chr2'}]})
    argv = ['hs', 'submit', f'--from-json={plan}@chunks', '-g', '0', '--template', 'echo {seqid}']
    rc, _, stderr = main(argv)
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['2'], NO_OUTPUT)

    # Same path, same content -> refused, naming the prior source; nothing new committed.
    rc, stdout, stderr = main(argv)
    assert rc == exit_status.bad_argument
    assert stdout == ''
    assert_output(r'CRITICAL .* was already submitted as source .*', stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['2'], NO_OUTPUT)


@mark.integration
def test_from_json_gate_repeat_resubmits_all(temp_site: Path) -> None:
    """--repeat ingests the JSON plan as a new source and submits every task again."""
    plan = create_json_taskfile(temp_site, {'chunks': [{'seqid': 'Chr1'}, {'seqid': 'Chr2'}]})
    argv = ['hs', 'submit', f'--from-json={plan}@chunks', '-g', '0', '--template', 'echo {seqid}']
    assert main(argv)[0] == exit_status.success
    rc, _, stderr = main(argv + ['--repeat'])
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)


@mark.integration
def test_from_json_gate_update_adds_novel_only(temp_site: Path) -> None:
    """--update on an edited JSON plan submits only records not already present."""
    plan = create_json_taskfile(temp_site, {'chunks': [{'seqid': 'Chr1'}, {'seqid': 'Chr2'}]})
    argv = ['hs', 'submit', f'--from-json={plan}@chunks', '-g', '0', '--template', 'echo {seqid}']
    assert main(argv)[0] == exit_status.success

    # Rewrite the same path with one extra record; --update adds only the novel one.
    create_json_taskfile(temp_site, {'chunks': [{'seqid': 'Chr1'}, {'seqid': 'Chr2'}, {'seqid': 'Chr3'}]})
    rc, _, stderr = main(argv + ['--update'])
    assert rc == exit_status.success, stderr
    assert_output(r'submitting 1 new tasks', stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['3'], NO_OUTPUT)
    _, args, _ = main_lines(['hs', 'list', 'args', '-f', 'plain'])
    assert sorted(args) == ['echo Chr1', 'echo Chr2', 'echo Chr3']


@mark.integration
def test_from_json_gate_cluster_restart_idempotent(temp_site: Path) -> None:
    """hsx --from-json ... --restart runs the plan and is idempotent on requeue.

    Also exercises the removed --from-json + --restart incompatibility (this run no longer
    errors) and the co-running-submitter scheduler guard (novel tasks against a DB of prior
    completed tasks).
    """
    plan = create_json_taskfile(temp_site, {'chunks': [
        {'seqid': 'Chr1', 'chunk_id': 'c1'},
        {'seqid': 'Chr2', 'chunk_id': 'c2'},
    ]})
    argv = ['hsx', f'--from-json={plan}@chunks', '--template', 'echo {seqid} {chunk_id}',
            '-N', '2', '--restart']
    rc, stdout, stderr = main(argv)
    assert rc == exit_status.success, stderr
    assert sorted(stdout.splitlines()) == ['Chr1 c1', 'Chr2 c2']
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['2'], NO_OUTPUT)

    # Requeue: fingerprint matches, every task already present -> nothing new submitted or run.
    rc, stdout, stderr = main(argv)
    assert rc == exit_status.success, stderr
    assert stdout == ''
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['2'], NO_OUTPUT)
