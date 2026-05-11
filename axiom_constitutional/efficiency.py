"""
AXIOM Efficiency Layer
======================
8-module compute governance layer that reduces AI token cost, energy,
and latency by routing to the smallest capable model, compressing
context, budgeting tokens, caching reasoning, escalating on low
confidence, and auditing every call.

Whitepaper reference: Axiom Efficiency Layer — Draft Whitepaper, Sections 1-17.

Usage:
    from axiom_constitutional.efficiency import EfficiencyLayer

    layer = EfficiencyLayer()
    result = layer.process(system_prompt, user_message)
    print(layer.auditor.summary())

    # Or enable globally via env var:
    #   export AXIOM_EFFICIENCY=1
    # Then all client.chat() calls route through the efficiency layer.
"""

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime

# ── Signing ──────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axiom_signing import derive_key

try:
    from openai import OpenAI as _OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
SIGNING_KEY = derive_key(b"axiom-efficiency-layer-v1")


def _sign(manifest):
    sig_str = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
    )
    return (
        "hmac-sha256:"
        + hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()[:32]
        + "..."
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. TASK CLASSIFIER
# ══════════════════════════════════════════════════════════════════════════════

# Keywords that bump complexity — no LLM call, pure heuristics
_CRITICAL_KEYWORDS = frozenset([
    "legal", "lawsuit", "medical", "diagnosis", "patient", "prescription",
    "financial", "investment", "securities", "compliance", "regulation",
    "safety", "hazard", "emergency", "classified", "confidential",
    "ethics", "ethical", "harm", "danger", "risk assessment",
])

_HARD_KEYWORDS = frozenset([
    "implement", "architect", "design", "refactor", "optimize",
    "algorithm", "data structure", "benchmark", "compare", "analyze",
    "multi-step", "pipeline", "distributed", "concurrent", "async",
    "proof", "theorem", "derive", "formal", "specification",
    "research", "hypothesis", "experiment", "simulation",
])

_MEDIUM_KEYWORDS = frozenset([
    "explain", "describe", "summarize", "write", "draft", "review",
    "translate", "convert", "debug", "fix", "test", "plan",
])


class TaskClassifier:
    """Classify request complexity without an LLM call."""

    def classify(self, user_message, system_prompt=""):
        combined = (user_message + " " + system_prompt).lower()
        word_count = len(combined.split())

        # Check critical first
        for kw in _CRITICAL_KEYWORDS:
            if kw in combined:
                return "critical"

        # Check hard
        hard_hits = sum(1 for kw in _HARD_KEYWORDS if kw in combined)
        if hard_hits >= 2 or (hard_hits >= 1 and word_count > 200):
            return "hard"

        # Check medium
        for kw in _MEDIUM_KEYWORDS:
            if kw in combined:
                return "medium"

        # Short messages with no complexity signals
        if word_count < 50:
            return "simple"

        return "medium"


# ══════════════════════════════════════════════════════════════════════════════
# 2. MODEL ROUTER
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_MODEL_LADDER = {
    "simple":   "claude-haiku-4-5-20251001",
    "medium":   "claude-sonnet-4-6",
    "hard":     "claude-sonnet-4-6",
    "critical": "claude-opus-4-6",
}

# Flat ladder for OpenAI-compatible backends (NIM typically serves one model)
_OAI_MODEL = os.environ.get("AXIOM_MODEL", "qwen/qwen3-235b-a22b")
DEFAULT_MODEL_LADDER_OPENAI = {
    "simple":   _OAI_MODEL,
    "medium":   _OAI_MODEL,
    "hard":     _OAI_MODEL,
    "critical": _OAI_MODEL,
}

# Ordered from cheapest to most expensive for escalation
MODEL_TIER_ORDER = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]


