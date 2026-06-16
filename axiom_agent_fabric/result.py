"""AgentResult — standard compact return shape for every woken micro-agent.

Each woken MiniSRDAgent produces exactly one AgentResult.  The
FabricCoordinator collects all results and merges them into a single
coordinator EventToken.

AgentResult wraps the raw LayerReport produced by the underlying
agent (TextAgent, AudioAgent, VideoAgent, etc.) and adds the higher-
level fields from the PDF spec:

  answer_summary          short prose answer
  confidence              [0, 1]
  evidence_used           list of source IDs or snippet hashes
  risk_flags              governance signals (from LayerReport payload)
  next_recommended_agent  handoff hint (agent_id or None)
  memory_delta            dict of fields to persist back to memory_pointer
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Optional

from axiom_signing import derive_key
from axiom_event_token.models import LayerReport

RESULT_KEY_NS = b"axiom-agent-result-v1"

_RESULT_KEY: Optional[bytes] = None


def _result_key() -> bytes:
    global _RESULT_KEY
    if _RESULT_KEY is None:
        _RESULT_KEY = derive_key(RESULT_KEY_NS)
    return _RESULT_KEY


def _canonical(d: dict) -> bytes:
    payload = {k: v for k, v in d.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _hmac_sign(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


# ─── Helpers ─────────────────────────────────────────────────────────────────

_RISK_SIGNAL_KEYS = {
    "risk_clusters", "signals", "risk_flags", "risk_level",
    "impact_profile", "alert_level",
}

_EVIDENCE_KEYS = {
    "source", "sources", "evidence", "evidence_used", "citation",
    "citations", "reference", "references",
}


def _extract_risk_flags(payload: dict) -> list[str]:
    """Pull risk-related strings from an agent's LayerReport payload."""
    flags: list[str] = []
    for key in _RISK_SIGNAL_KEYS:
        val = payload.get(key)
        if isinstance(val, list):
            flags.extend(str(v) for v in val if v)
        elif isinstance(val, str) and val:
            flags.append(val)
    # Governance-layer verdict as a risk flag when harmful
    verdict = payload.get("verdict") or payload.get("intent_class", "")
    if verdict in {"HARM", "DECEIVE", "REFUSE"}:
        flags.append(verdict)
    return list(dict.fromkeys(flags))  # deduplicate, preserve order


def _extract_evidence(payload: dict) -> list[str]:
    """Pull evidence / source references from an agent's LayerReport payload."""
    evid: list[str] = []
    for key in _EVIDENCE_KEYS:
        val = payload.get(key)
        if isinstance(val, list):
            evid.extend(str(v) for v in val if v)
        elif isinstance(val, str) and val:
            evid.append(val)
    return list(dict.fromkeys(evid))


def _summary_from_payload(agent_id: str, payload: dict, confidence: float) -> str:
    """Build a one-line summary from common payload fields."""
    # Governance / intent layer
    if "verdict" in payload or "intent_class" in payload:
        verdict = payload.get("verdict") or payload.get("intent_class", "unknown")
        signals = payload.get("signals", [])
        sig_str = (", ".join(signals[:3]) + " ..." if len(signals) > 3
                   else ", ".join(signals)) if signals else "none"
        return f"{agent_id}: verdict={verdict}  signals=[{sig_str}]  conf={confidence:.2f}"
    # Audio layer
    if "impact_profile" in payload or "material_signature" in payload:
        mat = payload.get("material_signature", "unknown")
        imp = payload.get("impact_profile", "unknown")
        return f"{agent_id}: material={mat}  impact={imp}  conf={confidence:.2f}"
    # Video / motion
    if "motion_class" in payload or "motions" in payload:
        mc = payload.get("motion_class") or (payload.get("motions") or ["unknown"])[0]
        return f"{agent_id}: motion={mc}  conf={confidence:.2f}"
    # Generic text / reasoning
    phrase = payload.get("phrase") or payload.get("text", "")
    if phrase:
        excerpt = phrase[:60] + ("…" if len(phrase) > 60 else "")
        return f"{agent_id}: \"{excerpt}\"  conf={confidence:.2f}"
    return f"{agent_id}: active  conf={confidence:.2f}"


# ─── AgentResult ─────────────────────────────────────────────────────────────


@dataclass
class AgentResult:
    """Standard compact return object for every woken micro-agent.

    Attributes
    ----------
    agent_id                Identifies the woken MiniSRDAgent.
    answer_summary          Short prose answer / key finding.
    confidence              [0, 1] self-rated confidence.
    evidence_used           Source IDs or snippet hashes supporting the answer.
    risk_flags              Governance signals that downstream should see.
    next_recommended_agent  Optional agent_id to hand off to.
    memory_delta            Dict of fields to persist to memory_pointer.
    layer_report            Raw LayerReport from the underlying agent.
    signature               HMAC-SHA256 over all other fields.
    """
    agent_id:               str
    answer_summary:         str
    confidence:             float
    evidence_used:          list[str]
    risk_flags:             list[str]
    next_recommended_agent: Optional[str]
    memory_delta:           dict
    layer_report:           LayerReport
    signature:              str = ""

    # ── Signing ──────────────────────────────────────────────────────

    def _signable(self) -> dict:
        return {
            "agent_id":               self.agent_id,
            "answer_summary":         self.answer_summary,
            "confidence":             self.confidence,
            "evidence_used":          self.evidence_used,
            "risk_flags":             self.risk_flags,
            "next_recommended_agent": self.next_recommended_agent,
            "memory_delta":           self.memory_delta,
            "layer_report_sig":       self.layer_report.signature,
            "signature":              self.signature,
        }

    def sign(self) -> "AgentResult":
        """Return a copy with signature set."""
        sig = _hmac_sign(_canonical(self._signable()), _result_key())
        return AgentResult(
            agent_id=self.agent_id,
            answer_summary=self.answer_summary,
            confidence=self.confidence,
            evidence_used=self.evidence_used,
            risk_flags=self.risk_flags,
            next_recommended_agent=self.next_recommended_agent,
            memory_delta=self.memory_delta,
            layer_report=self.layer_report,
            signature=sig,
        )

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _hmac_sign(_canonical(self._signable()), _result_key())
        return hmac.compare_digest(self.signature, expected)

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_layer_report(
        cls,
        agent_id: str,
        report: LayerReport,
        *,
        next_agent: Optional[str] = None,
        memory_delta: Optional[dict] = None,
    ) -> "AgentResult":
        """Construct from a LayerReport, extracting risk + evidence automatically."""
        risk  = _extract_risk_flags(report.payload)
        evid  = _extract_evidence(report.payload)
        summ  = _summary_from_payload(agent_id, report.payload, report.confidence)
        result = cls(
            agent_id               = agent_id,
            answer_summary         = summ,
            confidence             = report.confidence,
            evidence_used          = evid,
            risk_flags             = risk,
            next_recommended_agent = next_agent,
            memory_delta           = memory_delta or {},
            layer_report           = report,
        )
        return result.sign()
