# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The queue's envelope codec on its own: the closed vocabulary, its limits and its refusals.

Every value in the vocabulary round-trips equal and of the same type; every frame outside it is
refused with `CodecError` before anything outside the vocabulary is built; nothing outside it can be
encoded, and a refused encoding writes nothing. Every message shape HyperShell's roles send passes the
limits, which refuse one level deeper and one element more. The fixed reply to a peer of an earlier
release is pinned byte for byte.
"""


# Type annotations
from __future__ import annotations
from typing import Any

# Standard libs
import io
import json
import queue
import pickle
from multiprocessing.managers import Token, RemoteError

# External libs
from pytest import mark, raises, param

# Internal libs
from hypershell.core import codec
from hypershell.core.codec import (encode, decode, CodecError, PickleFrame, MAX_DEPTH, MAX_ITEMS,
                                   OLD_PEER_TEXT, OLD_PEER_REPLY)
from hypershell.core.queue import QUEUE_EXPOSED, DISPATCH_EXPOSED, PROTOCOL, make_sentinel


pytestmark = mark.unit


def same(a: Any, b: Any) -> bool:
    """Whether `a` and `b` are equal and of the same type all the way down (exceptions by class and args)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return list(a) == list(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, BaseException):
        return a.args == b.args
    if isinstance(a, Token):
        return (a.typeid, a.address, a.id) == (b.typeid, b.address, b.id)
    return a == b


VALUES = [
    param(None, id='none'), param(True, id='true'), param(False, id='false'), param(0, id='zero'),
    param(-7, id='negative'), param(2 ** 200, id='big-int'), param(1.5, id='float'), param(-0.0, id='negative-zero'),
    param(1e308, id='large-float'), param('', id='empty-str'), param('text é ☃ \U0001f600', id='unicode'),
    param('\udc80 lone surrogate', id='surrogate'), param(b'', id='empty-bytes'), param(bytes(range(256)), id='all-bytes'),
    param(make_sentinel(), id='sentinel'), param((), id='empty-tuple'), param((1, 'a', None), id='tuple'),
    param([], id='empty-list'), param([1, [2, (3,)]], id='list'), param({}, id='empty-dict'),
    param({'a': 1, 'b': [b'x'], 'c': {'d': (True,)}}, id='dict'), param({'l': 1, 'd': 2, 'b': 3}, id='tag-like-keys'),
    param(Token('_get_scheduled', ('127.0.0.1', 50001), '1a2b'), id='token'),
    param(queue.Empty(), id='empty'), param(queue.Full(), id='full'), param(RemoteError('text'), id='remote-error'),
]


@mark.parametrize('value', VALUES)
def test_round_trip(value: Any) -> None:
    """Every vocabulary value, alone and inside a message, comes back equal and of the same type."""
    assert same(decode(encode(value)), value)
    message = ('#RETURN', value)
    assert same(decode(encode(message)), message)


def test_bool_stays_apart_from_int() -> None:
    """`True` never comes back as 1, nor 1 as `True` (`(True,) == (1,)` would pass a protocol check)."""
    assert type(decode(encode((True,)))[0]) is bool
    assert type(decode(encode((1,)))[0]) is int
    assert decode(encode((PROTOCOL,))) == (PROTOCOL,) and type(decode(encode((PROTOCOL,)))[0]) is int


def test_tuple_stays_apart_from_list() -> None:
    """A tuple comes back a tuple and a list a list."""
    assert type(decode(encode((1, 2)))) is tuple
    assert type(decode(encode([1, 2]))) is list


def test_bytes_cross_byte_for_byte() -> None:
    """Every byte value crosses as itself, as one code point per byte."""
    for value in range(256):
        assert decode(encode(bytes([value]))) == bytes([value])
    assert decode(encode(bytes(range(256)) * 3)) == bytes(range(256)) * 3


def test_other_exceptions_cross_as_remote_error() -> None:
    """Any other exception becomes `RemoteError('<Type>: <text>')`; the three carried ones cross as themselves."""
    got = decode(encode(('#ERROR', ValueError('task_done() called too many times'))))
    assert type(got[1]) is RemoteError and got[1].args == ('ValueError: task_done() called too many times',)
    got = decode(encode(('#ERROR', KeyError('1a2b'))))
    assert type(got[1]) is RemoteError and got[1].args == ("KeyError: '1a2b'",)
    assert type(decode(encode(('#ERROR', queue.Empty())))[1]) is queue.Empty
    assert type(decode(encode(('#ERROR', queue.Full())))[1]) is queue.Full


