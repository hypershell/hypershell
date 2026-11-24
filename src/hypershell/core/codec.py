# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The queue's RPC envelope as data: JSON over a closed vocabulary of plain values.

Every request, reply and proxy token a queue connection carries is turned into JSON text by
:func:`encode` and back by :func:`decode`, and nothing outside this vocabulary crosses:

======================================  ==================================================
Python value                            JSON
======================================  ==================================================
``None``, ``bool``, ``int``, ``str``    the same
``float`` (finite)                      a number
``tuple``                               an array
``list``                                ``{"l": [...]}``
``dict`` with ``str`` keys              ``{"d": {...}}``
``bytes``                               ``{"b": "..."}``, one code point per byte (Latin-1)
the manager's ``Token``                 ``{"k": [typeid, [host, port], id]}``
``queue.Empty``, ``queue.Full``,        ``{"e": [name, [text, ...]]}``
``RemoteError``
======================================  ==================================================

Types are matched exactly, so a ``bool`` never passes for an ``int``, nor a subclass for its base.
A ``RemoteError`` always carries at least one text. Any other exception given to the encoder (the
error a remote method raised), and a ``RemoteError`` without text, crosses as
``RemoteError('<Type>: <text>')``. Any other value cannot be encoded, and :class:`CodecError` is
raised before anything is written.

Decoding parses JSON (which builds only dicts, lists, strings, numbers, booleans and ``None``), then
maps each node back, refusing any other shape with :class:`CodecError`: an unknown tag, a tag object
with more than one key or a value of the wrong type, a ``"b"`` string with a code point above U+00FF,
a malformed token or exception (a ``RemoteError`` without text among them), ``NaN`` or an infinite
number, invalid UTF-8 or JSON, trailing data, or an empty frame. Both directions refuse nesting deeper than :data:`MAX_DEPTH` containers and any
container of more than :data:`MAX_ITEMS` elements. A frame that begins with byte ``0x80`` is the
pickle framing of a release before 2.9.0a10; it is never decoded, and is refused with
:class:`PickleFrame`.

:data:`OLD_PEER_REPLY` is the one fixed reply a server sends such a peer before it closes the
connection: built here once, from constants, in the earlier framing that peer reads.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Final

# Standard libs
import json
import math
import pickle
from queue import Empty, Full
from multiprocessing.managers import Token, RemoteError

# Public interface
__all__ = ['encode', 'decode', 'CodecError', 'PickleFrame', 'MAX_DEPTH', 'MAX_ITEMS',
           'OLD_PEER_TEXT', 'OLD_PEER_REPLY']


MAX_DEPTH: Final[int] = 8
"""Most containers one message may nest, counting the message itself (a tuple, list, dict, token or exception)."""

MAX_ITEMS: Final[int] = 64
"""Most elements any one tuple, list or dict of a message may hold."""

PICKLE_FRAME: Final[int] = 0x80
"""First byte of a pickle of protocol 2 or later: the queue framing of a release before 2.9.0a10."""

OLD_PEER_TEXT: Final[str] = ('This queue server runs HyperShell 2.9.0a10 or later, whose queue framing a release '
                             'before 2.9.0a10 cannot read; run matching server and client versions')
"""What a peer of a release before 2.9.0a10 is told before its connection is closed."""

OLD_PEER_REPLY: Final[bytes] = pickle.dumps(('#ERROR', RuntimeError(OLD_PEER_TEXT)), protocol=4)
"""The fixed reply, in the earlier framing, to a peer of a release before 2.9.0a10.

Built once from constants (protocol 4, so it is the same bytes on every Python): such a peer raises
the error at its first request, logs it as one line and exits. Nothing a peer sends is ever unpickled.
"""

EXCEPTIONS: Final[dict] = {'Empty': Empty, 'Full': Full, 'RemoteError': RemoteError}
"""The exceptions that cross as themselves, by name."""


class CodecError(ValueError):
    """A value outside the queue vocabulary, or a frame that does not decode into it."""


class PickleFrame(CodecError):
    """A frame in the pickle framing of a release before 2.9.0a10 (never decoded)."""


def encode(obj: Any) -> bytes:
    """The frame for `obj`: compact, ASCII-only JSON of its vocabulary tree.

    Raises:
        CodecError: `obj` holds a value outside the vocabulary, or breaks a limit.
    """
    tree = _out(obj, 0)
    try:
        return json.dumps(tree, allow_nan=False, separators=(',', ':')).encode('ascii')
    except (ValueError, RecursionError) as error:  # An integer past the conversion limit
        raise CodecError(f'cannot encode message: {error}') from None


