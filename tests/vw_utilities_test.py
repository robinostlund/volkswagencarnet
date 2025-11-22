"""Utilities class tests."""

from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from unittest import TestCase

import pytest
from volkswagencarnet.vw_utilities import (
    camel2slug,
    is_valid_path,
    json_loads,
    make_url,
    obj_parser,
)


class UtilitiesTest(TestCase):
    """Test methods in utilities."""

    def test_camel_to_slug(self):
        """Test camel_to_slug conversion."""
        data = {
            "foo": "foo",
            "fooBar": "foo_bar",
            "XYZ": "x_y_z",
            "B4R": "b4_r",  # Should this actually be "b_4_r"? =)
            "Foo_Bar": "foo_bar",
            "_removeExtra_Underscores__": "remove_extra_underscores",
            "preserve___existing": "preserve___existing",
        }

        for key, expected in data.items():
            with self.subTest(msg=key, v=key):
                res = camel2slug(key)
                assert expected == res

    def test_is_valid_path(self):
        """Test that is_valid_path works as expected."""
        data = {
            "None": [None, None, True],
            "a in a": [{"a": 1}, "a", True],
            "b in a": [{"a": 1}, "b", False],
            "a.b in a.b": [{"a": {"b": 7}}, "a.b", True],
            "false path": [{"a": {"b": 7}}, False, True],
            "dict path": [{1, "a", "[[ :) ]]"}, {"crash": "me"}, False],
            "": [{"a": []}, datetime.now(), TypeError],
        }

        for test_name, test_case in data.items():
            with self.subTest(msg=test_name):
                source, path, expected = test_case

                if isinstance(expected, bool):
                    # If expected is a boolean, assert the result of is_valid_path
                    assert is_valid_path(source, path) == expected, (
                        f"Path validation error for '{path}' in '{source}'."
                    )
                else:
                    # If expected is an exception, assert it is raised
                    with pytest.raises(expected) as exc_info:
                        is_valid_path(source, path)
                    assert isinstance(exc_info.value, expected), (
                        f"Expected {expected.__name__}, but got {type(exc_info.value).__name__}. Exception: {str(exc_info.value)}"
                    )

    def test_is_valid_path_with_lists(self):
        """Test that is_valid_path can process lists."""
        assert is_valid_path({"a": [{"b": True}, {"c": True}]}, "a.0.b")
        assert not is_valid_path({"a": [{"b": True}, {"c": True}]}, "a.2")

    def test_obj_parser(self):
        """Test that the object parser works."""
        data = {
            "int": [0, AttributeError],
            "dict": [{"foo": "bar"}, {"foo": "bar"}],
            "dict with time": [
                {"foo": "2001-01-01T23:59:59Z"},
                {"foo": datetime(2001, 1, 1, 23, 59, 59, tzinfo=timezone.utc)},
            ],
            "dict with timezone": [
                {"foo": "2001-01-01T23:59:59+0200"},
                {
                    "foo": datetime(
                        2001, 1, 1, 23, 59, 59, tzinfo=timezone(timedelta(hours=2))
                    )
                },
            ],
        }

        for test_name, (input_data, expected_output) in data.items():
            with self.subTest(test_name=test_name):
                if isinstance(expected_output, dict):
                    res = obj_parser(input_data)
                    assert res == expected_output
                else:
                    with pytest.raises(expected_output):
                        obj_parser(input_data)

    def test_json_loads(self):
        """Test that json_loads works."""
        expected = {"foo": {"bar": "baz"}}
        actual = json_loads('{"foo":  {\n"bar":\t"baz"}}')
        assert expected == actual

        assert json_loads("42") == 42

        with pytest.raises(JSONDecodeError):
            json_loads("{[}")

        with pytest.raises(TypeError):
            json_loads(42)

    def test_make_url(self):
        """Test placeholder replacements."""
        assert make_url("foo/{bar}/baz{baz}", bar=2, baz="") == "foo/2/baz"
        assert make_url("foo/{baz}/$bar", bar=2, baz="asd") == "foo/asd/2"
