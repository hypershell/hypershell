# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the source/fingerprint core (Task.compute_fingerprint, Source schema)."""


# Type annotations
from __future__ import annotations
from pathlib import Path

# Standard libs
import os
import re

# External libs
from pytest import mark
from sqlalchemy import create_engine, inspect

# Internal libs
from hypershell.core.pretty_print import format_source
from hypershell.data.model import (
    Task, Source, Entity, DIRECT_SOURCE_ID, STDIN_SOURCE_ID,
)
from tests import main, main_lines, assert_output, create_taskfile_echo, UUID_PATTERN


# --- Identity fingerprint semantics (R2) -----------------------------------------------------------

@mark.unit
def test_fingerprint_is_order_independent_over_tags() -> None:
    """Tag insertion order must not change the fingerprint (canonical, sorted keys)."""
    a = Task.compute_fingerprint('echo 1', 0, {'alpha': '1', 'beta': '2', 'gamma': '3'})
    b = Task.compute_fingerprint('echo 1', 0, {'gamma': '3', 'alpha': '1', 'beta': '2'})
    assert a == b


@mark.unit
def test_fingerprint_stable_across_template_uuid_and_attempt() -> None:
    """Identity keys off the pre-template raw command, not the expanded args/uuid/attempt.

    Two tasks built from the same raw line but different templates (different expanded
    ``args``), different uuids, and different attempt counters share one fingerprint.
    """
    t1 = Task.new(args='echo hello', raw_args='hello', attempt=1)
    t2 = Task.new(args='printf hello', raw_args='hello', attempt=4)
    assert t1.args != t2.args           # different template expansions
    assert t1.id != t2.id               # freshly minted uuids
    assert t1.attempt != t2.attempt
    assert t1.fingerprint == t2.fingerprint


@mark.unit
def test_fingerprint_differs_on_args_group_or_tag_change() -> None:
    """Any change to args, group, or a user tag yields a different fingerprint."""
    base = Task.compute_fingerprint('echo a', 0, {'x': '1'})
    assert base != Task.compute_fingerprint('echo b', 0, {'x': '1'})   # args
    assert base != Task.compute_fingerprint('echo a', 1, {'x': '1'})   # group
    assert base != Task.compute_fingerprint('echo a', 0, {'x': '2'})   # tag value
    assert base != Task.compute_fingerprint('echo a', 0, {'y': '1'})   # tag key


@mark.unit
def test_fingerprint_excludes_part_tag() -> None:
    """The bookkeeping ``part`` tag (rewritten on rotation) must not affect identity."""
    a = Task.compute_fingerprint('echo a', 0, {'k': '1', 'part': 0})
    b = Task.compute_fingerprint('echo a', 0, {'k': '1', 'part': 7})
    c = Task.compute_fingerprint('echo a', 0, {'k': '1'})
    assert a == b == c


@mark.unit
def test_fingerprint_excludes_resource_knobs() -> None:
    """cores/memory/timeout are popped into columns before identity, so they don't count."""
    t1 = Task.new(args='echo', raw_args='echo', tag={'cores': 2, 'memory': 100, 'timeout': 30})
    t2 = Task.new(args='echo', raw_args='echo', tag={'cores': 8, 'memory': 999, 'timeout': 90})
    plain = Task.new(args='echo', raw_args='echo')
    assert t1.fingerprint == t2.fingerprint == plain.fingerprint


@mark.unit
def test_new_stamps_source_and_falls_back_to_args_without_raw() -> None:
    """``source`` is stamped verbatim; without ``raw_args`` the fingerprint falls back to args."""
    stamped = Task.new(args='echo x', raw_args='x', source=DIRECT_SOURCE_ID)
    assert stamped.source == DIRECT_SOURCE_ID
    fallback = Task.new(args='echo x')            # no raw_args -> uses expanded args
    assert fallback.fingerprint == Task.compute_fingerprint('echo x', 0, {'part': 0})


