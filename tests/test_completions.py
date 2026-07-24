# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the shipped shell completions (share/).

Zsh `_arguments` closes an option-description ``[ ... ]`` at the first *unescaped*
``]``. A description that embeds a literal bracket (e.g. ``("FILE[@path]")``) therefore
terminates early, and `comparguments` rejects the whole spec with "invalid option
definition", aborting the entire completion function. This module lints the committed
`_hs` completion so that defect class cannot return: for every `_arguments` spec with a
description, the first unescaped ``]`` must be followed by an action (``:``), the end of
the quoted segment (``'``), or end-of-line.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, Tuple

# Standard libs
from pathlib import Path

# External libs
from pytest import mark

# The hand-maintained zsh completion shipped in the wheel (share/).
REPO = Path(__file__).parent.parent
ZSH_COMPLETION = REPO / 'share' / 'zsh' / 'site-functions' / '_hs'


def iter_bad_arg_specs(text: str) -> Iterator[Tuple[int, str]]:
    """
    Yield ``(lineno, line)`` for each `_arguments` spec whose option-description
    contains an unescaped ``]`` that prematurely closes the ``[ ... ]`` bracket.

    Heuristic (matches how zsh parses specs in this file): a spec is a line whose
    stripped form starts with a single quote and contains ``[``. The first ``[`` opens
    the description; the char after its first *unescaped* ``]`` must be ``:``, ``'``, or
    end-of-line. Anything else means the description closed early.
    """
    for lineno, raw in enumerate(text.splitlines(), start=1):
        s = raw.strip()
        if not s.startswith("'") or '[' not in s:
            continue
        i = s.index('[')
        j = i + 1
        while j < len(s):
            if s[j] == '\\':
                j += 2  # Escaped char (e.g. \[ or \]) is literal; skip both.
                continue
            if s[j] == ']':
                nxt = s[j + 1] if j + 1 < len(s) else ''
                if nxt not in (':', "'", ''):
                    yield lineno, s
                break
            j += 1


@mark.unit
def test_zsh_completion_exists() -> None:
    """The zsh completion asset must be present (the wheel ships it)."""
    assert ZSH_COMPLETION.is_file(), f'missing completion: {ZSH_COMPLETION}'


@mark.unit
def test_zsh_completion_descriptions_well_formed() -> None:
    """No `_arguments` spec may close its description bracket early (see module docstring)."""
    bad = list(iter_bad_arg_specs(ZSH_COMPLETION.read_text()))
    assert not bad, 'malformed _arguments description bracket(s):\n' + '\n'.join(
        f'  L{n}: {line}' for n, line in bad)


@mark.unit
def test_from_json_brackets_are_escaped() -> None:
    """Both `--from-json` specs must keep the nested brackets escaped as ``FILE\\[@path\\]``."""
    lines = [ln for ln in ZSH_COMPLETION.read_text().splitlines() if '--from-json[' in ln]
    assert lines, 'no --from-json option spec found in completion'
    for ln in lines:
        assert r'"FILE\[@path\]"' in ln, f'unescaped FILE[@path] in: {ln.strip()}'


@mark.unit
def test_lint_detects_known_defect() -> None:
    """The linter itself must flag the pre-fix (unescaped) form and accept the fixed form."""
    broken = '''        '(1)--from-json[Read tasks from a JSON file ("FILE[@path]")]:json spec:_files' \\'''
    fixed = '''        '(1)--from-json[Read tasks from a JSON file ("FILE\\[@path\\]")]:json spec:_files' \\'''
    assert list(iter_bad_arg_specs(broken)), 'linter failed to flag the known defect'
    assert not list(iter_bad_arg_specs(fixed)), 'linter false-positived on the fixed form'
