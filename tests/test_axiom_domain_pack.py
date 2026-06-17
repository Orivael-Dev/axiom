"""Tests for DomainPackManifest, DomainPackStore, build_pack, check_tier."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "c" * 64)

from axiom_domain_pack import (
    DomainPackManifest,
    DomainPackStore,
    build_pack,
    check_tier,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _manifest(**kwargs) -> DomainPackManifest:
    defaults = dict(
        name="legal-us",
        title="US Legal Precedents",
        description="BM25 index over US case law",
        version="1.0.0",
        author="Axiom",
        license="CC-BY-4.0",
        domain="legal",
    )
    defaults.update(kwargs)
    return DomainPackManifest(**defaults)


def _index_dir(tmp: Path) -> Path:
    d = tmp / "index"
    d.mkdir(exist_ok=True)
    (d / "chunk_0.txt").write_text("Section 1: limitation of liability.")
    (d / "chunk_1.txt").write_text("Section 2: indemnification clause.")
    return d


# ── signing ───────────────────────────────────────────────────────────────────

class TestSigning:
    def test_sign_produces_signature(self):
        m = _manifest().sign()
        assert len(m.signature) == 64

    def test_verify_passes_on_untampered(self):
        m = _manifest().sign()
        assert m.verify()

    def test_verify_fails_unsigned(self):
        assert not _manifest().verify()

    def test_verify_fails_on_tampered_name(self):
        m = _manifest().sign()
        d = m.to_dict()
        d["name"] = "HACKED"
        m2 = DomainPackManifest.from_dict(d)
        m2.signature = m.signature
        assert not m2.verify()

    def test_verify_fails_on_tampered_tier(self):
        m = _manifest(tier="free").sign()
        d = m.to_dict()
        d["tier"] = "paid"
        m2 = DomainPackManifest.from_dict(d)
        m2.signature = m.signature
        assert not m2.verify()


# ── serialisation ─────────────────────────────────────────────────────────────

class TestSerialisation:
    def test_round_trip_preserves_all_fields(self):
        m = _manifest(
            domain="medical",
            languages=["en", "fr"],
            jurisdictions=["US", "EU"],
            tier="paid",
            lora_base_models=["Qwen/Qwen2.5-7B-Instruct"],
        ).sign()
        d = m.to_dict()
        m2 = DomainPackManifest.from_dict(d)
        assert m2.domain == "medical"
        assert m2.languages == ["en", "fr"]
        assert m2.tier == "paid"
        assert m2.signature == m.signature

    def test_from_dict_ignores_unknown_keys(self):
        d = _manifest().sign().to_dict()
        d["future_field"] = "ignored"
        m = DomainPackManifest.from_dict(d)
        assert m.name == "legal-us"

    def test_defaults_applied(self):
        m = _manifest()
        assert m.tier == "free"
        assert m.index_type == "bm25"
        assert m.languages == ["en"]
        assert m.format_version == "1.0"


# ── check_tier ────────────────────────────────────────────────────────────────

class TestCheckTier:
    def test_free_pack_always_passes(self, monkeypatch):
        monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "0")
        m = _manifest(tier="free").sign()
        assert check_tier(m) is True

    def test_paid_pack_passes_in_beta(self, monkeypatch):
        monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "1")
        m = _manifest(tier="paid").sign()
        assert check_tier(m) is True

    def test_paid_pack_blocked_post_beta_without_tier_env(self, monkeypatch):
        monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "0")
        monkeypatch.delenv("AXIOM_TIER", raising=False)
        m = _manifest(tier="paid").sign()
        assert check_tier(m) is False

    def test_paid_pack_passes_post_beta_with_tier_env(self, monkeypatch):
        monkeypatch.setenv("AXIOM_FIREWALL_BETA_MODE", "0")
        monkeypatch.setenv("AXIOM_TIER", "paid")
        m = _manifest(tier="paid").sign()
        assert check_tier(m) is True


# ── build_pack ────────────────────────────────────────────────────────────────

class TestBuildPack:
    def test_creates_pack_directory(self, tmp_path):
        m = _manifest().sign()
        pack = build_pack(manifest=m, index_dir=_index_dir(tmp_path), output_dir=tmp_path / "out")
        assert pack.is_dir()

    def test_manifest_written_to_pack(self, tmp_path):
        m = _manifest().sign()
        pack = build_pack(manifest=m, index_dir=_index_dir(tmp_path), output_dir=tmp_path / "out")
        assert (pack / "domain_pack.json").exists()
        data = json.loads((pack / "domain_pack.json").read_text())
        assert data["name"] == "legal-us"

    def test_index_files_copied(self, tmp_path):
        m = _manifest().sign()
        pack = build_pack(manifest=m, index_dir=_index_dir(tmp_path), output_dir=tmp_path / "out")
        assert (pack / "index" / "chunk_0.txt").exists()

    def test_pack_manifest_is_signed(self, tmp_path):
        m = _manifest()  # unsigned — build_pack should sign
        pack = build_pack(manifest=m, index_dir=_index_dir(tmp_path), output_dir=tmp_path / "out")
        data = json.loads((pack / "domain_pack.json").read_text())
        m2 = DomainPackManifest.from_dict(data)
        assert m2.verify()

    def test_starter_knowledge_copied(self, tmp_path):
        from axiom_knowledge_cookie import KnowledgeCookieStore
        ks_path = tmp_path / "knowledge.json"
        ks = KnowledgeCookieStore(ks_path)
        for sess in ["s1", "s2", "s3"]:
            ks.record_hit("legal fragment", "src:1", session_id=sess)
        ks.promote_and_save()

        m = _manifest(starter_knowledge="knowledge.cookie.json")
        pack = build_pack(
            manifest=m,
            index_dir=_index_dir(tmp_path),
            output_dir=tmp_path / "out",
            starter_knowledge=ks_path,
        )
        assert (pack / "knowledge.cookie.json").exists()

    def test_pack_directory_named_name_version(self, tmp_path):
        m = _manifest(name="legal-us", version="2.0.0").sign()
        pack = build_pack(manifest=m, index_dir=_index_dir(tmp_path), output_dir=tmp_path / "out")
        assert pack.name == "legal-us-2.0.0"


# ── DomainPackStore ───────────────────────────────────────────────────────────

class TestDomainPackStore:
    def _store(self, tmp: Path) -> DomainPackStore:
        return DomainPackStore(base_dir=tmp / "store")

    def _build(self, tmp: Path, **kwargs) -> Path:
        m = _manifest(**kwargs)
        return build_pack(manifest=m, index_dir=_index_dir(tmp), output_dir=tmp / "packs")

    def test_install_returns_verified_manifest(self, tmp_path):
        store = self._store(tmp_path)
        pack_dir = self._build(tmp_path)
        m = store.install(pack_dir)
        assert m.verify()
        assert m.name == "legal-us"

    def test_install_copies_pack_to_store(self, tmp_path):
        store = self._store(tmp_path)
        pack_dir = self._build(tmp_path)
        store.install(pack_dir)
        installed_dir = tmp_path / "store" / "legal-us-1.0.0"
        assert installed_dir.is_dir()

    def test_install_raises_on_invalid_signature(self, tmp_path):
        store = self._store(tmp_path)
        pack_dir = self._build(tmp_path)
        # Tamper the manifest
        manifest_path = pack_dir / "domain_pack.json"
        data = json.loads(manifest_path.read_text())
        data["tier"] = "paid"
        manifest_path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="signature"):
            store.install(pack_dir)

    def test_list_installed_empty_when_none(self, tmp_path):
        store = self._store(tmp_path)
        assert store.list_installed() == []

    def test_list_installed_returns_installed_packs(self, tmp_path):
        store = self._store(tmp_path)
        store.install(self._build(tmp_path, name="legal-us", version="1.0.0"))
        store.install(self._build(tmp_path, name="medical-icd11", version="1.0.0"))
        packs = store.list_installed()
        names = {m.name for m in packs}
        assert "legal-us" in names
        assert "medical-icd11" in names

    def test_get_returns_installed_pack(self, tmp_path):
        store = self._store(tmp_path)
        store.install(self._build(tmp_path))
        m = store.get("legal-us")
        assert m is not None
        assert m.name == "legal-us"

    def test_get_returns_none_when_not_installed(self, tmp_path):
        store = self._store(tmp_path)
        assert store.get("nonexistent") is None

    def test_uninstall_removes_pack(self, tmp_path):
        store = self._store(tmp_path)
        store.install(self._build(tmp_path))
        removed = store.uninstall("legal-us", "1.0.0")
        assert removed is True
        assert store.get("legal-us") is None

    def test_uninstall_returns_false_when_not_found(self, tmp_path):
        store = self._store(tmp_path)
        assert store.uninstall("nonexistent", "1.0.0") is False

    def test_index_path_points_into_store(self, tmp_path):
        store = self._store(tmp_path)
        m = store.install(self._build(tmp_path))
        ipath = store.index_path(m)
        assert ipath.exists()
        assert (ipath / "chunk_0.txt").exists()

    def test_starter_knowledge_path_none_when_not_present(self, tmp_path):
        store = self._store(tmp_path)
        m = store.install(self._build(tmp_path))
        assert store.starter_knowledge_path(m) is None
