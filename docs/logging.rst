.. _logging:

Logging
=======

|

Observability is an important feature in *HyperShell*. Logging is a big part of understanding
what is happening within your workflow. We want to know when events occur, like when something
starts, why something failed, or if a condition is met.

The program emits messages with different *levels* of severity. Unless otherwise configured,
the user will see `INFO`, `WARNING`, `ERROR`, or `CRITICAL` messages. Setting the ``logging.level``
determines which messages are emitted; only messages with a severity equal to or greater than
the current level will be shown. For example, by default the level is set to `INFO` and so
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

File-based Logging
------------------

|

By default *HyperShell* writes log messages only to the console (``stderr``). For
long-running servers and clusters, and for unattended batch pipelines, it is often
preferable to *also* persist messages to disk with automatic rotation and compression
so the logs never grow without bound. File-based logging is opt-in and operates
independently of the console: it has its own severity level and format, so you can keep
a quiet, human-friendly console while retaining a verbose, machine-parsable record on disk.

File-based logging is enabled the moment *any* ``logging.file`` parameter is set. The
simplest form enables it with all defaults, writing to the default per-process file
described below (rotation and compression disabled):

.. admonition:: Enable file-based logging
    :class: note

    .. code-block:: toml

        [logging]
        file = "enabled"    # or true, or an explicit path such as "/var/log/hypershell/hs.log"

For full control, define the ``[logging.file]`` section and set individual parameters:

.. admonition:: Configuration file with rotating, compressed logs
    :class: note

    .. code-block:: toml

        [logging.file]
        level    = "debug"     # captured to disk independently of the console level
        rotate   = "512MB"     # rotate once the active file reaches 512 MiB
        compress = "gzip"      # compress each rotated file in the background
        keep     = 2           # keep 2 uncompressed rotations alongside the archives

|

Per-process log files
~~~~~~~~~~~~~~~~~~~~~~~

|

Every process writes to its *own* file, named for its role and host. This is essential
in a distributed cluster: many clients — potentially on shared storage — would otherwise
contend for a single rotating file, renaming, compressing, and pruning it out from under
one another. The default file for each process is:

.. table::
    :widths: 40 60

    ==============================   ==========================================
    Process                          Default file
    ==============================   ==========================================
    ``hs server``                    ``server-<host>.log``
    ``hs cluster`` / ``hsx``         ``cluster-<host>.log``
    ``hs client``                    ``client-<host>.log``
    ``hs submit``                    ``submit-<host>.log``
    other commands, library use      ``main.log``
    ==============================   ==========================================

Here ``<host>`` is the short hostname. Files are written into the ``log`` directory of
your :ref:`site <config>`. If two processes of the same role run on one host at once, the
second claims a numbered slot (``client-<host>-2.log``, ``-3``, and so on); when a process
exits — even by crashing — its slot is immediately reused by the next one, so the number of
files tracks *peak concurrency on a host*, not the total number of processes ever launched
(important under autoscaling). Because names never collide across roles or hosts, the entire
log directory can be shipped or merged with ordinary tools — e.g. ``rsync`` to collect
per-node logs, or ``sort -m`` to interleave them into a single timeline.

If you set an explicit ``path`` it is honored verbatim, with one exception: for the
``client`` role — which is launched en masse across a cluster — the hostname is appended so
the mass-launched clients still never share a file.

|

Parameters
~~~~~~~~~~~

|

``path``
    Destination file path (default: the per-process file described above). A relative path
    is resolved against the current working directory; parent directories are created as needed.

``level``
    Minimum severity written to the file (default: ``trace``). Accepts the same names as
    ``logging.level``. This is independent of the console level, so the default captures
    *everything* to disk while the console stays at ``info``.

``style``
    Message format preset for the file (default: ``system``). Accepts the same presets as
    ``logging.style`` (``default``, ``detailed``, ``detailed-compact``, ``system``); a
    ``format`` string may be given instead. ANSI color is always stripped from file output.
    The default ``system`` style stamps every line with a timestamp, hostname, and instance
    ``app_id``, which is what makes merged, multi-file logs sortable and traceable.

``rotate``
    Rotation policy (default: ``never``). See `Rotation`_ below.

``compress``
    Compression applied to rotated files (default: none). See `Compression and retention`_ below.

``keep``
    Number of *uncompressed* rotated files to retain on disk (default: ``0``). See
    `Compression and retention`_ below.

|

Rotation
~~~~~~~~~

|

The ``rotate`` policy accepts three kinds of value:

``never``
    No rotation (the default); the file grows indefinitely.

*size-like*
    A byte threshold such as ``512MB`` or ``2GB``: the active file is rotated once it
    reaches that size. Accepted units are ``KB``, ``MB``, ``GB``, and ``TB`` — each a power
    of 1024 — and a bare number is interpreted as bytes. Rotated files are numbered
    (``name.1``, ``name.2``, …).

*time-like*
    A cron expression (requires the ``croniter`` package) such as ``@hourly``, ``@daily``,
    ``@midnight``, or ``0 1 * * 0``: the file is rotated on that schedule. ``@daily`` and
    ``@midnight`` name rotated files by date (``name.YYYYMMDD``); any other expression also
    includes the time (``name.YYYYMMDD-HHMMSS``).

Regardless of policy, sending ``SIGHUP`` to a process triggers an immediate rotation of its
log file. This is the conventional way to rotate on demand — for example from an external
scheduler or a ``logrotate``-style tool. See the :ref:`signals <config>` section.

|

Compression and retention
~~~~~~~~~~~~~~~~~~~~~~~~~~~

|

When ``compress`` is set, each rotated file is compressed in the background. Accepted formats
(and the extension applied) are ``gzip`` (``.gz``), ``bzip`` (``.bz2``), and ``lzma``
(``.xz``) from the Python standard library, plus ``zstd`` (``.zstd``), which requires the
optional ``zstandard`` package.

The ``keep`` parameter sets how many *uncompressed* rotated files to leave on disk; older
uncompressed rotations beyond this count are removed once they have been compressed. The
compressed archives themselves are always retained.

|

.. note::

    Rotated files are only ever deleted when compression is enabled. With ``compress`` unset,
    rotated files accumulate and ``keep`` has no effect — enable compression, or prune the
    directory yourself, to bound disk usage.

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
