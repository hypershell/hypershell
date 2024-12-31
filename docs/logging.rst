.. _logging:

Logging
=======

|

Observability is an important feature in *HyperShell*. Logging is a big part of understanding
what is happening within your workflow. We want to know when events occur, like when something
starts, why something failed, or if a condition is met.

The program emits messages with different *levels* of severity. Unless otherwise configured,
the user will only see `WARNING`, `ERROR`, or `CRITICAL` messages. Setting the ``logging.level``
determines which messages are emitted; only messages with a severity equal to or greater than
the current level will be shown. For example, by default the level is set to `WARNING` and so
only messages with a severity equal to *or higher* than that will be shown.

See also the `logging` section in the :ref:`configuration <config>` parameter reference.

-------------------

Levels
------

|

All severity levels are shown and described below in order of highest to lowest level.

**CRITICAL**
    These message are only emitted when the program will halt. Typically this is at the
    very beginning because of an issue with command-line arguments. It could be due to a failure on the
    system or because the program was sent an *interrupt* signal.

**ERROR**
    These messages are emitted when a fault has occurred but the program does not
    need to halt. For example, when the program attempts to terminate a running task that has exceeded
    the configured walltime limit and that task fails to actually halt.

**WARNING**
    These messages are emitted in many circumstances where a notice to the user is warranted
    but not because of a failure in the program. For example, a non-zero exit status from one of
    the tasks is expected behavior but warrants notifying the user.

**INFO**
    These messages are only emitted by clients at the start of a task. The idea is that under
    normal, stable operations the user should only see one message per task.

**DEBUG**
    These messages are emitted for any number of events within the operations of the system.
    Anytime an action occurs, a *DEBUG* message is emitted. For example, server-side and client-side
    task bundle operations, thread start and stop, and program transitions.

**TRACE**
    These messages are emitted for higher frequency activity not included in *DEBUG*. These
    are typically cycling, waiting behavior. For example, individualized task movement in the system,
    as well as polling behavior.

|

.. note::

    For developers, there is yet a deeper level, `DEVEL`, unused and otherwise undocumented in the
    released software. Within the :mod:`hypershell.core.fsm` module, developers can
    enable these messages to log state transitions in all program threads along with a *fuzzer* to
    randomize delays in these transitions.

-------------------

Formatting
----------

|

The user has complete control of what is included in the messages and how they are structured and
formatted. *HyperShell* is written in the `Python programming language <https://python.org>`_
and uses the `standard logging facility <https://docs.python.org/3/library/logging.html>`_.
Messages can include many other
`contextual attributes <https://docs.python.org/3/library/logging.html#logrecord-attributes>`_
along side the message itself; we extend these to include a few others.

Defining these formats can be cumbersome and in the majority of cases users will not want to
fiddle with these as they are not human friendly. As such we've pre-defined a number of `styles`
to make it easier to switch between a number of standard formats.

Nevertheless, here is an example of setting a basic format.

.. admonition:: Configuration file with logging format
    :class: note

    .. code-block:: toml

        [logging]
        level = "info"
        format = "[%(asctime)s %(levelname)s] %(message)s"

Instead of defining the `format` directly, we can refer to one of the following `styles`.

.. table:: Standard attributes
    :widths: 25 75

    =======================    ==========================================================
    Style                      Format
    =======================    ==========================================================
    ``default``                ``%(ansi_bold)s%(ansi_level)s%(levelname)8s%(ansi_reset)s %(ansi_faint)s[%(name)s]%(ansi_reset)s %(message)s``
    ``detailed``               ``%(ansi_faint)s%(asctime)s.%(msecs)03d %(hostname)s %(ansi_reset)s %(ansi_level)s%(ansi_bold)s%(levelname)8s%(ansi_reset)s %(ansi_faint)s[%(name)s]%(ansi_reset)s %(message)s``
    ``detailed-compact``       ``%(ansi_faint)s%(elapsed_hms)s [%(hostname_short)s] %(ansi_reset)s %(ansi_level)s%(ansi_bold)s%(levelname)8s%(ansi_reset)s %(ansi_faint)s[%(relative_name)s]%(ansi_reset)s %(message)s``
    ``system``                 ``%(asctime)s.%(msecs)03d %(hostname)s %(levelname)8s [%(app_id)s] [%(name)s] %(message)s``
    =======================    ==========================================================

The ``default`` style is aptly named as it is the default format used by *HyperShell*. It includes
rich color and formatting (see note about ``NO_COLOR``). Only the level, module name, and the message
itself are included. This is a good starting point for basic work as all the other details are more
suited for batch, pipeline work and only gets in the way initially.

