"""Interactive login stays separate from ingestion and rejects non-terminal input."""
import importlib.util
import os
from pathlib import Path
import sys
from unittest import mock
import pytest


def command():
    path = Path(__file__).resolve().parents[1] / 'scripts/garmin_login.py'
    spec = importlib.util.spec_from_file_location('interactive_login', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_login_command_requires_terminal(monkeypatch):
    module = command()
    monkeypatch.setattr(sys, 'argv', ['garmin_login.py'])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


@pytest.mark.parametrize('persists', [True, False])
def test_login_command_private_store_and_no_ingestion(monkeypatch, tmp_path, capsys, persists):
    module = command()
    target = tmp_path / 'private' / 'tokens'
    monkeypatch.setattr(sys, 'argv', ['garmin_login.py', '--token-dir', str(target)])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: True)
    collector = mock.MagicMock()
    def login():
        if persists:
            (target / 'synthetic-token.json').write_text('{}')
    collector.garmin_login.side_effect = login
    import garmin_grafana
    monkeypatch.setattr(garmin_grafana, 'garmin_fetch', collector, raising=False)
    with mock.patch.object(module.os, 'umask', wraps=os.umask) as umask:
        if persists:
            module.main()
        else:
            with pytest.raises(RuntimeError, match='without persisting tokens'):
                module.main()
        assert umask.call_count == 2
    assert target.stat().st_mode & 0o777 == 0o700
    collector.garmin_login.assert_called_once()
    collector.store.write.assert_not_called()
    collector.main.assert_not_called()
    if persists:
        assert (target / 'synthetic-token.json').stat().st_mode & 0o777 == 0o600
        assert 'No measurements fetched' in capsys.readouterr().out
