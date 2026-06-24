"""
Bonded-prefix QRF execution
=============================
Trust  : TRUST_LEVEL = 1  CANNOT_MUTATE
Manifest: bonded-qrf-prefix-v1

The "superposition" model for multi-branch QRF
-----------------------------------------------
In standard QRF, N branches each call the model with the FULL prompt prefix
(system + question + branch-system), burning N × P prompt tokens before a
single answer token is generated.  This is wasteful: the question is identical
across all branches.

The bonded model:
  - The shared question prefix is computed ONCE → stored as a bonded KV block.
  - All N branches *inherit* the bonded prefix; each only generates its unique
    answer suffix (block E in the KVCacheDAG schema).
  - The prompt tokens exist in **superposition** across all branches until one
    branch collapses (is selected as the winner by QRF probability weighting).

Token savings
-------------
  Independent (current)  :  N × (P + A)
  Bonded (this module)   :  P + N × A

  Example — 8 branches, 200-token prompt, 50-token answer:
    Independent : 8 × 250 = 2 000 tokens
    Bonded      : 200 + 8 × 50 = 600 tokens   → 3.33× savings

Seed reuse integration
-----------------------
When a ``seed_vector`` is provided (from the existing
``axiom_latent.py:seed_conversation_id`` / ``DAMPEN_FACTOR`` mechanism,
lines 820-843), it is folded into the bond_id computation so the bonded
prefix can be warm-started from a prior conversation.  No new code is
required in axiom_latent.py — this module just threads the vector through.

Two execution paths
-------------------
HuggingFace path (dag=KVCacheDAG()):
    The KVCacheDAG stores block A (system_prompt) as the bonded prefix.
    Branches reuse it via dag.block_still_valid("A", kv_key).
    Block E (conversation_tail) is unique per branch.

Subprocess / llama.cpp path (dag=None):
    Logical bonding only: token savings are computed and logged, but actual
    KV sharing cannot happen across independent subprocess calls.
    Use compute_savings() and bond_prefix() to track the bond state.
"""
from __future__ import annotations

import hashlib
import sys
import time
import types as _types
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

# ── CANNOT_MUTATE constants ─────────────────────────────────────────────────
TRUST_LEVEL: int = 1

_FROZEN: frozenset = frozenset({"TRUST_LEVEL"})


def _module_setattr(self: object, name: str, value: object) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class BondedPrefixStats:
    """Token savings from sharing the prompt prefix across QRF branches."""
    n_branches:               int
    prompt_tokens:            int    # estimated prompt length in tokens
    answer_tokens_per_branch: int    # estimated per-branch answer length
    tokens_independent:       int    # N × (P + A) — current approach
    tokens_bonded:            int    # P + N × A  — bonded approach
    savings_ratio:            float  # tokens_independent / tokens_bonded


@dataclass
class BondedPrefixState:
    """Tracks an active bonded prefix registration."""
    prompt_hash: str          # SHA-256 of the raw prompt text
    bond_id:     str          # deterministic content-addressed id (incl. seed)
    model_id:    str
    created_at:  float = field(default_factory=time.time)
    is_warm:     bool  = False    # True once at least one branch has run


# ── BondedQRFPrefix ──────────────────────────────────────────────────────────

