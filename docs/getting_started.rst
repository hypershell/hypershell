.. _getting_started:

Getting Started
===============


Installation
------------

`HyperShell` is built on Python and can be installed from the Python package index using ``pip``.
It is recommended however to isolate the application within its own virtual environment and only expose
the top-level entry point *script* on your `PATH`. The well-known `uv <https://docs.astral.sh/uv/>`_
utility handles all of this nicely for unprivileged users installing for themselves.

See the :ref:`installation <install>` guide for more options and additional notes and
recommendations.


.. tab:: uv

    .. code-block:: shell

        uv tool install hypershell

.. tab:: pipx

    .. code-block:: shell

        pipx install hypershell

.. tab:: homebrew

    .. code-block:: shell

        brew tap hypershell/tap
        brew install hypershell


-------------------

Features
--------

|

**Simple, Scalable**

Take a listing of shell commands and process them in parallel.
In this example, we use the ``-t`` option to specify a template for the input arguments
which are not fully formed shell commands. Larger workloads will want to use a database
for managing tasks and scheduling. Without having configured the database the program
will manage tasks entirely within memory.

.. admonition:: Hello World
    :class: note

    .. code-block:: shell

        seq 4 | hs cluster -t 'echo {}'

    .. details:: Output

        .. code-block:: none

            WARNING [hypershell.server] No database configured - automatically disabled
            0
            1
            2
            4

|

Scale out to remote servers with SSH and even define *groups* in your configuration file.
By default, all command `stdout` and `stderr` are joined and written out directly.
Capture individual task `stdout` and `stderr` with ``--capture``.
Set the :ref:`logging <logging>` level to ``INFO`` to see each task start or ``DEBUG`` to
see additional detail about what is running, where, and when.

.. admonition:: Distributed Cluster over SSH
    :class: note

    .. code-block:: shell

        hs cluster tasks.in -N16 --ssh-group=xyz --capture

    .. details:: Logs

        .. code-block:: none

            2022-03-14 12:29:19.659 a00.cluster.xyz   INFO [hypershell.client] Running task (5fb74a31-fc38-4535-8b45-c19bc3dbedee)
            2022-03-14 12:29:19.665 a01.cluster.xyz   INFO [hypershell.client] Running task (c1d32c32-3e76-48e0-b2c3-9420ea20b41b)
            2022-03-14 12:29:19.668 a02.cluster.xyz   INFO [hypershell.client] Running task (4a6e19ec-d325-468f-a55b-03a797eb51d5)
            2022-03-14 12:29:19.671 a03.cluster.xyz   INFO [hypershell.client] Running task (09587f55-4b50-4e2b-a528-55c60667b62a)
            2022-03-14 12:29:19.674 a04.cluster.xyz   INFO [hypershell.client] Running task (1336f778-c9ab-4111-810e-229d572be62e)

|

Use the provided launcher on HPC clusters to bring up workers within your job allocation.
Specify which program to use with the ``--launcher`` option. Achieve higher throughput by
aggregating tasks in bundles with ``-b``, ``--bundlesize``. Add a database configuration to
allow for retries with ``-r``, ``--max-retries``. Using a negative value for ``--delay-start``
causes the remote clients to sleep some random interval in seconds up to that value. In this
example we stagger the launch process over one minute.

.. admonition:: Distributed Cluster over Slurm
    :class: note

    .. code-block:: shell

        hs cluster tasks.in -N128 -b128 --launcher=srun --max-retries=2 --delay-start=-60 >task.out

    .. details:: Logs

        .. code-block:: none

            2022-03-14 12:29:19.659 a00.cluster.xyz   INFO [hypershell.client] Running task (5fb74a31-fc38-4535-8b45-c19bc3dbedee)
            2022-03-14 12:29:19.665 a01.cluster.xyz   INFO [hypershell.client] Running task (c1d32c32-3e76-48e0-b2c3-9420ea20b41b)
            2022-03-14 12:29:19.668 a02.cluster.xyz   INFO [hypershell.client] Running task (4a6e19ec-d325-468f-a55b-03a797eb51d5)
            2022-03-14 12:29:19.671 a03.cluster.xyz   INFO [hypershell.client] Running task (09587f55-4b50-4e2b-a528-55c60667b62a)
            2022-03-14 12:29:19.674 a04.cluster.xyz   INFO [hypershell.client] Running task (1336f778-c9ab-4111-810e-229d572be62e)


|

**Flexible**

One of several novel features of `HyperShell`, is the ability to independently
stand up the *server* on one machine and then connect to that server using a *client* from
a different environment.

Start the server with a bind address of ``0.0.0.0`` to allow remote connections.
The server schedules tasks on a distributed queue. It is recommended that you protect your instance
with a private *key* (``-k/--auth``).

.. admonition:: Server
    :class: note

    .. code-block:: shell

        hs server --forever --bind '0.0.0.0' --auth '<AUTHKEY>'


Connect to the running server from a different host (even from a different platform, e.g., Windows).
You can connect with any number of clients from any number of hosts. The separate client connections
will each pull tasks off the queue asynchronously, balancing the load.

.. admonition:: Client
    :class: note

    .. code-block:: shell

        hs client --host '<HOSTNAME>' --auth '<AUTHKEY>' --capture

|

**Dynamic**

Individual task metadata is exposed to tasks as environment variables. For example, ``TASK_ID`` provides
the UUID for the task, and ``TASK_SUBMIT_TIME`` records the date and time the task was submitted.

Any environment variable defined with the ``HYPERSHELL_EXPORT_`` prefix will be injected into
the environment of each task, *sans prefix*.

Use ``-t`` (short for ``--template``) to expand a template; ``{}`` can be used to insert the incoming
task arguments (alternatively, use ``TASK_ARGS``). Be sure to use single quotes to delay the variable
expansion. Many meta-patterns are supported (see full overview of :ref:`templates <templates>`):

* File operations (e.g., the basename ``'{/}'``)
* Slicing on whitespace (e.g., first ``'{[0]}'``, first three ``'{[:3]}'``, every other ``'{[::2]}'``)
* Sub-commands (e.g., ``'{% dirname @ %}'``)
* Lambda expressions in *x* (e.g., ``'{= x + 1 =}'``)

.. admonition:: Templates
    :class: note

    .. code-block:: shell

        hs cluster tasks.in -N12 -t './some_program.py {} >outputs/{/-}.out'

Capturing `stdout` and `stderr` is supported directly in fact with the ``--capture`` option.
See the full documentation for environment variables under :ref:`configuration <config>`.

Add arbitrary tags to one or whole collections of tasks to track additional context.

.. admonition:: Include user-defined tags
    :class: note

    .. code-block:: shell

        hs submit tasks.in --tag prod instr:B12 site:us-west-1 batch:12

    .. details:: Logs

        .. code-block:: none

            INFO [hypershell.submit] Submitted 20 tasks

|