"""Common utility functions."""

from datetime import datetime
from typing import Any
import json
import logging
import re

_LOGGER = logging.getLogger(__name__)

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def json_loads(s: str) -> object:
    """Load JSON from string and parse timestamps."""
    return json.loads(s, object_hook=obj_parser)


def obj_parser(obj: dict[str, Any]) -> dict[str, Any]:
    """Parse datetime strings to datetime objects."""
    for key, val in obj.items():
        try:
            obj[key] = datetime.strptime(val, DATETIME_FORMAT)
        except (TypeError, ValueError):
            pass  # Value is not a datetime string, keep original
    return obj


def find_path(
    src: dict | list, path: str | list | None, _original_path: str | list | None = None
) -> Any:
    """Return data at path in source.

    Simple navigation of a hierarchical dict/list structure using dot notation.

    Args:
        src: Dictionary or list to search
        path: Dot-separated path (e.g., 'a.b.c') or list of keys
        _original_path: Internal parameter to track the full original path for error logging

    Returns:
        Value at path or None if not found

    Examples:
        >>> find_path({"a": 1}, "a")
        1
        >>> find_path({"a": {"b": 1}}, "a.b")
        1
        >>> find_path({"a": [1, 2]}, "a.0")
        1
        >>> find_path({"a": 1}, "b")
    """
    # Store the original path on first call
    if _original_path is None:
        _original_path = path

    try:
        if not path:
            return src
        if isinstance(path, str):
            path = path.split(".")
        if isinstance(src, list):
            try:
                f = float(path[0])
                if f.is_integer() and len(src) > 0:
                    return find_path(src[int(f)], path[1:], _original_path)
                raise KeyError("Key not found")
            except ValueError as valerr:
                raise KeyError(f"{path[0]} should be an integer") from valerr
            except IndexError as idxerr:
                raise KeyError("Index out of range") from idxerr
        return find_path(src[path[0]], path[1:], _original_path)
    except KeyError:
        # Format the original path for display
        original_path_str = _original_path
        if isinstance(_original_path, list):
            original_path_str = ".".join(str(p) for p in _original_path)

        # Get the current path segment that failed
        failed_segment = path[0] if path and len(path) > 0 else "unknown"

        _LOGGER.debug(
            "'%s' not found in the original path '%s'",
            failed_segment,
            original_path_str,
        )
        return None


def is_valid_path(src: dict | list, path: str | list | None) -> bool:
    """Check if path exists in source.

    Examples:
        >>> is_valid_path({"a": 1}, "a")
        True
        >>> is_valid_path({"a": 1}, "b")
        False
        >>> is_valid_path({"a": [{"b": True}]}, "a.0.b")
        True
    """
    try:
        if not path:
            return True
        if isinstance(path, str):
            path = path.split(".")
        if isinstance(src, list):
            f = float(path[0])
            if f.is_integer() and len(src) > 0:
                return is_valid_path(src[int(f)], path[1:])
            return False
        return is_valid_path(src[path[0]], path[1:])
    except (KeyError, ValueError, IndexError):
        return False


_CAMEL_CASE_PATTERN = re.compile(r"((?<!_)[A-Z])")


def camel2slug(s: str) -> str:
    """Convert camelCase to snake_case.

    Examples:
        >>> camel2slug("fooBar")
        'foo_bar'
        >>> camel2slug("FooBar")
        'foo_bar'
    """
    return _CAMEL_CASE_PATTERN.sub(r"_\1", s).lower().strip("_ \n\t\r")


def make_url(url: str, **kwargs: str) -> str:
    """Replace placeholders in a URL with given keyword arguments.

    Supports both {key} and $key placeholder styles.

    Args:
        url: URL template with placeholders
        **kwargs: Placeholder values

    Returns:
        URL with all placeholders replaced

    Raises:
        ValueError: If URL is empty or contains unreplaced placeholders

    Examples:
        >>> make_url("https://api.example.com/users/{id}", id="123")
        'https://api.example.com/users/123'
    """
    if not url:
        raise ValueError("URL cannot be empty")

    # Replace both `{key}` and `$key` placeholders
    for key, value in kwargs.items():
        placeholder1 = f"{{{key}}}"
        placeholder2 = f"${key}"
        url = url.replace(placeholder1, str(value)).replace(placeholder2, str(value))

    # Check if any unreplaced placeholders remain
    if "{" in url or "}" in url:
        raise ValueError(f"Unreplaced placeholders in URL: {url}")

    return url