The ``detailed`` style expands on ``default`` to include a precise timestamp including milliseconds,
and the hostname of the machine the message originated from.

The ``detailed-compact`` includes the same information as ``detailed``, but in a compacted form.
The timestamp is relative elapsed time since program start, and both the module and hostname are
shorter/relative. So ``hypershell.`` is dropped from the module name and hostnames will only be the
specific node name if operating in a cluster environment within a given subnet (e.g., ``a123`` instead
of ``a123.cluster.univ.edu``).

The ``system`` format is similar to ``detailed`` but explicitly disables colorization and includes
the specific UUID of each instance of the program operating in the cluster. This format is useful when
operating as a system service.

|

.. note::

    The `ANSI` escape sequences injected into the logging output work well and are compatible with
    all major platforms, not only `UNIX`-like systems but also in the modern
    `Windows terminal <https://learn.microsoft.com/en-us/windows/terminal/>`_.

    These sequences are only emitted if and only if the connected `stderr` channel is a `TTY`.
    Essentially, if your process is connected to a live terminal session we allow formatting.
    Otherwise it is automatically disabled; e.g., in a UNIX-pipeline or redirect.

    If you like the available style you are using and simply do not want the colors and formatting,
    you can disable them manually by defining the ``NO_COLOR`` environment variable.
    See `no-color.org <https://no-color.org>`_ for an understanding of this convention.
    To make this change permanent, put this in your shell login profile (e.g., ``~/.bashrc``).

    Conversely, if the non-TTY aspect is disabling color but you want to keep them for whatever
    reason you can force colors regardless of the connected output channel by defining the
    ``FORCE_COLOR`` environment variable.

|

The following is a table of *extra* attributes defined by *HyperShell* beyond what is described
in the Python logging documentation.

.. table::
    :widths: 30 70

    =======================    ==========================================================
    Format                     Description
    =======================    ==========================================================
    ``%(app_id)s``             Application-level instance UUID.
                               Clients tend to be identified by their hostname, but that
                               may not be distinct at once or over time.

    ``%(hostname)s``           Hostname (e.g., ``a123.cluster.foo.edu``).

    ``%(hostname_short)s``     Shortened hostname (e.g., ``a123``).

    ``%(relative_name)s``      Module name without package (e.g., ``client`` instead of
                               ``hypershell.client``).

    ``%(elapsed)s``            Relative time elapsed since start of program formatted
                               as integer number of seconds.

    ``%(elapsed_ms)s``         Relative time elapsed since start of program formatted
                               as integer number of milliseconds.

    ``%(elapsed_delta)s``      Relative time elapsed since start of program formatted
                               in automatically (e.g., ``1 hr 2 sec``).

    ``%(elapsed_hms)s``        Relative time elapsed since start of program formatted
                               in hour, minutes, and seconds: ``HH::MM::SS``.

    ``%(ansi_level)s``         ANSI escape sequence associated with message level
                               (e.g., if the current message has  level `INFO` then
                               this will correspond to ``%(ansi_green)s``).

    ``%(ansi_reset)s``         ANSI escape sequence for `reset`.

    ``%(ansi_bold)s``          ANSI escape sequence for `bold`.

    ``%(ansi_faint)s``         ANSI escape sequence for `faint`.

    ``%(ansi_italic)s``        ANSI escape sequence for `italic`.

    ``%(ansi_underline)s``     ANSI escape sequence for `underline`.

    ``%(ansi_black)s``         ANSI escape sequence for `black`.

    ``%(ansi_red)s``           ANSI escape sequence for `red`.

    ``%(ansi_green)s``         ANSI escape sequence for `green`.

    ``%(ansi_yellow)s``        ANSI escape sequence for `yellow`.

    ``%(ansi_blue)s``          ANSI escape sequence for `blue`.

    ``%(ansi_magenta)s``       ANSI escape sequence for `magenta`.

    ``%(ansi_cyan)s``          ANSI escape sequence for `cyan`.

    ``%(ansi_white)s``         ANSI escape sequence for `white`.

    =======================    ==========================================================

-------------------

Uncaught Exceptions and Tracebacks
----------------------------------

|

If for whatever reason the program crashes with an unexpected fault, we stash the full Python
traceback in a file within the default logging directory. See the section on file system
paths under :ref:`configuration <config>` for details. This will be in the `system` location
if the program is run as root or the `user` location, unless the ``HYPERSHELL_SITE`` variable
is set, which will take precedence.

We always log a `CRITICAL` message with the path to the created file.

|
