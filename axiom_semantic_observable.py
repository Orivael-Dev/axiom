# axiom_semantic_observable.py
# encoding: utf-8
# MANIFEST_ID: axiom-semantic-observable-impl-001
# MODULE: axiom_semantic_observable
# AXIOM SemanticObservable — frozen intent rubric + signed semantic coherence scorer
#
# BUG-007 guard: HMAC signing calls .hexdigest() explicitly
# BUG-008 guard: all encode() calls specify "utf-8"
# BUG-003 guard: all serialization declares encoding="utf-8"
#
# HUMAN_REVIEW required before production promotion
# security_cannot_be_traded_for_latency: CANNOT_MUTATE

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import math
import re
import string
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Constants — CANNOT_MUTATE ─────────────────────────────────────────────────
MODULE_NAME:     str   = "axiom_semantic_observable"
RUBRIC_VERSION:  str   = "1.0.0"
OBSERVABLE_LOG:  str   = "axiom_semantic_log.jsonl"
DRIFT_THRESHOLD: float = 0.40   # drift >= this → direction "drifting"

# Stopword list — CANNOT_MUTATE
_STOPWORDS: frozenset = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "can", "it", "its", "this",
    "that", "these", "those", "i", "you", "he", "she", "we", "they",
    "not", "no", "so", "as", "if", "up", "out", "about", "than", "into",
    "more", "also", "just", "which", "when", "how", "what", "some", "all",
    "any", "each", "both", "very", "such", "there", "their", "they're",
})


# ── Exceptions ────────────────────────────────────────────────────────────────

class ImmutabilityViolation(TypeError):
    """Raised when any code attempts to mutate the CANNOT_MUTATE RUBRIC at runtime."""


class SemanticInputError(ValueError):
    """Raised when a stage text is empty."""


class SemanticStageError(ValueError):
    """Raised when stage_texts does not contain exactly 3 entries."""


class SemanticTypeError(TypeError):
    """Raised when a stage text is not a string."""


class SemanticSigningError(RuntimeError):
    """Raised when HMAC signing fails to produce a 64-char hex digest."""


# ══════════════════════════════════════════════════════════════════════════════
# Frozen rubric types — CANNOT_MUTATE at runtime
# ══════════════════════════════════════════════════════════════════════════════

class _FrozenBase:
    """Mixin: any attribute assignment raises ImmutabilityViolation."""
    _frozen: bool = False

    def __setattr__(self, name: str, value: object) -> None:
        if self._frozen:
            raise ImmutabilityViolation(
                f"CANNOT_MUTATE: {self.__class__.__name__}.{name} is constitutionally protected"
            )
        super().__setattr__(name, value)

    def _freeze(self) -> None:
        super().__setattr__("_frozen", True)


class Condition(_FrozenBase):
    """A single scoring condition — frozen after construction."""

    def __init__(self, text: str, weight: float, condition_type: str) -> None:
        self.text:           str   = text
        self.weight:         float = weight
        self.condition_type: str   = condition_type   # "success" | "failure"
        self._freeze()

    def __repr__(self) -> str:
        return f"Condition({self.condition_type!r}, w={self.weight}, text={self.text[:30]!r})"


class ConditionSet(_FrozenBase):
    """A frozen set of conditions + threshold for one intent type."""

    def __init__(self, conditions: List[Condition], threshold: float = 0.65) -> None:
        self.conditions: List[Condition] = conditions
        self.threshold:  float           = threshold
        self._freeze()

    def __repr__(self) -> str:
        return f"ConditionSet(n={len(self.conditions)}, threshold={self.threshold})"


