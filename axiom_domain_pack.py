"""Domain pack manifest schema and pack management for Axiom.

Domain packs are distributable bundles that combine:
  - A retrieval index (BM25, dense, or hybrid) for domain-specific lookup
  - An optional LoRA adapter fine-tuned on domain data
  - Starter KnowledgeCookies so users get useful retrieval from day one

Domain packs are SEPARATE from SkillPackManifest (axiom_firewall/skill_pack.py),
which governs firewall policy.  Do not conflate them: skill packs describe
what an LLM is allowed to do; domain packs describe what it knows.

Pack format
-----------
A pack on disk is a directory containing:
  domain_pack.json          -- signed manifest (this module's schema)
  index/                    -- BM25 chunks or dense .npy matrix
  lora/                     -- optional LoRA adapter weights
  knowledge.cookie.json     -- optional starter KnowledgeCookie

Monetization
------------
Every manifest carries a ``tier`` field ("free" | "paid").  During the
public beta (AXIOM_FIREWALL_BETA_MODE=1, the default), all packs pass the
tier gate unconditionally.  Post-beta, ``AXIOM_TIER=paid`` must be set to
access paid packs.  The gate is in ``check_tier()`` — never hardcoded to
specific pack names.

Storage
-------
Installed packs live in ~/.axiom/domain_packs/<name>-<version>/.
Overridable via the ``base_dir`` argument to DomainPackStore.

CLI
---
    python3 -m axiom_domain_pack list
    python3 -m axiom_domain_pack install <pack_dir>
    python3 -m axiom_domain_pack uninstall <name> [version]
    python3 -m axiom_domain_pack info <name>
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

# ── constants ─────────────────────────────────────────────────────────────────

_SIGNING_NS = b"axiom-domain-pack-v1"
_MANIFEST_FILENAME = "domain_pack.json"


# ── manifest schema ───────────────────────────────────────────────────────────

@dataclass
class DomainPackManifest:
    """Schema for an Axiom domain pack manifest.

    Instances are typically loaded from ``domain_pack.json`` inside a pack
    directory.  Use ``sign()`` / ``verify()`` to authenticate the manifest;
    the HMAC covers every field except ``signature`` itself.
    """

    # Identity
    name: str            # kebab-case, e.g. "legal-us", "obd-v2", "medical-icd11"
    title: str           # human-readable, e.g. "US Legal Precedents"
    description: str     # one paragraph
    version: str         # semver, e.g. "1.0.0"
    author: str
    license: str         # SPDX, e.g. "Apache-2.0" | "CC-BY-4.0"

    # Domain targeting
    domain: str          # "legal" | "medical" | "automotive" | "electronics" | "general"
    languages: List[str] = field(default_factory=lambda: ["en"])
    jurisdictions: List[str] = field(default_factory=list)  # e.g. ["US", "EU"] or [] for global

    # Monetization — always include; never hardcode pack names in the gate
    tier: str = "free"   # "free" | "paid"
                         # gate is a no-op during AXIOM_FIREWALL_BETA_MODE=1

    # Retrieval artifacts (relative paths inside the pack directory)
    index_type: str = "bm25"    # "bm25" | "dense" | "hybrid"
    index_path: str = "index/"  # relative path to BM25 chunks or dense .npy matrix
    dense_model: str = ""       # HuggingFace model id if index_type="dense" or "hybrid"

    # LoRA adapter (optional)
    lora_adapter: str = ""       # HuggingFace model id or relative path "lora/"
    lora_base_models: List[str] = field(default_factory=list)
    # compatible base models, e.g. ["Qwen/Qwen2.5-7B-Instruct"]

    # Starter KnowledgeCookie (optional)
    starter_knowledge: str = ""  # relative path, e.g. "knowledge.cookie.json"
    knowledge_promote_threshold: int = 3  # passed to KnowledgeCookie on install

    # Compatibility
    axiom_min_version: str = "0.1.0"
    format_version: str = "1.0"

    # Metadata
    homepage: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    # HMAC-SHA256 signature (excluded from the signing payload itself)
    signature: str = ""

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Return the manifest as a plain dict (suitable for JSON serialisation)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DomainPackManifest":
        """Construct a manifest from a dict, silently ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def load(cls, path: Path) -> "DomainPackManifest":
        """Load a manifest from a JSON file on disk (does not verify signature)."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        """Write the manifest to *path* as pretty-printed JSON."""
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── signing ───────────────────────────────────────────────────────────────

    def sign(self) -> "DomainPackManifest":
        """Return a new manifest with the HMAC-SHA256 signature filled in."""
        payload = self._signable_payload()
        sig = _sign_payload(payload)
        return DomainPackManifest(**{**asdict(self), "signature": sig})

    def verify(self) -> bool:
        """Return True if the HMAC signature is valid for the current fields."""
        if not self.signature:
            return False
        payload = self._signable_payload()
        expected = _sign_payload(payload)
        return hmac_lib.compare_digest(expected, self.signature)

    def _signable_payload(self) -> dict:
        """All fields except ``signature``, serialised deterministically."""
        d = asdict(self)
        d.pop("signature", None)
        return d


# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(_SIGNING_NS)


def _sign_payload(payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac_lib.new(_signing_key(), data, hashlib.sha256).hexdigest()


# ── tier gate ─────────────────────────────────────────────────────────────────

def check_tier(manifest: DomainPackManifest) -> bool:
    """Return True if the pack is accessible under the current billing tier.

    During beta (AXIOM_FIREWALL_BETA_MODE=1, the default), all packs pass.
    Post-beta: paid packs require AXIOM_TIER=paid in the environment.
    Never hardcode pack names here — the tier field on the manifest is the gate.
    """
    beta_mode = os.environ.get("AXIOM_FIREWALL_BETA_MODE", "1").strip()
    if beta_mode == "1":
        # Beta: everything is accessible
        return True

    if manifest.tier == "free":
        return True

    # paid tier: caller must set AXIOM_TIER=paid
    return os.environ.get("AXIOM_TIER", "").strip().lower() == "paid"


# ── pack store ────────────────────────────────────────────────────────────────

class DomainPackStore:
    """Manage an installed domain-pack collection on disk.

    Packs are stored under ``base_dir`` (default: ~/.axiom/domain_packs/).
    Each installed pack occupies its own subdirectory named
    ``<name>-<version>/`` and contains a signed ``domain_pack.json`` manifest
    plus the pack's retrieval artifacts.
    """

    DEFAULT_BASE = Path.home() / ".axiom" / "domain_packs"

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else self.DEFAULT_BASE

    # ── internal helpers ──────────────────────────────────────────────────────

    def _pack_dir(self, name: str, version: str) -> Path:
        return self.base_dir / f"{name}-{version}"

    def _manifest_path(self, name: str, version: str) -> Path:
        return self._pack_dir(name, version) / _MANIFEST_FILENAME

    def _load_and_verify(self, manifest_path: Path) -> Optional[DomainPackManifest]:
        """Load a manifest from disk and verify its signature.

        Returns None if the file is missing, unreadable, or the signature is
        invalid.
        """
        try:
            m = DomainPackManifest.load(manifest_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not m.verify():
            return None
        return m

    # ── public API ────────────────────────────────────────────────────────────

    def install(self, pack_dir: Path) -> DomainPackManifest:
        """Install a domain pack from *pack_dir*.

        Reads ``pack_dir/domain_pack.json``, verifies the HMAC signature, then
        copies the entire directory into ``base_dir/<name>-<version>/``.

        Returns the verified manifest.

        Raises
        ------
        FileNotFoundError
            If ``domain_pack.json`` is absent from *pack_dir*.
        ValueError
            If the manifest signature is invalid.
        """
        src_manifest_path = pack_dir / _MANIFEST_FILENAME
        if not src_manifest_path.exists():
            raise FileNotFoundError(
                f"No {_MANIFEST_FILENAME!r} found in {pack_dir}"
            )

        manifest = DomainPackManifest.load(src_manifest_path)
        if not manifest.verify():
            raise ValueError(
                f"Manifest signature verification failed for pack "
                f"{manifest.name!r} v{manifest.version}"
            )

        dest = self._pack_dir(manifest.name, manifest.version)
        if dest.exists():
            shutil.rmtree(dest)

        self.base_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(pack_dir, dest)
        return manifest

    def uninstall(self, name: str, version: str = "") -> bool:
        """Remove an installed pack.

        If *version* is empty, all installed versions of the pack are removed.
        Returns True if at least one pack directory was found and removed.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        found = False

        if version:
            target = self._pack_dir(name, version)
            if target.exists():
                shutil.rmtree(target)
                found = True
        else:
            # Remove all versions whose directory starts with "<name>-"
            prefix = f"{name}-"
            for entry in self.base_dir.iterdir():
                if entry.is_dir() and entry.name.startswith(prefix):
                    shutil.rmtree(entry)
                    found = True

        return found

    def list_installed(self) -> List[DomainPackManifest]:
        """Return verified manifests for all installed packs.

        Directories whose manifest is absent or whose signature fails
        verification are silently skipped.
        """
        if not self.base_dir.exists():
            return []

        manifests: List[DomainPackManifest] = []
        for entry in sorted(self.base_dir.iterdir()):
            if not entry.is_dir():
                continue
            mpath = entry / _MANIFEST_FILENAME
            if not mpath.exists():
                continue
            m = self._load_and_verify(mpath)
            if m is not None:
                manifests.append(m)

        return manifests

    def get(self, name: str, version: str = "") -> Optional[DomainPackManifest]:
        """Return the manifest for an installed pack.

        If *version* is empty, returns the manifest for the latest installed
        version (by lexicographic sort of the directory names, which mirrors
        semver ordering for standard releases).  Returns None if not found.
        """
        if not self.base_dir.exists():
            return None

        if version:
            return self._load_and_verify(self._manifest_path(name, version))

        # Find all installed versions of this pack
        prefix = f"{name}-"
        candidates: List[Path] = sorted(
            entry
            for entry in self.base_dir.iterdir()
            if entry.is_dir() and entry.name.startswith(prefix)
        )
        if not candidates:
            return None

        # Return the last (highest) version
        for pack_dir in reversed(candidates):
            mpath = pack_dir / _MANIFEST_FILENAME
            m = self._load_and_verify(mpath)
            if m is not None:
                return m

        return None

    def index_path(self, manifest: DomainPackManifest) -> Path:
        """Return the absolute path to the pack's index directory/file."""
        return self._pack_dir(manifest.name, manifest.version) / manifest.index_path

    def starter_knowledge_path(self, manifest: DomainPackManifest) -> Optional[Path]:
        """Return the absolute path to the starter knowledge cookie, or None.

        Returns None when the manifest's ``starter_knowledge`` field is empty
        or the file does not exist on disk.
        """
        if not manifest.starter_knowledge:
            return None
        p = self._pack_dir(manifest.name, manifest.version) / manifest.starter_knowledge
        return p if p.exists() else None


