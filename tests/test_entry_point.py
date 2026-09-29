"""The garmin-fetch console script declared in pyproject.toml ([project.scripts] garmin-fetch = "garmin_grafana:main")."""
import sys
from unittest import mock

import garminconnect
import pytest

from helpers import SRC


class LoginAttempted(Exception):
    pass


@pytest.fixture
def package(offline, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC.parent))
    for name in ["garmin_grafana", "garmin_grafana.garmin_fetch"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(garminconnect, "Garmin", mock.MagicMock(side_effect=LoginAttempted))
    import garmin_grafana
    yield garmin_grafana
    for name in ["garmin_grafana", "garmin_grafana.garmin_fetch"]:
        sys.modules.pop(name, None)


def test_main_starts_syncing(package):
    with pytest.raises(LoginAttempted):
        package.main()