class ModelRouter:
    """Route to smallest capable model based on task class."""

    def __init__(self, ladder=None):
        env_ladder = os.environ.get("AXIOM_MODEL_LADDER")
        if env_ladder:
            try:
                self.ladder = json.loads(env_ladder)
            except (ValueError, TypeError):
                self.ladder = ladder or DEFAULT_MODEL_LADDER
        else:
            self.ladder = ladder or DEFAULT_MODEL_LADDER

    def route(self, task_class):
        return self.ladder.get(task_class, self.ladder.get("medium", "claude-sonnet-4-6"))


# ══════════════════════════════════════════════════════════════════════════════
# 3. CONTEXT COMPRESSOR
# ══════════════════════════════════════════════════════════════════════════════

class ContextCompressor:
    """Compress context before generation to reduce token waste."""

    MAX_SYSTEM_WORDS = 2000
    MAX_USER_WORDS = 4000

    def compress(self, system_prompt, user_message):
        original_len = len(system_prompt) + len(user_message)

        # Strip redundant whitespace
        c_system = re.sub(r"\n{3,}", "\n\n", system_prompt)
        c_system = re.sub(r"[ \t]{2,}", " ", c_system)
        c_user = re.sub(r"\n{3,}", "\n\n", user_message)
        c_user = re.sub(r"[ \t]{2,}", " ", c_user)

        # Deduplicate repeated instruction lines in system prompt
        seen_lines = set()
        deduped = []
        for line in c_system.splitlines():
            stripped = line.strip()
            if stripped and stripped in seen_lines:
                continue
            seen_lines.add(stripped)
            deduped.append(line)
        c_system = "\n".join(deduped)

        # Truncate system prompt if too long
        sys_words = c_system.split()
        if len(sys_words) > self.MAX_SYSTEM_WORDS:
            c_system = " ".join(sys_words[:self.MAX_SYSTEM_WORDS]) + "\n[...truncated]"

        # Truncate user message if too long
        user_words = c_user.split()
        if len(user_words) > self.MAX_USER_WORDS:
            c_user = " ".join(user_words[:self.MAX_USER_WORDS]) + "\n[...truncated]"

        compressed_len = len(c_system) + len(c_user)
        savings = 1.0 - (compressed_len / original_len) if original_len > 0 else 0.0

        return c_system, c_user, savings


# ══════════════════════════════════════════════════════════════════════════════
# 4. TOKEN BUDGETER
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_TOKEN_BUDGETS = {
    "simple":   300,
    "medium":   1000,
    "hard":     4000,
    "critical": 8000,
}


class TokenBudgeter:
    """Set max_tokens dynamically based on task class."""

    def __init__(self, budgets=None):
        self.budgets = budgets or DEFAULT_TOKEN_BUDGETS

    def budget(self, task_class):
        return self.budgets.get(task_class, 1000)


# ══════════════════════════════════════════════════════════════════════════════
# 5. REASONING CACHE
# ══════════════════════════════════════════════════════════════════════════════

class ReasoningCache:
    """Hash-based lookup for previously answered requests."""

    def __init__(self, max_size=500, ttl_seconds=3600):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.cache = OrderedDict()

    def _hash(self, system_prompt, user_message):
        content = (system_prompt + "|||" + user_message).encode()
        return hashlib.sha256(content).hexdigest()

    def lookup(self, system_prompt, user_message):
        key = self._hash(system_prompt, user_message)
        entry = self.cache.get(key)
        if entry is None:
            return None
        if time.time() - entry["ts"] > self.ttl:
            del self.cache[key]
            return None
        # Move to end (most recently used)
        self.cache.move_to_end(key)
        return entry["response"]

    def store(self, system_prompt, user_message, response, confidence=0.80):
        if confidence < 0.70:
            return  # Don't cache low-confidence responses
        key = self._hash(system_prompt, user_message)
        self.cache[key] = {
            "response": response,
            "confidence": confidence,
            "ts": time.time(),
        }
        # Evict oldest if over capacity
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)


# ══════════════════════════════════════════════════════════════════════════════
# 6. MODEL ESCALATOR
# ══════════════════════════════════════════════════════════════════════════════

