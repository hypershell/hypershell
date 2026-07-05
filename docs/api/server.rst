.. _mod_server:

:mod:`hypershell.server`
========================

.. module:: hypershell.server
    :platform: Unix, Windows

|

Directly embed the server either as a dedicated :class:`ServerThread` or using one of the provided
high-level functions, :meth:`serve_from`, :meth:`serve_file`, or :meth:`serve_forever`.

All of the parameters used below are largely the same in each instance. Configuration files have
no impact when using the library; i.e., default values are not overridden by configuration.

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
