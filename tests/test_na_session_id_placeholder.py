"""Placeholder test for x-mobile-session-id header field name (CLEAN-03).

The header name "x-mobile-session-id" was derived from APK decompilation
(d20/i.java UserSession interceptor) and has NOT been confirmed from a
live HTTP traffic capture. When confirmed, remove this placeholder and
update the field name if it differs.
"""

import pytest


class TestNASessionIdPlaceholder:
    """Placeholder tests for x-mobile-session-id header."""

    def test_session_id_header_name_is_documented(self):
        """Verify the expected header name constant is used in connection code.

        This test confirms that the codebase uses "x-mobile-session-id" as the
        session header name. If live traffic analysis reveals a different name,
        update both vw_connection.py and this test.
        """
        import re
        from pathlib import Path

        connection_src = Path("volkswagencarnet/vw_connection.py").read_text()
        # The header name should appear in the RVS headers dict assignment
        assert re.search(r'rvs_headers\["x-mobile-session-id"\]', connection_src), (
            "x-mobile-session-id header assignment not found in vw_connection.py"
        )

    @pytest.mark.xfail(
        reason="Field name not yet confirmed from live traffic -- CLEAN-03"
    )
    def test_session_id_confirmed_from_live_traffic(self):
        """Fail until x-mobile-session-id is confirmed from live HTTP capture.

        Remove the xfail marker and this docstring once the field name is
        confirmed via mitmproxy/Charles Proxy traffic capture.
        """
        pytest.fail("x-mobile-session-id field name awaiting live traffic confirmation")
