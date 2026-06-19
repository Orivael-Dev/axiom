"""ORVL-004 MKB — Constitutional Router (Claim 3).

Selects which KnowledgeBlocks to activate for a given task by matching
task keywords against each block's domain keywords extracted from its
PURPOSE, CONSTRAINT, and CONCEPT lines.

This is Phase 4 of the ORVL-004 build roadmap: the runtime router that
implements Claim 3 — "A runtime router that selects and activates
knowledge blocks based on task analysis and constitutional DELEGATES
specifications."

No ML inference — the router is purely keyword-constitutional, keeping
it deterministic and auditable. The ORVL-016 IntentClassifier can be
wired in as an optional pre-filter for richer routing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_mkb import BlockRegistry, KnowledgeBlock, BLOCK_TYPES

def _all_blocks(registry: BlockRegistry) -> list[KnowledgeBlock]:
    result = []
    for btype in BLOCK_TYPES:
        result.extend(registry.list_blocks(btype))
    return result

# ── Domain keyword map ────────────────────────────────────────────────────
# Maps block name substrings / block types to task keywords that should
# trigger that block. Ordered by specificity — first match wins per block.

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "guard":       ["guard", "security", "protect", "injection", "xss", "sqli",
                    "vulnerability", "exploit", "attack", "sanitize", "input validation"],
    "privacy":     ["pii", "privacy", "gdpr", "redact", "personal data", "email",
                    "phone", "ssn", "credential", "password", "token", "secret"],
    "healthcare":  ["hipaa", "health", "medical", "patient", "clinical", "phi",
                    "npi", "mrn", "doctor", "hospital", "prescription"],
    "financial":   ["finra", "sox", "financial", "payment", "bank", "credit",
                    "wire", "transaction", "account", "invoice", "audit"],
    "legal":       ["legal", "law", "compliance", "contract", "liability",
                    "gdpr", "regulation", "statute", "case", "court", "bar"],
    "visual":      ["image", "visual", "photo", "picture", "vlm", "multimodal",
                    "ocr", "caption", "scene", "object detection"],
    "bug":         ["bug", "fix", "error", "crash", "exception", "regression",
                    "patch", "defect", "issue", "debug"],
    "sovereign":   ["govern", "fleet", "oversight", "killswitch", "audit",
                    "trust level", "escalate", "due process"],
}


class ConstitutionalRouter:
    """Route a task to the set of KnowledgeBlocks that should handle it.

    Algorithm:
      1. Normalise task text (lowercase, strip punctuation)
      2. For each registered block, compute domain score:
         count of domain keyword hits from _DOMAIN_KEYWORDS entries
         that match the block's name/type
      3. Return all blocks with score > 0, sorted by score descending
      4. If no keywords match, return the highest-TRUST_LEVEL block
         as the fallback sovereign

    Claim 3 implementation: selection is based on task content + block
    domain declarations (extracted from block name and block_type).
    """

    def __init__(self, hmac_key: bytes) -> None:
        self._hmac_key = hmac_key

    def route(
        self,
        task: str,
        registry: BlockRegistry,
        max_blocks: int = 4,
        min_score: int = 1,
    ) -> list[KnowledgeBlock]:
        """Select blocks from registry that match the task.

        Returns an ordered list (highest relevance first), capped at max_blocks.
        """
        tokens = set(re.findall(r"[a-z0-9]+", task.lower()))
        all_blocks = _all_blocks(registry)

        scored: list[tuple[int, KnowledgeBlock]] = []

        for block in all_blocks:
            score = self._score_block(block, tokens)
            if score >= min_score:
                scored.append((score, block))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [b for _, b in scored[:max_blocks]]

        # Fallback: if nothing matched, activate sovereign/highest-trust block
        if not selected and all_blocks:
            fallback = max(all_blocks,
                           key=lambda b: 1 if b.block_type == "SOVEREIGN" else 0)
            selected = [fallback]
            print(f"  [Router] No domain match — fallback to {fallback.name} (SOVEREIGN)")

        return selected

    def _score_block(self, block: KnowledgeBlock, task_tokens: set[str]) -> int:
        """Score a block against task tokens by domain keyword overlap.

        Uses whole-word matching (tokenise each keyword) to prevent short
        task tokens like 'a' or 'in' from matching inside longer keywords.
        """
        block_label = (block.name + " " + block.block_type).lower()
        score = 0
        for domain_key, keywords in _DOMAIN_KEYWORDS.items():
            if domain_key not in block_label:
                continue
            for kw in keywords:
                kw_tokens = set(re.findall(r"[a-z0-9]+", kw))
                # All words of the keyword must appear in the task (handles
                # multi-word phrases like "object detection" precisely)
                if kw_tokens and kw_tokens <= task_tokens:
                    score += 1
        return score

    def explain(
        self,
        task: str,
        registry: BlockRegistry,
    ) -> dict:
        """Return a full routing explanation for auditability."""
        tokens = set(re.findall(r"[a-z0-9]+", task.lower()))
        all_blocks = _all_blocks(registry)

        breakdown = []
        for block in all_blocks:
            score = self._score_block(block, tokens)
            matched_domains = [
                d for d, kws in _DOMAIN_KEYWORDS.items()
                if d in (block.name + " " + block.block_type).lower()
                and any(
                    (kw_t := set(re.findall(r"[a-z0-9]+", kw))) and kw_t <= tokens
                    for kw in kws
                )
            ]
            breakdown.append({
                "block":           block.name,
                "block_type":      block.block_type,
                "score":           score,
                "matched_domains": matched_domains,
                "activated":       score >= 1,
            })

        breakdown.sort(key=lambda x: x["score"], reverse=True)
        return {
            "task":      task,
            "breakdown": breakdown,
            "activated": [b["block"] for b in breakdown if b["activated"]],
        }
