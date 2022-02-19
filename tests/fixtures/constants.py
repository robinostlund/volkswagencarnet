"""Misc constants and other data for tests."""
import os
from pathlib import Path

MOCK_VIN = "WVWZZZAUZFW000000"

current_path = Path(os.path.dirname(os.path.realpath(__file__)))
resource_path = os.path.join(current_path, "resources")
status_report_json_file = os.path.join(resource_path, "responses", "status.json")
timers_json_file = os.path.join(resource_path, "responses", "timer.json")
