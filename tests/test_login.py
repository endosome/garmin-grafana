"""garmin_login: stored tokens first, then credentials (env or prompt) with MFA."""
from unittest import mock

import pytest
import requests
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)


@pytest.fixture
def garmin_cls(gf, monkeypatch, tmp_path):
    """Replaces the Garmin class; each instantiation returns a new mock recorded in .instances."""
    monkeypatch.setattr(gf, "TOKEN_DIR", str(tmp_path / "tokens"))
    instances = []

    def create(*args, **kwargs):
        instance = mock.MagicMock(name=f"Garmin{len(instances)}")
        instance.init_kwargs = kwargs
        instances.append(instance)
        return instance

    cls = mock.MagicMock(side_effect=create)
    cls.instances = instances
    monkeypatch.setattr(gf, "Garmin", cls)
    return cls


def test_stored_tokens_are_used_first(gf, garmin_cls, tmp_path):
    garmin = gf.garmin_login()

    assert garmin is garmin_cls.instances[0]
    garmin.login.assert_called_once_with(str(tmp_path / "tokens"))
    assert garmin_cls.call_count == 1


@pytest.mark.parametrize("token_error", [
    FileNotFoundError("no tokens"),
    GarminConnectAuthenticationError("expired"),
    GarminConnectConnectionError("401"),
])
def test_falls_back_to_credentials_from_environment(gf, garmin_cls, monkeypatch, token_error):
    monkeypatch.setattr(gf, "GARMINCONNECT_EMAIL", "me@example.com")
    monkeypatch.setattr(gf, "GARMINCONNECT_PASSWORD", "secret")
    monkeypatch.setattr(gf, "GARMINCONNECT_IS_CN", True)
    monkeypatch.setattr("builtins.input", mock.MagicMock(side_effect=AssertionError("must not prompt")))
    garmin_cls.side_effect = None
    token_client, credential_client = mock.MagicMock(), mock.MagicMock()
    token_client.login.side_effect = token_error
    garmin_cls.side_effect = [token_client, credential_client]

    garmin = gf.garmin_login()

    assert garmin is credential_client
    kwargs = garmin_cls.call_args_list[1].kwargs
    assert (kwargs["email"], kwargs["password"], kwargs["is_cn"]) == ("me@example.com", "secret", True)
    credential_client.login.assert_called_once()


def test_prompts_for_missing_credentials_and_mfa(gf, garmin_cls, monkeypatch):
    monkeypatch.setattr(gf, "GARMINCONNECT_EMAIL", None)
    monkeypatch.setattr(gf, "GARMINCONNECT_PASSWORD", None)
    answers = iter(["  typed@example.com ", " typed-secret ", " 123456 "])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    token_client, credential_client = mock.MagicMock(), mock.MagicMock()
    token_client.login.side_effect = FileNotFoundError
    garmin_cls.side_effect = [token_client, credential_client]

    gf.garmin_login()

    kwargs = garmin_cls.call_args_list[1].kwargs
    assert (kwargs["email"], kwargs["password"]) == ("typed@example.com", "typed-secret")
    assert kwargs["prompt_mfa"]() == "123456"


@pytest.mark.parametrize("login_error", [
    GarminConnectAuthenticationError("bad password"),
    GarminConnectConnectionError("down"),
    GarminConnectTooManyRequestsError("429"),
    requests.exceptions.HTTPError("403"),
    FileNotFoundError("token dir"),
])
def test_failed_credential_login_raises(gf, garmin_cls, monkeypatch, login_error):
    monkeypatch.setattr(gf, "GARMINCONNECT_EMAIL", "me@example.com")
    monkeypatch.setattr(gf, "GARMINCONNECT_PASSWORD", "secret")
    token_client, credential_client = mock.MagicMock(), mock.MagicMock()
    token_client.login.side_effect = FileNotFoundError
    credential_client.login.side_effect = login_error
    garmin_cls.side_effect = [token_client, credential_client]

    with pytest.raises(Exception, match="Garmin login failed"):
        gf.garmin_login()


def test_unexpected_token_login_error_propagates(gf, garmin_cls):
    token_client = mock.MagicMock()
    token_client.login.side_effect = RuntimeError("bug")
    garmin_cls.side_effect = [token_client]

    with pytest.raises(RuntimeError, match="bug"):
        gf.garmin_login()


def test_legacy_token_file_uses_sibling_directory(gf, garmin_cls, monkeypatch, tmp_path):
    legacy = tmp_path / "garmin_tokens"
    legacy.write_text("{}")
    monkeypatch.setattr(gf, "TOKEN_DIR", str(legacy))

    gf.garmin_login().login.assert_called_once_with(str(legacy) + "_tokens")


def test_json_token_file_is_used_as_is(gf, garmin_cls, monkeypatch, tmp_path):
    token_file = tmp_path / "tokens.json"
    token_file.write_text("{}")
    monkeypatch.setattr(gf, "TOKEN_DIR", str(token_file))

    gf.garmin_login().login.assert_called_once_with(str(token_file))


def test_home_directory_is_expanded(gf, garmin_cls, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(gf, "TOKEN_DIR", "~/.garminconnect")

    gf.garmin_login().login.assert_called_once_with(str(tmp_path / ".garminconnect"))
