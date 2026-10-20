from unittest import mock
import pytest

@pytest.mark.parametrize("start, end, expected", [
    ("2026-01-01", "2026-01-03", ["2026-01-03", "2026-01-02", "2026-01-01"]),
    ("2026-01-01", "2026-01-01", ["2026-01-01"]),
    ("2026-01-02", "2026-01-01", []),
    ("2025-12-31", "2026-01-01", ["2026-01-01", "2025-12-31"]),
    ("2024-02-28", "2024-03-01", ["2024-03-01", "2024-02-29", "2024-02-28"]),
])
def test_iter_days_runs_backwards_inclusive(gf, start, end, expected):
    assert list(gf.iter_days(start, end)) == expected


@pytest.mark.parametrize("error, status, expected", [
    (mock.Mock(response=mock.Mock(status_code=500)), 500, True),
    (mock.Mock(response=mock.Mock(status_code=404), spec=["response"]), 500, False),
    (mock.Mock(status_code=429, spec=["status_code"]), 429, True),
    (Exception("500 Server Error: Internal Server Error"), 500, True),
    (Exception("Error in request: 500"), 500, True),
    (Exception("404 Client Error"), 500, False),
    (Exception("took 15000 ms"), 500, False),
    (Exception("code 5000"), 500, False),
])
def test_is_http_status_error(gf, error, status, expected):
    assert gf._is_http_status_error(error, status) is expected
