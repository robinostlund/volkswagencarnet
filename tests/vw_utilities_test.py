"""Utilities class tests."""

from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from unittest import TestCase

import pytest
from volkswagencarnet.vw_utilities import (
    camel2slug,
    find_path,
    is_valid_path,
    json_loads,
    make_url,
    obj_parser,
    redact,
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


class RedactTest(TestCase):
    """Tests for the redact() credential-redaction utility."""

    def test_redact_normal_token(self):
        """Full JWT is truncated to 8 chars + ellipsis."""
        token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.somePayload"
        assert redact(token) == "eyJhbGci..."

    def test_redact_none_returns_placeholder(self):
        """None input returns '(none)' — never crashes."""
        assert redact(None) == "(none)"

    def test_redact_empty_string_returns_placeholder(self):
        """Empty string returns '(none)'."""
        assert redact("") == "(none)"

    def test_redact_short_string(self):
        """Strings shorter than 8 chars return all chars + ellipsis."""
        assert redact("abc") == "abc..."

    def test_redact_exactly_8_chars(self):
        """8-char string returns all 8 chars + ellipsis."""
        assert redact("12345678") == "12345678..."

    def test_redact_long_token_never_in_full(self):
        """40-char token is never fully present in output."""
        token = "A" * 40
        result = redact(token)
        assert len(result) == 11  # 8 chars + "..."
        assert token not in result


class FindPathEdgeCaseTest(TestCase):
    """Additional edge case tests for find_path."""

    def test_find_path_with_deeply_nested_key(self):
        """find_path navigates deeply nested structures."""
        data = {"a": {"b": {"c": {"d": {"e": 42}}}}}
        assert find_path(data, "a.b.c.d.e") == 42

    def test_find_path_with_nonexistent_key_returns_none(self):
        """find_path returns None for nonexistent keys."""
        data = {"a": {"b": 1}}
        assert find_path(data, "a.x.y") is None

    def test_find_path_with_list_index(self):
        """find_path navigates through lists using numeric index."""
        data = {"items": [{"name": "first"}, {"name": "second"}]}
        assert find_path(data, "items.1.name") == "second"

    def test_find_path_with_empty_path_returns_source(self):
        """find_path with empty string returns the source dict."""
        data = {"a": 1}
        assert find_path(data, "") == data


class Camel2SlugEdgeCaseTest(TestCase):
    """Additional edge case tests for camel2slug."""

    def test_camel2slug_with_consecutive_caps(self):
        """Consecutive capitals are split into individual letters."""
        assert camel2slug("HTTPSConnection") == "h_t_t_p_s_connection"

    def test_camel2slug_single_letter(self):
        """Single letter input returns lowercase."""
        assert camel2slug("A") == "a"

    def test_camel2slug_empty_string(self):
        """Empty string returns empty string."""
        assert camel2slug("") == ""


class MakeUrlEdgeCaseTest(TestCase):
    """Additional edge case tests for make_url."""

    def test_make_url_with_special_characters_in_value(self):
        """make_url handles special characters in replacement values."""
        result = make_url("api/{endpoint}", endpoint="v1/users")
        assert result == "api/v1/users"

    def test_make_url_dollar_sign_syntax(self):
        """make_url handles $-prefix syntax for parameters."""
        result = make_url("api/$version/data", version="v2")
        assert result == "api/v2/data"
