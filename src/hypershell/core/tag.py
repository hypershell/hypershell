# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Tag interface and parsing."""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional, Type

# Standard libs
from dataclasses import dataclass

# Internal libs
from hypershell.core.types import JSONValue, smart_coerce

# Public interface
__all__ = ['Tag', ]


@dataclass
class Tag:
    """Tag specification."""

    name: str
    value: JSONValue = ''

    def to_dict(self: Tag) -> Dict[str, JSONValue]:
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
    def parse_cmdline_list(cls: Type[Tag], args: List[str]) -> Dict[str, Optional[JSONValue]]:
        """Parse command-line list of tags."""
        return {tag.name: tag.value for tag in map(cls.from_cmdline, args)}

