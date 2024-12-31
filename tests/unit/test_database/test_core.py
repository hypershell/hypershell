# SPDX-FileCopyrightText: 2024 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for database core module."""


# standard libs
import os

# external libs
import pytest

# internal libs
from hypershell.data.core import DatabaseURL, Namespace


class TestDatabaseURL:
    """Unit tests for `DatabaseURL`."""

    def test_missing_provider(self) -> None:
        with pytest.raises(AttributeError):
            DatabaseURL()

    def test_no_allow_positionals(self) -> None:
        with pytest.raises(TypeError):
            DatabaseURL('a', 'b')  # noqa

    def test_sqlite_no_allow_database(self) -> None:
        try:
            DatabaseURL(provider='sqlite', file='local.db', database='main')
        except AttributeError as error:
            message, = error.args
            assert message == 'Cannot provide both \'file\' and \'database\' for SQLite'
        else:
            assert False, 'Did not raise AttributeError'

    def test_sqlite_no_allow_field(self) -> None:
        fields = {'user': 'me', 'password': 'foo', 'host': 'localhost', 'port': 1234}
        for field, value in fields.items():
            try:
                DatabaseURL(provider='sqlite', file='local.db', **{field: value})
            except AttributeError as error:
                message, = error.args
                assert message == f'Cannot provide \'{field}\' for SQLite'
            else:
                raise AssertionError('Did not raise AttributeError')

    def test_no_allow_file(self) -> None:
        try:
            DatabaseURL(provider='postgres', file='local.db')
        except AttributeError as error:
            message, = error.args
            assert message == f'Cannot provide \'file\' if not SQLite'
        else:
            assert False, 'Did not raise AttributeError'

    def test_requires_database(self) -> None:
        try:
            DatabaseURL(provider='postgres')
        except AttributeError as error:
            message, = error.args
            assert message == f'Must provide \'database\' if not SQLite'
        else:
            assert False, 'Did not raise AttributeError'

    def test_requires_password_with_user(self) -> None:
        try:
            DatabaseURL(provider='postgres', database='main', user='me')
        except AttributeError as error:
            message, = error.args
            assert message == 'Must provide \'password\' if \'user\' provided'
        else:
            assert False, 'Did not raise AttributeError'

    def test_isolates_parameters(self) -> None:
        url = DatabaseURL(provider='postgres', database='main', encoding='utf-8')
        assert dict(url) == {'provider': 'postgres', 'database': 'main', 'file': None, 'user': None,
                             'password': None, 'host': None, 'port': None, 'parameters': {'encoding': 'utf-8'}}

    def test_encode_sqlite(self) -> None:
        url = DatabaseURL(provider='sqlite', file='local.db')
        assert url.encode() == 'sqlite:///local.db'

    def test_encode_basic(self) -> None:
        url = DatabaseURL(provider='postgres', database='main')
        assert url.encode() == 'postgres:///main'

    def test_encode_with_host(self) -> None:
        url = DatabaseURL(provider='postgres', database='main', host='other.net')
        assert url.encode() == 'postgres://other.net/main'

    def test_encode_with_user_and_password(self) -> None:
        url = DatabaseURL(provider='postgres', database='main', user='me', password='foo')
        assert url.encode() == 'postgres://me:foo@localhost/main'

    def test_encode_with_port(self) -> None:
        url = DatabaseURL(provider='postgres', database='main', port=4321)
        assert url.encode() == 'postgres://localhost:4321/main'

    def test_encode_with_parameters(self) -> None:
        url = DatabaseURL(provider='postgres', database='main', port=4321, a=1, b=2)
        assert url.encode() == 'postgres://localhost:4321/main?a=1&b=2'

    def test_repr(self) -> None:
        assert repr(DatabaseURL(provider='mysql', database='main', )) == (
            '<DatabaseURL(provider=\'mysql\', database=\'main\')>'
        )

    def test_repr_with_extras(self) -> None:
        assert repr(DatabaseURL(provider='mysql', database='main', a=1)) == (
            '<DatabaseURL(provider=\'mysql\', database=\'main\', a=1)>'
        )

    def test_repr_masks_password(self) -> None:
        assert repr(DatabaseURL(provider='mysql', database='main', user='me', password='foo')) == (
            '<DatabaseURL(provider=\'mysql\', database=\'main\', user=\'me\', password=\'****\')>'
        )

    def test_from_namespace(self) -> None:
        fields = {'provider': 'mariadb', 'database': 'main', 'user': 'me', 'password': 'foo'}
        assert DatabaseURL(**fields) == DatabaseURL.from_namespace(Namespace(fields))

    def test_from_namespace_with_expand_env(self) -> None:
        os.environ['PASSWORD'] = 'foo'
        fields = {'provider': 'mariadb', 'database': 'main', 'user': 'me', 'password_env': 'PASSWORD'}
        url = DatabaseURL.from_namespace(Namespace(fields))
        assert url.password == 'foo'

    def test_from_namespace_with_expand_eval(self) -> None:
        fields = {'provider': 'mariadb', 'database': 'main', 'user': 'me', 'password_eval': 'echo foo'}
        url = DatabaseURL.from_namespace(Namespace(fields))
        assert url.password == 'foo'
