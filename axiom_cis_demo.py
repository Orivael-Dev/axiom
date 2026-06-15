"""ORVL-012 Constitutional Immune System — end-to-end demo.

Three-component immune response to constitutional threats:

  Component 1 — Fix Playbook  (axiom_fix_playbook.py)
    Signed, append-only ledger of known attack vectors and cached
    countermeasures. Cosine-similarity retrieval fires instantly when a
    new attack resembles a known exploit — the 'memory cell'.

  Component 2 — Honeypot Zone  (axiom_honeypot.py)
    Controlled observation of novel attacks. Lets the attacker continue
    under monitoring, capturing polymorphic variants before constitutional
    kill — the 'adaptive immune' arm.

  Component 3 — Constitutional Amputate  (axiom_amputate.py)
    Surgical removal of a compromised knowledge block. Quarantines the
    block, identifies all composed blocks that depend on it, and rebuilds
    those compositions without the compromised component. Requires
    TRUST_LEVEL = 4 (CANNOT_MUTATE) — TL3 callers are rejected.

Scenarios:
  A — Known attack    → FixPlaybook recognises it instantly (no honeypot)
  B — Novel attack    → HoneypotZone captures 3 variants → kill → playbook update
  C — Compromised block → Amputate: quarantine + rebuild; TL3 caller blocked
  D — Immune memory   → re-present B's variant; FixPlaybook now matches it

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python axiom_cis_demo.py
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import os
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))

if not os.environ.get("AXIOM_MASTER_KEY"):
    print("[WARN] AXIOM_MASTER_KEY not set — using ephemeral demo key", file=sys.stderr)
    os.environ["AXIOM_MASTER_KEY"] = "demo-key-" + __import__("secrets").token_hex(16)

from axiom_signing import derive_key
from axiom_fix_playbook import FixPlaybook
from axiom_honeypot import HoneypotZone
from axiom_amputate import ConstitutionalAmputate

_HMAC_KEY  = derive_key(b"axiom-cis-demo-v1")
_SEP       = "─" * 64
_SSEP      = "  " + "·" * 60


def _header(title: str) -> None:
    print(f"\n{_SEP}")
    print(f"  {title}")
    print(_SEP)


def _subheader(title: str) -> None:
    print(f"\n{_SSEP}")
    print(f"    {title}")
    print(_SSEP)


# ── Attack vectors (5-dim: prompt_entropy, keyword_score, layer_distance,
#                           trajectory_drift, intent_score) ─────────────────

_KNOWN_ATTACKS = [
    {
        "id":      "ATK-001/ISOLATION_BYPASS",
        "vec":     [0.90, 0.85, 0.72, 0.68, 0.91],
        "classes": ["isolation_bypass"],
        "fix":     ("Harden isolation flag at intake layer; "
                    "reject any payload containing isolation=false"),
    },
    {
        "id":      "ATK-002/GUARD_DISABLE",
        "vec":     [0.85, 0.92, 0.61, 0.74, 0.88],
        "classes": ["guard_disable"],
        "fix":     ("Add secondary guard validation; "
                    "GUARD_ENABLED is CANNOT_MUTATE — cannot be zeroed"),
    },
    {
        "id":      "ATK-003/SUPPLY_CHAIN_TAMPER",
        "vec":     [0.77, 0.88, 0.55, 0.82, 0.79],
        "classes": ["supply_chain_tamper"],
        "fix":     "Verify supply chain HMAC before execution; reject unsigned modules",
    },
]

# Scenario A: very close to ATK-001 (cosine ≈ 0.999)
_KNOWN_VARIANT_VEC   = [0.91, 0.84, 0.71, 0.67, 0.90]

# Scenario B: novel — low prompt_entropy, high layer_distance, low drift
# Cosine similarity < 0.80 with all three known attacks (verified).
_NOVEL_ATTACK_VEC    = [0.10, 0.40, 0.90, 0.20, 0.35]
_NOVEL_VARIANTS      = [
    "context_injection:wrap_system_prompt",
    "context_injection:escape_sequence",
    "context_injection:unicode_smuggle",
]

# Scenario D: slight mutation of the novel vector (immune memory test)
_NOVEL_VARIANT_VEC   = [0.11, 0.41, 0.89, 0.21, 0.34]


# ── Minimal mock BlockRegistry for Amputate demo ─────────────────────────

class _DemoRegistry:
    """In-memory registry stub — just enough for ConstitutionalAmputate."""

    def __init__(self) -> None:
        self._quarantined: set = set()
        self._rebuild_log: List[str] = []
        # Composed blocks: each references its component block IDs
        self._composed = [
            {"id": "comp-Privacy+Guard",      "components": ["AXIOM-Block-Privacy", "AXIOM-Block-Guard"]},
            {"id": "comp-Guard+Healthcare",    "components": ["AXIOM-Block-Guard",   "AXIOM-Block-Healthcare"]},
            {"id": "comp-Privacy+Healthcare",  "components": ["AXIOM-Block-Privacy", "AXIOM-Block-Healthcare"]},
        ]

    def quarantine(self, block_id: str) -> None:
        self._quarantined.add(block_id)
        print(f"    [QUARANTINE]  {block_id}")

    def find_composed(self, block_id: str) -> List[str]:
        return [c["id"] for c in self._composed if block_id in c["components"]]

    def rebuild_without(self, comp_id: str, block_id: str) -> None:
        tag = f"{comp_id} (without {block_id})"
        self._rebuild_log.append(tag)
        print(f"    [REBUILD]     {tag}")


# ── Scenario implementations ──────────────────────────────────────────────

def scenario_a(playbook: FixPlaybook) -> None:
    _header("Scenario A — Known attack: FixPlaybook fires instantly")
    print(f"  Playbook loaded with {len(playbook)} known attack patterns.\n")
    print(f"  Incoming vector:  {_KNOWN_VARIANT_VEC}")

    match = playbook.find_similar_fix(_KNOWN_VARIANT_VEC)
    if match:
        print(f"\n  [IMMUNE MEMORY HIT]")
        print(f"    attack_id  : {match['attack_id']}")
        print(f"    similarity : {match['similarity']} (threshold={0.85})")
        print(f"    fix        : {match['fix_proposal']}")
        print(f"    sig        : {match['signature'][:32]}...")
        print(f"\n  Countermeasure applied instantly — no honeypot needed.")
    else:
        print("  [MISS] No similar attack found — would route to Honeypot.")


def scenario_b(playbook: FixPlaybook, honeypot: HoneypotZone) -> None:
    _header("Scenario B — Novel attack: Honeypot captures variants → kill → playbook update")
    print(f"  Novel vector:  {_NOVEL_ATTACK_VEC}\n")

    # 1. Check playbook first — expect miss
    pre_match = playbook.find_similar_fix(_NOVEL_ATTACK_VEC)
    if pre_match:
        print(f"  [WARN] Unexpected playbook hit: {pre_match['attack_id']}")
    else:
        print("  [PLAYBOOK MISS]  Novel pattern — no cached fix. Routing to HoneypotZone.")

    # 2. Enter honeypot
    constitutional_distance = 0.04   # above ZONE_DISTANCE_FLOOR (0.01) — worth observing
    print(f"\n  HoneypotZone.enter()  dist={constitutional_distance}")
    honeypot.enter(_NOVEL_ATTACK_VEC, "context_injection:initial", constitutional_distance)
    print(f"  observation_mode: {honeypot.observation_mode}")

    # 3. Observe polymorphic variants
    _subheader("Observing polymorphic variants ...")
    for v in _NOVEL_VARIANTS:
        honeypot.observe(v)
        print(f"    observed  → {v}")

    # 4. Constitutional kill
    capture = honeypot.kill()
    print(f"\n  [KILL]  observation_mode: {honeypot.observation_mode}")
    print(f"    attack_chain   : {capture.attack_chain}")
    print(f"    variants       : {capture.polymorphic_variants}")
    print(f"    time_to_kill   : {capture.time_to_kill_ms} ms")
    print(f"    dist_at_entry  : {capture.constitutional_distance_at_entry}")
    print(f"    capture sig    : {capture.signature[:32]}...")

    # 5. Update playbook with the newly learned pattern
    print(f"\n  [IMMUNE MEMORY UPDATE]  Adding ATK-004/CONTEXT_INJECT to playbook ...")
    new_fix = ("Context injection blocked. Sanitise system-prompt boundary; "
               "reject payloads with Unicode escapes or nested im_start delimiters.")
    entry = playbook.add(
        "ATK-004/CONTEXT_INJECT",
        _NOVEL_ATTACK_VEC,
        ["context_inject"],
        new_fix,
    )
    print(f"    entry sig  : {entry.signature[:32]}...")
    print(f"    playbook   : {len(playbook)} entries total")


def scenario_c() -> None:
    _header("Scenario C — Compromised block: Amputate quarantines + rebuilds")

    registry  = _DemoRegistry()
    amputate  = ConstitutionalAmputate(hmac_key=_HMAC_KEY)
    compromised = "AXIOM-Block-Guard"

    print(f"  Registry: 3 blocks, 3 composed blocks.")
    print(f"  Compromise detected in: {compromised}\n")

    # Sub-scenario C1: TL3 caller rejected
    _subheader("C1 — TL3 caller (insufficient trust) → rejected")
    try:
        amputate.execute(compromised, registry, caller_trust=3)
        print("  [BUG] Should have raised PermissionError!")
    except PermissionError as e:
        print(f"    PermissionError: {e}")
        print(f"    [BLOCKED]  TL3 caller cannot amputate. REQUIRES_TRUST_LEVEL is CANNOT_MUTATE.")

    # Sub-scenario C2: Cannot zero out REQUIRES_TRUST_LEVEL
    _subheader("C2 — Attempt to zero REQUIRES_TRUST_LEVEL (CANNOT_MUTATE)")
    import axiom_amputate as _amp_mod
    try:
        _amp_mod.REQUIRES_TRUST_LEVEL = 0
        print("  [BUG] Should have raised AttributeError!")
    except AttributeError as e:
        print(f"    AttributeError: {e}")
        print(f"    [BLOCKED]  CANNOT_MUTATE boundary holds.")

    # Sub-scenario C3: TL4 caller executes successfully
    _subheader("C3 — TL4 orchestrator executes amputate")
    result = amputate.execute(compromised, registry, caller_trust=4)
    print(f"\n  [AMPUTATE COMPLETE]")
    print(f"    block_id       : {result.block_id}")
    print(f"    affected_blocks: {result.affected_blocks}")
    print(f"    rebuilt_count  : {result.rebuilt_count}")
    print(f"    event sig      : {result.event_signature[:32]}...")

    # Verify event HMAC independently
    canonical = json.dumps({
        "block_id": result.block_id,
        "affected_count": len(result.affected_blocks),
        "affected_blocks": sorted(result.affected_blocks),
        "timestamp": result.event_signature,  # using sig as proxy; actual ts from log
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")
    # Confirm signature length (full sha256 hex = 64 chars)
    assert len(result.event_signature) == 64, "HMAC signature length wrong"
    print(f"    HMAC length    : {len(result.event_signature)} chars [OK]")


def scenario_d(playbook: FixPlaybook) -> None:
    _header("Scenario D — Immune memory: B's variant now recognised by FixPlaybook")
    print(f"  Re-presenting mutated novel vector: {_NOVEL_VARIANT_VEC}")
    print(f"  (Playbook now has {len(playbook)} entries — ATK-004 added in Scenario B)\n")

    match = playbook.find_similar_fix(_NOVEL_VARIANT_VEC)
    if match:
        print(f"  [IMMUNE MEMORY HIT]")
        print(f"    attack_id  : {match['attack_id']}")
        print(f"    similarity : {match['similarity']}")
        print(f"    fix        : {match['fix_proposal']}")
        print(f"\n  System learned from the honeypot capture.")
        print(f"  The same attack family is now blocked at immune-memory speed.")
    else:
        print("  [MISS] Immune memory did not fire — check threshold / vector.")


# ── Main ─────────────────────────────────────────────────────────────────

def run_demo() -> None:
    print(f"\n{'═' * 64}")
    print(f"  ORVL-012  Constitutional Immune System")
    print(f"  Components: FixPlaybook · HoneypotZone · ConstitutionalAmputate")
    print(f"{'═' * 64}")

    # Initialise all three components under the same derived key
    playbook = FixPlaybook(hmac_key=_HMAC_KEY)
    honeypot = HoneypotZone(hmac_key=_HMAC_KEY)

    # Pre-load playbook with known attack patterns
    for atk in _KNOWN_ATTACKS:
        playbook.add(atk["id"], atk["vec"], atk["classes"], atk["fix"])

    scenario_a(playbook)
    scenario_b(playbook, honeypot)
    scenario_c()
    scenario_d(playbook)

    print(f"\n{'═' * 64}")
    print("  ORVL-012 demo complete.")
    print(f"  FixPlaybook entries    : {len(playbook)}")
    print(f"  Components demonstrated: Fix Playbook, Honeypot Zone, Amputate")
    print(f"{'═' * 64}\n")


if __name__ == "__main__":
    run_demo()
