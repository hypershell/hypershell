.. _install:

Installation
============

|

Installing *HyperShell* can take several forms. At the end of the day it is a Python package
and needs to live within some prefix and be tied to some Python runtime. As a system utility
we probably do not want to expose our dependencies to other user environments incidentally.
For these reasons, it is recommended to isolate *HyperShell* within its own virtual environment
and only exposed the top-level entry point *script* to the users `PATH`.

-------------------

Basic Installation
------------------

|

The `uv <https://docs.astral.sh/uv/>`_ utility wraps all of this up nicely for user-level
installations. On any platform, if installing for yourself, especially if you lack root
or administrative privileges, we recommend the following.

.. admonition:: Install HyperShell using uv
    :class: note

    .. code-block:: shell

        uv tool install hypershell


For `macOS` users we can accomplish the same thing with `Homebrew <https://brew.sh>`_.
This formula essentially does the same thing but managed by ``brew`` instead.


.. admonition:: Install HyperShell using Homebrew
    :class: note

    .. code-block:: shell

        brew tap hypershell/tap
        brew install hypershell


The `macOS` Homebrew installation method automatically includes the package extras needed
for using PostgreSQL as a backend but does not include the UUIDv7 extra. When installing
the package directly from the package index, the following optional `extras` are available:

* ``postgres``: PostgreSQL support via `psycopg (v3) <https://www.psycopg.org>`_, using
  self-contained binary wheels (no compiler or system libraries needed). See
  `PostgreSQL Support`_ below for the ``postgres-system`` and ``postgres-c`` variants
  intended for production deployments.
* ``uuid7``: install `uuid-utils <https://pypi.org/project/uuid-utils/>`_ to auto-enable
  UUIDv7 task identifiers. Without this extra, task IDs use the standard-library UUIDv4.
* ``zstd``: enable `Zstandard <https://facebook.github.io/zstd/>`_ as a log-rotation
  compression option (``logging.compress = "zstd"``).
* ``cron``: enable cron-style, time-based log-rotation schedules via
  `croniter <https://pypi.org/project/croniter/>`_ (e.g., ``logging.rotate = "@midnight"``).

Extras can be combined, e.g. ``'hypershell[postgres,uuid7]'``.

For example, you could install HyperShell with PostgreSQL support using the following:

.. admonition:: Install HyperShell with PostgreSQL support
    :class: note

    .. code-block:: shell

        uv tool install 'hypershell[postgres]' --python 3.13


-------------------

Python Versions and Dependencies
--------------------------------

|

*HyperShell* supports CPython 3.11 through 3.14 on Linux, macOS, and Windows. Installing from
the Python Package Index uses pre-built binary `wheels` for every dependency, so a normal
install requires **no C compiler or Rust toolchain** on any supported version. On Linux the
wheels target ``manylinux_2_28`` (glibc ≥ 2.28), which covers current enterprise
distributions including Rocky, Alma, and RHEL 8 and newer.

The dependency version floors declared by *HyperShell* are intentionally conservative so the
package stays installable from system packages on long-term-support distributions (such as
RHEL and EPEL); ``pip`` and ``uv`` still resolve to the newest compatible releases when
installing from the package index.

.. admonition:: Python 3.15
    :class: warning

    Python 3.15 is not yet supported. It remains a pre-release, and several native
    dependencies do not yet publish 3.15 wheels — installing there would fall back to
    building from source. Support will be added once those wheels are available upstream.

-------------------

PostgreSQL Support
------------------

|

By default *HyperShell* uses SQLite and needs no additional packages. PostgreSQL support is
provided through `psycopg (v3) <https://www.psycopg.org>`_ (the ``postgresql+psycopg``
SQLAlchemy dialect). Three `extras` install the same functionality but differ in where the C
``libpq`` client library — and the TLS/OpenSSL stack it links against — comes from. Choose
based on whether you are optimizing for ease of installation or for production hardening.

