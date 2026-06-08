"""Settings persistence (survives restart) + location for time & weather."""
import importlib

import aui.settings as settings


def test_settings_default_path_is_stable_home(monkeypatch, tmp_path):
    # no explicit AX_OS_SETTINGS → a stable file under AX_OS_HOME, not CWD-relative
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AX_OS_SETTINGS", raising=False)
    p = settings._path()
    assert p.endswith("settings.json") and str(tmp_path / "home") in p


def test_settings_survive_restart(monkeypatch, tmp_path):
    # write with one "session", then reload as a fresh process would — the values
    # are still there (no reset on power-down).
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AX_OS_SETTINGS", raising=False)
    settings.update_llm({"enabled": True, "model": "mistral:7b"})
    settings.update_location({"name": "Paris", "lat": 48.85, "lon": 2.35,
                              "timezone": "Europe/Paris"})
    fresh = importlib.reload(settings)            # simulate a restart
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    data = fresh.load()
    assert data["llm"]["enabled"] is True and data["llm"]["model"] == "mistral:7b"
    assert data["location"]["name"] == "Paris" and data["location"]["timezone"] == "Europe/Paris"


def test_location_default_and_update(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AX_OS_SETTINGS", raising=False)
    assert settings.public_location()["name"] == "London"     # sensible default
    settings.update_location({"name": "Tokyo", "lat": 35.68, "lon": 139.69,
                              "timezone": "Asia/Tokyo"})
    loc = settings.public_location()
    assert loc["name"] == "Tokyo" and loc["lat"] == 35.68 and loc["timezone"] == "Asia/Tokyo"
