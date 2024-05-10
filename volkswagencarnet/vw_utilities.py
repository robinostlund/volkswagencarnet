"""Common utility functions."""

from datetime import datetime
import json
import logging
import re

_LOGGER = logging.getLogger(__name__)


def json_loads(s) -> object:
    """Load JSON from string and parse timestamps."""
    return json.loads(s, object_hook=obj_parser)


def obj_parser(obj: dict) -> dict:
    """Parse datetime."""
    for key, val in obj.items():
        try:
            obj[key] = datetime.strptime(val, "%Y-%m-%dT%H:%M:%S%z")
        except (TypeError, ValueError):
            """The value was not a date."""  # pylint: disable=pointless-string-statement
    return obj


def find_path_in_dict(src: dict | list, path: str | list) -> object:
    """Return data at path in dictionary source.

    Simple navigation of a hierarchical dict structure using XPATH-like syntax.

    >>> find_path_in_dict(dict(a=1), 'a')
    1

    >>> find_path_in_dict(dict(a=1), '')
    {'a': 1}

    >>> find_path_in_dict(dict(a=None), 'a')


    >>> find_path_in_dict(dict(a=1), 'b')
    Traceback (most recent call last):
    ...
    KeyError: 'b'

    >>> find_path_in_dict(dict(a=dict(b=1)), 'a.b')
    1

    >>> find_path_in_dict(dict(a=dict(b=1)), 'a')
    {'b': 1}

    >>> find_path_in_dict(dict(a=dict(b=1)), 'a.c')
    Traceback (most recent call last):
    ...
    KeyError: 'c'

    """
    if not path:
        return src
    if isinstance(path, str):
        path = path.split(".")
    if isinstance(src, list):
        try:
            f = float(path[0])
            if f.is_integer() and len(src) > 0:
                return find_path_in_dict(src[int(f)], path[1:])
            raise KeyError("Key not found")
        except ValueError as valerr:
            raise KeyError(f"{path[0]} should be an integer") from valerr
        except IndexError as idxerr:
            raise KeyError("Index out of range") from idxerr
    return find_path_in_dict(src[path[0]], path[1:])


def find_path(src: dict | list, path: str | list) -> object:
    """Return data at path in source."""
    try:
        return find_path_in_dict(src, path)
    except KeyError:
        _LOGGER.error(
            "Dictionary path: %s is no longer present. Dictionary: %s", path, src
        )
        return None


def is_valid_path(src, path):
    """Check if path exists in source.

    >>> is_valid_path(dict(a=1), 'a')
    True

    >>> is_valid_path(dict(a=1), '')
    True

    >>> is_valid_path(dict(a=1), None)
    True

    >>> is_valid_path(dict(a=1), 'b')
    False

    >>> is_valid_path({"a": [{"b": True}, {"c": True}]}, 'a.0.b')
    True

    >>> is_valid_path({"a": [{"b": True}, {"c": True}]}, 'a.1.b')
    False
    """
    try:
        find_path_in_dict(src, path)
    except KeyError:
        return False
    else:
        return True


def camel2slug(s: str) -> str:
    """Convert camelCase to camel_case.

    >>> camel2slug('fooBar')
    'foo_bar'

    Should not produce "__" in case input contains something like "Foo_Bar"
    """
    return re.sub("((?<!_)[A-Z])", "_\\1", s).lower().strip("_ \n\t\r")


def make_url(url: str, **kwargs: str) -> str:
    """Replace placeholders in a URL with given keyword arguments."""

    # Replace both `{key}` and `$key` in the URL
    for key, value in kwargs.items():
        placeholder1 = f"{{{key}}}"
        placeholder2 = f"${key}"
        url = url.replace(placeholder1, str(value)).replace(placeholder2, str(value))

    # Check if any unreplaced placeholders remain
    if "{" in url or "}" in url:
        raise ValueError("Not all placeholders were replaced in the URL")
    return url