_HEDGE_WORDS = [
    "i'm not sure", "i am not sure", "possibly", "might", "maybe",
    "unclear", "uncertain", "i don't know", "i cannot", "i can't",
    "not enough information", "ambiguous", "it depends",
]


class ModelEscalator:
    """Escalate to a stronger model when response confidence is low."""

    CONFIDENCE_THRESHOLD = 0.70

    def should_escalate(self, response, task_class):
        if not response or len(response.strip()) < 20:
            return True
        if response.startswith("# ERROR"):
            return True

        resp_lower = response.lower()
        hedge_count = sum(1 for h in _HEDGE_WORDS if h in resp_lower)
        if hedge_count >= 2:
            return True

        # Don't escalate simple tasks — not worth the cost
        if task_class == "simple":
            return False

        return False

    def next_model(self, current_model, ladder=None):
        try:
            idx = MODEL_TIER_ORDER.index(current_model)
        except ValueError:
            return None
        if idx + 1 < len(MODEL_TIER_ORDER):
            return MODEL_TIER_ORDER[idx + 1]
        return None  # Already at top tier


# ══════════════════════════════════════════════════════════════════════════════
# 7. EFFICIENCY AUDITOR
# ══════════════════════════════════════════════════════════════════════════════

# Approximate cost per 1K tokens (input + output averaged)
_COST_PER_1K = {
    "claude-haiku-4-5-20251001": 0.0005,
    "claude-sonnet-4-6": 0.003,
    "claude-opus-4-6": 0.015,
}


class EfficiencyAuditor:
    """Append-only audit log for every LLM call."""

    def __init__(self, log_path=None):
        self.log_path = log_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "efficiency_audit.jsonl"
        )
        self.session_stats = {
            "calls": 0,
            "cache_hits": 0,
            "escalations": 0,
            "total_tokens_budget": 0,
            "total_latency_ms": 0,
            "total_cost_estimate": 0.0,
            "compression_savings_pct": 0.0,
            "models_used": {},
        }

    def log(self, entry):
        entry["timestamp"] = datetime.now().isoformat() + "Z"
        entry["session_call_number"] = self.session_stats["calls"]

        # Update session stats
        self.session_stats["calls"] += 1
        if entry.get("cache_hit"):
            self.session_stats["cache_hits"] += 1
        if entry.get("escalated"):
            self.session_stats["escalations"] += 1
        self.session_stats["total_tokens_budget"] += entry.get("tokens_budget", 0)
        self.session_stats["total_latency_ms"] += entry.get("latency_ms", 0)
        self.session_stats["total_cost_estimate"] += entry.get("cost_estimate", 0.0)

        model = entry.get("model", "unknown")
        self.session_stats["models_used"][model] = (
            self.session_stats["models_used"].get(model, 0) + 1
        )

        # Append to JSONL
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except IOError:
            pass  # Non-fatal — don't crash on audit write failure

    def summary(self):
        stats = dict(self.session_stats)
        if stats["calls"] > 0:
            stats["avg_latency_ms"] = stats["total_latency_ms"] // stats["calls"]
            stats["cache_hit_rate"] = stats["cache_hits"] / stats["calls"]
        else:
            stats["avg_latency_ms"] = 0
            stats["cache_hit_rate"] = 0.0
        return stats

    def estimate_cost(self, model, tokens_budget):
        rate = _COST_PER_1K.get(model, 0.003)
        return round(rate * tokens_budget / 1000, 6)


# ══════════════════════════════════════════════════════════════════════════════
# 8. EFFICIENCY LAYER (ORCHESTRATOR)
# ══════════════════════════════════════════════════════════════════════════════