def decode(buf: Any) -> Any:
    """The value a frame (`bytes` or any bytes-like buffer) carries.

    Raises:
        PickleFrame: the frame is in the pickle framing of an earlier release.
        CodecError: the frame is not one this vocabulary decodes.
    """
    with memoryview(buf) as data:  # Released on return, so the caller's buffer can be freed
        if not data.nbytes:
            raise CodecError('empty frame')
        if data[0] == PICKLE_FRAME:
            raise PickleFrame('frame in the pickle framing of a release before 2.9.0a10')
        try:
            text = str(data, 'utf-8')
        except ValueError as error:
            raise CodecError(f'frame is not valid: {error}') from None
    try:
        tree = json.loads(text, parse_constant=_refuse_constant)
    except RecursionError:
        raise CodecError('frame nests too deep') from None
    except ValueError as error:  # Invalid UTF-8 or JSON, trailing data, an integer past the limit
        raise CodecError(f'frame is not valid: {error}') from None
    try:
        return _in(tree, 0)
    except RecursionError:
        raise CodecError('frame nests too deep') from None


def _refuse_constant(name: str) -> None:
    raise CodecError(f'{name} is not a number this vocabulary carries')


def _enter(depth: int, size: int) -> int:
    """The depth of a container `size` long inside `depth` containers; a broken limit is refused."""
    depth += 1
    if depth > MAX_DEPTH:
        raise CodecError(f'message nests deeper than {MAX_DEPTH} containers')
    if size > MAX_ITEMS:
        raise CodecError(f'message holds a container of {size} elements (more than {MAX_ITEMS})')
    return depth


def _out(value: Any, depth: int) -> Any:
    """The JSON-ready tree of `value` (depth: the containers around it)."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return value
    if kind is float:
        if not math.isfinite(value):
            raise CodecError(f'cannot encode {value!r}')
        return value
    if kind is bytes:
        return {'b': value.decode('latin-1')}
    if kind is tuple:
        inner = _enter(depth, len(value))
        return [_out(item, inner) for item in value]
    if kind is list:
        inner = _enter(depth, len(value))
        return {'l': [_out(item, inner) for item in value]}
    if kind is dict:
        inner = _enter(depth, len(value))
        out = {}
        for key, item in value.items():
            if type(key) is not str:
                raise CodecError(f'cannot encode a dict key of type {type(key).__name__}')
            out[key] = _out(item, inner)
        return {'d': out}
    if kind is Token:
        _enter(depth, 3)
        address = value.address
        if not (type(value.typeid) is str and type(value.id) is str and type(address) is tuple
                and len(address) == 2 and type(address[0]) is str and type(address[1]) is int):
            raise CodecError(f'cannot encode token {value!r}')
        return {'k': [value.typeid, [address[0], address[1]], value.id]}
    if isinstance(value, BaseException):
        _enter(depth, 2)
        if kind in (Empty, Full) or (kind is RemoteError and value.args):
            return {'e': [kind.__name__, [str(arg) for arg in value.args]]}
        text = '' if kind is RemoteError else str(value)  # Here a RemoteError has no text, and no str()
        return {'e': ['RemoteError', [f'{kind.__name__}: {text}']]}
    raise CodecError(f'cannot encode a value of type {kind.__name__}')


def _in(node: Any, depth: int) -> Any:
    """The value of a parsed JSON node (depth: the containers around it)."""
    kind = type(node)
    if node is None or kind is bool or kind is int or kind is str:
        return node
    if kind is float:
        if not math.isfinite(node):
            raise CodecError('an infinite number is not one this vocabulary carries')
        return node
    if kind is list:
        inner = _enter(depth, len(node))
        return tuple(_in(item, inner) for item in node)
    if kind is not dict or len(node) != 1:
        raise CodecError('a JSON object that is not one tag')
    (tag, value), = node.items()
    if tag == 'b':
        if type(value) is not str:
            raise CodecError('a "b" tag that is not a string')
        try:
            return value.encode('latin-1')
        except UnicodeEncodeError:
            raise CodecError('a "b" tag with a code point above U+00FF') from None
    if tag == 'l':
        if type(value) is not list:
            raise CodecError('an "l" tag that is not an array')
        inner = _enter(depth, len(value))
        return [_in(item, inner) for item in value]
    if tag == 'd':
        if type(value) is not dict:
            raise CodecError('a "d" tag that is not an object')
        inner = _enter(depth, len(value))
        return {key: _in(item, inner) for key, item in value.items()}
    if tag == 'k':
        _enter(depth, 3)
        if not (type(value) is list and len(value) == 3 and type(value[0]) is str and type(value[2]) is str
                and type(value[1]) is list and len(value[1]) == 2
                and type(value[1][0]) is str and type(value[1][1]) is int):
            raise CodecError('a "k" tag that is not a token')
        return Token(value[0], (value[1][0], value[1][1]), value[2])
    if tag == 'e':
        _enter(depth, 2)
        if not (type(value) is list and len(value) == 2 and type(value[0]) is str and type(value[1]) is list):
            raise CodecError('an "e" tag that is not an exception')
        name, args = value
        if name not in EXCEPTIONS:
            raise CodecError(f'an exception this vocabulary does not carry ({name[:64]!r})')
        if len(args) > MAX_ITEMS or not all(type(arg) is str for arg in args):
            raise CodecError('an exception whose arguments are not text')
        if name == 'RemoteError' and not args:
            raise CodecError('a remote error without text')
        return EXCEPTIONS[name](*args)
    raise CodecError(f'an unknown tag ({tag[:64]!r})')
