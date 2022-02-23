"""Common utility functions."""
import json
import logging
import re
from datetime import datetime
from itertools import product
from os import environ as env
from os.path import join, dirname, expanduser
from sys import argv
from typing import Any, TextIO, Union

_LOGGER = logging.getLogger(__name__)


def read_config() -> dict:
    """Read config from file."""
    for directory, filename in product(
        [
            dirname(argv[0]),
            expanduser("~"),
            env.get("XDG_CONFIG_HOME", join(expanduser("~"), ".config")),
        ],
        ["vw.conf", ".vw.conf"],
    ):
        try:
            config_file = join(directory, filename)
            _LOGGER.debug("checking for config file %s", config_file)
            config: TextIO
            with open(config_file) as config:
                return dict(x.split(": ") for x in config.read().strip().splitlines() if not x.startswith("#"))
        except OSError:
            continue
    return {}


def json_loads(s) -> Any:
    """Load JSON from string and parse timestamps."""
    return json.loads(s, object_hook=obj_parser)


def obj_parser(obj: dict) -> dict:
    """Parse datetime."""
    for key, val in obj.items():
        try:
            obj[key] = datetime.strptime(val, "%Y-%m-%dT%H:%M:%S%z")
        except (TypeError, ValueError):
            """The value was not a date."""
    return obj


def find_path(src: Union[dict, list], path: Union[str, list]) -> Any:
    """
    Return data at path in source.

    Simple navigation of a hierarchical dict structure using XPATH-like syntax.

    >>> find_path(dict(a=1), 'a')
    1

    >>> find_path(dict(a=1), '')
    {'a': 1}

    >>> find_path(dict(a=None), 'a')


    >>> find_path(dict(a=1), 'b')
    Traceback (most recent call last):
    ...
    KeyError: 'b'

    >>> find_path(dict(a=dict(b=1)), 'a.b')
    1

    >>> find_path(dict(a=dict(b=1)), 'a')
    {'b': 1}

    >>> find_path(dict(a=dict(b=1)), 'a.c')
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
                return find_path(src[int(f)], path[1:])
            raise KeyError("Key not found")
        except ValueError:
            raise KeyError(f"{path[0]} should be an integer")
        except IndexError:
            raise KeyError("Index out of range")
    return find_path(src[path[0]], path[1:])


def is_valid_path(src, path):
    """
    Check if path exists in source.

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
        find_path(src, path)
        return True
    except KeyError:
        return False


def camel2slug(s: str) -> str:
    """Convert camelCase to camel_case.

    >>> camel2slug('fooBar')
    'foo_bar'

    Should not produce "__" in case input contains something like "Foo_Bar"
    """
    return re.sub("((?<!_)[A-Z])", "_\\1", s).lower().strip("_ \n\t\r")


def make_url(url: str, **kwargs):
    """Replace placeholders in URLs."""
    for a in kwargs:
        url = url.replace("{" + a + "}", str(kwargs[a]))
        url = url.replace("$" + a, str(kwargs[a]))
    if "{" in url or "}" in url:
        raise ValueError("Not all values were substituted")
    return url


# TODO: is VW using 273.15 or 273? :)
def celsius_to_vw(val: float) -> int:
    """Convert Celsius to VW format."""
    return int(5 * round(2 * (273.15 + val)))


def fahrenheit_to_vw(val: float) -> int:
    """Convert Fahrenheit to VW format."""
    return int(5 * round(2 * (273.15 + (val - 32) * 5 / 9)))


def vw_to_celsius(val: int) -> float:
    """Convert Celsius to VW format."""
    return round(2 * ((val / 10) - 273.15)) / 2


# TODO: are F ints of floats?
def vw_to_fahrenheit(val: int) -> int:
    """Convert Fahrenheit to VW format."""
    return int(round((val / 10 - 273.15) * 9 / 5 + 32))
