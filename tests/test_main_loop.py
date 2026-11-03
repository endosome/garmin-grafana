"""The scheduler uses dataset progress, independent of heart-rate or watch uploads."""
from datetime import date
from unittest import mock
import psycopg
import pytest


class StopLoop(BaseException):
    pass


def test_scheduler_uses_ledger_and_keeps_running_after_failure(gf, monkeypatch):
    monkeypatch.setattr(gf, 'garmin_login', lambda: mock.Mock())
    monkeypatch.setattr(gf, 'USER_TIMEZONE', 'America/New_York')
    gf.store.pending_start.return_value = date(2026, 1, 1)
    fetch = mock.Mock(side_effect=RuntimeError('network unavailable'))
    monkeypatch.setattr(gf, 'fetch_write_bulk', fetch)
    gf.time.sleep.side_effect = StopLoop
    with pytest.raises(StopLoop):
        gf.main()
    fetch.assert_called_once()
    assert fetch.call_args.args[0] == '2026-01-01'
    gf.store.pending_start.assert_called_once()


@pytest.mark.parametrize('complete,code', [(True, 0), (False, 1)])
def test_manual_range_exit_status(gf, monkeypatch, complete, code):
    monkeypatch.setattr(gf, 'garmin_login', mock.Mock())
    monkeypatch.setattr(gf, 'MANUAL_START_DATE', '2026-01-01')
    monkeypatch.setattr(gf, 'MANUAL_END_DATE', '2026-01-02')
    fetch = mock.Mock(return_value=complete)
    monkeypatch.setattr(gf, 'fetch_write_bulk', fetch)
    with pytest.raises(SystemExit) as err:
        gf.main()
    assert err.value.code == code
    fetch.assert_called_once_with('2026-01-01', '2026-01-02')


@pytest.fixture
def clock(gf, monkeypatch):
    """time.monotonic that only moves when the code under test sleeps."""
    now = [0.0]
    gf.time.sleep.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    monkeypatch.setattr(gf.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(gf, 'UPDATE_INTERVAL_SECONDS', 300)
    monkeypatch.setattr(gf, 'FETCH_REQUEST_CHECK_SECONDS', 5)
    return now


def test_fetch_now_request_ends_the_wait(gf, clock):
    gf.store.latest_fetch_request.side_effect = [3, 3, 4]
    gf.wait_for_next_sync(3)
    assert clock[0] == 15


def test_wait_keeps_the_interval_without_requests_or_database(gf, clock):
    gf.store.latest_fetch_request.side_effect = [psycopg.OperationalError('down')] + [3] * 100
    gf.UPDATE_INTERVAL_SECONDS = 298
    gf.wait_for_next_sync(3)
    assert clock[0] == 298 and gf.time.sleep.call_args_list[-1] == mock.call(3)


def test_a_request_during_a_sync_starts_the_next_one_right_after(gf, clock, monkeypatch):
    monkeypatch.setattr(gf, 'garmin_login', lambda: mock.Mock())
    gf.store.pending_start.return_value = date(2026, 1, 1)
    gf.store.latest_fetch_request.side_effect = [7, 8, 8]  # seen at the start of the sync, then the button's request
    fetch = mock.Mock(side_effect=[True, StopLoop])
    monkeypatch.setattr(gf, 'fetch_write_bulk', fetch)
    with pytest.raises(StopLoop):
        gf.main()
    assert fetch.call_count == 2 and clock[0] == 5


@pytest.mark.parametrize('complete,recorded', [(True, 1), (False, 0)])
def test_only_a_finished_sync_updates_last_fetch(gf, monkeypatch, complete, recorded):
    monkeypatch.setattr(gf, 'garmin_login', lambda: mock.Mock())
    gf.store.pending_start.return_value = date(2026, 1, 1)
    monkeypatch.setattr(gf, 'fetch_write_bulk', mock.Mock(return_value=complete))
    gf.time.sleep.side_effect = StopLoop
    with pytest.raises(StopLoop):
        gf.main()
    assert gf.store.record_fetch.call_count == recorded
