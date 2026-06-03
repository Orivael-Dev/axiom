"""
AXIOM Marketplace — signed-agent install with bonded authority.
===============================================================
The trust spine of AX OS's signed agent/tool marketplace (§6): an agent
package is a signed ``SkillPackManifest``; installing it mints a
**bonded paired-token** whose authority a human grants and can revoke
live. The lifecycle:

  verify  → check the manifest signature + structure
  sandbox → mint a bonded pair, park it ACTIVE_PENDING (installed, NOT
            yet authorized — sandboxed)
  review  → human-readable access report (what the pack's policy grants)
  approve → transition the pair to ACTIVE_VALIDATED (scoped authority on)
  revoke  → transition to REVOKED (terminal); authority cut instantly
  authority → the gate AX OS calls before letting the agent act

This module *composes* existing primitives — ``axiom_firewall.skill_pack``
(signing) and ``axiom_event_token.bonded_pair`` (live-revocable
authority) — it does not reimplement them.

github.com/Orivael-Dev/axiom | Patent Pending ORVL-001-PROV
"""
from __future__ import annotations

from typing import Any, Optional

from axiom_firewall.skill_pack import SkillPackManifest, verify_first_party
from axiom_event_token.bonded_pair import (
    BondedPairLedger, BondedPairLedgerError, mint_pair, verify_pair,
    is_authorized,
)

SANDBOX_STATE = "ACTIVE_PENDING"
APPROVED_STATE = "ACTIVE_VALIDATED"


class MarketplaceError(RuntimeError):
    """An install/approve/revoke step was rejected."""


def _policy_report(manifest: SkillPackManifest) -> dict:
    """Human-readable summary of what a pack's policy would grant."""
    pol = manifest.policy or {}
    return {
        "additional_block_patterns": len(pol.get("additional_block_patterns", []) or []),
        "disabled_default_classes": list(pol.get("disabled_default_classes", []) or []),
        "allow_only_classes": pol.get("allow_only_classes"),
        "tags": list(manifest.tags),
    }


class Marketplace:
    """Signed-agent install + bonded-authority lifecycle."""

    def __init__(self, ledger_path: Optional[Any] = None):
        self._ledger = BondedPairLedger(ledger_path)

    # ── verify ───────────────────────────────────────────────────
    def verify(self, manifest_dict: dict) -> dict:
        """Parse + signature-check an agent manifest (no install)."""
        try:
            manifest = SkillPackManifest.parse(manifest_dict)
        except ValueError as e:
            return {"valid": False, "error": f"malformed manifest: {e}"}
        valid = verify_first_party(manifest)
        return {
            "valid": bool(valid),
            "name": manifest.name,
            "version": manifest.version,
            "author": manifest.author,
            "title": manifest.title,
        }

    # ── sandbox install ──────────────────────────────────────────
    def sandbox_install(self, manifest_dict: dict) -> dict:
        """Verify, then mint a bonded pair parked in the sandbox state.

        Installed but NOT authorized — the agent has no live authority
        until a human approves it.
        """
        manifest = SkillPackManifest.parse(manifest_dict)
        if not verify_first_party(manifest):
            raise MarketplaceError(
                f"refusing to install {manifest.name!r}: signature invalid")
        primary, mirror = mint_pair(
            {"agent": manifest.name, "version": manifest.version,
             "scope": _policy_report(manifest)},
            {"role": "axiom-authority", "agent": manifest.name},
        )
        if not verify_pair(primary, mirror):
            raise MarketplaceError("bonded pair failed self-verification")
        pair_id = primary.pair_id
        # init -> ACTIVE_VALIDATED (genesis), then park in the sandbox.
        self._ledger.init_pair(pair_id, actor="marketplace")
        self._ledger.transition(pair_id, SANDBOX_STATE, actor="marketplace")
        return {
            "installed": True,
            "agent": manifest.name,
            "version": manifest.version,
            "pair_id": pair_id,
            "state": SANDBOX_STATE,
            "authorized": False,
            "primary_signature": primary.signature,
            "mirror_signature": mirror.signature,
        }

    # ── review ───────────────────────────────────────────────────
    def review(self, manifest_dict: dict, pair_id: str) -> dict:
        """Access report for the human: what the pack grants + state."""
        manifest = SkillPackManifest.parse(manifest_dict)
        return {
            "agent": manifest.name,
            "version": manifest.version,
            "author": manifest.author,
            "requested_access": _policy_report(manifest),
            "state": self._ledger.current_state(pair_id),
            "authorized": is_authorized(self._ledger, pair_id),
        }

    # ── approve / revoke ─────────────────────────────────────────
    def approve(self, pair_id: str, actor: str = "human") -> dict:
        try:
            self._ledger.transition(pair_id, APPROVED_STATE, actor=actor)
        except BondedPairLedgerError as e:
            raise MarketplaceError(str(e))
        return {"pair_id": pair_id, "state": APPROVED_STATE,
                "authorized": is_authorized(self._ledger, pair_id)}

    def revoke(self, pair_id: str, actor: str = "human") -> dict:
        try:
            self._ledger.revoke(pair_id, actor=actor)
        except BondedPairLedgerError as e:
            raise MarketplaceError(str(e))
        return {"pair_id": pair_id, "state": "REVOKED",
                "authorized": is_authorized(self._ledger, pair_id)}

    # ── authority gate ───────────────────────────────────────────
    def authority(self, pair_id: str) -> dict:
        """The gate AX OS calls before letting an installed agent act."""
        return {
            "pair_id": pair_id,
            "state": self._ledger.current_state(pair_id),
            "authorized": is_authorized(self._ledger, pair_id),
            "chain_verified": self._ledger.verify_chain(),
        }


if __name__ == "__main__":
    print("AXIOM Marketplace — signed-agent install with bonded authority")
    print("  from axiom_marketplace import Marketplace")
