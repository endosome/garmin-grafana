"""The scheduler uses dataset progress, independent of heart-rate or watch uploads."""
from datetime import date
from unittest import mock
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