def test_remote_error_always_carries_text() -> None:
    """A remote error crosses with at least one text, which is how its receiver reports it: one without text
    is refused on decoding, and one raised without text is sent with its type's name."""
    with raises(CodecError):
        decode(b'["#ERROR", {"e": ["RemoteError", []]}]')
    got = decode(encode(('#ERROR', RemoteError())))[1]
    assert type(got) is RemoteError and got.args == ('RemoteError: ',)
    assert 'RemoteError' in str(got)


def test_traceback_text_crosses_verbatim() -> None:
    """A traceback's text crosses unchanged (a stale proxy is read from its last line)."""
    text = "Traceback (most recent call last):\n  File \"x\", line 1, in f\nKeyError: '10a2cf820'\n"
    assert decode(encode(('#TRACEBACK', text))) == ('#TRACEBACK', text)


def test_frame_is_ascii_json_of_a_tuple() -> None:
    """A message's frame is ASCII JSON whose first byte is `[`, never a pickle opcode."""
    frame = encode((None, 'dummy', (), {}))
    assert frame == b'[null,"dummy",[],{"d":{}}]'
    assert frame.isascii() and frame[:1] == b'['


def test_decode_takes_any_buffer() -> None:
    """A frame decodes from bytes, a memoryview, or the buffer of a received frame."""
    frame = encode(('#RETURN', b'x'))
    for buf in (frame, bytearray(frame), memoryview(frame), io.BytesIO(frame).getbuffer()):
        assert decode(buf) == ('#RETURN', b'x')


def nested(depth: int) -> Any:
    """A tuple nesting `depth` containers, counting itself."""
    value: Any = 1
    for _ in range(depth):
        value = (value,)
    return value


def nested_json(depth: int) -> bytes:
    return b'[' * depth + b'1' + b']' * depth


# Each frame is refused for its shape alone.
REFUSED = [
    param(b'', id='empty'),
    param(b'\x80\x04\x95', id='pickle-frame'),
    param(b'[null, "dummy", [{"z": 1}], {"d": {}}]', id='unknown-tag'),
    param(b'[{"l": [], "d": {}}]', id='two-key-tag'),
    param(b'[{}]', id='no-key-object'),
    param(b'[{"d": [[1, "one"]]}]', id='non-str-key'),
    param(b'[{"l": {"a": 1}}]', id='list-tag-not-array'),
    param(b'[{"b": 1}]', id='bytes-tag-not-string'),
    param(b'[{"b": "\\u0100"}]', id='bytes-above-ff'),
    param(b'[{"k": ["t", ["h", "1"], "i"]}]', id='token-port-not-int'),
    param(b'[{"k": ["t", ["h", 1]]}]', id='token-short'),
    param(b'[{"k": ["t", "h:1", "i"]}]', id='token-address-not-pair'),
    param(b'[{"e": ["SystemExit", []]}]', id='exception-unknown'),
    param(b'[{"e": ["Empty", [1]]}]', id='exception-args-not-text'),
    param(b'[{"e": ["Empty"]}]', id='exception-short'),
    param(b'[{"e": ["RemoteError", []]}]', id='remote-error-without-text'),
    param(b'[NaN]', id='nan'),
    param(b'[Infinity]', id='infinity'),
    param(b'[-Infinity]', id='negative-infinity'),
    param(b'[1e400]', id='overflow-float'),
    param(b'["\xff"]', id='bad-utf8'),
    param(b'[1, ', id='bad-json'),
    param(b'[1] []', id='trailing-data'),
    param(b'[' + b'9' * 5000 + b']', id='huge-int'),
    param(nested_json(MAX_DEPTH + 1), id='depth-9'),
    param(b'[' + b','.join([b'0'] * (MAX_ITEMS + 1)) + b']', id='65-elements'),
    param(b'[{"l": [' + b','.join([b'0'] * (MAX_ITEMS + 1)) + b']}]', id='65-element-list'),
    param(b'[{"d": {' + b','.join(b'"%d": 0' % i for i in range(MAX_ITEMS + 1)) + b'}}]', id='65-key-dict'),
    param(b'[' * 100_000 + b']' * 100_000, id='past-parser-depth'),
]


