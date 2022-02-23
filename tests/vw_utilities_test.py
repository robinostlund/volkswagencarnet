"""Utilities class tests."""
from datetime import datetime, timezone, timedelta
from json import JSONDecodeError
from unittest import TestCase, mock
from unittest.mock import DEFAULT

from volkswagencarnet.vw_utilities import (
    camel2slug,
    is_valid_path,
    obj_parser,
    json_loads,
    read_config,
    make_url,
    fahrenheit_to_vw,
    vw_to_celsius,
    vw_to_fahrenheit,
    celsius_to_vw,
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
        for v in data:
            with self.subTest(msg=v, v=v):
                res = camel2slug(v)
                self.assertEqual(data[v], res)

    def test_is_valid_path(self):
        """Test that is_valid_path works as expected."""
        data = {
            "None": [None, None, True],
            "a in a": [{"a": 1}, "a", True],
            "b in a": [{"a": 1}, "b", False],
            "a.b in a.b": [{"a": {"b": 7}}, "a.b", True],
            "false path": [{"a": {"b": 7}}, False, True],
            "dict path": [{1, "a", "[[ :) ]]"}, {"crash": "me"}, False],
            # "list with True path": [[1, "a", None], True, False], # FIXME
            "": [{"a": []}, datetime.now(), TypeError],
        }

        for v in data:
            with self.subTest(msg=data[v]):
                try:
                    if isinstance(data[v][2], bool):
                        self.assertEqual(
                            data[v][2],
                            is_valid_path(data[v][0], data[v][1]),
                            msg=f"Path validation error for {data[v][1]} in {data[v][0]}",
                        )
                    else:
                        with self.assertRaises(data[v][2], msg=data[v]):
                            is_valid_path(data[v][0], data[v][1])
                except Exception as e:
                    if isinstance(e, AssertionError):
                        raise
                    self.fail(f"Wrong exception? Got {type(e)} but expected {data[v][2]}")

    def test_is_valid_path_with_lists(self):
        """Test that is_valid_path can process lists."""
        self.assertTrue(is_valid_path({"a": [{"b": True}, {"c": True}]}, "a.0.b"))
        self.assertFalse(is_valid_path({"a": [{"b": True}, {"c": True}]}, "a.2"))

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
                {"foo": datetime(2001, 1, 1, 23, 59, 59, tzinfo=timezone(timedelta(hours=2)))},
            ],
        }
        for v in data:
            if isinstance(data[v][1], dict):
                res = obj_parser(data[v][0])
                self.assertEqual(data[v][1], res)
            else:
                with self.assertRaises(data[v][1]):
                    obj_parser(data[v][0])

    def test_json_loads(self):
        """Test that json_loads works."""
        expected = {"foo": {"bar": "baz"}}
        actual = json_loads('{"foo":  {\n"bar":\t"baz"}}')
        self.assertEqual(expected, actual)

        self.assertEqual(42, json_loads("42"))

        with self.assertRaises(JSONDecodeError):
            json_loads("{[}")
        with self.assertRaises(TypeError):
            json_loads(42)

    def test_read_config_success(self):
        """Successfully read configuration from a file."""
        read_data = """
# Comment
foo: bar
"""
        mock_open = mock.mock_open(read_data=read_data)
        with mock.patch("builtins.open", mock_open):
            res = read_config()
            self.assertEqual({"foo": "bar"}, res)

    def test_read_config_error(self):
        """Success on second file, but parse error."""
        read_data = """
foo: bar
baz
"""
        mock_open = mock.mock_open(read_data=read_data)
        mock_open.side_effect = [IOError, DEFAULT]
        with mock.patch("builtins.open", mock_open):
            with self.assertRaises(ValueError):
                read_config()

    def test_read_config_not_found(self):
        """Empty config on no file found."""
        mock_open = mock.mock_open()
        mock_open.side_effect = IOError
        with mock.patch("builtins.open", mock_open):
            self.assertEqual({}, read_config())

    def test_make_url(self):
        """Test placeholder replacements."""
        self.assertEqual("foo/2/baz", make_url("foo/{bar}/baz{baz}", bar=2, baz=""))
        self.assertEqual("foo/asd/2", make_url("foo/{baz}/$bar", bar=2, baz="asd"))

    def test_celcius_to_vw(self):
        """Test Celsius conversion."""
        self.assertEqual(2730, celsius_to_vw(0))
        self.assertEqual(2955, celsius_to_vw(22.4))
        self.assertEqual(2960, celsius_to_vw(22.7))

    def test_fahrenheit_to_vw(self):
        """Test Fahrenheit conversion."""
        self.assertEqual(2730, fahrenheit_to_vw(32))
        self.assertEqual(2955, fahrenheit_to_vw(72.3))
        self.assertEqual(2960, fahrenheit_to_vw(72.9))

    def test_vw_to_celcius(self):
        """Test Celsius conversion."""
        self.assertEqual(0, vw_to_celsius(2730))
        self.assertEqual(22.5, vw_to_celsius(2955))
        self.assertEqual(23, vw_to_celsius(2960))

    def test_vw_to_fahrenheit(self):
        """Test Fahrenheit conversion."""
        self.assertEqual(32, vw_to_fahrenheit(2730))
        self.assertEqual(72, vw_to_fahrenheit(2955))
        self.assertEqual(73, vw_to_fahrenheit(2960))
