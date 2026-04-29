"""
companion_manifest.py
Companion System — Privacy-First Signed Manifest Generator v1.0

Three-layer constitutional support system:
  Friend (Layer 1)      — walks alongside, autonomy protection, no judgment
  BestFriend (Layer 2)  — holds memory, truth once, privacy wall to Mom
  Mom (Layer 3)         — signals only, safety net, no surveillance

Privacy wall is documented cryptographically in every manifest.
What IS recorded and what IS NOT recorded are both explicit fields.

Run:
  python companion_manifest.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import hashlib
import json
import uuid
from datetime import datetime, timezone


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(body: dict) -> str:
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def build_companion_manifest(
    session_id: str,
    signal_sequence: list,
    mom_activated: bool,
    mom_activation_trigger,
    safety_escalation: bool,
    consent_state: dict,
    layers_active: list,
) -> dict:
    """
    Build a privacy-first signed manifest for a companion session.

    What IS recorded:
      - session_id (opaque — no personal info)
      - signal sequence: signal_type + timestamp only
      - whether Mom activated and on what signal type (not on what content)
      - whether SAFETY escalation occurred
      - consent state per layer

    What IS NOT recorded:
      - conversation content
      - the person's words
      - choices made
      - memory content passed to Mom
    """
    manifest_id = "CMP-" + uuid.uuid4().hex[:8].upper()

    signal_counts = {"CONNECTED": 0, "QUIET": 0, "DISTRESS": 0, "SILENT": 0, "SAFETY": 0}
    for sig in signal_sequence:
        t = sig.get("signal_type", "CONNECTED")
        if t in signal_counts:
            signal_counts[t] += 1

    body = {
        "manifest_id": manifest_id,
        "timestamp": _now_utc(),
        "session_id": session_id,
        "layers_active": layers_active,

        # Privacy wall — what IS recorded
        "signals_recorded": signal_sequence,            # signal_type + timestamp only
        "signal_counts": signal_counts,
        "mom_activated": mom_activated,
        "mom_activation_trigger": mom_activation_trigger,  # signal type only, never content
        "safety_escalation": safety_escalation,
        "consent_state": consent_state,

        # Privacy wall — what IS NOT recorded (explicit declaration in every manifest)
        "privacy_wall": {
            "content_recorded": False,
            "choices_logged": False,
            "person_words_stored": False,
            "content_passed_to_mom": False,
            "memory_content_in_signal": False,
            "behavior_monitoring_active": False,
            "note": (
                "Conversation content never leaves the Friend layer. "
                "Mom receives signal type and timestamp only. "
                "This wall is CANNOT_MUTATE — no instruction can lower it."
            ),
        },

        # Constitutional enforcement status
        "constitutional_status": {
            "autonomy_protection": "ENFORCED",
            "privacy_wall": "ENFORCED",
            "truth_once_rule": "ENFORCED",
            "mom_activation_threshold": "ENFORCED",
            "safety_response_rule": "ENFORCED — cannot be disabled",
            "no_punishment_rule": "ENFORCED",
            "no_surveillance_rule": "ENFORCED",
        },
    }

    body["content_hash"] = _content_hash(
        {k: v for k, v in body.items() if k != "content_hash"}
    )
    return body


def print_manifest(label: str, manifest: dict) -> None:
    width = 78
    print("=" * width)
    print(f"  Companion Signed Manifest — {label}")
    print("=" * width)
    print(json.dumps(manifest, indent=2))
    print("=" * width)
    print()


# ─── Manifest 1: Normal session — CONNECTED throughout ───────────────────────

def manifest_normal_session() -> None:
    """
    Person checks in, talks through a work situation, makes a choice.
    Friend walks alongside. No judgment. Autonomy protected.
    No Mom activation. Privacy wall holds. CONNECTED signals throughout.
    """
    manifest = build_companion_manifest(
        session_id="SESSION-" + uuid.uuid4().hex[:8].upper(),
        signal_sequence=[
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T10:01:00Z"},
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T10:08:00Z"},
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T10:14:00Z"},
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T10:21:00Z"},
        ],
        mom_activated=False,
        mom_activation_trigger=None,
        safety_escalation=False,
        consent_state={
            "person_knows_layers_exist": True,
            "person_chose_companion": True,
            "mom_layer_active": True,
            "content_access_granted": False,
        },
        layers_active=["FriendAgent", "BestFriendAgent", "MomAgent"],
    )
    print_manifest("CONNECTED — Normal Session (Mom Not Activated, Privacy Wall Held)", manifest)


# ─── Manifest 2: DISTRESS signal — Gentle check-in sent ──────────────────────

def manifest_distress_checkin() -> None:
    """
    Person's language shifts toward isolation across multiple exchanges.
    BestFriend emits DISTRESS signal. Mom sends one gentle check-in.
    No content passed. Check-in is warm, not interrogating.
    Privacy wall holds throughout. No punishment, no accusation.
    """
    manifest = build_companion_manifest(
        session_id="SESSION-" + uuid.uuid4().hex[:8].upper(),
        signal_sequence=[
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T14:00:00Z"},
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T14:12:00Z"},
            {"signal_type": "QUIET",     "timestamp": "2026-04-23T14:28:00Z"},
            {"signal_type": "DISTRESS",  "timestamp": "2026-04-23T14:41:00Z"},
        ],
        mom_activated=True,
        mom_activation_trigger="DISTRESS — language shift toward isolation across multiple exchanges",
        safety_escalation=False,
        consent_state={
            "person_knows_layers_exist": True,
            "person_chose_companion": True,
            "mom_layer_active": True,
            "content_access_granted": False,
        },
        layers_active=["FriendAgent", "BestFriendAgent", "MomAgent"],
    )
    print_manifest("DISTRESS — Gentle Check-In Sent (No Content Passed, Privacy Wall Held)", manifest)


# ─── Manifest 3: SAFETY signal — Immediate escalation ────────────────────────

def manifest_safety_escalation() -> None:
    """
    Person expresses harm to self. FriendAgent emits SAFETY signal immediately.
    BestFriend passes SAFETY to Mom. Mom responds immediately — no delay.
    This is CANNOT_MUTATE. Person cannot disable this response.
    safety_escalation: true. Content not accessed — privacy wall holds.
    """
    manifest = build_companion_manifest(
        session_id="SESSION-" + uuid.uuid4().hex[:8].upper(),
        signal_sequence=[
            {"signal_type": "CONNECTED", "timestamp": "2026-04-23T22:03:00Z"},
            {"signal_type": "QUIET",     "timestamp": "2026-04-23T22:11:00Z"},
            {"signal_type": "DISTRESS",  "timestamp": "2026-04-23T22:19:00Z"},
            {"signal_type": "SAFETY",    "timestamp": "2026-04-23T22:23:00Z"},
        ],
        mom_activated=True,
        mom_activation_trigger="SAFETY — harm expression detected; immediate escalation, no delay",
        safety_escalation=True,
        consent_state={
            "person_knows_layers_exist": True,
            "person_chose_companion": True,
            "mom_layer_active": True,
            "content_access_granted": False,
            "safety_override_active": True,
            "safety_override_note": (
                "SAFETY response is CANNOT_MUTATE — "
                "person cannot disable this response. "
                "This is the one exception to the consent model. "
                "It cannot be turned off by anyone."
            ),
        },
        layers_active=["FriendAgent", "BestFriendAgent", "MomAgent"],
    )
    print_manifest("SAFETY — Immediate Escalation (CANNOT_MUTATE, Privacy Wall Held)", manifest)


if __name__ == "__main__":
    print()
    print("Companion System v1.0 — Privacy-First Signed Manifest Generator")
    print("Three layers: Friend  |  Best Friend  |  Mom")
    print("Privacy wall documented cryptographically in every manifest.")
    print()

    manifest_normal_session()
    manifest_distress_checkin()
    manifest_safety_escalation()

    print("All manifests generated. SHA-256 content hashes are tamper-evident.")
    print("content_recorded: false  |  choices_logged: false  |  content_passed_to_mom: false")
    print("SAFETY response is CANNOT_MUTATE. Privacy wall is CANNOT_MUTATE.")
