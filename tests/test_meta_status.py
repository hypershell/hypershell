# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the harness-feedback reader (.agents/factory/bin/meta_status.py).

The self-improvement loop's producers append markdown findings to spec/<slug>/META.md
and its consumers (hs-publish, hs-harness) read them only through this parser — so its
fence-skipping, field extraction, and filter semantics are pinned here. Also lints every
committed spec/*/META.md and the template.
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

import meta_status  # noqa: E402
from meta_status import parse_findings  # noqa: E402


SAMPLE = """\
# META — Sample

## What worked well

- something helped

## F1 — first finding · seen again
`origin=hs-build:P2 severity=medium category=tooling status=open target=a/b.md`
- **What happened:** the thing.
- **Skill cause:** the reason.
- **Recommended fix:** the change.
- **Confidence:** high · **Effort:** small

```markdown
## F9 — fenced example that must be skipped
`origin=x severity=high category=instruction status=open target=y`
```

## F2 — second finding
`origin=hs-review:step3 severity=high category=steering status=applied target=c/d.md`
- **What happened:** other thing.
"""


# --- parser semantics -------------------------------------------------------------------------------

@mark.unit
def test_parse_findings_extracts_fields_and_meta() -> None:
    """Header id/title, the backtick metadata line, and bullet fields all land in the record."""
    records = parse_findings(SAMPLE)
    assert [r['id'] for r in records] == ['F1', 'F2']
    f1 = records[0]
    assert f1['title'] == 'first finding · seen again'
    assert f1['severity'] == 'medium' and f1['status'] == 'open'
    assert f1['origin'] == 'hs-build:P2' and f1['target'] == 'a/b.md'
    assert f1['what_happened'] == 'the thing.'
    assert f1['confidence'] == 'high' and f1['effort'] == 'small'


@mark.unit
def test_parse_findings_skips_fenced_examples() -> None:
    """The schema example inside a code fence is illustrative, never a real finding."""
    assert all(r['id'] != 'F9' for r in parse_findings(SAMPLE))


@mark.unit
def test_non_finding_section_closes_a_finding() -> None:
    """A '## ' header that is not an F# section ends the open finding cleanly."""
    text = SAMPLE + '\n## Notes\n\n- **What happened:** stray text that belongs to no finding.\n'
    records = parse_findings(text)
    assert [r['id'] for r in records] == ['F1', 'F2']
    assert records[1]['what_happened'] == 'other thing.'


@mark.unit
def test_template_meta_has_zero_findings() -> None:
    """The shipped META.md template must parse to no findings (its schema lives in a fence)."""
    text = (REPO / '.agents' / 'factory' / 'templates' / 'META.md').read_text(encoding='utf-8')
    assert parse_findings(text) == []


# --- CLI: counts, filters, missing file -------------------------------------------------------------

@mark.unit
def test_main_counts_and_status_filter(tmp_path: Path, capsys) -> None:
    """Counts cover ALL findings while --status narrows the selection."""
    path = tmp_path / 'META.md'
    path.write_text(SAMPLE)
    assert meta_status.main([str(path), '--status', 'open']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['counts'] == {
        'total': 2, 'open': 1, 'applied': 1, 'rejected': 0, 'deferred': 0,
        'high': 1, 'medium': 1, 'low': 0,
    }
    assert [f['id'] for f in report['findings']] == ['F1']


@mark.unit
def test_main_severity_and_id_filters(tmp_path: Path, capsys) -> None:
    path = tmp_path / 'META.md'
    path.write_text(SAMPLE)
    assert meta_status.main([str(path), '--severity', 'high', '--id', 'F2']) == 0
    report = json.loads(capsys.readouterr().out)
    assert [f['id'] for f in report['findings']] == ['F2']


@mark.unit
def test_main_missing_file_is_a_valid_empty_state(tmp_path: Path, capsys) -> None:
    """A feature that logged no friction has no META.md — that is success, not an error."""
    assert meta_status.main([str(tmp_path / 'absent.md')]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['exists'] is False and report['findings'] == []


# --- committed-artifact lint ------------------------------------------------------------------------

@mark.unit
def test_all_committed_meta_files_parse(capsys) -> None:
    """Every committed spec/*/META.md must go through the reader without error."""
    for path in sorted((REPO / 'spec').glob('*/META.md')):
        assert meta_status.main([str(path)]) == 0, f'{path} failed to parse'
        report = json.loads(capsys.readouterr().out)
        for finding in report['findings']:
            assert finding['id'].startswith('F')
            assert finding['status'] in ('open', 'applied', 'rejected', 'deferred'), \
                f'{path}: {finding["id"]} has invalid status {finding["status"]!r}'
