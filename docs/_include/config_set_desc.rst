Set configuration option.

The value is written :ref:`by the key's type <config_value_types>`, the rule an environment variable
for the key is read by. A key that takes text holds the value as written, ``true``, ``null``,
``007`` and an empty value included. A whole number, a number or a boolean is converted, a boolean
from ``true``/``false``, ``yes``/``no``, ``on``/``off`` or ``1``/``0`` in any letter case; a value
that does not convert is refused with exit status 2, and nothing is written. A duration or a size
is written as a number when the value is one and as the text otherwise, and is checked by the
command that reads it. A key with no type of its own (an entry of ``database.connect_args`` or
``database.pragmas``, a ``[database]`` provider argument, or a path that names no documented key)
holds a TOML integer, float or boolean when the value is exactly one (``5``, ``2.5``, ``true``), and
the text otherwise. An empty value for a key that takes text is written as ``""`` and overrides the
key in a lower file. For an optional text key, such as ``database.user`` or ``server.bind``, an empty
variable would not: it is read as not given. A path or value that cannot be written as UTF-8 is
refused with exit status 2, and nothing is written.
