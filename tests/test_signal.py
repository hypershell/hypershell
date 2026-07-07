# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for signal name <-> exit_status mapping."""


# Standard libs
import signal

# External libs
from pytest import mark, raises

# Internal libs
from hypershell.core.signal import SIGNAL_EXIT_STATUS, exit_status_for_signal


@mark.unit
class TestSignalExitStatus:
    """Unit tests for `exit_status_for_signal` and `SIGNAL_EXIT_STATUS`."""

    def test_known_signals(self) -> None:
        """Signals resolve to the negative of their number (subprocess convention)."""
        assert exit_status_for_signal('TERM') == -15
        assert exit_status_for_signal('KILL') == -9
        assert exit_status_for_signal('INT') == -2
        assert exit_status_for_signal('HUP') == -1

    def test_prefix_and_case_insensitive(self) -> None:
        """Names accept an optional `SIG` prefix and any case."""
        assert exit_status_for_signal('SIGTERM') == -15
        assert exit_status_for_signal('sigterm') == -15
        assert exit_status_for_signal('Term') == -15
        assert exit_status_for_signal(' kill ') == -9

    def test_unknown_signal_raises(self) -> None:
        """An unknown signal name raises ValueError."""
        with raises(ValueError):
            exit_status_for_signal('BOGUS')
        with raises(ValueError):
            exit_status_for_signal('SIGNOPE')

    def test_map_matches_platform(self) -> None:
        """The map agrees with the platform signal table for a ubiquitous signal."""
        assert SIGNAL_EXIT_STATUS['SIGTERM'] == -int(signal.SIGTERM)
        # Every recorded value is the negation of a real signal number.
        assert all(status < 0 for status in SIGNAL_EXIT_STATUS.values())