.. list-table::
    :header-rows: 1
    :widths: 22 12 22 44

    * - Extra
      - Compiler
      - ``libpq`` source
      - Use when
    * - ``postgres``
      - not needed
      - bundled in the wheel
      - Quickstart and development. Fully self-contained binary wheels; nothing to install at
        the system level. The bundled ``libpq`` and OpenSSL do **not** receive operating-system
        security updates.
    * - ``postgres-system``
      - not needed
      - system ``libpq``
      - Production and long-lived servers. Uses the operating-system ``libpq`` so that ``libpq``
        and OpenSSL are patched by your OS. Requires ``libpq`` to be installed
        (``dnf install libpq`` or ``apt install libpq5``). This is the flavor the EPEL/RPM
        package maps onto the distribution ``python3-psycopg3``.
    * - ``postgres-c``
      - required
      - system ``libpq``
      - Maximum performance. Compiles the psycopg C extension against the system ``libpq``.
        Requires a build toolchain and headers (``dnf install gcc libpq-devel``, which provides
        ``pg_config``).

.. admonition:: Easy install — self-contained, no system dependencies
    :class: note

    .. code-block:: shell

        uv tool install 'hypershell[postgres]'

.. admonition:: Production install — OS-patched libpq
    :class: note

    .. code-block:: shell

        sudo dnf install libpq          # or: sudo apt install libpq5
        uv tool install 'hypershell[postgres-system]'

Connection details, and TLS options such as ``sslmode`` and ``sslrootcert``, are covered on
the :ref:`database <database>` page.

-------------------

Linux Packages
--------------

|

We want to support Linux package managers directly to make it easy to use HyperShell as a system-level
tool with minimal effort to add to your environment. The following Linux distributions are currently
being considered but may or may not be available currently.

|

Debian / Ubuntu (coming soon)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. admonition:: Install as a Debian package
    :class: note

    .. code-block:: shell

        sudo apt install hypershell

|

Ubuntu (coming soon)
^^^^^^^^^^^^^^^^^^^^

Installing as a `snap` allows for a self-contained package and even control over the
level of `confinement`, unlike other container formats. This is particularly nice for
Ubuntu-like distributions on small devices (such as Raspberry Pi).

.. admonition:: Install as a Snap package
    :class: note

    .. code-block:: shell

        sudo snap install hypershell

|

Fedora / Alma / Rocky / RHEL
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

We are now shipping in `Fedora <https://packages.fedoraproject.org/pkgs/hypershell/hypershell/>`_
(Fedora 42, Fedora Rawhide, EPEL 10.0, EPEL 10.1).

Note `issue 35 <https://github.com/hypershell/hypershell/issues/35>`_ where ``/usr/bin/hs`` is in
conflict with another `package <https://crates.io/crates/heatseeker>`_. For the time being,
the ``hs`` command is renamed to ``hsh`` (though this is not ideal and may change later).

.. admonition:: Install from EPEL
    :class: note

    .. code-block:: shell

        sudo dnf install hypershell

.. admonition:: Install from EPEL
    :class: note

    .. code-block:: shell

        sudo yum install hypershell


-------------------

Docker and Apptainer
--------------------

|

The project includes both a ``Dockerfile`` and ``Apptainer`` build definition.
Using the software within a container is of limited utility however as the entire point
of HyperShell is to run other programs (and in a distributed fashion).
Deploying the `server` as a Docker container (e.g., within a `Kubernetes` environment)
is indeed useful though as it allows you to run it in a persistent fashion alongside of an HPC
environment.

In a purely containerized environment HyperShell can be included in your own container image.
HyperShell has both improved ergonomics and performance over scheduling "jobs" directly
within a Kubernetes-like environment (e.g., ``kubectl``),
with much better throughput and responsiveness.

The fact that your configuration can be entirely defined in terms of environment variables
makes it easy to setup your deployment without needing to embed the configuration.


-------------------

