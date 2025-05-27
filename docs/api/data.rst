.. _mod_data:

:mod:`hypershell.data`
========================

.. module:: hypershell.data
    :platform: Unix, Windows

|

Access to low-level database objects and methods.

-------------------

Functions
---------

|

.. autofunction:: initdb

|

.. autofunction:: checkdb

|

.. autofunction:: ensuredb

|

.. autofunction:: truncatedb

    .. warning::

        This action is destructive, cannot be reversed, and proceeds without confirmation.

|

.. autofunction:: vacuumdb

|

.. autofunction:: rotatedb

-------------------

Constants
---------

|

.. autodata:: DATABASE_ENABLED

.. autodata:: DATABASE_PROVIDER

|