@mark.unit
def test_supplied_fingerprint_is_not_recomputed() -> None:
    """A retry copies its parent's fingerprint verbatim rather than recomputing it."""
    parent_fp = 'deadbeef' * 4
    child = Task.new(args='echo different', raw_args='different', fingerprint=parent_fp)
    assert child.fingerprint == parent_fp


# --- Schema groundwork (R1, R17) -------------------------------------------------------------------

@mark.unit
def test_fresh_schema_creates_source_table_columns_and_indices() -> None:
    """A fresh ``create_all`` yields the source table, the new task columns, and both indices."""
    engine = create_engine('sqlite://')  # throwaway in-memory db, independent of the global engine
    Entity.metadata.create_all(engine)
    insp = inspect(engine)

    assert insp.has_table('source')
    source_columns = {col['name'] for col in insp.get_columns('source')}
    assert source_columns == {'id', 'path', 'fingerprint', 'task_count', 'created'}

    task_columns = {col['name'] for col in insp.get_columns('task')}
    assert {'source', 'fingerprint'} <= task_columns

    source_indices = {ix['name'] for ix in insp.get_indexes('source')}
    task_indices = {ix['name'] for ix in insp.get_indexes('task')}
    assert 'index_source_lookup' in source_indices
    assert 'index_tasks_source' in task_indices


@mark.unit
def test_tasks_source_index_is_full_covering_not_partial() -> None:
    """The de-dup/detection index covers (source, fingerprint) and is NOT partial (R17).

    A partial ``WHERE source NOT IN (reserved)`` predicate is only honored when the query
    repeats it with matching literals; the parameterized ``count_for_source`` /
    ``fingerprints_for_sources`` lookups bind ``source`` instead, which no engine can prove
    satisfies a literal partial predicate — so a partial index silently degrades them to a
    full table scan. A full index is used with plain bound parameters everywhere.
    """
    engine = create_engine('sqlite://')
    Entity.metadata.create_all(engine)
    insp = inspect(engine)
    (entry,) = [ix for ix in insp.get_indexes('task') if ix['name'] == 'index_tasks_source']
    assert entry['column_names'] == ['source', 'fingerprint']         # covering (source, fingerprint)
    assert not entry.get('dialect_options', {}).get('sqlite_where')   # no partial predicate


@mark.unit
def test_source_lookups_use_the_index_not_a_full_scan() -> None:
    """R17: the count + lineage de-dup lookups are index-backed, not full table scans.

    Mirrors the ``WHERE`` clauses of :meth:`Task.count_for_source` and
    :meth:`Task.fingerprints_for_sources` on a populated throwaway table and asserts the
    planner reaches for ``index_tasks_source`` instead of scanning ``task`` (the linear cost
    R17 forbids at billions–trillions of rows). A partial index would ``SCAN`` here.
    """
    engine = create_engine('sqlite://')
    Entity.metadata.create_all(engine)
    real = 'aaaaaaaa-0000-7000-8000-000000000000'
    columns = ('id', '"group"', 'args', 'submit_id', 'submit_time', 'attempt', 'retried', 'tag',
               'source', 'fingerprint')
    rows = ([(f'{i:036d}', 1, 'echo', 'sub', '2026-01-01', 1, 0, '{}', real, f'fp{i % 5}')
             for i in range(30)] +
            [(f'r{i:035d}', 1, 'echo', 'sub', '2026-01-01', 1, 0, '{}', DIRECT_SOURCE_ID, None)
             for i in range(2000)])   # bulk reserved rows -> a scan would be far costlier than the index
    with engine.begin() as conn:
        conn.exec_driver_sql(f'INSERT INTO task ({", ".join(columns)}) VALUES ({", ".join(["?"] * len(columns))})', rows)
        conn.exec_driver_sql('ANALYZE')
    with engine.connect() as conn:
        count_plan = ' | '.join(r[-1] for r in conn.exec_driver_sql(
            'EXPLAIN QUERY PLAN SELECT count(*) FROM task WHERE source = ?', (real,)))
        fp_plan = ' | '.join(r[-1] for r in conn.exec_driver_sql(
            'EXPLAIN QUERY PLAN SELECT DISTINCT fingerprint FROM task WHERE source IN (?) '
            'AND fingerprint IS NOT NULL', (real,)))
    assert 'index_tasks_source' in count_plan and 'SCAN task' not in count_plan, count_plan
    assert 'index_tasks_source' in fp_plan and 'SCAN task' not in fp_plan, fp_plan


