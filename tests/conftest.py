"""Configure tests."""
import sys

pytest_plugins = ["pytest_cov"]

if sys.version_info >= (3, 8):
    pytest_plugins.append("tests.fixtures.connection")
