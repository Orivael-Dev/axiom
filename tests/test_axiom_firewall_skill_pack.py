"""Tests for Phase 2 Skill Pack format + signing + tenant install.

Covers: manifest parsing/validation, HMAC sign+verify roundtrip,
install/uninstall, lineage tracking, integration with the existing
tenant_policy table (installing a pack writes its policy into the
verdict path).
"""
from __future__ import annotations

import json
import sys

import pytest


@pytest.fixture
def isolated_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test")
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth", "axiom_firewall.billing",
        "axiom_firewall.limits", "axiom_firewall.policy",
        "axiom_firewall.skill_pack", "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        sys.modules.pop(mod, None)
    yield tmp_path


_VALID_MANIFEST = {
    "format_version": "1.0",
    "name":           "fdcpa",
    "title":          "Fair Debt Collection Practices Act",
    "description":    "Block FDCPA-prohibited debt-collection patterns.",
    "version":        "0.1.0",
    "author":         "Orivael Dev",
    "license":        "MIT",
    "homepage":       "https://docs.orivael.dev/firewall/packs/fdcpa",
    "tags":           ["compliance", "finance"],
    "tested_against": ["axiom-firewall>=0.1.0"],
    "policy": {
        "version": 1,
        "additional_block_patterns": [
            {"class": "HARM", "regex": "warrant for your arrest"},
        ],
        "disabled_default_classes": [],
        "allow_only_classes": None,
    },
}


# ─── parse() validation ─────────────────────────────────────────────────