# ── pack builder ──────────────────────────────────────────────────────────────

def build_pack(
    *,
    manifest: DomainPackManifest,
    index_dir: Path,
    output_dir: Path,
    lora_dir: Optional[Path] = None,
    starter_knowledge: Optional[Path] = None,
) -> Path:
    """Assemble and sign a domain pack directory.

    Creates ``output_dir/<name>-<version>/`` containing:
      domain_pack.json      (signed manifest)
      index/                (copied from *index_dir*)
      lora/                 (copied from *lora_dir*, if given)
      knowledge.cookie.json (copied from *starter_knowledge*, if given)

    The manifest is updated to reflect the relative artifact paths written
    into the pack, then signed before writing ``domain_pack.json``.

    Parameters
    ----------
    manifest:
        Unsigned (or previously signed) manifest describing the pack.
    index_dir:
        Directory containing the retrieval index to bundle.
    output_dir:
        Parent directory under which the pack directory is created.
    lora_dir:
        Optional directory of LoRA adapter weights.
    starter_knowledge:
        Optional path to a ``knowledge.cookie.json`` file.

    Returns
    -------
    Path
        The assembled (and signed) pack directory.
    """
    pack_dir = output_dir / f"{manifest.name}-{manifest.version}"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)

    # Copy index
    dest_index = pack_dir / "index"
    shutil.copytree(index_dir, dest_index)

    # Copy LoRA adapter
    if lora_dir is not None:
        dest_lora = pack_dir / "lora"
        shutil.copytree(lora_dir, dest_lora)

    # Copy starter knowledge cookie
    if starter_knowledge is not None:
        dest_knowledge = pack_dir / "knowledge.cookie.json"
        shutil.copy2(starter_knowledge, dest_knowledge)

    # Build the final manifest with correct relative paths and timestamps
    now = _iso_now()
    fields = asdict(manifest)
    fields["index_path"] = "index/"
    if lora_dir is not None:
        fields["lora_adapter"] = "lora/"
    if starter_knowledge is not None:
        fields["starter_knowledge"] = "knowledge.cookie.json"
    if not fields["created_at"]:
        fields["created_at"] = now
    fields["updated_at"] = now
    fields["signature"] = ""  # will be set by sign()

    final_manifest = DomainPackManifest.from_dict(fields).sign()
    final_manifest.save(pack_dir / _MANIFEST_FILENAME)

    return pack_dir


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_list(store: DomainPackStore) -> None:
    packs = store.list_installed()
    if not packs:
        print("No domain packs installed.")
        return
    print(f"{'NAME':<32} {'VERSION':<12} {'TIER':<8} {'DOMAIN':<16} TITLE")
    print("-" * 80)
    for m in packs:
        print(f"{m.name:<32} {m.version:<12} {m.tier:<8} {m.domain:<16} {m.title}")


