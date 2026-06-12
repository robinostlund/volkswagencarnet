"""Configure tests."""

from aioresponses import aioresponses
import pytest
from pathlib import Path

pytest_plugins = ["pytest_cov"]
pytest_plugins.append("tests.fixtures.connection")

VW_VEHICLE_SRC = Path(__file__).parent.parent / "volkswagencarnet" / "vw_vehicle.py"


@pytest.fixture
def mock_aiohttp():
    """Provide an aioresponses context manager for mocking aiohttp requests."""
    with aioresponses() as m:
        yield m