HPC Module (LMOD)
-----------------

|

System administrators may want to install and expose `HyperShell` in a custom location.
On something like an HPC cluster this could be an entirely different file system.
Let us assume this is the case, and that we already have our own Python installation
managed by some `module` system.

Here we will create an isolated prefix for the installation with version number included
and only expose the entry-point scripts to users, along with shell completions and the
manual page. Some desired runtime, ``python3.13``, is already loaded.

If there is not already a preferred Python module it might be easier to use
`conda-forge <https://conda-forge.org>`_ (or `mamba <https://mamba.readthedocs.io>`_)
or ``uv`` directly to create a virtual environment.

.. admonition:: Create installation manually on a shared system
    :class: note

    .. code-block:: shell

        mkdir -p /apps/x86_64-any/hypershell/$VERSION
        cd /apps/x86_64-any/hypershell/$VERSION

        git clone --depth=1 --branch=$VERSION https://github.com/hypershell/hypershell src

        python3.13 -m venv libexec
        libexec/bin/pip install './src[postgres]'

        mkdir -p bin
        ln -sf ../libexec/bin/hs bin/hs
        ln -sf ../libexec/bin/hsx bin/hsx
        ln -sf src/share

|

Based on this installation, a simple `LMOD <https://lmod.readthedocs.io/en/latest/>`_
configuration file might then be:

