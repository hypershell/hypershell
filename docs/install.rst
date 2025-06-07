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
the package directly from the package index, the following extras are available

* ``postgres``: includes ``psycopg2`` for using PostgreSQL
* ``uuid7``: includes ``uuid-utils`` and to auto-enable use of UUIDv7 for task IDs.

For example, you could install HyperShell with the following:

.. admonition:: Install HyperShell with PostgreSQL support
    :class: note

    .. code-block:: shell

        uv tool install 'hypershell[postgres]' --python 3.13


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
        libexec/bin/pip install ./src
        libexec/bin/pip install psycopg2

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

On `Linux` and `macOS` platforms we provide shell completion definitions for Bash-like
environments (specialized ZShell completions coming-soon). As suggested by the LMOD
definition file included above, sourcing the ``/share/bash_completion.d/hs`` file enables
completions for the entire command-line interface.

Some completions are simple, like what options are available for the given subcommand.
Some completions are basic, such as ``--bind <tab>`` returning either ``localhost`` or ``0.0.0.0``.
Some are more sophisticated.

Some examples (but not everything):

* ``hs config get <tab>`` will autocomplete all options.
* ``hs config set OPT <tab>`` will autocomplete the current value for OPT.
* ``hs client ... --host <tab>`` will parse your host file and return possible known hosts.
* ``hs server ... --auth <tab>`` will auto-generate secure keys at random.
* ``hs server ... --port <tab>`` will select an available port on your machine.
* ``hs list <tab>`` will complete known fields.
* ``hs list ... -t <tab>`` will complete known tags in the database.
* ``hs list ... -t key:<tab>`` will complete known values for that key in the database.
* ``hsx ... --ssh-group <tab>`` will autocomplete known groups in your config.


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
Tests should pass for Python 3.9 and beyond.
These are largely lightweight tests in isolated ``HYPERSHELL_SITE`` directories
but can be slow because of the time to launch processes.
Use ``-n`` to parallelize tests.

.. admonition:: Run tests
    :class: note

    .. code-block:: shell

        uv run --python 3.13 pytest -v -n 8

|
