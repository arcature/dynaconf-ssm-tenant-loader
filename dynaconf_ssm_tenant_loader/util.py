#+begin_src python
"""
Utility functions for the Dynaconf SSM tenant loader.
"""

from __future__ import annotations

import typing as t


def slashes_to_dict(data: t.Mapping[str, str]) -> dict:
    """
    Convert a mapping of slash-delimited relative keys into a nested
    dictionary, e.g. ``{"database/host": "x"}`` -> ``{"database": {"host": "x"}}``.

    :param data: A mapping of slash-delimited keys to values.
    :return: A nested dictionary representation of the input mapping.
    """

    result: dict = {}

    for key, value in data.items():
        segments = key.strip("/").split("/")
        cursor = result
        for segment in segments[:-1]:
            cursor = cursor.setdefault(segment, {})
        cursor[segments[-1]] = value

    return result


def pull_from_env_or_obj(key_name: str, env: t.Mapping, obj: t.Any) -> str | None:
    """
    Get a value from the process environment or the settings object,
    preferring the environment. Environment-sourced values are set back
    on the object for later introspection.

    :param key_name: The name of the key to retrieve.
    :param env: A mapping representing the process environment (e.g., ``os.environ``).
    :param obj: The settings object to retrieve from or set on.
    :return: The value of the key, or None if not found.
    """

    value: str | None = env.get(key_name)

    if value is None:
        value = obj.get(key_name)
    else:
        obj.set(key_name, value)

    return value
