.. _mod_client:

:mod:`hypershell.client`
========================

.. module:: hypershell.client
    :platform: Unix, Windows

|

Directly embed client as either a dedicated :class:`ClientThread`
or using the provided high-level :meth:`run_client` function.

All of the parameters used below are largely the same in each instance. Configuration files have
no impact when using the library; i.e., default values are not overridden by configuration.

-------------------

Functions
---------

|

.. autofunction:: run_client

-------------------

Classes
-------

|

.. autoclass:: ClientThread
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

|

.. admonition:: Important Note
    :class: warning

    While nothing prevents you from creating more than one :class:`~hypershell.client.ClientThread`
    it should be considered an error to do so. The point of the thread is to provide for asynchronous
    operations not to allow for multiple instances. There are several unavoidable process-wide global
    considerations such as signal handling, resource tracking, and encryption key management that make
    it an error to spawn multiple instances of the thread with differing parameters.

-------------------

Constants
---------

|

.. autodata:: DEFAULT_NUM_THREADS
.. autodata:: DEFAULT_TEMPLATE
.. autodata:: DEFAULT_BUNDLESIZE
.. autodata:: DEFAULT_BUNDLEWAIT
.. autodata:: DEFAULT_SIGNALWAIT
.. autodata:: DEFAULT_HEARTRATE
.. autodata:: DEFAULT_DELAY
.. autodata:: DEFAULT_HOST
.. autodata:: DEFAULT_PORT
.. autodata:: DEFAULT_AUTH

|
