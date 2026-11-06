.. _mod_submit:

:mod:`hypershell.submit`
========================

.. module:: hypershell.submit
    :platform: Unix, Windows

|

Directly embed submit capabilities either as a dedicated :class:`SubmitThread`, :class:`LiveSubmitThread`,
or using one of the provided high-level functions, :meth:`submit_from`, :meth:`submit_file`.

All of the parameters used below are largely the same in each instance. Configuration files have
no impact when using the library; i.e., default values are not overridden by configuration.

-------------------

Functions
---------

|

.. autofunction:: submit_from

.. versionchanged:: 2.9.0
   :pr:`48`: ``source`` may yield dictionaries as well as strings, as ``--from-json`` does.

|

.. autofunction:: submit_file

-------------------

Classes
-------

|

.. versionchanged:: 2.9.0
   :pr:`48`: ``source`` on both classes may yield dictionaries as well as strings.

.. autoclass:: SubmitThread
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

|

.. autoclass:: LiveSubmitThread
    :show-inheritance:

    .. automethod:: new
    .. automethod:: start
    .. automethod:: join
    .. automethod:: stop

-------------------

Constants
---------

|

.. autodata:: DEFAULT_BUNDLESIZE
.. autodata:: DEFAULT_BUNDLEWAIT
.. autodata:: DEFAULT_TASK_GROUP

|
