.. _cli_submit:

Submit
======

Usage
-----

.. include:: ../_include/submit_usage.rst

Description
-----------

.. include:: ../_include/submit_desc.rst

|

.. versionadded:: 2.9.0
   ``--from-json`` (:pr:`48`); ``--repeat`` and ``--update`` (:pr:`50`); ``--zone`` (:pr:`78`).

.. versionchanged:: 2.9.0
   :pr:`50`: submitting a named task file that was already submitted is refused unless
   ``--repeat`` or ``--update`` is given. :pr:`78`: ``-q``/``--queue`` needs a server started
   with ``--no-db``.

.. versionchanged:: 2.9.0
   A key on the command line is refused; ``--queue`` takes it from the environment or a
   configuration file.

.. versionchanged:: 2.9.0
   ``-w``/``--bundlewait`` takes a duration with units or a Slurm time, as ``-W`` does, and a
   signed value is refused.

.. include:: ../_include/submit_help.rst
