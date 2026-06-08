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


# ── one-time migration of legacy CWD files into the stable home ──────────────

def test_settings_migrates_legacy_cwd_file(monkeypatch, tmp_path):
    import os
    monkeypatch.delenv("AX_OS_NO_MIGRATE", raising=False)   # opt into migration
    monkeypatch.delenv("AX_OS_SETTINGS", raising=False)
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ax_os_settings.json").write_text(
        '{"llm": {"enabled": true, "model": "legacy-model"}}', encoding="utf-8")
    data = settings.load()                                  # triggers _path → migrate
    assert data["llm"]["model"] == "legacy-model" and data["llm"]["enabled"] is True
    assert os.path.isfile(str(tmp_path / "home" / "settings.json"))   # copied into home


def test_migration_skipped_when_guarded(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_NO_MIGRATE", "1")             # the test-suite default
    monkeypatch.delenv("AX_OS_SETTINGS", raising=False)
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ax_os_settings.json").write_text(
        '{"llm": {"model": "legacy-model"}}', encoding="utf-8")
    assert settings.load()["llm"]["model"] != "legacy-model"   # defaults, not migrated


def test_migration_never_clobbers_existing_home(monkeypatch, tmp_path):
    import os
    monkeypatch.delenv("AX_OS_NO_MIGRATE", raising=False)
    monkeypatch.delenv("AX_OS_SETTINGS", raising=False)
    monkeypatch.setenv("AX_OS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    os.makedirs(str(tmp_path / "home"), exist_ok=True)
    (tmp_path / "home" / "settings.json").write_text(
        '{"llm": {"model": "home-model"}}', encoding="utf-8")
    (tmp_path / "ax_os_settings.json").write_text(
        '{"llm": {"model": "legacy-model"}}', encoding="utf-8")
    assert settings.load()["llm"]["model"] == "home-model"     # existing home wins
