The following environment variable may be set to harden the installation against
accidentally polluting the package resolution of HyperShell itself from user software.


``HYPERSHELL_PYTHONPATH``
    As a Python application HyperShell may be exposed to ``PYTHONPATH``.
    This can happen in instances where the user tasks themselves are Python based.
    To avoid possible collisions we can guard HyperShell by explicitly setting the
    path list (``sys.path``). This environment variable may be set in a similar
    fashion as the standard ``PYTHONPATH``. Or it may instead contain a file path,
    in which case the content of the file should have one path per line.