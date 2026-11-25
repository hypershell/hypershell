.. _mod_cluster:

:mod:`hypershell.cluster`
=========================

.. module:: hypershell.cluster
    :platform: Unix, Windows

|

Directly embed the full cluster using one of the available thread variants
(:class:`~hypershell.cluster.local.LocalCluster`, :class:`~hypershell.cluster.remote.RemoteCluster`,
:class:`~hypershell.cluster.remote.AutoScalingCluster`, or :class:`~hypershell.cluster.ssh.SSHCluster`)
or by using one of the provided high-level functions
(:meth:`~hypershell.cluster.local.run_local`, :meth:`~hypershell.cluster.remote.run_cluster`,
or :meth:`~hypershell.cluster.ssh.run_ssh`).

All of the parameters used below are largely the same in each instance. The library reads only
these settings from configuration (files or environment): ``server.attempts``, as a ``max_retries``
not given is ``server.attempts`` - 1, read when the function is called or the cluster built; the
server's queue size (``server.queuesize``) and bundle maximum (``server.bundlemax``), which no
parameter sets; for the clients :class:`~hypershell.cluster.local.LocalCluster` runs in-process,
the task working directory (``task.cwd``) and the task environment (``[export]``); for an SSH
cluster given no ``launcher_args``, ``ssh.args``; and the database connection (see below).
The other classes launch ``hs client`` processes, which read their own configuration for any
option not passed to them (cores, memory, timeouts, rate limit and the like); the launcher-mode
classes also give each launch the ``[export]`` environment. Otherwise everything is passed by the
caller.

.. versionchanged:: 2.9.0
   :pr:`119`: a ``max_retries`` not given follows ``server.attempts`` (none with the built-in
   default); before, each cluster class retried every failed task once, whatever the configuration.

|

.. note::

    The database connection details are only specified via configuration (files or environment).
    To control the database configuration programmatically inject environment variables before
    importing ``hypershell``.


.. versionchanged:: 2.9.0
   :pr:`125`: importing :mod:`hypershell` loads the configuration and holds each numeric key to its
   range (see the :ref:`parameter reference <config_params>`), so the settings read here are inside
   it. A value passed as an explicit argument is used as given: the ranges apply to the
   configuration and the command line, not to explicit arguments, except ``evict_after``, which is
   held to ``server.evict``'s range (10 s to 2,147,483,647 s) and raises ``RuntimeError`` outside
   it, however wide the number.

-------------------

Functions
---------

|

.. autofunction:: hypershell.cluster.local.run_local

|

.. autofunction:: hypershell.cluster.remote.run_cluster

|

.. autofunction:: hypershell.cluster.ssh.run_ssh

-------------------

Classes
-------

|

.. versionchanged:: 2.9.0
   Each cluster class takes a ``from_json`` parameter (:pr:`48`) and a ``zone`` parameter
   (:pr:`78`).

.. autoclass:: hypershell.cluster.local.LocalCluster
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

|

.. autoclass:: hypershell.cluster.remote.RemoteCluster
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

|

.. autoclass:: hypershell.cluster.remote.AutoScalingCluster
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

.. versionchanged:: 2.9.0
   :pr:`82`: the ``evict_after`` parameter.

|

.. autoclass:: hypershell.cluster.ssh.SSHCluster
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

|

.. warning::

    While nothing prevents you from creating more than one of these cluster-mode thread instances
    it should be considered an error to do so. The point of the thread is to provide for asynchronous
    operations not to allow for multiple instances. There are several unavoidable process-wide global
    considerations such as signal handling, resource tracking, and encryption key management that make
    it an error to spawn multiple instances of the thread with differing parameters.

-------------------

Constants
---------

|

.. autodata:: hypershell.cluster.remote.DEFAULT_LAUNCHER
.. autodata:: hypershell.cluster.remote.DEFAULT_REMOTE_EXE
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_LAUNCHER
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_POLICY
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_PERIOD
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_FACTOR
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_MIN_SIZE
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_MAX_SIZE
.. autodata:: hypershell.cluster.remote.DEFAULT_AUTOSCALE_INIT_SIZE

|