class EfficiencyLayer:
    """
    Ties all 8 modules together:
    CLASSIFY → CACHE → ROUTE → COMPRESS → BUDGET → CALL → ESCALATE → AUDIT

    CANNOT_MUTATE: safety_checks, constitutional_suffix, output_validation, audit_log
    """

    def __init__(self, ladder=None, budgets=None, log_path=None):
        # Detect backend — OpenAI-compatible first, then Anthropic
        self._backend = None
        self._client = None
        oai_key = (os.environ.get("AXIOM_API_KEY")
                   or os.environ.get("NVIDIA_API_KEY")
                   or os.environ.get("OPENAI_API_KEY"))
        if _OPENAI_AVAILABLE and oai_key:
            base_url = (os.environ.get("AXIOM_BASE_URL")
                        or os.environ.get("NVIDIA_BASE_URL"))
            self._client = _OpenAI(api_key=oai_key, base_url=base_url) if base_url else _OpenAI(api_key=oai_key)
            self._backend = "openai"
            default_ladder = DEFAULT_MODEL_LADDER_OPENAI
        else:
            self._backend = "anthropic"
            default_ladder = DEFAULT_MODEL_LADDER

        self.classifier = TaskClassifier()
        self.router = ModelRouter(ladder=ladder or default_ladder)
        self.compressor = ContextCompressor()
        self.budgeter = TokenBudgeter(budgets=budgets)
        self.cache = ReasoningCache()
        self.escalator = ModelEscalator()
        self.auditor = EfficiencyAuditor(log_path=log_path)

    def process(self, system_prompt, user_message, model_override=None,
                temperature=0.7, task_class_override=None):
        """
        Full efficiency pipeline. Returns the LLM response string.

        If model_override is set, skip routing (user locked a specific model).
        If task_class_override is set, skip classification (pipeline agent knows its class).
        """
        t0 = time.time()

        # 1. CLASSIFY
        task_class = task_class_override or self.classifier.classify(
            user_message, system_prompt
        )

        # 2. CACHE CHECK
        cached = self.cache.lookup(system_prompt, user_message)
        if cached is not None:
            self.auditor.log({
                "task_class": task_class,
                "model": "cache",
                "tokens_budget": 0,
                "latency_ms": int((time.time() - t0) * 1000),
                "cache_hit": True,
                "escalated": False,
                "cost_estimate": 0.0,
            })
            return cached

        # 3. ROUTE
        model = model_override or self.router.route(task_class)

        # 4. COMPRESS
        c_system, c_user, savings = self.compressor.compress(
            system_prompt, user_message
        )

        # 5. BUDGET
        max_tokens = self.budgeter.budget(task_class)

        # 6. CALL LLM
        response = self._call_llm(c_system, c_user, model, temperature, max_tokens)
        latency_ms = int((time.time() - t0) * 1000)

        # 7. ESCALATE if needed
        escalated = False
        if self.escalator.should_escalate(response, task_class):
            next_model = self.escalator.next_model(model)
            if next_model:
                escalated = True
                model = next_model
                max_tokens = max_tokens * 2
                response = self._call_llm(
                    c_system, c_user, next_model, temperature, max_tokens
                )
                latency_ms = int((time.time() - t0) * 1000)

        # 8. CACHE store
        self.cache.store(system_prompt, user_message, response)

        # 9. AUDIT
        cost = self.auditor.estimate_cost(model, max_tokens)
        self.auditor.log({
            "task_class": task_class,
            "model": model,
            "tokens_budget": max_tokens,
            "latency_ms": latency_ms,
            "cache_hit": False,
            "escalated": escalated,
            "cost_estimate": cost,
            "compression_savings_pct": round(savings * 100, 1),
        })

        return response

    def _call_llm(self, system_prompt, user_message, model, temperature, max_tokens):
        """Raw LLM call. Dispatches to OpenAI-compatible or Anthropic backend."""
        try:
            if self._backend == "openai" and self._client:
                resp = self._client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                )
                return (resp.choices[0].message.content or "").strip()
            else:
                import anthropic
                client = anthropic.Anthropic()
                msg = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                return msg.content[0].text.strip()
        except Exception as exc:
            return "# ERROR: %s" % str(exc)
