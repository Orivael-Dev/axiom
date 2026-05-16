"""Axiom Skill Pack public registry — packs.orivael.dev.

Serves signed Skill Pack manifests over HTTP. The Firewall fetches
packs from this registry instead of reading from the local filesystem.

Phase 2 ship: first-party packs only. Third-party publisher keys come
later in Phase 2 when AWS KMS publisher key management is wired up.
"""
__version__ = "0.1.0"
