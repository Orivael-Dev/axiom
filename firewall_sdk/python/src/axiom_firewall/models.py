"""Result + intent dataclasses returned by the SDK."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intent:
    """The constitutional intent classification of a checked prompt."""
    intent_class: str  # "INFORM" | "CLARIFY" | "REFUSE" | "HARM" | "DECEIVE" | "UNCERTAIN"
    confidence: float
    signals: tuple[str, ...] = field(default_factory=tuple)
    signature: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Intent":
        return cls(
            intent_class=str(d.get("class", "UNCERTAIN")),
            confidence=float(d.get("confidence", 0.0)),
            signals=tuple(d.get("signals", ())),
            signature=str(d.get("signature", "")),
        )


@dataclass(frozen=True)
class CheckResult:
    """The verdict returned by /v1/guard/check."""
    verdict: str  # "allow" | "block"
    intent: Intent

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"

    @property
    def blocked(self) -> bool:
        return self.verdict == "block"

    @classmethod
    def from_dict(cls, d: dict) -> "CheckResult":
        return cls(
            verdict=str(d.get("verdict", "allow")),
            intent=Intent.from_dict(d.get("intent", {})),
        )
