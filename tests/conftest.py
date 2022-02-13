import sys

if sys.version_info >= (3, 8):
    pytest_plugins = ["tests.fixtures.connection"]
