"""Misc tests for exception and their handling."""
from unittest import TestCase

from volkswagencarnet.vw_exceptions import AuthenticationException


class ExceptionTests(TestCase):
    """Unit tests for exceptions."""

    def test_auth_exception(self):
        """Test that message matches. Dummy test."""
        ex = AuthenticationException("foo failed")
        self.assertEqual("foo failed", ex.__str__())
