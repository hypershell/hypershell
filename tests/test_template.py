# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test template expansion (executor slot context)."""


# Type annotations
from __future__ import annotations

# External libs
from pytest import mark, raises

# Internal libs
from hypershell.core.template import Template


@mark.unit
def test_template_slot_context() -> None:
    """The {slot}/{slot_count} placeholders resolve from the expansion context."""
    assert Template('{slot}/{slot_count}').expand('x', context={'slot': 2, 'slot_count': 4}) == '2/4'
    # Slot zero renders as '0', not the empty string.
    assert Template('{slot}').expand('x', context={'slot': 0, 'slot_count': 1}) == '0'


@mark.unit
def test_template_slot_coexists_with_args() -> None:
    """A {slot} placeholder does not shadow the default {} argument expansion."""
    assert Template('run {} on {slot}').expand('job', context={'slot': 3, 'slot_count': 4}) == 'run job on 3'


@mark.unit
def test_template_slot_requires_context() -> None:
    """Without a context, {slot} is an unmatched pattern (fail-fast at expansion)."""
    with raises(Template.UnmatchedPattern):
        Template('{slot}').expand('x')
