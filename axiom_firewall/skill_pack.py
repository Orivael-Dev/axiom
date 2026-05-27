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
    """Canonical JSON bytes over the manifest minus the signature field.

    Goes through SkillPackManifest.parse so the canonical form is the
    NORMALIZED manifest (e.g. omitted optional fields become explicit
    None) — signatures stay stable whether the source dict is sparse
    or fully-populated.
    """
    payload = {k: v for k, v in d.items() if k != "signature"}
    normalized = SkillPackManifest.parse(payload).to_dict()
    normalized.pop("signature", None)
    return json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True
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
                installed_at  TEXT    NOT NULL,
                active        INTEGER NOT NULL DEFAULT 1
            )
        """)
        # Schema migration for tenant DBs created before stacking shipped:
        # add `active` if the column doesn't exist. Rows pre-migration are
        # treated as active (DEFAULT 1) so existing single-pack tenants
        # keep their policy after upgrade.
        cols = [r["name"] for r in c.execute(
            "PRAGMA table_info(installed_pack)"
        ).fetchall()]
        if "active" not in cols:
            c.execute(
                "ALTER TABLE installed_pack "
                "ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            )


def compose_policy(manifests: list) -> dict:
    """Merge N pack policies into one effective policy.

    Semantics:
      - additional_block_patterns: UNION across all packs (every regex
        contributed by any pack is enforced).
      - disabled_default_classes: UNION (the strictest pack wins —
        anything disabled by any pack is disabled in the merge).
      - allow_only_classes: INTERSECTION across packs that specify
        one (most-restrictive wins). Packs that don't specify
        allow_only contribute nothing to the intersection — they
        accept whatever the restrictive packs allow.
    """
    if not manifests:
        return {
            "version": 1,
            "additional_block_patterns": [],
            "disabled_default_classes": [],
            "allow_only_classes": None,
        }

    block_patterns: list = []
    disabled: set = set()
    allow_only_sets: list = []

    for m in manifests:
        p = m.policy or {}
        for pat in p.get("additional_block_patterns") or []:
            block_patterns.append(pat)
        for cls in p.get("disabled_default_classes") or []:
            disabled.add(cls)
        ao = p.get("allow_only_classes")
        if ao is not None:
            allow_only_sets.append(set(ao))

    if allow_only_sets:
        intersected = set.intersection(*allow_only_sets)
        allow_only_merged = sorted(intersected) if intersected else []
    else:
        allow_only_merged = None

    return {
        "version": 1,
        "additional_block_patterns": block_patterns,
        "disabled_default_classes": sorted(disabled),
        "allow_only_classes": allow_only_merged,
    }


def _save_merged_policy(tenant_id: str) -> None:
    """Recompute the effective policy from active packs and persist it.

    If no packs remain active, delete the tenant_policy row so verdict
    flow falls back to the default classifier.
    """
    from .policy import save_policy, delete_policy

    active = list_installed_packs(tenant_id)
    if active:
        merged = compose_policy(active)
        save_policy(tenant_id, json.dumps(merged, separators=(",", ":")))
    else:
        delete_policy(tenant_id)


def install_pack(tenant_id: str, manifest: SkillPackManifest) -> None:
    """Install a pack. Stacks on top of any already-active packs.

    Behavior:
      - First install of a pack: INSERT new row with active=1
      - Re-install of an already-active pack: idempotent — updates the
        version + manifest_json in place (lets ops upgrade a pack
        without uninstall/reinstall)
      - Re-install of a previously-removed pack: revives the existing
        row (active 0 → 1) so the audit trail stays intact

    Side effects:
      - Recomputes the merged policy across all active packs and writes
        it to tenant_policy (drives verdicts).
    """
    init_install_table(tenant_id)
    now = datetime.utcnow().isoformat()
    manifest_blob = json.dumps(manifest.to_dict(), separators=(",", ":"))

    with _conn(_tenant_path(tenant_id)) as c:
        # Already active under the same name? Update in place.
        active_row = c.execute(
            "SELECT id FROM installed_pack "
            "WHERE pack_name = ? AND active = 1 LIMIT 1",
            (manifest.name,),
        ).fetchone()
        if active_row:
            c.execute(
                "UPDATE installed_pack "
                "SET pack_version = ?, manifest_json = ?, installed_at = ? "
                "WHERE id = ?",
                (manifest.version, manifest_blob, now, active_row["id"]),
            )
        else:
            # Look for an uninstalled row to revive (preserves audit).
            uninstalled_row = c.execute(
                "SELECT id FROM installed_pack "
                "WHERE pack_name = ? AND active = 0 "
                "ORDER BY id DESC LIMIT 1",
                (manifest.name,),
            ).fetchone()
            if uninstalled_row:
                c.execute(
                    "UPDATE installed_pack "
                    "SET pack_version = ?, manifest_json = ?, "
                    "    installed_at = ?, active = 1 "
                    "WHERE id = ?",
                    (manifest.version, manifest_blob, now,
                     uninstalled_row["id"]),
                )
            else:
                c.execute(
                    "INSERT INTO installed_pack "
                    "(pack_name, pack_version, manifest_json, installed_at, active) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (manifest.name, manifest.version, manifest_blob, now),
                )

    _save_merged_policy(tenant_id)


def list_installed_packs(tenant_id: str) -> list:
    """Return all currently-active installed packs, oldest-installed
    first (so install order is preserved for display)."""
    init_install_table(tenant_id)
    out: list = []
    with _conn(_tenant_path(tenant_id)) as c:
        rows = c.execute(
            "SELECT manifest_json FROM installed_pack "
            "WHERE active = 1 ORDER BY id ASC"
        ).fetchall()
    for row in rows:
        try:
            out.append(SkillPackManifest.parse(row["manifest_json"]))
        except ValueError:
            # Corrupt manifest_json — skip it, don't break the list.
            continue
    return out


def get_installed_pack(tenant_id: str) -> Optional[SkillPackManifest]:
    """Return the most-recently-installed active pack, or None.

    Backwards-compat shim for callers that predate stacking. Prefer
    `list_installed_packs()` in new code.
    """
    init_install_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        row = c.execute(
            "SELECT manifest_json FROM installed_pack "
            "WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        return SkillPackManifest.parse(row["manifest_json"])
    except ValueError:
        return None


def uninstall_pack(tenant_id: str, name: Optional[str] = None) -> None:
    """Mark a pack as uninstalled (audit trail preserved via active=0).

    If `name` is given, remove only that pack. If `name` is None,
    remove ALL active packs (legacy single-pack behavior — callers
    that want "wipe everything" still work unchanged).

    Recomputes the merged policy across remaining active packs. When
    no packs remain, deletes the tenant_policy row so verdicts fall
    back to the default classifier.
    """
    init_install_table(tenant_id)
    with _conn(_tenant_path(tenant_id)) as c:
        if name:
            c.execute(
                "UPDATE installed_pack SET active = 0 "
                "WHERE pack_name = ? AND active = 1",
                (name,),
            )
        else:
            c.execute("UPDATE installed_pack SET active = 0 WHERE active = 1")

    _save_merged_policy(tenant_id)
