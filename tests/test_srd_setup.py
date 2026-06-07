"""Route A: SRD GGUF → Ollama → Aria wiring (aui.srd_setup)."""
import os

from aui import srd_setup


def test_render_modelfile_minimal(tmp_path):
    gguf = tmp_path / "tinyllama_srd.gguf"
    gguf.write_bytes(b"\0")
    mf = srd_setup.render_modelfile(str(gguf), temperature=0.6)
    assert f"FROM {gguf.resolve()}" in mf
    assert "PARAMETER temperature 0.6" in mf
    # no guessed chat template unless asked
    assert "TEMPLATE" not in mf and "SYSTEM" not in mf


def test_render_modelfile_with_overrides(tmp_path):
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"\0")
    mf = srd_setup.render_modelfile(str(gguf), template="<|user|>{{.Prompt}}",
                                    system="You are Aria.")
    assert "TEMPLATE" in mf and "<|user|>" in mf
    assert "SYSTEM" in mf and "You are Aria." in mf


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("AX_OS_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AX_OS_PERSONA", str(tmp_path / "persona"))


def test_configure_aria_flips_settings_and_persona(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    summary = srd_setup.configure_aria("aria-srd", base_url="http://x:9/v1")
    assert summary["base_model"] == "aria-srd"
    assert summary["base_url"] == "http://x:9/v1"

    from aui.settings import load
    from aui.persona import PersonaStore
    llm = load()["llm"]
    assert llm["enabled"] is True and llm["base_url"] == "http://x:9/v1"
    assert PersonaStore().load_or_mint().base_model == "aria-srd"


def test_configure_aria_records_lineage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from aui.persona import PersonaStore
    PersonaStore().load_or_mint()                      # mint the default first
    srd_setup.configure_aria("aria-srd")               # change → appends history
    assert len(PersonaStore().lineage()) >= 2          # prior + current


def test_main_no_ollama_only_flips_config(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    called = {"ollama": False}
    monkeypatch.setattr(srd_setup, "ollama_create",
                        lambda *a, **k: called.__setitem__("ollama", True))
    rc = srd_setup.main(["--no-ollama", "--name", "aria-srd",
                         "--base-url", "http://localhost:8080/v1"])
    assert rc == 0 and called["ollama"] is False       # llama-server path: no create
    from aui.persona import PersonaStore
    assert PersonaStore().load_or_mint().base_model == "aria-srd"


def test_main_missing_gguf_errors(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    rc = srd_setup.main(["--gguf", str(tmp_path / "nope.gguf")])
    assert rc == 2                                      # GGUF not found
