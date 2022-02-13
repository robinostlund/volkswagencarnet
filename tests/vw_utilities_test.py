import unittest
from datetime import datetime, timezone, timedelta
from json import JSONDecodeError
from unittest import mock
from unittest.mock import DEFAULT

from volkswagencarnet.vw_utilities import camel2slug, is_valid_path, obj_parser, json_loads, read_config


class UtilitiesTest(unittest.TestCase):
    def test_camel_to_slug(self):
        data = {"foo": "foo", "fooBar": "foo_bar", "XYZ": "x_y_z", "B4R": "b4_r"}  # Should this actually be "b_4_r"? =)
        for v in data:
            res = camel2slug(v)
            self.assertEqual(data[v], res)

    def test_is_valid_path(self):
        data = {
            "None": [None, None, True],
            "a in a": [{"a": 1}, "a", True],
            "b in a": [{"a": 1}, "b", False],
            "a.b in a.b": [{"a": {"b": 7}}, "a.b", True],
            "list": [[1, "a", None], "a", TypeError],
        }

        for v in data:
            with self.subTest():
                try:
                    if isinstance(data[v][2], bool):
                        self.assertEqual(
                            data[v][2],
                            is_valid_path(data[v][0], data[v][1]),
                            msg=f"Path validation error for {data[v][1]} in {data[v][0]}",
                        )
                    else:
                        with self.assertRaises(data[v][2]):
                            is_valid_path(data[v][0], data[v][1])
                except Exception as e:
                    if isinstance(e, AssertionError):
                        raise
                    self.fail(f"Wrong exception? Got {type(e)} but expected {data[v][2]}")

    def test_obj_parser(self):
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
        expected = {"foo": {"bar": "baz"}}
        actual = json_loads('{"foo":  {\n"bar":\t"baz"}}')
        self.assertEqual(expected, actual)

        self.assertEqual(42, json_loads("42"))

        with self.assertRaises(JSONDecodeError):
            json_loads("{[}")
        with self.assertRaises(TypeError):
            json_loads(42)

    def test_read_config_success(self):
        """successfully read configuration from a file"""
        read_data = """
# Comment
foo: bar
"""
        mock_open = mock.mock_open(read_data=read_data)
        with mock.patch("builtins.open", mock_open):
            res = read_config()
            self.assertEqual({"foo": "bar"}, res)

    def test_read_config_error(self):
        """success on second file, but parse error"""
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
        """empty config on no file found"""
        mock_open = mock.mock_open()
        mock_open.side_effect = IOError
        with mock.patch("builtins.open", mock_open):
            self.assertEqual({}, read_config())
