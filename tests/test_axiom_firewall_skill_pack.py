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


# ─── Multi-pack stacking ────────────────────────────────────────────────


def _make_manifest(name: str, *, blocks=None, disabled=None, allow_only=None,
                   version: str = "0.1.0") -> dict:
    """Build a fresh manifest dict with custom policy bits, derived from
    _VALID_MANIFEST. Used by the stacking + compose tests below."""
    m = dict(_VALID_MANIFEST)
    m["name"] = name
    m["title"] = f"Test pack {name}"
    m["version"] = version
    m["policy"] = {
        "version": 1,
        "additional_block_patterns": blocks or [],
        "disabled_default_classes": disabled or [],
        "allow_only_classes": allow_only,
    }
    return m


def test_install_stacks_multiple_packs(isolated_tenants):
    """install_pack on a new name appends to the stack; both packs
    remain active and list_installed_packs returns them in install
    order (oldest first)."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        SkillPackManifest, install_pack, list_installed_packs,
    )

    t = Tenant.new(email="stack@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("fdcpa",
                       blocks=[{"class": "HARM", "regex": "fdcpa-pattern"}])))
    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("gdpr",
                       blocks=[{"class": "HARM", "regex": "gdpr-pattern"}])))

    active = list_installed_packs(t.tenant_id)
    assert [p.name for p in active] == ["fdcpa", "gdpr"]


def test_install_same_pack_is_idempotent(isolated_tenants):
    """Re-installing an already-active pack updates in place rather
    than creating a duplicate active row."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        SkillPackManifest, install_pack, list_installed_packs,
    )

    t = Tenant.new(email="idem@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("fdcpa")))
    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("fdcpa")))
    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("fdcpa", version="0.2.0")))

    active = list_installed_packs(t.tenant_id)
    assert len(active) == 1
    assert active[0].version == "0.2.0"   # in-place upgrade succeeded


def test_install_revives_previously_removed_pack(isolated_tenants):
    """install → uninstall → install of the same name reuses the
    audit-trail row (active flipped 1→0→1) rather than creating a
    second row."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import _conn, _tenant_path, insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        SkillPackManifest, install_pack, uninstall_pack,
    )

    t = Tenant.new(email="rev@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("fdcpa")))
    uninstall_pack(t.tenant_id, name="fdcpa")
    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("fdcpa")))

    with _conn(_tenant_path(t.tenant_id)) as c:
        rows = c.execute(
            "SELECT COUNT(*) AS n FROM installed_pack WHERE pack_name = ?",
            ("fdcpa",),
        ).fetchone()
    assert rows["n"] == 1   # single audit row, not duplicates


def test_uninstall_by_name_keeps_others(isolated_tenants):
    """Removing one pack from the stack leaves the others active and
    the merged policy reflects only what remains."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.policy import get_policy
    from axiom_firewall.skill_pack import (
        SkillPackManifest, install_pack, list_installed_packs, uninstall_pack,
    )

    t = Tenant.new(email="byname@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("fdcpa",
                       blocks=[{"class": "HARM", "regex": "fdcpa-only"}])))
    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("gdpr",
                       blocks=[{"class": "HARM", "regex": "gdpr-only"}])))

    uninstall_pack(t.tenant_id, name="fdcpa")

    active = list_installed_packs(t.tenant_id)
    assert [p.name for p in active] == ["gdpr"]
    policy = get_policy(t.tenant_id)
    patterns = [regex.pattern for (_cls, regex) in policy.additional_block_patterns]
    assert any("gdpr-only" in pat for pat in patterns)
    assert not any("fdcpa-only" in pat for pat in patterns)