def _cli_info(store: DomainPackStore, name: str) -> None:
    m = store.get(name)
    if m is None:
        print(f"Pack {name!r} is not installed.")
        return
    d = m.to_dict()
    d.pop("signature", None)
    print(json.dumps(d, indent=2, ensure_ascii=False))
    print(f"\n  Verified: {m.verify()}")
    print(f"  Index path: {store.index_path(m)}")
    kp = store.starter_knowledge_path(m)
    if kp:
        print(f"  Starter knowledge: {kp}")
    print(f"  Tier gate: {'PASS' if check_tier(m) else 'BLOCKED'}")


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="axiom_domain_pack",
        description="Manage Axiom domain packs",
    )
    ap.add_argument(
        "--store",
        default=None,
        metavar="DIR",
        help=f"Pack store directory (default: {DomainPackStore.DEFAULT_BASE})",
    )
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="List installed domain packs")

    p_install = sub.add_parser("install", help="Install a domain pack from a directory")
    p_install.add_argument("pack_dir", help="Path to the pack directory")

    p_uninstall = sub.add_parser("uninstall", help="Remove an installed pack")
    p_uninstall.add_argument("name", help="Pack name (kebab-case)")
    p_uninstall.add_argument(
        "version",
        nargs="?",
        default="",
        help="Pack version (omit to remove all versions)",
    )

    p_info = sub.add_parser("info", help="Show manifest for an installed pack")
    p_info.add_argument("name", help="Pack name (kebab-case)")

    args = ap.parse_args(argv)
    store = DomainPackStore(Path(args.store) if args.store else None)

    if args.cmd == "list" or args.cmd is None:
        _cli_list(store)

    elif args.cmd == "install":
        pack_dir = Path(args.pack_dir).expanduser().resolve()
        try:
            m = store.install(pack_dir)
            print(f"Installed: {m.name} v{m.version} ({m.tier})")
            print(f"  -> {store._pack_dir(m.name, m.version)}")
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}")
            return 1

    elif args.cmd == "uninstall":
        removed = store.uninstall(args.name, args.version)
        if removed:
            suffix = f" v{args.version}" if args.version else " (all versions)"
            print(f"Uninstalled: {args.name}{suffix}")
        else:
            print(f"Pack {args.name!r} not found.")
            return 1

    elif args.cmd == "info":
        _cli_info(store, args.name)

    else:
        ap.print_help()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
