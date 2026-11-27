# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Tag interface and parsing."""


# Type annotations
from __future__ import annotations
from typing import Any, Collection, Dict, List, Optional, Tuple, Type, Final

# Standard libs
import re
from dataclasses import dataclass

# Internal libs
from hypershell.core.types import JSONData, smart_coerce, parse_bytes, parse_time

# Public interface
__all__ = ['Tag', 'TagError', 'split_token', 'edit_distance', 'near_names', 'read_token', 'check_tag', ]


TAG_PATTERN: Final[re.Pattern] = re.compile(r'^[A-Za-z0-9_.+-]+$')
"""The characters a tag key, and a tag's text value, may hold."""

TAG_SIZE_MAX: Final[int] = 120
"""Longest tag key, and longest tag text value (characters)."""


class TagError(ValueError):
    """A `KEY=VALUE` or `KEY:VALUE` token refused by the token rules; each caller gives it its own form."""


@dataclass
class Tag:
    """Tag specification."""

    name: str
    value: JSONData = ''

    def to_dict(self: Tag) -> Dict[str, JSONData]:
        """Format tag specification as dictionary."""
        return {self.name: self.value, }

    @classmethod
    def from_cmdline(cls: Type[Tag], arg: str) -> Tag:
        """Construct from command-line `arg`."""
        tag_part = arg.strip().split(':', 1)
        if len(tag_part) == 1:
            return cls(name=tag_part[0].strip())
        else:
            name, value = tag_part[0].strip(), smart_coerce(tag_part[1].strip())
            # Task.ensure_valid_tag({name: value})
            return cls(name, value)

    @classmethod
    def parse_cmdline_list(cls: Type[Tag], args: List[str]) -> Dict[str, Optional[JSONData]]:
        """Parse command-line list of tags."""
        return {tag.name: tag.value for tag in map(cls.from_cmdline, args)}


def split_token(token: str, naked: bool) -> Tuple[str, str, str]:
    """The key, separator and text of `token`, split at the first of ``=`` and ``:``.

    The separator is ``''`` for a token with neither, which is a tag with no value when `naked` is
    allowed (task input) and is refused with :class:`TagError` otherwise.
    """
    equals, colon = token.find('='), token.find(':')
    if equals < 0 and colon < 0:
        if naked:
            return token, '', ''
        raise TagError(f'Argument not recognized ({token}): missing "=" or ":"')
    at = colon if colon >= 0 and (equals < 0 or colon < equals) else equals
    return token[:at], token[at], token[at + 1:]


def edit_distance(a: str, b: str) -> int:
    """The optimal string alignment distance of `a` and `b`: insertions, deletions, substitutions and adjacent swaps."""
    before, previous = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            row[j] = min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], before[j - 2] + 1)
        before, previous = previous, row
    return previous[-1]


def near_names(key: str, names: Collection[str]) -> List[str]:
    """The `names` within one edit of `key`, ignoring the letter case of `key`, in the order given."""
    return [name for name in names if edit_distance(key.lower(), name) <= 1]


def read_token(key: str, sep: str, text: str, *, columns: Collection[str], fields: Collection[str],
               colon_fields: Collection[str], way_out: str) -> Tuple[str, str, Any]:
    """What the token `key`, `sep`, `text` sets: ``('field', key, text)`` or ``('tag', key, value)``.

    With ``=`` a key in `fields` is a field; a key among the `columns` but not in `fields` is refused
    as a column the caller does not set; a key one edit from a column (letter case aside) is refused
    as a likely typo. With ``:``, or with no separator, a key in `colon_fields` is a field. Any other
    token is a tag, its value typed by `smart_coerce` (``''`` with no separator). A refusal is a
    :class:`TagError` ending with `way_out`, the way to write a tag by that name.
    """
    if sep == '=':
        if key in fields:
            return 'field', key, text
        if key in columns:
            raise TagError(f'{key}: a task column, not set from task input; to set a tag by that name, {way_out}')
        near = near_names(key, columns)
        if near:
            raise TagError(f'{key}: not a task column; did you mean {" or ".join(near)}? '
                           f'To set a tag by that name, {way_out}')
    elif key in colon_fields:
        return 'field', key, text
    return 'tag', key, (smart_coerce(text) if sep else '')


def check_tag(key: Any, value: Any, strict: bool = True) -> None:
    """Refuse the tag `key` with `value` if it is not one by the tag rules: a TypeError or a ValueError.

    A key is a non-empty text and a value a scalar. With ``strict=False`` (a JSON record's tags) that
    is all; otherwise a key, and a text value, hold only the tag characters, at most 120 of them. A
    text value that is empty is a tag with no value. A `memory` or `timeout` text value is checked by
    its own grammar instead of the tag characters, so a Slurm time (``1:30:00``) is taken.
    """
    if not isinstance(key, str):
        raise TypeError(f'Tag key, {key} ({type(key)}) is not string')
    if len(key.strip()) == 0:
        raise TagError(f'Tag key was empty, "{key}:{value}"')
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise TypeError(f'Invalid type for tag value, {type(value)})')
    if not strict:
        return
    if len(key.strip()) > TAG_SIZE_MAX:
        raise TagError(f'Tag key size ({len(key.strip())}) exceeds {TAG_SIZE_MAX} characters ({key}:{value})')
    if not TAG_PATTERN.match(key):
        raise TagError(f'Tag key must only contain alphanumerics and basic symbols [+._-]: "{key}:{value}"')
    if isinstance(value, str):
        if not value.strip():
            return
        if key == 'memory':
            parse_bytes(value)
            return
        if key == 'timeout':
            parse_time(value)
            return
        if len(value) > TAG_SIZE_MAX:
            raise TagError(f'Tag value size ({len(value)}) exceeds {TAG_SIZE_MAX} characters ({key}:{value})')
        if not TAG_PATTERN.match(value):
            raise TagError(f'Tag value must only contain alphanumerics and basic symbols [+._-]: "{key}:{value}"')