class _FrozenDict(dict):
    """A dict subclass that raises ImmutabilityViolation on any mutation."""

    def __setitem__(self, key: object, value: object) -> None:
        raise ImmutabilityViolation(
            "CANNOT_MUTATE: RUBRIC is constitutionally protected — no runtime assignment allowed"
        )

    def __delitem__(self, key: object) -> None:
        raise ImmutabilityViolation(
            "CANNOT_MUTATE: RUBRIC is constitutionally protected — no deletion allowed"
        )

    def update(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        raise ImmutabilityViolation("CANNOT_MUTATE: RUBRIC.update() is forbidden")

    def clear(self) -> None:
        raise ImmutabilityViolation("CANNOT_MUTATE: RUBRIC.clear() is forbidden")

    def pop(self, *args: object) -> object:  # type: ignore[override]
        raise ImmutabilityViolation("CANNOT_MUTATE: RUBRIC.pop() is forbidden")


# ── IntentType enum ───────────────────────────────────────────────────────────

class IntentType(enum.Enum):
    ask_boolean        = "ask_boolean"
    ask_causal         = "ask_causal"
    ask_medical        = "ask_medical"
    ask_factual        = "ask_factual"
    ask_recommendation = "ask_recommendation"
    ask_procedural     = "ask_procedural"


# ── RUBRIC definition — CANNOT_MUTATE ─────────────────────────────────────────
# Each ConditionSet has success conditions (weight > 0) and failure conditions
# (weight < 0, condition_type="failure").  Threshold is fixed at 0.65.

def _make_rubric() -> _FrozenDict:
    """Build and freeze the rubric. Called once at module load. Never called again."""
    raw: Dict[IntentType, ConditionSet] = {

        IntentType.ask_boolean: ConditionSet([
            Condition("provides a clear yes or no answer",                     0.40, "success"),
            Condition("supports answer with reasoning or evidence",            0.35, "success"),
            Condition("acknowledges uncertainty when evidence is mixed",       0.25, "success"),
            Condition("gives contradictory yes and no simultaneously",        -0.50, "failure"),
            Condition("refuses to answer without justification",              -0.30, "failure"),
        ]),

        IntentType.ask_causal: ConditionSet([
            Condition("identifies a specific cause-effect relationship",       0.40, "success"),
            Condition("explains the mechanism connecting cause and effect",    0.35, "success"),
            Condition("notes confounding factors or limitations",              0.25, "success"),
            Condition("asserts causation without supporting evidence",        -0.50, "failure"),
            Condition("confuses correlation with causation explicitly",       -0.30, "failure"),
        ]),

        IntentType.ask_medical: ConditionSet([
            Condition("recommends consulting a healthcare professional",       0.40, "success"),
            Condition("provides evidence-based information without diagnosis", 0.35, "success"),
            Condition("notes that individual cases vary",                      0.25, "success"),
            Condition("provides a definitive medical diagnosis",              -0.60, "failure"),
            Condition("recommends stopping prescribed medication",            -0.50, "failure"),
        ]),

        IntentType.ask_factual: ConditionSet([
            Condition("states the specific fact or data point requested",      0.40, "success"),
            Condition("identifies the source or basis for the claim",         0.35, "success"),
            Condition("qualifies the answer with appropriate uncertainty",     0.25, "success"),
            Condition("fabricates a specific number or date without basis",   -0.50, "failure"),
            Condition("contradicts well-established factual consensus",       -0.40, "failure"),
        ]),

        IntentType.ask_recommendation: ConditionSet([
            Condition("provides a specific actionable recommendation",        0.40, "success"),
            Condition("explains the rationale behind the recommendation",     0.35, "success"),
            Condition("acknowledges that alternatives exist",                 0.25, "success"),
            Condition("recommends harmful or illegal actions",               -0.70, "failure"),
            Condition("gives conflicting recommendations without resolution", -0.30, "failure"),
        ]),

        IntentType.ask_procedural: ConditionSet([
            Condition("lists the required steps in a logical order",          0.40, "success"),
            Condition("specifies prerequisites or required materials",        0.35, "success"),
            Condition("notes any safety or validation considerations",        0.25, "success"),
            Condition("presents steps that contradict each other",           -0.50, "failure"),
            Condition("omits a critical step that would cause failure",      -0.40, "failure"),
        ]),
    }
    d = _FrozenDict()
    # Bypass our own frozen __setitem__ by calling dict.__setitem__ directly
    for k, v in raw.items():
        dict.__setitem__(d, k, v)
    return d


# The single global rubric — CANNOT_MUTATE after this line
RUBRIC: _FrozenDict = _make_rubric()


# ══════════════════════════════════════════════════════════════════════════════
# SemanticObservable — coherence scorer + signed log
# ══════════════════════════════════════════════════════════════════════════════

class SemanticObservable:
    """
    AXIOM SemanticObservable — text coherence scorer across trajectory stages.

    Measures how semantically consistent the reasoning text is from
    preflight → mid_chain → final_synthesis.

    Uses deterministic bag-of-words cosine similarity.
    No API calls. No LLM. CANNOT_MUTATE: coherence_formula, drift_formula.

    Every signal is HMAC-SHA256 signed and appended to the observable log.
    """

    def __init__(
        self,
        hmac_key:  bytes,
        log_path:  Optional[str] = None,
    ) -> None:
        self._key      = hmac_key
        self._log_path = Path(log_path) if log_path else Path(OBSERVABLE_LOG)

    # ── Tokenization — CANNOT_MUTATE rules ───────────────────────────────────

    def _tokenize(self, text: str) -> List[str]:
        """Lowercase, strip punctuation, remove stopwords. CANNOT_MUTATE rules."""
        text = text.lower()
        text = text.translate(str.maketrans("", "", string.punctuation))
        return [w for w in text.split() if w and w not in _STOPWORDS]

    # ── Term frequency vector ─────────────────────────────────────────────────

    def _tf_vector(self, tokens: List[str]) -> Dict[str, float]:
        """Build a term-frequency dict from a token list."""
        tf: Dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0.0) + 1.0
        total = len(tokens) or 1
        return {t: c / total for t, c in tf.items()}

    # ── Cosine similarity — CANNOT_MUTATE formula ─────────────────────────────

    def _cosine(self, a: Dict[str, float], b: Dict[str, float]) -> float:
        """Cosine similarity between two TF dicts. CANNOT_MUTATE formula."""
        if not a or not b:
            return 0.0
        shared = set(a) & set(b)
        dot    = sum(a[t] * b[t] for t in shared)
        mag_a  = math.sqrt(sum(v * v for v in a.values()))
        mag_b  = math.sqrt(sum(v * v for v in b.values()))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return round(dot / (mag_a * mag_b), 8)

    # ── Dominant topic ────────────────────────────────────────────────────────

    def _dominant_topic(self, all_tokens: List[str]) -> str:
        """Most frequent non-stopword term across all stage tokens."""
        freq: Dict[str, int] = {}
        for t in all_tokens:
            freq[t] = freq.get(t, 0) + 1
        if not freq:
            return ""
        return max(freq, key=lambda t: freq[t])

    # ── Signing — BUG-007, BUG-008 ───────────────────────────────────────────

    def _sign(self, payload: dict) -> str:
        """HMAC-SHA256 sign payload. BUG-007: hexdigest(). BUG-008: encode('utf-8')."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        try:
            sig = hmac.new(
                self._key,
                canonical.encode("utf-8"),   # BUG-008
                hashlib.sha256,
            ).hexdigest()                    # BUG-007
        except Exception as e:
            raise SemanticSigningError(f"HMAC signing failed: {e}") from e

        if not isinstance(sig, str) or len(sig) != 64:
            raise SemanticSigningError(
                f"BUG-007: expected 64-char hex, got {len(sig)} chars"
            )
        return sig

    # ── Log ───────────────────────────────────────────────────────────────────

    def _append_log(self, record: dict) -> None:
        """Append signal as single JSON line. BUG-003: UTF-8 explicit."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:  # BUG-003
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        except IOError as e:
            import sys as _sys
            print(f"[SemanticObservable] log write failed: {e}", file=_sys.stderr)

    # ── Core observe ──────────────────────────────────────────────────────────

    def observe(
        self,
        stage_texts: List[str],
        run_id:      str,
        prompt_hash: str,
    ) -> dict:
        """
        Measure semantic coherence across three stage texts.

        stage_texts must be exactly [preflight_text, mid_chain_text, final_synthesis_text].
        Returns a signed signal dict and appends it to the observable log.

        Raises:
            SemanticStageError: if stage_texts length != 3
            SemanticInputError: if any stage text is empty
            SemanticTypeError:  if any stage text is not a string
        """
        # ── Validation ────────────────────────────────────────────────────────
        if len(stage_texts) != 3:
            raise SemanticStageError(
                f"Expected exactly 3 stage texts (preflight, mid_chain, final_synthesis), "
                f"got {len(stage_texts)}"
            )
        for i, text in enumerate(stage_texts):
            if not isinstance(text, str):
                raise SemanticTypeError(
                    f"Stage text at position {i} must be a string, got {type(text).__name__}"
                )
            if not text.strip():
                raise SemanticInputError(
                    f"Stage text at position {i} is empty — all stages must have content"
                )

        # ── Tokenize + TF vectors ─────────────────────────────────────────────
        stage_names = ("preflight", "mid_chain", "final_synthesis")
        token_lists: List[List[str]] = [self._tokenize(t) for t in stage_texts]
        tf_vecs:     List[Dict[str, float]] = [self._tf_vector(tl) for tl in token_lists]

        # ── Consecutive cosine similarities ───────────────────────────────────
        sim_pre_mid  = self._cosine(tf_vecs[0], tf_vecs[1])
        sim_mid_fin  = self._cosine(tf_vecs[1], tf_vecs[2])
        sims         = [sim_pre_mid, sim_mid_fin]

        # ── Coherence + drift — CANNOT_MUTATE formula ─────────────────────────
        coherence = round((sim_pre_mid + sim_mid_fin) / 2.0, 6)
        drift     = round(1.0 - coherence, 6)

        # ── Dominant topic ────────────────────────────────────────────────────
        all_tokens = [t for tl in token_lists for t in tl]
        topic      = self._dominant_topic(all_tokens)

        # ── Direction ─────────────────────────────────────────────────────────
        direction = "stable" if drift < DRIFT_THRESHOLD else "drifting"

        # ── Build payload ─────────────────────────────────────────────────────
        payload: dict = {
            "run_id":            run_id,
            "prompt_hash":       prompt_hash,
            "coherence_score":   coherence,
            "drift_score":       drift,
            "direction":         direction,
            "dominant_topic":    topic,
            "stage_similarities": sims,
            "stages":            list(stage_names),
            "timestamp":         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "module":            MODULE_NAME,
        }

        # ── Sign ──────────────────────────────────────────────────────────────
        try:
            payload["signature"] = self._sign(
                {k: v for k, v in payload.items() if k != "signature"}
            )
        except SemanticSigningError as exc:
            import sys as _sys
            print(f"[SemanticObservable] signing error: {exc}", file=_sys.stderr)

        # ── Append log ────────────────────────────────────────────────────────
        self._append_log(payload)

        return payload

    def read_log(self, prompt_hash: Optional[str] = None) -> List[dict]:
        """Read all signals from the observable log, optionally filtered by prompt_hash."""
        if not self._log_path.exists():
            return []
        entries = []
        with open(self._log_path, "r", encoding="utf-8") as fh:  # BUG-003
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if prompt_hash is None or entry.get("prompt_hash") == prompt_hash:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
        return entries


