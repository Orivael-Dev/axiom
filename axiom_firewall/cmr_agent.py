"""Customer Manager Agent (CMR) — intent-classified routing for customer queries.

CMR sits between a customer contact surface (chat widget, email intake, IVR
transcript) and your backend handlers.  It classifies every incoming query
with the Axiom IntentClassifier, matches the class to a configured routing
rule, and escalates to a human when confidence is too low to trust automation.

Every decision is HMAC-signed and optionally written to the exoskeleton
ledger for audit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from axiom_signing import derive_key
from axiom_intent_classifier import IntentClassifier

_CMR_KEY_NS = b"axiom-cmr-v1"


def _cmr_key() -> bytes:
    return derive_key(_CMR_KEY_NS)


def _sign(d: dict) -> str:
    body = json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_cmr_key(), body, hashlib.sha256).hexdigest()


@dataclass
class CMRConfig:
    """Per-deployment CMR configuration."""
    domain: str                        # "healthcare" | "retail" | "finance" | custom
    escalation_threshold: float        # confidence below this → human escalation
    routing_rules: List[dict]          # [{"intent": "CLARIFY", "handler": "faq_bot"}, …]
    business_constraints: List[str]    # additional regex block patterns (re.search)

    @classmethod
    def default(cls, domain: str = "general") -> "CMRConfig":
        return cls(
            domain=domain,
            escalation_threshold=0.50,
            routing_rules=[
                {"intent": "INFORM",    "handler": "info_response"},
                {"intent": "CLARIFY",   "handler": "faq_bot"},
                {"intent": "REFUSE",    "handler": "refusal_handler"},
                {"intent": "UNCERTAIN", "handler": "human_escalation"},
                {"intent": "HARM",      "handler": "block_and_flag"},
                {"intent": "DECEIVE",   "handler": "block_and_flag"},
            ],
            business_constraints=[],
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CMRDecision:
    """Result of a CMR routing decision, HMAC-signed."""
    query: str
    query_excerpt: str
    intent_class: str
    confidence: float
    handler: str
    escalated: bool
    blocked: bool
    block_reason: Optional[str]
    signals: List[str]
    timestamp_utc: str
    signature: str = ""

    def _payload(self) -> dict:
        return {
            "query_excerpt": self.query_excerpt,
            "intent_class":  self.intent_class,
            "confidence":    round(self.confidence, 6),
            "handler":       self.handler,
            "escalated":     self.escalated,
            "blocked":       self.blocked,
            "timestamp_utc": self.timestamp_utc,
        }

    def sign(self) -> "CMRDecision":
        sig = _sign(self._payload())
        self.signature = sig
        return self

    def to_dict(self) -> dict:
        d = self._payload()
        d["query_excerpt"] = self.query_excerpt
        d["block_reason"]  = self.block_reason
        d["signals"]       = self.signals
        d["signature"]     = self.signature
        return d


class CMRAgent:
    """Customer Manager Agent.

    Usage:
        config = CMRConfig.default("retail")
        agent  = CMRAgent(config)
        decision = agent.route("I want to return my order")
        print(decision.handler, decision.escalated)
    """

    def __init__(self, config: CMRConfig) -> None:
        self._config  = config
        self._key     = _cmr_key()
        self._clf     = IntentClassifier(self._key)
        self._history: List[CMRDecision] = []

    def route(self, customer_query: str) -> CMRDecision:
        """Classify and route one customer query.

        Steps:
          1. Check business constraint patterns (block immediately if matched).
          2. Classify intent with IntentClassifier.
          3. Look up routing_rules for the class.
          4. Escalate to "human_escalation" if confidence < escalation_threshold.
          5. Return a signed CMRDecision.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        excerpt = customer_query[:200]

        # Step 1 — business constraint patterns
        for pattern in self._config.business_constraints:
            try:
                if re.search(pattern, customer_query, re.IGNORECASE):
                    d = CMRDecision(
                        query=customer_query,
                        query_excerpt=excerpt,
                        intent_class="HARM",
                        confidence=1.0,
                        handler="block_and_flag",
                        escalated=False,
                        blocked=True,
                        block_reason=f"Matched business constraint: {pattern}",
                        signals=["business_constraint_match"],
                        timestamp_utc=ts,
                    ).sign()
                    self._history.append(d)
                    return d
            except re.error:
                pass  # ignore malformed regexes

        # Step 2 — intent classification
        result = self._clf.classify(customer_query)
        intent = result.intent_class
        confidence = result.confidence
        signals = list(result.signals)

        # Step 3 — routing lookup
        handler = "human_escalation"
        for rule in self._config.routing_rules:
            if rule.get("intent") == intent:
                handler = rule.get("handler", "human_escalation")
                break

        # Step 4 — safety escalation
        blocked = intent in ("HARM", "DECEIVE")
        escalated = (
            not blocked
            and (confidence < self._config.escalation_threshold
                 or intent == "UNCERTAIN")
        )
        if escalated:
            handler = "human_escalation"

        d = CMRDecision(
            query=customer_query,
            query_excerpt=excerpt,
            intent_class=intent,
            confidence=confidence,
            handler=handler,
            escalated=escalated,
            blocked=blocked,
            block_reason=f"Intent {intent} blocked by policy" if blocked else None,
            signals=signals,
            timestamp_utc=ts,
        ).sign()

        self._history.append(d)
        if len(self._history) > 200:
            self._history = self._history[-200:]
        return d

    def recent_decisions(self, n: int = 20) -> List[CMRDecision]:
        return list(reversed(self._history[-n:]))

    @property
    def config(self) -> CMRConfig:
        return self._config


# Module-level singleton for the dashboard — re-created when config changes.
_default_agent: Optional[CMRAgent] = None
_default_config: Optional[CMRConfig] = None


def get_default_agent(config: Optional[CMRConfig] = None) -> CMRAgent:
    """Return (or build) the module-level CMR agent."""
    global _default_agent, _default_config
    if config is not None:
        _default_agent  = CMRAgent(config)
        _default_config = config
    elif _default_agent is None:
        _default_config = CMRConfig.default("general")
        _default_agent  = CMRAgent(_default_config)
    return _default_agent
