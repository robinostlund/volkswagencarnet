"""Code quality tests: AST analysis and structural source code verification.

These tests verify code quality properties (no bare response.text, no dead code references,
.gitignore structure) via AST parsing and source code inspection.

Merged from phase21_pr_review_fixes_test.py (structural test classes).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from tests.conftest import VW_VEHICLE_SRC

VW_CONNECTION_SRC = (
    Path(__file__).parent.parent / "volkswagencarnet" / "vw_connection.py"
)


def _read_source() -> str:
    """Read vw_connection.py source code."""
    return VW_CONNECTION_SRC.read_text()


# ---------------------------------------------------------------------------
# H-4: _request() Logs Only Status Code and URL
# ---------------------------------------------------------------------------


class RequestLoggingTest(IsolatedAsyncioTestCase):
    """Tests for H-4: _request() logs only status code and URL, no response body/headers."""

    def test_no_response_body_in_request_debug_logs(self):
        """_request() method source contains no log of response body or full headers."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_request":
                request_source = ast.get_source_segment(source, node)
                if request_source:
                    header_log_matches = re.findall(
                        r"_LOGGER\.\w+\([^)]*response\.headers[^)]*\)", request_source
                    )
                    self.assertEqual(
                        len(header_log_matches),
                        0,
                        f"Found response.headers in _LOGGER calls: {header_log_matches}",
                    )
                    body_log_matches = re.findall(
                        r"_LOGGER\.\w+\([^)]*(?:response\.text|resp_text|response_body)[^)]*\)",
                        request_source,
                    )
                    self.assertEqual(
                        len(body_log_matches),
                        0,
                        f"Found response body in _LOGGER calls: {body_log_matches}",
                    )
                break

    def test_request_debug_logs_contain_status_and_url_only(self):
        """Verify the _request() logging pattern: only status code and URL."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_request":
                request_source = ast.get_source_segment(source, node)
                if request_source:
                    self.assertIn("response.status", request_source)
                    self.assertIn("url", request_source)
                break

    def test_no_bare_response_text_in_request_method(self):
        """_request() contains no bare response.text (coroutine reference without call)."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_request":
                request_source = ast.get_source_segment(source, node)
                if request_source:
                    bare_text_refs = re.findall(
                        r"response\.text(?!\s*\()", request_source
                    )
                    self.assertEqual(
                        len(bare_text_refs),
                        0,
                        f"Found {len(bare_text_refs)} bare response.text reference(s) in _request()",
                    )
                break


# ---------------------------------------------------------------------------
# H-3: response.text Bare Coroutine Bug Eliminated
# ---------------------------------------------------------------------------


class ResponseTextCoroutineBugTest(IsolatedAsyncioTestCase):
    """Tests for H-3: No bare response.text (without parentheses) in log calls."""

    def test_no_bare_response_text_in_logger_calls(self):
        """No _LOGGER call references response.text without () -- would be a coroutine object."""
        source = _read_source()
        matches = re.findall(
            r"_LOGGER\.\w+\([^)]*response\.text(?!\s*\()[^)]*\)", source
        )
        self.assertEqual(
            len(matches),
            0,
            f"Found {len(matches)} _LOGGER call(s) with bare response.text: {matches}",
        )

    def test_all_response_text_calls_have_parentheses(self):
        """Every response.text in the source is followed by () (properly called)."""
        source = _read_source()
        all_response_text = list(re.finditer(r"response\.text", source))
        for match in all_response_text:
            after = source[match.end() : match.end() + 5].strip()
            self.assertTrue(
                after.startswith("("),
                f"Found bare response.text at position {match.start()}: "
                f"...{source[max(0, match.start() - 20) : match.end() + 20]}...",
            )


# ---------------------------------------------------------------------------
# H-6: Stale pylint disable=unreachable Comment Removed
# ---------------------------------------------------------------------------


class StalePylintCommentTest(IsolatedAsyncioTestCase):
    """Tests for H-6: No stale pylint disable=unreachable comment."""

    def test_no_pylint_disable_unreachable_in_source(self):
        """vw_connection.py contains no 'pylint: disable=unreachable' comment."""
        source = _read_source()
        matches = re.findall(r"pylint:\s*disable=unreachable", source)
        self.assertEqual(
            len(matches),
            0,
            f"Found {len(matches)} stale 'pylint: disable=unreachable' comment(s)",
        )


# ---------------------------------------------------------------------------
# H-1: Dead _discover_endpoints Method Removed
# ---------------------------------------------------------------------------


class DeadCodeRemovalTest(IsolatedAsyncioTestCase):
    """Tests for H-1: _discover_endpoints dead code is removed."""

    def test_connection_has_no_discover_endpoints_method(self):
        """Connection class does not have a _discover_endpoints method."""
        from volkswagencarnet.vw_connection import Connection

        self.assertFalse(
            hasattr(Connection, "_discover_endpoints"),
            "Connection still has _discover_endpoints method -- should be removed",
        )

    def test_no_discover_endpoints_reference_in_source(self):
        """No reference to '_discover_endpoints' exists anywhere in vw_connection.py."""
        source = _read_source()
        matches = re.findall(r"_discover_endpoints", source)
        self.assertEqual(
            len(matches),
            0,
            f"Found {len(matches)} reference(s) to _discover_endpoints in source",
        )