def test_parse_accepts_valid_manifest(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    m = SkillPackManifest.parse(_VALID_MANIFEST)
    assert m.name == "fdcpa"
    assert m.version == "0.1.0"
    assert m.tags == ("compliance", "finance")
    assert m.tested_against == ("axiom-firewall>=0.1.0",)
    assert m.policy["additional_block_patterns"][0]["class"] == "HARM"


def test_parse_accepts_json_string(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    m = SkillPackManifest.parse(json.dumps(_VALID_MANIFEST))
    assert m.name == "fdcpa"


def test_parse_rejects_missing_fields(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    bad = dict(_VALID_MANIFEST)
    del bad["author"]
    with pytest.raises(ValueError, match="missing required field 'author'"):
        SkillPackManifest.parse(bad)


def test_parse_rejects_wrong_format_version(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    bad = dict(_VALID_MANIFEST)
    bad["format_version"] = "2.0"
    with pytest.raises(ValueError, match="Unsupported pack format_version"):
        SkillPackManifest.parse(bad)


def test_parse_rejects_bad_name(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    bad = dict(_VALID_MANIFEST)
    bad["name"] = "Invalid Name With Spaces"
    with pytest.raises(ValueError, match="must be lowercase"):
        SkillPackManifest.parse(bad)


def test_parse_rejects_bad_version(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    bad = dict(_VALID_MANIFEST)
    bad["version"] = "1.0"  # missing patch
    with pytest.raises(ValueError, match="must be semver"):
        SkillPackManifest.parse(bad)


def test_parse_validates_embedded_policy(isolated_tenants):
    """A pack with a broken policy should fail at parse time."""
    from axiom_firewall.skill_pack import SkillPackManifest
    bad = json.loads(json.dumps(_VALID_MANIFEST))
    bad["policy"]["additional_block_patterns"][0]["regex"] = "("  # invalid regex
    with pytest.raises(ValueError, match="invalid regex"):
        SkillPackManifest.parse(bad)


def test_to_policy_returns_compiled(isolated_tenants):
    from axiom_firewall.skill_pack import SkillPackManifest
    m = SkillPackManifest.parse(_VALID_MANIFEST)
    p = m.to_policy()
    assert len(p.additional_block_patterns) == 1
    cls, pattern = p.additional_block_patterns[0]
    assert cls == "HARM"
    assert pattern.search("a warrant for your arrest now") is not None


# ─── Signing + verification ─────────────────────────────────────────────


def test_sign_verify_first_party_roundtrip(isolated_tenants):
    from axiom_firewall.skill_pack import (
        SkillPackManifest, sign_first_party, verify_first_party,
    )
    payload = dict(_VALID_MANIFEST)
    sig = sign_first_party(payload)
    payload["signature"] = sig
    m = SkillPackManifest.parse(payload)
    assert verify_first_party(m) is True


def test_verify_rejects_tampered_payload(isolated_tenants):
    from axiom_firewall.skill_pack import (
        SkillPackManifest, sign_first_party, verify_first_party,
    )
    payload = dict(_VALID_MANIFEST)
    payload["signature"] = sign_first_party(payload)
    m = SkillPackManifest.parse(payload)
    # Tamper: change the policy content but keep the original signature.
    tampered = m.to_dict()
    tampered["policy"]["additional_block_patterns"][0]["regex"] = "different"
    m2 = SkillPackManifest.parse(tampered)
    assert verify_first_party(m2) is False


def test_verify_rejects_wrong_key(isolated_tenants):
    from axiom_firewall.skill_pack import (
        SkillPackManifest, sign_payload, verify_signature,
    )
    payload = dict(_VALID_MANIFEST)
    payload["signature"] = sign_payload(payload, b"other-key-32-bytes-of-entropy-zz")
    m = SkillPackManifest.parse(payload)
    assert verify_signature(m, b"different-key-32-bytes-yyyyyyyyy") is False


def test_verify_rejects_empty_signature(isolated_tenants):
    from axiom_firewall.skill_pack import (
        SkillPackManifest, verify_first_party,
    )
    m = SkillPackManifest.parse(_VALID_MANIFEST)  # no signature set
    assert m.signature == ""
    assert verify_first_party(m) is False


def test_signature_excludes_signature_field(isolated_tenants):
    """Putting any value in the signature field shouldn't change what's signed."""
    from axiom_firewall.skill_pack import sign_first_party
    a = dict(_VALID_MANIFEST)
    b = dict(_VALID_MANIFEST)
    b["signature"] = "garbage"  # should be excluded from the canonical payload
    assert sign_first_party(a) == sign_first_party(b)


# ─── Tenant install ────────────────────────────────────────────────────


def test_install_persists_pack_and_writes_policy(isolated_tenants):
    """Install ↔ pack lineage + policy applied for verdicts."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.policy import get_policy
    from axiom_firewall.skill_pack import (
        SkillPackManifest, get_installed_pack, install_pack,
    )

    t = Tenant.new(email="i@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    m = SkillPackManifest.parse(_VALID_MANIFEST)
    install_pack(t.tenant_id, m)

    lineage = get_installed_pack(t.tenant_id)
    assert lineage is not None
    assert lineage.name == "fdcpa"
    assert lineage.version == "0.1.0"

    # And the policy is what drives verdicts.
    p = get_policy(t.tenant_id)
    assert len(p.additional_block_patterns) == 1


def test_install_replaces_prior_pack(isolated_tenants):
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        SkillPackManifest, get_installed_pack, install_pack,
    )

    t = Tenant.new(email="r@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(_VALID_MANIFEST))

    second = dict(_VALID_MANIFEST)
    second["name"] = "gdpr-article-9"
    second["title"] = "GDPR Article 9 special-category data"
    second["version"] = "0.2.0"
    install_pack(t.tenant_id, SkillPackManifest.parse(second))

    lineage = get_installed_pack(t.tenant_id)
    assert lineage.name == "gdpr-article-9"
    assert lineage.version == "0.2.0"


def test_uninstall_clears_lineage(isolated_tenants):
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        SkillPackManifest, get_installed_pack, install_pack, uninstall_pack,
    )

    t = Tenant.new(email="u@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(_VALID_MANIFEST))
    assert get_installed_pack(t.tenant_id) is not None
    uninstall_pack(t.tenant_id)
    assert get_installed_pack(t.tenant_id) is None


def test_corrupt_pack_persistence_returns_none(isolated_tenants):
    """If something corrupts the installed_pack row, lineage falls back to None."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import _conn, _tenant_path, insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        get_installed_pack, init_install_table,
    )

    t = Tenant.new(email="c@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    init_install_table(t.tenant_id)
    with _conn(_tenant_path(t.tenant_id)) as c:
        c.execute(
            "INSERT INTO installed_pack "
            "(pack_name, pack_version, manifest_json, installed_at) "
            "VALUES (?, ?, ?, ?)",
            ("broken", "0.0.1", "{bad json", "2026-05-16T00:00:00"),
        )
    assert get_installed_pack(t.tenant_id) is None