def test_uninstall_all_clears_policy(isolated_tenants):
    """uninstall_pack with no name removes every active pack and
    clears tenant_policy so verdicts fall back to defaults."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.policy import get_policy
    from axiom_firewall.skill_pack import (
        SkillPackManifest, install_pack, list_installed_packs, uninstall_pack,
    )

    t = Tenant.new(email="all@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("fdcpa")))
    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("gdpr")))
    uninstall_pack(t.tenant_id)

    assert list_installed_packs(t.tenant_id) == []
    policy = get_policy(t.tenant_id)
    assert policy.additional_block_patterns == ()


def test_compose_policy_unions_block_patterns(isolated_tenants):
    """Installing two packs with disjoint block patterns yields a
    merged policy that enforces both."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.policy import get_policy
    from axiom_firewall.skill_pack import SkillPackManifest, install_pack

    t = Tenant.new(email="union@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)

    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("pack-a", blocks=[{"class": "HARM", "regex": "alpha-pat"}])))
    install_pack(t.tenant_id, SkillPackManifest.parse(
        _make_manifest("pack-b", blocks=[{"class": "DECEIVE", "regex": "beta-pat"}])))

    policy = get_policy(t.tenant_id)
    patterns = [regex.pattern for (_cls, regex) in policy.additional_block_patterns]
    assert any("alpha-pat" in pat for pat in patterns)
    assert any("beta-pat" in pat for pat in patterns)


def test_compose_policy_intersects_allow_only(isolated_tenants):
    """allow_only_classes intersects across packs that specify one —
    most-restrictive wins. Packs without allow_only don't relax the
    restriction."""
    from axiom_firewall.skill_pack import SkillPackManifest, compose_policy

    a = SkillPackManifest.parse(_make_manifest(
        "pack-a", allow_only=["INFORM", "CLARIFY"]))
    b = SkillPackManifest.parse(_make_manifest(
        "pack-b", allow_only=["CLARIFY", "REFUSE"]))
    c = SkillPackManifest.parse(_make_manifest("pack-c"))

    merged = compose_policy([a, b, c])
    assert merged["allow_only_classes"] == ["CLARIFY"]


def test_compose_policy_unions_disabled_default_classes(isolated_tenants):
    """disabled_default_classes UNIONs — any pack disabling a class
    disables it in the merged policy."""
    from axiom_firewall.skill_pack import SkillPackManifest, compose_policy

    a = SkillPackManifest.parse(_make_manifest("pack-a", disabled=["CLARIFY"]))
    b = SkillPackManifest.parse(_make_manifest("pack-b", disabled=["REFUSE"]))

    merged = compose_policy([a, b])
    assert merged["disabled_default_classes"] == ["CLARIFY", "REFUSE"]


def test_compose_policy_empty_returns_empty_policy(isolated_tenants):
    """Edge case: composing zero manifests yields an empty default
    policy shape (used when the last pack is uninstalled)."""
    from axiom_firewall.skill_pack import compose_policy

    merged = compose_policy([])
    assert merged["additional_block_patterns"] == []
    assert merged["disabled_default_classes"] == []
    assert merged["allow_only_classes"] is None


def test_migration_adds_active_column_to_legacy_db(isolated_tenants):
    """Tenant DBs created before the `active` column shipped should be
    auto-migrated on next init_install_table call, with pre-existing
    rows treated as active (DEFAULT 1)."""
    from axiom_firewall.auth import hash_password
    from axiom_firewall.db import _conn, _tenant_path, init_tenant_db, insert_tenant
    from axiom_firewall.models import Tenant
    from axiom_firewall.skill_pack import (
        SkillPackManifest, init_install_table, install_pack,
        list_installed_packs,
    )

    t = Tenant.new(email="mig@b.com", pw_hash=hash_password("longenoughpw"))
    insert_tenant(t)
    init_tenant_db(t.tenant_id)

    with _conn(_tenant_path(t.tenant_id)) as c:
        c.execute("""
            CREATE TABLE installed_pack (
                id            INTEGER PRIMARY KEY,
                pack_name     TEXT NOT NULL,
                pack_version  TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                installed_at  TEXT NOT NULL
            )
        """)
        legacy_manifest = SkillPackManifest.parse(_make_manifest("legacy-pack"))
        c.execute(
            "INSERT INTO installed_pack "
            "(pack_name, pack_version, manifest_json, installed_at) "
            "VALUES (?, ?, ?, ?)",
            ("legacy-pack", "0.1.0",
             json.dumps(legacy_manifest.to_dict(), separators=(",", ":")),
             "2026-05-01T00:00:00"),
        )

    init_install_table(t.tenant_id)
    active = list_installed_packs(t.tenant_id)
    assert [p.name for p in active] == ["legacy-pack"]

    install_pack(t.tenant_id, SkillPackManifest.parse(_make_manifest("new-pack")))
    active = list_installed_packs(t.tenant_id)
    assert {p.name for p in active} == {"legacy-pack", "new-pack"}