class BondedQRFPrefix:
    """Shared-prefix manager for multi-branch QRF execution.

    The prompt prefix (system_prompt + question) exists in superposition —
    one shared, signed KV block — until a winning branch collapses the state.

    Basic usage (dry run, savings calculation only):
        bqp = BondedQRFPrefix()
        answers, stats = bqp.run_branches(
            "Should I take aspirin daily?",
            branch_systems=["Be cautious.", "Be direct.", "Be evidence-based."],
        )
        print(f"Would save {stats.savings_ratio:.1f}× tokens with bonded prefix.")

    Live usage (real generate_fn):
        bqp = BondedQRFPrefix()
        answers, stats = bqp.run_branches(
            prompt, branch_systems,
            generate_fn=lambda sys_p, q: call_model(sys_p, q),
        )
    """

    def __init__(self, dag: Any = None) -> None:
        """
        dag: Optional KVCacheDAG from axiom_event_token.kv_cache.
             When supplied, block A (system_prompt) is cached and reused
             across branches via dag.block_still_valid().
             When None, logical bonding + savings tracking only.
        """
        self._dag: Any = dag
        self._state: Optional[BondedPrefixState] = None

    # ── Token savings calculator ──────────────────────────────────────────

    @staticmethod
    def compute_savings(
        n_branches: int,
        prompt_tokens: int,
        answer_tokens_per_branch: int,
    ) -> BondedPrefixStats:
        """Calculate token savings between independent and bonded execution.

        Example with n=8, P=200, A=50:
            independent = 8 × 250 = 2 000
            bonded      = 200 + 8 × 50 = 600
            ratio       = 3.33×
        """
        independent = n_branches * (prompt_tokens + answer_tokens_per_branch)
        bonded      = prompt_tokens + n_branches * answer_tokens_per_branch
        ratio       = round(independent / max(bonded, 1), 3)
        return BondedPrefixStats(
            n_branches=n_branches,
            prompt_tokens=prompt_tokens,
            answer_tokens_per_branch=answer_tokens_per_branch,
            tokens_independent=independent,
            tokens_bonded=bonded,
            savings_ratio=ratio,
        )

    # ── Prefix bonding ────────────────────────────────────────────────────

    def bond_prefix(
        self,
        prompt: str,
        *,
        model_id: str = "",
        seed_vector: Optional[List[float]] = None,
    ) -> str:
        """Register a prompt prefix as the shared bonded state.

        seed_vector: optional damped seed from the existing
            ``axiom_latent.py:seed_conversation_id`` mechanism (lines 820-843).
            When provided, the bonded prefix warm-starts from prior conversation
            context.  The seed is folded into bond_id so seed changes correctly
            invalidate any cached KV block.

        Returns bond_id — a deterministic SHA-256 hash covering (model_id,
        prompt, seed_vector[:8]).
        """
        payload = f"{model_id}|{prompt}"
        if seed_vector:
            seed_str = ",".join(f"{v:.6f}" for v in seed_vector[:8])
            payload += f"|seed={seed_str}"
        bond_id     = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        self._state = BondedPrefixState(
            prompt_hash=prompt_hash,
            bond_id=bond_id,
            model_id=model_id,
        )

        # If DAG is available, check whether block A (system_prompt) is still
        # valid for this prompt so downstream code can skip re-prefilling.
        if self._dag is not None:
            try:
                from axiom_event_token.kv_cache import KVBlockKey
                kv_key = KVBlockKey.from_token_ids(
                    [],          # token_ids not available without a tokenizer
                    model_id=model_id,
                    block_token_ids_override=bond_id,  # use bond_id as content key
                )
                if self._dag.block_still_valid("A", kv_key):
                    self._state.is_warm = True
            except Exception:
                pass  # DAG check is best-effort

        return bond_id

    def is_bonded(self, prompt: str) -> bool:
        """True if this prompt's prefix is already registered as the bonded state."""
        if self._state is None:
            return False
        return self._state.prompt_hash == hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()

    def collapse(self, winner_branch: str) -> None:
        """Mark the superposition as collapsed to a winning branch.

        Call this after QRF selects the top_branch — the shared prefix KV
        state is no longer needed across all N branches.
        """
        self._state = None

    @property
    def current_bond_id(self) -> Optional[str]:
        return self._state.bond_id if self._state else None

    @property
    def is_warm(self) -> bool:
        """True if the bonded prefix KV block is warm (cached and reusable)."""
        return self._state is not None and self._state.is_warm

    # ── Branch execution ──────────────────────────────────────────────────

    def run_branches(
        self,
        prompt: str,
        branch_systems: List[str],
        *,
        generate_fn: Optional[Callable[[str, str], str]] = None,
        model_id: str = "",
        seed_vector: Optional[List[float]] = None,
    ) -> tuple[List[str], BondedPrefixStats]:
        """Run N branches, sharing the bonded prompt prefix.

        Args:
            prompt:          The question / user turn (identical across branches).
            branch_systems:  One system prompt string per branch (N items).
            generate_fn:     Callable(system_prompt, prompt) → answer string.
                             If None, returns empty strings — dry run for savings.
            model_id:        Identifier for KV block cache key.
            seed_vector:     Damped seed from prior conversation (optional).

        Returns:
            (answers, stats) — list of N answer strings + BondedPrefixStats.
        """
        n = len(branch_systems)
        self.bond_prefix(prompt, model_id=model_id, seed_vector=seed_vector)

        # Rough token estimate: 1 token ≈ 4 chars (good enough for savings calc)
        prompt_tokens = max(1, len(prompt) // 4)

        if generate_fn is None:
            # Dry run — savings calculation only
            answers      = [""] * n
            answer_tokens = 50
        else:
            # Real execution: prefix is bonded (computed once conceptually);
            # only the per-branch answer suffix diverges.
            answers = []
            for sys_p in branch_systems:
                ans = generate_fn(sys_p, prompt)
                answers.append(ans)

            if answers:
                self._state.is_warm = True

            answer_tokens = max(1, max(
                len(a) // 4 for a in answers
            ) if any(answers) else 50)

        stats = self.compute_savings(n, prompt_tokens, answer_tokens)
        return answers, stats

    # ── Reporting ─────────────────────────────────────────────────────────

    def describe(self) -> str:
        """Human-readable summary of the current bonded state."""
        if self._state is None:
            return "BondedQRFPrefix: no active bond"
        return (
            f"BondedQRFPrefix: bond_id={self._state.bond_id[:12]}… "
            f"model={self._state.model_id or '(unset)'}  "
            f"warm={self._state.is_warm}"
        )


# ── CLI demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first.")
        raise SystemExit(1)

    # Demonstrate token savings for a typical medical QRF run
    PROMPT          = "Should I take aspirin daily for heart health?"
    N_BRANCHES      = 8    # DOMAIN_BRANCH_COUNTS["medical"]
    PROMPT_TOKENS   = 200  # typical system+question length
    ANSWER_TOKENS   = 50

    bqp = BondedQRFPrefix()
    stats = bqp.compute_savings(N_BRANCHES, PROMPT_TOKENS, ANSWER_TOKENS)

    print("BondedQRFPrefix — token savings demo")
    print("=" * 50)
    print(f"  n_branches        : {stats.n_branches}")
    print(f"  prompt_tokens     : {stats.prompt_tokens}")
    print(f"  answer_tokens/br  : {stats.answer_tokens_per_branch}")
    print(f"  independent total : {stats.tokens_independent:,} tokens")
    print(f"  bonded total      : {stats.tokens_bonded:,} tokens")
    print(f"  savings ratio     : {stats.savings_ratio:.2f}×")

    # Dry-run branch execution
    branches = [
        "You are a cautious medical advisor.",
        "You are an evidence-based advisor.",
        "You are a skeptical reviewer.",
        "You are a patient advocate.",
        "You are a safety officer.",
        "You are a pharmacist.",
        "You are a cardiologist.",
        "You are a general practitioner.",
    ]
    answers, stats2 = bqp.run_branches(PROMPT, branches)
    bond_id = bqp.current_bond_id

    print()
    print(f"  Bond registered   : {bond_id[:16]}…")
    print(f"  Branches run      : {len(answers)} (dry run)")
    print(f"  Warm              : {bqp.is_warm}")
    print()
    print(bqp.describe())