@mark.parametrize('frame', REFUSED)
def test_decode_refuses(frame: bytes) -> None:
    """Every frame outside the vocabulary is refused with `CodecError`."""
    with raises(CodecError):
        decode(frame)


def test_pickle_frame_is_refused_as_such(monkeypatch) -> None:
    """A frame in the earlier, pickle framing is refused as `PickleFrame`, and is never unpickled."""
    reached = []
    monkeypatch.setattr(pickle, 'loads', lambda *args, **kwargs: reached.append(args))
    for frame in (pickle.dumps(('#RETURN', None), protocol=2), pickle.dumps((None, 'dummy', (), {}), protocol=5)):
        with raises(PickleFrame):
            decode(frame)
    assert reached == []
    assert issubclass(PickleFrame, CodecError) and issubclass(CodecError, ValueError)


def test_decode_builds_nothing_outside_the_vocabulary() -> None:
    """A refused frame raises before anything is returned; an accepted one holds only vocabulary types."""
    allowed = (type(None), bool, int, float, str, bytes, tuple, list, dict, Token, queue.Empty, queue.Full, RemoteError)

    def walk(value: Any) -> None:
        assert type(value) in allowed, type(value)
        if isinstance(value, (tuple, list)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                assert type(key) is str
                walk(item)

    for value in VALUES:
        walk(decode(encode(('#RETURN', value.values[0]))))


class Opaque:
    """An object outside the vocabulary."""


class Text(str):
    """A `str` subclass, which is not a `str` here."""


class Blob(bytes):
    """A `bytes` subclass, which is not `bytes` here."""


@mark.parametrize('value', [
    param({1, 2}, id='set'), param(frozenset(), id='frozenset'), param(Opaque(), id='object'),
    param({1: 'a'}, id='int-key'), param({('a',): 1}, id='tuple-key'), param(float('nan'), id='nan'),
    param(float('inf'), id='infinity'), param(Text('x'), id='str-subclass'), param(Blob(b'x'), id='bytes-subclass'),
    param(bytearray(b'x'), id='bytearray'), param(1j, id='complex'), param(Token('t', '/a/path', 'i'), id='token-path'),
    param(10 ** 5000, id='huge-int'), param(nested(MAX_DEPTH + 1), id='depth-9'),
    param(tuple(range(MAX_ITEMS + 1)), id='65-elements'), param(list(range(MAX_ITEMS + 1)), id='65-element-list'),
    param({str(i): i for i in range(MAX_ITEMS + 1)}, id='65-key-dict'), param(Opaque, id='class'), param(len, id='function'),
])
def test_encode_refuses(value: Any) -> None:
    """A value outside the vocabulary or past a limit cannot be encoded, inside a message or alone."""
    with raises(CodecError):
        encode(value)
    with raises(CodecError):
        encode(('#RETURN', value))


def test_refused_encoding_writes_nothing() -> None:
    """Encoding returns the whole frame or raises: there is no partial frame to write."""
    written = []
    try:
        written.append(encode((None, 'put', (b'payload', Opaque()), {})))
    except CodecError:
        pass
    assert written == []


def test_limits() -> None:
    """The limits admit depth 8 and 64 elements, and refuse one more of either, both ways."""
    assert (MAX_DEPTH, MAX_ITEMS) == (8, 64)
    assert decode(encode(nested(MAX_DEPTH))) == nested(MAX_DEPTH)
    assert decode(nested_json(MAX_DEPTH)) == nested(MAX_DEPTH)
    wide = tuple(range(MAX_ITEMS))
    assert decode(encode(wide)) == wide
    for value in (nested(MAX_DEPTH + 1), tuple(range(MAX_ITEMS + 1))):
        with raises(CodecError):
            encode(value)
    for frame in (nested_json(MAX_DEPTH + 1), json.dumps(list(range(MAX_ITEMS + 1))).encode()):
        with raises(CodecError):
            decode(frame)


TASKS = b'[' + b', '.join([b'{"id": "0123456789abcdef", "args": "echo 1", "submit_time": "2026-11-23"}'] * 2000) + b']'
TOKEN = Token('_get_scheduled', ('127.0.0.1', 50001), '1a2b3c')

# Every message shape HyperShell's roles send (and the manager's own paths they never take), at the
# largest bundle: a bundle, reply or heartbeat is one bytes or str leaf, whatever its size.
MESSAGES = [
    param((None, 'dummy', (), {}), id='dummy'),
    param((None, 'create', ('_get_scheduled', PROTOCOL), {}), id='create-scheduled'),
    param((None, 'create', ('_get_completed',), {}), id='create'),
    param(('#RETURN', ('1a2b3c', QUEUE_EXPOSED)), id='create-reply'),
    param(('#RETURN', ('1a2b3c', DISPATCH_EXPOSED)), id='create-dispatch-reply'),
    param((None, 'incref', ('1a2b3c',), {}), id='incref'),
    param((None, 'decref', ('1a2b3c',), {}), id='decref'),
    param((None, 'accept_connection', ('MainProcess|Thread-1',), {}), id='accept-connection'),
    param(('#RETURN', None), id='return-none'),
    param(('1a2b3c', 'put', (TASKS,), {'timeout': 2}), id='put-bundle'),
    param(('1a2b3c', 'get', (True, 2), {}), id='get'),
    param(('1a2b3c', 'get', (), {'block': True, 'timeout': 0.5}), id='get-kwargs'),
    param(('#RETURN', TASKS), id='get-reply'),
    param(('1a2b3c', 'request', (TASKS.decode(),), {}), id='dispatcher-request'),
    param(('#RETURN', TASKS.decode()), id='dispatcher-reply'),
    param(('1a2b3c', 'takes_submissions', (), {}), id='takes-submissions'),
    param(('#RETURN', True), id='bool-reply'), param(('#RETURN', 3), id='int-reply'),
    param(('#ERROR', queue.Empty()), id='empty'), param(('#ERROR', queue.Full()), id='full'),
    param(('#TRACEBACK', 'Traceback (most recent call last):\n...\nKeyError: \'1a2b\'\n'), id='traceback'),
    param(('#UNSERIALIZABLE', 'Traceback ...'), id='unserializable'),
    param(('#PROXY', (QUEUE_EXPOSED, TOKEN)), id='proxy'),
    param((None, 'get_methods', (TOKEN,), {}), id='get-methods'),
    param(('#RETURN', {'status': 'ok', 'value': 42}), id='getvalue'),
    param(('#RETURN', tuple(f'method{i}' for i in range(11))), id='public-methods'),
]


@mark.parametrize('message', MESSAGES)
def test_real_messages_pass_the_limits(message: Any) -> None:
    """Every real message shape passes the limits and round-trips, however large its bundle."""
    assert same(decode(encode(message)), message)


def test_old_peer_reply_is_pinned() -> None:
    """The fixed reply to an earlier release is this exact pickle (protocol 4) of an `#ERROR` reply carrying
    a `RuntimeError` with the fixed text; the test reads back our own constant, never a peer's bytes."""
    text = (b'This queue server runs HyperShell 2.9.0a10 or later, whose queue framing a release before '
            b'2.9.0a10 cannot read; run matching server and client versions')
    assert OLD_PEER_TEXT.encode() == text
    assert OLD_PEER_REPLY == (b'\x80\x04\x95\xc6\x00\x00\x00\x00\x00\x00\x00\x8c\x06#ERROR\x94\x8c\x08builtins\x94'
                              b'\x8c\x0cRuntimeError\x94\x93\x94\x8c\x97' + text + b'\x94\x85\x94R\x94\x86\x94.')
    kind, error = pickle.loads(OLD_PEER_REPLY)
    assert kind == '#ERROR' and type(error) is RuntimeError and error.args == (OLD_PEER_TEXT,)
    assert 'before 2.9.0a10' in OLD_PEER_TEXT and '\n' not in OLD_PEER_TEXT


def test_codec_only_dumps_pickles() -> None:
    """The codec's only use of `pickle` is to build the fixed reply: it never loads one."""
    source = open(codec.__file__, encoding='utf-8').read()
    assert source.count('pickle.') == 1 and 'pickle.dumps(' in source
    for name in ('loads', 'load', 'Unpickler', '_ForkingPickler'):
        assert f'pickle.{name}' not in source and f' {name}(' not in source
    for func in (codec.encode, codec.decode, codec._out, codec._in):
        assert 'pickle' not in func.__code__.co_names