.. admonition:: Module file definition (e.g., /etc/module/x86_64-any/hypershell/<VERSION>.lua)
    :class: note

    .. code-block:: lua

        local appname = "hypershell"
        local version = "<version>" -- replace with actual version
        local appsdir = "/apps/x86_64-any"
        local modroot = pathJoin(appsdir, appname, version)

        whatis("Name: HyperShell")
        whatis("Version: " .. version)
        whatis("Description: A cross-platform, high-throughput computing utility for processing
        shell commands over a distributed, asynchronous queue.")

        prepend_path("PATH", pathJoin(modroot, "bin"))
        prepend_path("MANPATH", pathJoin(modroot, "share", "man"))
        prepend_path("FPATH", pathJoin(modroot, "share", "zsh", "site-functions"))  -- Zsh completions

        -- Raw source b/c `complete -F _hs hs` does not persist with source_sh
        execute { cmd="source " .. pathJoin(modroot, "share", "bash_completion.d", "hs"), modeA={"load"} }

Presumably, users would then be able to activate the software by loading the module as such.

.. admonition:: Load module
    :class: note

    .. code-block:: shell

        module load hypershell

------

Runtime Package Resolution
--------------------------

|

.. include:: _include/config_pythonpath.rst

For example, from the above installation we might add the following to our module:

.. admonition:: Extra setting for LMOD
    :class: note

    .. code-block:: lua

        ...
        prepend_path("HYPERSHELL_PYTHONPATH", pathJoin(modroot, "frozen-python.path"))
        ...

And we can include the following paths in our frozen set.

.. admonition:: Contents of ``frozen-python.path``
    :class: note

    .. code-block:: shell

        /apps/x86_64-any/hypershell/<version>/libexec/lib/python3.13
        /apps/x86_64-any/hypershell/<version>/libexec/lib/python3.13/lib-dynload
        /apps/x86_64-any/hypershell/<version>/libexec/lib/python3.13/site-packages


------

Shell Completions
-----------------

|

On `Linux` and `macOS` platforms we provide tab-completion definitions for both **Bash** and
**Zsh**, covering the entire command-line interface for the ``hs`` and ``hsx`` commands.

When installing from the package index (``pip`` / ``uv``), the completion files are placed under
the environment prefix automatically:

* Bash — ``<prefix>/share/bash-completion/completions/{hs,hsx}``
* Zsh  — ``<prefix>/share/zsh/site-functions/_hs``

The completions call the ``hs`` program to compute dynamic values (available fields, tags, ports,
configuration values, and so on), so the ``hs`` entry-point script must be on your ``PATH`` for
completion to work.

.. admonition:: The completion scripts require ``hs`` on ``PATH``
    :class: warning

    Dynamic completions shell out to ``hs`` (e.g. ``hs list --fields``). In a development
    checkout where only ``uv run hs`` works, completion produces nothing until the package is
    installed such that ``hs`` resolves on ``PATH``.

|

Bash
^^^^

Completion requires the `bash-completion <https://github.com/scop/bash-completion>`_ package
(version 2.x), which most distributions install and enable from your shell startup files by
default. With the file at the standard location above it is loaded automatically the first time
you complete an ``hs`` command. To enable it from an arbitrary location, drop it into your user
completions directory or source it directly.

.. admonition:: Enable Bash completions manually
    :class: note

    .. code-block:: shell

        # user-level (bash-completion autoloads by command name)
        install -Dm644 share/bash_completion.d/hs ~/.local/share/bash-completion/completions/hs

        # ... or source it directly from ~/.bashrc
        source /path/to/share/bash_completion.d/hs

|

Zsh
^^^

The Zsh completion is an autoloaded ``#compdef`` function. Place the directory containing
``_hs`` on your ``fpath`` before ``compinit`` runs. When installed from the package index this
is ``<prefix>/share/zsh/site-functions`` (already on ``fpath`` for a system install; for a
virtual-environment or ``uv tool`` prefix, add it explicitly). Do not ``source`` the file — Zsh
loads it on demand from ``fpath``.

.. admonition:: Enable Zsh completions
    :class: note

    .. code-block:: shell

        # in ~/.zshrc, before `compinit`
        fpath=(/path/to/prefix/share/zsh/site-functions $fpath)
        autoload -Uz compinit && compinit

|

Examples
^^^^^^^^

Both shells surface the same information. Completion ranges from the simple (which options a
subcommand accepts, e.g. ``--bind <tab>`` offering ``localhost`` or ``0.0.0.0``) to the dynamic:

* ``hs config get <tab>`` completes configuration keys.
* ``hs config set OPT <tab>`` suggests the current value for ``OPT``.
* ``hs client ... --host <tab>`` parses your host files for known hosts.
* ``hs server ... --auth <tab>`` generates a secure random key.
* ``hs server ... --port <tab>`` selects an available port on your machine.
* ``hs list <tab>`` completes known task fields.
* ``hs list ... -t <tab>`` completes tag keys present in the database.
* ``hs list ... -t key:<tab>`` completes known values for that tag key.
* ``hsx ... --ssh-group <tab>`` completes SSH nodelist groups from your config.

.. admonition:: Completions that query the database
    :class: note

    A few completions (tags, task IDs, available ports) run a live ``hs`` query on each key
    press. Against a large database or a remote PostgreSQL backend these add a small latency to
    completion; the Zsh definitions cache results briefly per session to reduce repeat queries.


-------------------

Development
-----------

|

As a library dependency, `HyperShell` can easily be added to your project using whatever package
tooling you like. For development of `HyperShell` itself, contributors should create their own fork
of the repository on `GitHub <https://github.com/hypershell/hypershell>`_ and clone the fork locally.
We use `uv <https://docs.astral.sh/uv/>`_ for managing the development environment. The
``uv.lock`` file is included in the repository, simply run the following command to initialize
your virtual environment.

.. admonition:: Install development dependencies inside local forked repository
    :class: note

    .. code-block:: shell

        uv sync --all-packages --python 3.13


Unit and integration tests can be run using `pytest <https://pytest.org>`_.
Tests should pass for Python 3.11 through 3.14.
These are largely lightweight tests in isolated ``HYPERSHELL_SITE`` directories
but can be slow because of the time to launch processes.
Use ``-n`` to parallelize tests.

.. admonition:: Run tests
    :class: note

    .. code-block:: shell

        uv run --python 3.13 pytest -v -n 8

|
