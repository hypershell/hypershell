# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for data methods."""


# Standard libs
import os

# External libs
import pytest

# Internal libs
from hypershell.data.model import Task


@pytest.mark.unit
class TestTask:
    """Unit tests for `Task` methods."""

    def test_split_argline(self) -> None:
        """Test comment and inline-tag functionality on task argument lines."""
        assert ('', {}) == Task.split_argline('')
        assert ('', {}) == Task.split_argline('   ')
        assert ('', {}) == Task.split_argline('# Comment here')
        assert ('', {'a': '', 'b': 12, 'c': False}) == Task.split_argline('# HYPERSHELL a b:12 c:false')
        assert ('', {'d': '', 'e': 'special'}) == Task.split_argline('#HYPERSHELL d e:special')
        assert ('', {'d': '', 'e': 'special'}) == Task.split_argline('#HYPERSHELL: d e:special')
        assert ('echo "hello world"', {}) == Task.split_argline('echo "hello world" # Comment')
        assert ('echo "hello world"', {}) == Task.split_argline('echo "hello world" #Comment')
        assert ('echo "hello world"', {}) == Task.split_argline('echo "hello world" # Hypershell x')  # non-conforming
        assert ('echo "hello world"', {}) == Task.split_argline('echo "hello world" # hypershell x')  # non-conforming
        assert ('echo "hello world"', {}) == Task.split_argline('echo "hello world" # HS x')  # non-conforming
        assert (
            ('echo "hello world"', {'x': 42, 'y': True, 'z': ''}) ==
                Task.split_argline('echo "hello world" #HYPERSHELL x:42 y:true z')
        )
        assert (
                ('echo "hello world"', {'x': 42, 'y': True, 'z': ''}) ==
                Task.split_argline('echo "hello world" #HYPERSHELL: x:42 y:true z')
        )
        assert (
                ('echo "hello world"', {'x': 42, 'y': True, 'z': ''}) ==
                Task.split_argline('echo "hello world" # HYPERSHELL: x:42 y:true z')
        )