# --- Presentation: source resolution for humans (R18-adjacent) -------------------------------------

@mark.unit
def test_format_source_passes_sentinels_shows_paths_and_keeps_specs_opaque() -> None:
    """Sentinels pass through; real paths are absolute by default / relative on request; @node opaque."""
    # reserved sentinels pass through unchanged in both modes
    assert format_source('<direct>') == '<direct>'
    assert format_source('<stdin>') == '<stdin>'
    assert format_source('<stdin>', relative=True) == '<stdin>'
    # a real path shows as-stored (absolute) by default, relativized only on request
    assert format_source('/data/tasks/run.in') == '/data/tasks/run.in'
    assert format_source(os.path.abspath('run.in'), relative=True) == 'run.in'
    # a --from-json spec carrying an @node suffix is opaque and never relativized
    spec = '/data/plan.json@chunks'
    assert format_source(spec) == spec
    assert format_source(spec, relative=True) == spec


@mark.integration
def test_normal_view_resolves_source_while_machine_formats_stay_raw(temp_site: Path) -> None:
    """The human `normal` view resolves `source` UUID -> path (single + batched) with the raw Source.id
    in parens, and shows the identity fingerprint in parens on the id; machine surfaces keep raw values."""
    taskfile = create_taskfile_echo(temp_site, count=3)
    assert main(['hs', 'submit', str(taskfile)])[0] == 0

    rc, ids, _ = main_lines(['hs', 'list', 'id', '-f', 'plain'])
    assert rc == 0
    task_id = ids[0]

    # `hs info` (single-task normal view) resolves source -> abspath with the Source.id in parens, and
    # shows the identity fingerprint (md5) in parens on the id line.
    rc, stdout, _ = main(['hs', 'info', task_id])
    assert rc == 0
    assert_output(rf'source: {re.escape(str(taskfile))} \([0-9a-f-]+\)$', stdout, 1)
    assert_output(r'id: [0-9a-f-]+ \([0-9a-f]{32}\)$', stdout, 1)   # fingerprint (md5) in parens on the id
    assert 'fingerprint:' not in stdout            # shown inline on `id`, not as a separate labeled field

    # ...and the many-task normal view resolves every row via the batched source_map (no N+1)
    rc, listing, _ = main(['hs', 'list', '--all'])
    assert rc == 0
    assert_output(rf'source: {re.escape(str(taskfile))} \([0-9a-f-]+\)$', listing, 3)

    # machine surfaces keep the raw source UUID (stable, scriptable)
    rc, raw, _ = main(['hs', 'info', task_id, '-x', 'source'])
    assert rc == 0
    assert UUID_PATTERN.match(raw.strip('"'))


@mark.integration
def test_normal_view_resolves_reserved_sentinels(temp_site: Path) -> None:
    """Single-command (`<direct>`) submissions resolve to the reserved sentinel in the normal view."""
    assert main(['hs', 'submit', 'echo direct-presentation'])[0] == 0
    rc, ids, _ = main_lines(['hs', 'list', 'id', '-f', 'plain'])
    assert rc == 0
    rc, stdout, _ = main(['hs', 'info', ids[0]])
    assert rc == 0
    assert_output(rf'source: <direct> \({re.escape(DIRECT_SOURCE_ID)}\)$', stdout, 1)
