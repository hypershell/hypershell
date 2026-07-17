# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the software-factory FSM scripts (.agents/factory/bin).

These scripts own every feature's lifecycle state (spec/<slug>/TECH.md frontmatter);
they are load-bearing for the spec-driven workflow, so their parsing, validation,
transition, and mutation behavior is pinned here. Also lints every committed
spec/*/TECH.md and the template so a corrupt FSM cannot land through CI.
"""


# Type annotations
from __future__ import annotations
from pathlib import Path

# Standard libs
import json
import sys

# External libs
from pytest import mark

# Internal libs (the factory scripts are not a package; import by path)
REPO = Path(__file__).parent.parent
FACTORY_BIN = str(REPO / '.agents' / 'factory' / 'bin')
if FACTORY_BIN not in sys.path:
    sys.path.insert(0, FACTORY_BIN)

from _fsm import FSMError, compute_next, dump_document, split_frontmatter, validate  # noqa: E402
import next_phase  # noqa: E402
import set_phase  # noqa: E402


TEMPLATE = REPO / '.agents' / 'factory' / 'templates' / 'TECH.md'


def make_doc(frontmatter: str, body: str = '\n# Body\n') -> str:
    """Assemble a minimal TECH.md-style document."""
    return f'---\n{frontmatter}---{body}'


MINIMAL = """\
slug: test-slug
kind: feature
appetite: small
status: in_progress
branch: feature/test-slug
base: develop
current_phase: P1
phases:
- id: P1
  name: first
  status: pending
  depends_on: []
  verify: 'true'
- id: P2
  name: second
  status: pending
  depends_on: [P1]
  verify: 'true'
"""


# --- split/dump round-trip --------------------------------------------------------------------------

@mark.unit
def test_split_frontmatter_and_body_preserved() -> None:
    """The YAML block parses to a mapping and the body survives verbatim."""
    data, body = split_frontmatter(make_doc(MINIMAL, '\n# Title\n\ncontent\n'))
    assert data['slug'] == 'test-slug'
    # The body's leading newline is consumed with the fence line; dump_document restores it.
    assert body == '# Title\n\ncontent\n'


@mark.unit
def test_dump_document_round_trip_is_idempotent() -> None:
    """Canonical serialization must be a fixed point: dump(parse(dump(x))) == dump(x)."""
    data, body = split_frontmatter(make_doc(MINIMAL))
    once = dump_document(data, body)
    data2, body2 = split_frontmatter(once)
    assert dump_document(data2, body2) == once


@mark.unit
def test_split_frontmatter_accepts_crlf_line_endings() -> None:
    """A CRLF-saved TECH.md must still find its closing fence."""
    data, _body = split_frontmatter(make_doc(MINIMAL).replace('\n', '\r\n'))
    assert data['slug'] == 'test-slug'


@mark.unit
def test_split_frontmatter_rejects_missing_or_unterminated_fence() -> None:
    """No opening fence and no closing fence are both hard errors."""
    for text in ('slug: x\n', '---\nslug: x\n'):
        try:
            split_frontmatter(text)
        except FSMError:
            continue
        raise AssertionError(f'expected FSMError for {text!r}')


# --- validate ---------------------------------------------------------------------------------------

@mark.unit
def test_validate_accepts_minimal_valid_document() -> None:
    data, _ = split_frontmatter(make_doc(MINIMAL))
    assert validate(data) == []


@mark.unit
def test_validate_flags_structural_errors() -> None:
    """Missing keys, bad statuses, duplicate ids, unknown deps, and a bad pointer all surface."""
    data, _ = split_frontmatter(make_doc(MINIMAL))
    del data['branch']
    data['status'] = 'bogus'
    data['phases'][1]['id'] = 'P1'                  # Duplicate id.
    data['phases'][0]['depends_on'] = ['P9']        # Unknown dependency.
    data['current_phase'] = 'P9'                    # Pointer at a phase that does not exist.
    errors = '\n'.join(validate(data))
    for expected in ('branch', 'bogus', 'duplicate', 'P9'):
        assert expected in errors


# --- compute_next -----------------------------------------------------------------------------------

@mark.unit
def test_compute_next_respects_dependencies() -> None:
    """P2 is not actionable until P1 is done; then it is."""
    data, _ = split_frontmatter(make_doc(MINIMAL))
    nxt, _ = compute_next(data)
    assert nxt['id'] == 'P1'
    data['phases'][0]['status'] = 'done'
    data['current_phase'] = 'P2'
    nxt, warnings = compute_next(data)
    assert nxt['id'] == 'P2' and warnings == []


@mark.unit
def test_compute_next_warns_on_pointer_drift_and_blocked() -> None:
    """A stale current_phase pointer and a blocked phase both raise warnings."""
    data, _ = split_frontmatter(make_doc(MINIMAL))
    data['current_phase'] = 'P2'                    # Stale: P1 is the computed next.
    data['phases'][1]['status'] = 'blocked'
    warnings = '\n'.join(compute_next(data)[1])
    assert 'reconcile' in warnings and 'blocked' in warnings


@mark.unit
def test_compute_next_warns_at_three_failed_attempts() -> None:
    """The durable circuit breaker fires once the next actionable phase has attempts >= 3."""
    data, _ = split_frontmatter(make_doc(MINIMAL))
    data['phases'][0]['attempts'] = 3
    warnings = '\n'.join(compute_next(data)[1])
    assert 'circuit breaker' in warnings


# --- set_phase.main ---------------------------------------------------------------------------------

@mark.unit
def test_set_phase_advances_state_and_touches(tmp_path: Path) -> None:
    """Mark P1 done, advance the pointer, stamp the date — the canonical build transition."""
    doc = tmp_path / 'TECH.md'
    doc.write_text(make_doc(MINIMAL))
    code = set_phase.main([str(doc), '--phase', 'P1', '--phase-status', 'done', '--current', 'P2', '--touch'])
    assert code == 0
    data, _ = split_frontmatter(doc.read_text())
    assert data['phases'][0]['status'] == 'done'
    assert data['current_phase'] == 'P2'
    assert data['last_updated']


@mark.unit
def test_set_phase_record_attempt_increments(tmp_path: Path) -> None:
    """--record-attempt counts red verify gates durably (absent field starts at 0)."""
    doc = tmp_path / 'TECH.md'
    doc.write_text(make_doc(MINIMAL))
    for _ in range(2):
        assert set_phase.main([str(doc), '--phase', 'P1', '--record-attempt']) == 0
    data, _ = split_frontmatter(doc.read_text())
    assert data['phases'][0]['attempts'] == 2


@mark.unit
def test_set_phase_verdict_increments_review_cycle(tmp_path: Path) -> None:
    """Every verdict marks one completed review pass; the counter backs the bounded loop."""
    doc = tmp_path / 'TECH.md'
    doc.write_text(make_doc(MINIMAL))
    assert set_phase.main([str(doc), '--verdict', 'changes-requested', '--reviewed-commit', 'aaa']) == 0
    assert set_phase.main([str(doc), '--verdict', 'approved', '--reviewed-commit', 'bbb']) == 0
    data, _ = split_frontmatter(doc.read_text())
    assert data['review']['cycle'] == 2
    assert data['review']['verdict'] == 'approved'
    assert data['review']['last_reviewed_commit'] == 'bbb'


@mark.unit
def test_set_phase_add_phase_inserts_with_safe_defaults(tmp_path: Path) -> None:
    """--add-phase goes through the validated serializer: position, defaults, list parsing."""
    doc = tmp_path / 'TECH.md'
    doc.write_text(make_doc(MINIMAL))
    code = set_phase.main([
        str(doc), '--add-phase', 'P3', '--name', 'remediation', '--satisfies', 'R1, R2',
        '--depends-on', 'P1', '--after', 'P1', '--verify', 'true',
    ])
    assert code == 0
    data, _ = split_frontmatter(doc.read_text())
    assert [p['id'] for p in data['phases']] == ['P1', 'P3', 'P2']
    added = data['phases'][1]
    assert added['status'] == 'pending'
    assert added['satisfies'] == ['R1', 'R2']
    assert added['parallel'] is False and added['hammerable'] is False and added['hill'] == 'uphill'


@mark.unit
def test_set_phase_refuses_bad_mutations(tmp_path: Path) -> None:
    """Unknown ids and invalid results are refused without writing the file."""
    doc = tmp_path / 'TECH.md'
    doc.write_text(make_doc(MINIMAL))
    before = doc.read_text()
    assert set_phase.main([str(doc), '--phase', 'P9', '--phase-status', 'done']) == 3
    assert set_phase.main([str(doc), '--add-phase', 'P1', '--name', 'dup', '--verify', 'true']) == 2
    assert set_phase.main([str(doc), '--add-phase', 'P4', '--name', 'x', '--verify', 'y', '--after', 'P9']) == 3
    assert set_phase.main([str(doc), '--record-attempt']) == 2       # Requires --phase.
    assert doc.read_text() == before


# --- next_phase.main --------------------------------------------------------------------------------

@mark.unit
def test_next_phase_reports_json(tmp_path: Path, capsys) -> None:
    """The emitted JSON carries the transition ground truth hs-build resumes from."""
    doc = tmp_path / 'TECH.md'
    doc.write_text(make_doc(MINIMAL))
    assert next_phase.main([str(doc)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['next_phase']['id'] == 'P1'
    assert report['counts']['pending'] == 2
    assert report['all_done'] is False


@mark.unit
def test_next_phase_rejects_invalid_frontmatter(tmp_path: Path, capsys) -> None:
    doc = tmp_path / 'TECH.md'
    doc.write_text('---\nslug: incomplete\n---\n')
    assert next_phase.main([str(doc)]) == 2


@mark.unit
def test_next_phase_all_walks_the_portfolio(monkeypatch, capsys) -> None:
    """--all summarizes every committed spec/*/TECH.md without dying on any one record."""
    monkeypatch.chdir(REPO)
    assert next_phase.main(['--all']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert isinstance(rows, list) and rows
    assert all(('slug' in row) or ('error' in row) for row in rows)


# --- committed-artifact lint ------------------------------------------------------------------------

@mark.unit
def test_template_tech_frontmatter_is_valid() -> None:
    """The shipped template must always satisfy its own validator."""
    data, _ = split_frontmatter(TEMPLATE.read_text(encoding='utf-8'))
    assert validate(data) == []


@mark.unit
def test_all_committed_tech_files_are_valid() -> None:
    """A corrupt spec/<slug>/TECH.md must not be able to land through CI."""
    for path in sorted((REPO / 'spec').glob('*/TECH.md')):
        data, _ = split_frontmatter(path.read_text(encoding='utf-8'))
        assert validate(data) == [], f'{path} failed validation'