# ---------------------------------------------------------------------------
# C-1 / H-5: Git Tracking (Structural Tests)
# ---------------------------------------------------------------------------


class GitIgnoreStructureTest(IsolatedAsyncioTestCase):
    """Tests for C-1/H-5: .gitignore contains proper credential and docs rules."""

    def test_gitignore_has_explicit_testing_creds_entry(self):
        """'.gitignore' explicitly lists 'testing_creds.env'."""
        gitignore = (Path(__file__).parent.parent / ".gitignore").read_text()
        self.assertIn("testing_creds.env", gitignore)

    def test_gitignore_has_docs_directory_rule(self):
        """'.gitignore' contains a docs/ directory exclusion rule."""
        gitignore = (Path(__file__).parent.parent / ".gitignore").read_text()
        self.assertIn("docs/", gitignore)

    def test_gitignore_has_env_glob_rule(self):
        """'.gitignore' contains *.env glob pattern for general credential files."""
        gitignore = (Path(__file__).parent.parent / ".gitignore").read_text()
        self.assertIn("*.env", gitignore)


# ---------------------------------------------------------------------------
# Dead Code: response == 429 check in _handle_action_result
# ---------------------------------------------------------------------------


class TestDeadCodeRemoval(IsolatedAsyncioTestCase):
    """Verify dead `response == 429` check has been removed from _handle_action_result."""

    def test_no_response_429_check_in_handle_action_result(self):
        """AST of _handle_action_result contains no comparison to integer 429."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_handle_action_result"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.Compare):
                        for comparator in child.comparators:
                            if (
                                isinstance(comparator, ast.Constant)
                                and comparator.value == 429
                            ):
                                self.fail(
                                    "Found dead comparison to 429 in _handle_action_result "
                                    f"at line {child.lineno}"
                                )
                        # Also check left side
                        if (
                            isinstance(child.left, ast.Constant)
                            and child.left.value == 429
                        ):
                            self.fail(
                                "Found dead comparison to 429 in _handle_action_result "
                                f"at line {child.lineno}"
                            )
                return
        self.fail("_handle_action_result method not found in source")


# ---------------------------------------------------------------------------
# Dead Code: bare try/except in _is_allowed_vw_domain
# ---------------------------------------------------------------------------


class TestDeadTryExcept(IsolatedAsyncioTestCase):
    """Verify dead try/except has been removed from _is_allowed_vw_domain."""

    def test_no_bare_except_in_is_allowed_vw_domain(self):
        """_is_allowed_vw_domain has no ast.Try nodes (urlparse never raises)."""
        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_is_allowed_vw_domain"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        self.fail(
                            "_is_allowed_vw_domain still contains a try block "
                            f"at line {child.lineno} -- urlparse never raises"
                        )
                return
        self.fail("_is_allowed_vw_domain method not found in source")


# ---------------------------------------------------------------------------
# Assert Guards: assert self._connection replaced with RuntimeError
# ---------------------------------------------------------------------------


class TestAssertGuards(IsolatedAsyncioTestCase):
    """Verify assert self._connection is not None replaced with RuntimeError."""

    def test_no_assert_connection_in_vehicle(self):
        """vw_vehicle.py has no 'assert self._connection is not None' statements."""
        source = VW_VEHICLE_SRC.read_text()
        import re as _re

        matches = _re.findall(r"assert\s+self\._connection\s+is\s+not\s+None", source)
        self.assertEqual(
            len(matches),
            0,
            f"Found {len(matches)} 'assert self._connection is not None' statement(s)",
        )

    async def test_runtime_error_on_none_connection(self):
        """Action method raises RuntimeError when _connection is None."""
        from unittest.mock import PropertyMock, patch
        from volkswagencarnet.vw_vehicle import Vehicle

        vehicle = Vehicle(None, "https://example.com")
        vehicle._connection = None

        # Mock is_charging_supported to True so we reach the connection guard
        with patch.object(
            type(vehicle),
            "is_charging_supported",
            new_callable=PropertyMock,
            return_value=True,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await vehicle.set_charger("start")
        self.assertIn("Vehicle not associated", str(ctx.exception))


# ---------------------------------------------------------------------------
# Type Annotations: model_year returns int | None
# ---------------------------------------------------------------------------


class TestTypeAnnotations(IsolatedAsyncioTestCase):
    """Verify type annotations are correct in vw_vehicle.py."""

    def test_model_year_returns_int_or_none(self):
        """model_year property has return annotation int | None, not bool | None."""
        source = VW_VEHICLE_SRC.read_text()
        import re as _re

        # Find the def model_year line
        match = _re.search(r"def model_year\(self\)\s*->\s*(.+?):", source)
        self.assertIsNotNone(match, "model_year property not found")
        annotation = match.group(1).strip()
        self.assertEqual(
            annotation,
            "int | None",
            f"model_year return annotation is '{annotation}', expected 'int | None'",
        )
