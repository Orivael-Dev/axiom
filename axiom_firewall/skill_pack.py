"""Skill Pack format and signing — Phase 2 foundation.

A Skill Pack is a single-file JSON manifest that bundles a tenant
policy with metadata (name, version, author, license, tested-against
constraints). Packs are distributed via packs.orivael.dev (Phase 2
week 6) and installable into a tenant's Firewall as their active
policy.

Format stability: per docs/PHASE_1_DECISIONS.md §1, format_version
"1.0" is committed to 2-year backward compatibility (until 2028-05-16).

Manifest schema (format_version 1.0):

    {
      "format_version":   "1.0",
      "name":             "fdcpa",
      "title":            "Fair Debt Collection Practices Act",
      "description":      "...",
      "version":          "0.1.0",
      "author":           "Orivael Dev",
      "license":          "MIT",
      "homepage":         "https://docs.orivael.dev/firewall/packs/fdcpa",
      "tags":             ["compliance", "finance"],
      "tested_against":   ["axiom-firewall>=0.1.0"],
      "policy": {              # exactly the TenantPolicy schema
        "version": 1,
        "additional_block_patterns": [...],
        "disabled_default_classes": [...],
        "allow_only_classes": null
      },
      "signature":        "<hex hmac-sha256 over canonical JSON minus the signature field>"
    }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from axiom_signing import derive_key

from .db import _conn, _tenant_path, init_tenant_db
from .policy import TenantPolicy

PACK_FORMAT_VERSION = "1.0"

# Signing namespace for first-party packs published by Orivael Dev.
# Third-party publishers will get their own keys (Phase 2 week 6,
# managed via Stripe/AWS KMS); first-party packs use this one.
FIRST_PARTY_KEY_NAMESPACE = b"axiom-skill-pack-v1"

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?$")


# ─── Manifest dataclass ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SkillPackManifest:
    format_version: str
    name: str
    title: str
    description: str
    version: str
    author: str
    license: str
    policy: dict
    tags: tuple[str, ...] = field(default_factory=tuple)
    tested_against: tuple[str, ...] = field(default_factory=tuple)
    homepage: Optional[str] = None
    signature: str = ""

    @classmethod
    def parse(cls, body: str | dict) -> "SkillPackManifest":
        """Validate + load a pack from JSON bytes or a parsed dict.

        Validates structure ONLY — does not verify the signature.
        Use `verify_signature()` to check the HMAC.
        """
        d = json.loads(body) if isinstance(body, str) else body
        if not isinstance(d, dict):
            raise ValueError("Pack manifest must be a JSON object")

        required = (
            "format_version", "name", "title", "description",
            "version", "author", "license", "policy",
        )
        for k in required:
            if k not in d:
                raise ValueError(f"Pack manifest missing required field {k!r}")

        if d["format_version"] != PACK_FORMAT_VERSION:
            raise ValueError(
                f"Unsupported pack format_version {d['format_version']!r}; "
                f"this build understands {PACK_FORMAT_VERSION!r}"
            )

        if not _NAME_RE.match(d["name"]):
            raise ValueError(
                f"Pack name {d['name']!r} must be lowercase, "
                "start with a letter, and contain only a-z 0-9 - (max 64 chars)"
            )
        if not _VERSION_RE.match(d["version"]):
            raise ValueError(
                f"Pack version {d['version']!r} must be semver (e.g. 0.1.0 "
                "or 1.0.0-rc.1)"
            )

        policy_dict = d["policy"]
        if not isinstance(policy_dict, dict):
            raise ValueError("Pack 'policy' field must be a JSON object")
        # Validate via the policy parser — raises ValueError on schema mismatch.
        TenantPolicy.parse(policy_dict)

        tags = tuple(d.get("tags", ()))
        if not isinstance(tags, tuple) or not all(isinstance(t, str) for t in tags):
            raise ValueError("Pack 'tags' must be a list of strings")

        tested = tuple(d.get("tested_against", ()))
        if not isinstance(tested, tuple) or not all(isinstance(t, str) for t in tested):
            raise ValueError("Pack 'tested_against' must be a list of strings")

        homepage = d.get("homepage")
        if homepage is not None and not isinstance(homepage, str):
            raise ValueError("Pack 'homepage' must be a string or absent")

        return cls(
            format_version=d["format_version"],
            name=d["name"],
            title=d["title"],
            description=d["description"],
            version=d["version"],
            author=d["author"],
            license=d["license"],
            homepage=homepage,
            tags=tags,
            tested_against=tested,
            policy=policy_dict,
            signature=d.get("signature", ""),
        )

    def to_policy(self) -> TenantPolicy:
        """Compile the pack's policy section into a usable TenantPolicy."""
        return TenantPolicy.parse(self.policy)

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "name":           self.name,
            "title":          self.title,
            "description":    self.description,
            "version":        self.version,
            "author":         self.author,
            "license":        self.license,
            "homepage":       self.homepage,
            "tags":           list(self.tags),
            "tested_against": list(self.tested_against),
            "policy":         self.policy,
            "signature":      self.signature,
        }