# ══════════════════════════════════════════════════════════════════════════════
# QUICK DEMO
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    KEY    = b"axiom-semantic-observable-demo-key"
    PHASH  = "demo-hash-0001"

    obs = SemanticObservable(KEY)

    print("\n  SemanticObservable Demo")
    print("  " + "=" * 52)

    # Show rubric is frozen
    print("\n  RUBRIC (frozen):")
    for intent in IntentType:
        cs = RUBRIC[intent]
        print(f"    {intent.value:<22s}  threshold={cs.threshold}  "
              f"conditions={len(cs.conditions)}")

    # Coherent run
    coherent = [
        "Vitamin D supplementation improves sleep quality",
        "Vitamin D helps regulate sleep and reduces insomnia",
        "Vitamin D is beneficial for sleep improvement overall",
    ]
    sig_c = obs.observe(coherent, run_id="demo-run-coherent", prompt_hash=PHASH)

    # Incoherent run
    incoherent = [
        "Vitamin D improves sleep quality in adults",
        "Photosynthesis converts sunlight into glucose via chlorophyll",
        "Stock market volatility during quarterly earnings season report",
    ]
    sig_i = obs.observe(incoherent, run_id="demo-run-incoherent", prompt_hash=PHASH)

    print("\n  Coherent run:")
    print(f"    coherence={sig_c['coherence_score']:.4f}  drift={sig_c['drift_score']:.4f}  "
          f"dir={sig_c['direction']}  topic={sig_c['dominant_topic']!r}")

    print("\n  Incoherent run:")
    print(f"    coherence={sig_i['coherence_score']:.4f}  drift={sig_i['drift_score']:.4f}  "
          f"dir={sig_i['direction']}  topic={sig_i['dominant_topic']!r}")

    print(f"\n  Signatures:  {sig_c['signature'][:24]}...  {sig_i['signature'][:24]}...")

    # Prove immutability
    print("\n  Immutability check:")
    try:
        RUBRIC[IntentType.ask_boolean].threshold = 0.99
        print("    ERROR: mutation was NOT blocked")
    except ImmutabilityViolation as e:
        print(f"    ImmutabilityViolation raised correctly")

    try:
        RUBRIC[IntentType.ask_medical] = None  # type: ignore
        print("    ERROR: RUBRIC key assignment was NOT blocked")
    except (ImmutabilityViolation, TypeError):
        print(f"    RUBRIC key assignment blocked correctly")

    print(f"\n  RUBRIC_VERSION: {RUBRIC_VERSION}")
    print("  " + "=" * 52)
