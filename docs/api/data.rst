.. _mod_data:

:mod:`hypershell.data`
========================

.. module:: hypershell.data
    :platform: Unix, Windows

|

Access to low-level database objects and methods.

The command-line programs check the database before they touch it. Python API callers
(e.g., :func:`hypershell.server.serve_from`) bypass that check, so run :func:`ensuredb` first;
it initializes the database or upgrades an existing one in place.

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

.. autofunction:: upgradedb

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
