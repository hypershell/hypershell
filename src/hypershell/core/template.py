# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Template expansion facility for task execution."""


# Type annotations
from __future__ import annotations
from typing import Dict, Callable
from types import ModuleType

# Standard libs
import os
import re
import math
import datetime
import subprocess
import functools

# Internal libs
from hypershell.core.types import smart_coerce

# Public interface
__all__ = ['Template', 'DEFAULT_TEMPLATE', ]


# Matched in template and expanded accordingly
PATTERN: re.Pattern = re.compile(r'{(.*?)}')


# A plain {} is replaced verbatim with the input arguments
DEFAULT_TEMPLATE = '{}'


# Exposed modules for lambda expressions in templates
EXPOSED_MODULES: Dict[str, ModuleType] = {
    'os': os,
    'path': os.path,
    'math': math,
    'dt': datetime,
}


class Template:
    """Manage template expansion from command arguments."""

    _template: str

    def __init__(self: Template, template: str = DEFAULT_TEMPLATE) -> None:
        """Initialize template with input `args` and `template` for expansion."""
        self.template = template

    @property
    def template(self: Template) -> str:
        """Access underlying raw string value."""
        return self._template

    @template.setter
    def template(self: Template, value: str) -> None:
        """Set underlying raw string value."""
        if isinstance(value, str):
            self._template = value
        else:
            raise AttributeError(f'Expected type \'str\' for member `template`')

    class Error(Exception):
        """Base exception type for Template exceptions."""

    class UnmatchedPattern(Error):
        """Template pattern does not match any implemented expansion."""

    class FailedExpansion(Error):
        """Failure to successfully expand a pattern given input arguments."""

    def expand(self: Template, args: str) -> str:
        """Expand template against input `args`."""
        index = 0
        expansion = ''
        if not PATTERN.search(self.template):
            return self.template
        for match in PATTERN.finditer(self.template):
            (key, ), start, end = match.groups(), match.start(), match.end()
            expansion += self.template[index:start] + self._expand(args, key, start)
            index = end
        else:
            return expansion + self.template[index:]

    def _expand(self: Template, args: str, key: str, start: int) -> str:
        """Determine simple vs complex pattern expansion."""
        key = key.strip()  # allow whitespace (likely in shell and lambda patterns)
        if key in self.simple_patterns:
            return self.simple_patterns[key](args)
        else:
            return self._expand_complex(args, key, start)

    def _expand_complex(self: Template, args: str, key: str, start: int) -> str:
        """Expand complex (nested) patterns in template against `args`."""
        key_ = '{' + key + '}'
        for pattern, routine in self.complex_patterns.items():
            if inner_match := re.match(pattern, key):
                try:
                    inner_key, = inner_match.groups()
                    return routine(args, inner_key)
                except Exception as error:
                    raise self.FailedExpansion(f'Could not expand \'{key_}\' for args ({args}): {error}')
        else:
            raise self.UnmatchedPattern(f'\'{key_}\' in template (at position {start})')

    @functools.cached_property
    def simple_patterns(self: Template) -> Dict[str, Callable[[str], str]]:
        """Map of pattern literals to their expansion routines."""
        return {
            '': self.expand_null,
            '.': self.expand_first_dirname,
            '..': self.expand_second_dirname,
            '/': self.expand_basename,
            '/-': self.expand_basename_without_ext,
            '-': self.expand_fullpath_without_ext,
            '+': self.expand_file_extension,
            '++': self.expand_file_extension_without_dot,
        }

    @functools.cached_property
    def complex_patterns(self: Template) -> Dict[str, Callable[[str, str], str]]:
        """Map of complex patterns to their expansion routines."""
        return {
            r'\[(.*?)]': self.expand_slice,
            '=(.*?)=': self.expand_lambda,
            '%(.*?)%': self.expand_shell,
        }

    @staticmethod
    def expand_null(args: str) -> str:
        """Return `args` without change."""
        return args

    @staticmethod
    def expand_first_dirname(args: str) -> str:
        """Return parent directory of `args` assuming a valid path."""
        return os.path.dirname(args)

    @staticmethod
    def expand_second_dirname(args: str) -> str:
        """Return second parent directory of `args` assuming a valid path."""
        return os.path.dirname(os.path.dirname(args))

    @staticmethod
    def expand_basename(args: str) -> str:
        """Expand to basename of `args` assuming a valid path."""
        return os.path.basename(args)

    @staticmethod
    def expand_basename_without_ext(args: str) -> str:
        """Expand to basename of `args` without the file extension assuming a valid path."""
        return os.path.splitext(os.path.basename(args))[0]

    @staticmethod
    def expand_fullpath_without_ext(args: str) -> str:
        """Drop file extension from `args` assuming a valid path."""
        return os.path.splitext(args)[0]

    @staticmethod
    def expand_file_extension(args: str) -> str:
        """Return file extension of `args` assuming a valid path."""
        return os.path.splitext(args)[1]

    @staticmethod
    def expand_file_extension_without_dot(args: str) -> str:
        """Return file extension of `args` without the leading dot assuming a valid path."""
        return os.path.splitext(args)[1].strip('.')

    def expand_slice(self: Template, args: str, key: str) -> str:
        """Expand slice `key` ([start][:stop][:step]) against args (on white space)."""
        if re.match(r'(\d+)?:?(\d+)?:?(\d+)?$', key):
            result = eval(f'chunks[{key}]', {'chunks': args.split()})
            return ' '.join(result if isinstance(result, list) else [result, ])
        else:
            raise self.FailedExpansion(f'Invalid slice expression \'{key}\'')

    @staticmethod
    def expand_shell(args: str, key: str) -> str:
        """Expand `key` as a shell command with @ replaced with `args`."""
        return subprocess.check_output(key.replace('@', args), shell=True).decode().strip()

    def expand_lambda(self: Template, args: str, key: str) -> str:
        """Expand `key` as a lambda expression in `x` and evaluate against `args`."""
        return str(self.build_lambda(key)(smart_coerce(args)))

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def build_lambda(expression: str) -> Callable[[str], str]:
        """Construct lambda `expression` with single argument 'x'."""
        return eval(f'lambda x: {expression}', EXPOSED_MODULES)

    def __repr__(self: Template) -> str:
        """Interactive representation."""
        return f'Template(\'{self.template}\')'

    def __str__(self: Template) -> str:
        """String representation."""
        return self.template