# ─── Signing + verification ─────────────────────────────────────────────


def _canonical_payload(d: dict) -> bytes:
    """Canonical JSON bytes over the manifest minus the signature field."""
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sign_payload(manifest_dict: dict, key: bytes) -> str:
    """Return the HMAC-SHA256 hex signature for a manifest dict.

    Caller is responsible for setting the result on the manifest's
    `signature` field before persisting.
    """
    return hmac.new(key, _canonical_payload(manifest_dict), hashlib.sha256).hexdigest()


def sign_first_party(manifest_dict: dict) -> str:
    """Sign as a first-party (Orivael-published) pack."""
    return sign_payload(manifest_dict, derive_key(FIRST_PARTY_KEY_NAMESPACE))


def verify_signature(manifest: SkillPackManifest, key: bytes) -> bool:
    """True iff the manifest's signature was produced by the given key."""
    if not manifest.signature:
        return False
    expected = sign_payload(manifest.to_dict(), key)
    return hmac.compare_digest(manifest.signature, expected)


def verify_first_party(manifest: SkillPackManifest) -> bool:
    return verify_signature(manifest, derive_key(FIRST_PARTY_KEY_NAMESPACE))


# ─── Per-tenant install ─────────────────────────────────────────────────


def init_install_table(tenant_id: str) -> None:
    init_tenant_db(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS installed_pack (
                id            INTEGER PRIMARY KEY,
                pack_name     TEXT    NOT NULL,
                pack_version  TEXT    NOT NULL,
                manifest_json TEXT    NOT NULL,
                installed_at  TEXT    NOT NULL
            )
        """)


def install_pack(tenant_id: str, manifest: SkillPackManifest) -> None:
    """Install a pack as the tenant's active policy.

    Side effects:
      - Records the install in installed_pack (audit trail)
      - Writes the pack's policy section to tenant_policy (drives verdicts)

    Installing a new pack OVERWRITES any prior custom policy or pack. The
    tenant can subsequently edit /dashboard/policy to fork; future
    `get_installed_pack()` calls still return the original lineage so we
    can show "based on fdcpa@0.1.0" in the dashboard.
    """
    from .policy import save_policy

    init_install_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute(
            "INSERT INTO installed_pack "
            "(pack_name, pack_version, manifest_json, installed_at) "
            "VALUES (?, ?, ?, ?)",
            (
                manifest.name, manifest.version,
                json.dumps(manifest.to_dict(), separators=(",", ":")),
                datetime.utcnow().isoformat(),
            ),
        )

    # Push the pack's policy into the tenant_policy table so verdict
    # path picks it up. Re-serialize to canonical JSON for stable
    # storage.
    save_policy(tenant_id, json.dumps(manifest.policy, separators=(",", ":")))


def get_installed_pack(tenant_id: str) -> Optional[SkillPackManifest]:
    """Return the most-recently-installed pack, or None.

    Note: this returns the LINEAGE (which pack the tenant chose), not
    necessarily the active policy. Tenants who edit /dashboard/policy
    after install diverge from the pack — the dashboard surfaces that
    state as "modified".
    """
    init_install_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT manifest_json FROM installed_pack "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        return SkillPackManifest.parse(row["manifest_json"])
    except ValueError:
        return None


def uninstall_pack(tenant_id: str) -> None:
    """Forget the pack lineage. Does NOT clear the tenant_policy table —
    callers who want to revert the policy too should call
    policy.delete_policy() in addition.
    """
    init_install_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        c.execute("DELETE FROM installed_pack")
