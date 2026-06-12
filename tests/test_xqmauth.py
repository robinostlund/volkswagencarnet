"""Tests for X-QMAuth HMAC-SHA256 header calculation."""

import pytest
from freezegun import freeze_time
from volkswagencarnet.vw_connection import Connection


class TestXQMAuth:
    """Test X-QMAuth header calculation."""

    def test_xqmauth_explicit_timestamp_vector_1(self):
        """Test X-QMAuth with explicit timestamp 1700000000.0 (2023-11-14 22:13:20 UTC)."""
        result = Connection._calculate_xqmauth(timestamp=1700000000.0)
        # 1700000000 / 100 = 17000000
        assert (
            result
            == "v1:01da27b0:4568b3386463d2326d4eceb7d59df33114a02da75c6e0f9b6cf62121efc18619"
        )

    def test_xqmauth_explicit_timestamp_vector_2(self):
        """Test X-QMAuth with explicit timestamp 1738961400.0 (2025-02-07 19:30:00 UTC)."""
        result = Connection._calculate_xqmauth(timestamp=1738961400.0)
        # 1738961400 / 100 = 17389614
        assert (
            result
            == "v1:01da27b0:227f8265a2def2408f83fbefc85f8d9b4877f8ab322d3649dd2235bcca2014e9"
        )

    @freeze_time("2026-01-15 12:00:00")
    def test_xqmauth_frozen_time(self):
        """Test X-QMAuth with frozen time (no explicit timestamp, uses time.time())."""
        # 2026-01-15 12:00:00 UTC = epoch 1768478400 -> 1768478400/100 = 17684784
        result = Connection._calculate_xqmauth()
        assert (
            result
            == "v1:01da27b0:b741a83920e4f353d9a0b66a9449712966ac672c99b55ebe875f981068717948"
        )

    def test_xqmauth_format(self):
        """Test X-QMAuth output format: prefix + 64-char hex digest."""
        result = Connection._calculate_xqmauth(timestamp=1700000000.0)
        assert result.startswith("v1:01da27b0:")
        hex_part = result[len("v1:01da27b0:") :]
        assert len(hex_part) == 64  # SHA-256 produces 64 hex chars
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_xqmauth_different_timestamps_produce_different_results(self):
        """Different timestamps in different 100-second windows produce different HMACs."""
        result_a = Connection._calculate_xqmauth(timestamp=1700000000.0)
        result_b = Connection._calculate_xqmauth(
            timestamp=1700000200.0
        )  # 200 seconds later
        assert result_a != result_b

    def test_xqmauth_same_100sec_window_produces_same_result(self):
        """Timestamps within the same 100-second window produce the same HMAC."""
        result_a = Connection._calculate_xqmauth(timestamp=1700000000.0)
        result_b = Connection._calculate_xqmauth(
            timestamp=1700000050.0
        )  # 50 seconds later, same window
        assert result_a == result_b
