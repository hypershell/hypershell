.. _mod_server:

:mod:`hypershell.server`
========================

.. module:: hypershell.server
    :platform: Unix, Windows

|

Directly embed the server either as a dedicated :class:`ServerThread` or using one of the provided
high-level functions, :meth:`serve_from`, :meth:`serve_file`, or :meth:`serve_forever`.

All of the parameters used below are largely the same in each instance. The library reads only
these settings from configuration (files or environment): ``server.attempts``, as a ``max_retries``
not given is ``server.attempts`` - 1, read when the function is called or the thread built; the
queue size (``server.queuesize``) and bundle maximum (``server.bundlemax``) of
:class:`ServerThread`, which no parameter sets; and the database connection. Everything else is
passed by the caller, and configuration does not override the defaults below.

.. versionchanged:: 2.9.0
   :pr:`119`: a ``max_retries`` not given follows ``server.attempts`` (none with the built-in
   default).

.. versionchanged:: 2.9.0
   Importing :mod:`hypershell` loads the configuration and holds each numeric key to its range
   (see the :ref:`parameter reference <config_params>`), so the settings read here are inside it.
   A value passed as an explicit argument is used as given: the ranges apply to the configuration
   and the command line, not to explicit arguments, except ``evict_after``, which is held to
   ``server.evict``'s range (10 s to 2,147,483,647 s) and raises ``RuntimeError`` outside it, however
   wide the number.

|

.. warning::

    While all parameters have reasonable defaults you should **always** provide your own
    cryptographically secure authentication key (named `auth` in all cases).

    See :const:`DEFAULT_AUTH`.

-------------------

Functions
---------

|

.. autofunction:: serve_from

|

.. autofunction:: serve_file

|

.. autofunction:: serve_forever

-------------------

Classes
-------

|

.. autoclass:: ServerThread
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

.. versionchanged:: 2.9.0
   :pr:`48`: the ``template`` and ``from_json`` parameters. :pr:`76`: ``start`` raises a failure
   to start the queue, such as an address already in use, in the caller.

|

.. warning::

    While nothing prevents you from creating more than one :class:`~hypershell.server.ServerThread`
    it should be considered an error to do so. The point of the thread is to provide for asynchronous
    operations not to allow for multiple instances. There are several unavoidable process-wide global
    considerations such as signal handling, resource tracking, and encryption key management that make
    it an error to spawn multiple instances of the thread with differing parameters.

-------------------

Constants
---------

|

.. autodata:: DEFAULT_BUNDLESIZE
.. autodata:: DEFAULT_EVICT
.. autodata:: DEFAULT_ATTEMPTS
.. autodata:: DEFAULT_EAGER_MODE
.. autodata:: DEFAULT_SERVER_POLL
.. autodata:: DEFAULT_BIND
.. autodata:: DEFAULT_PORT
.. autodata:: DEFAULT_AUTH

|
