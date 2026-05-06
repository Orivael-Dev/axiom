"""
AXIOM Constitutional Layers — Jeopardy GameWatcher (standalone).

Provides JeopardyWatcher, JeopardyPlayer, JeopardyEvaluator for use by
browser_watcher.py (screen capture via mss).

Works on Jetson Nano / Python 3.8 / ARM64 — no pygame, no OCR libraries.
Claude Vision API (haiku) handles OCR + answering in one shot.

Game interface expected by these classes:
  frame_arr   numpy array (H, W, 3) RGB — current screen capture
  state dict  frame, clue_active, clue_hash
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

# ── Constitutional constants ───────────────────────────────────────────────────
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from axiom_signing import derive_key
SIGNING_KEY = derive_key(b"axiom-gamewatcher-jeopardy-v1")

HEDGE_WORDS = (
    "possibly", "might", "maybe", "i'm not sure", "i am not sure",
    "unclear", "uncertain", "unsure", "could be", "not certain",
    "approximately", "probably", "likely",
)


def _sign(manifest):
    # type: (dict) -> str
    sig_str = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
    )
    return (
        "hmac-sha256:"
        + hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()[:32]
        + "..."
    )


# ══════════════════════════════════════════════════════════════
# LAYER 1 — WATCHER
# ══════════════════════════════════════════════════════════════

class JeopardyWatcher:
    """
    Layer 1 — Observes every frame, detects when a Jeopardy clue card appears.
    Uses color heuristic (no API call): center region blue-dominant.
    CANNOT_MUTATE: can_take_action = False
    """

    def __init__(self):
        self.last_clue_hash = ""
        self.kept_count  = 0
        self.skip_count  = 0
        self.flag_count  = 0

    def _center_crop(self, arr):
        # type: (object) -> object
        """Return center 50% of frame as numpy array."""
        import numpy as np
        h, w = arr.shape[:2]
        return arr[h // 4: 3 * h // 4, w // 4: 3 * w // 4]

    def _clue_hash(self, crop):
        # type: (object) -> str
        import numpy as np
        data = np.ascontiguousarray(crop).tobytes()
        return hashlib.md5(data).hexdigest()[:16]

    def _clue_visible(self, arr):
        # type: (object) -> bool
        """
        Heuristic: a Jeopardy clue card is shown when the center of the screen
        is dominated by a dark-blue background (blue mean > 130, red mean < 110).
        Covers jeopardylabs.com, j-archive, and most blue-themed variants.
        """
        import numpy as np
        crop = self._center_crop(arr)
        mean_r = float(np.mean(crop[:, :, 0]))
        mean_g = float(np.mean(crop[:, :, 1]))
        mean_b = float(np.mean(crop[:, :, 2]))
        return mean_b > 130 and mean_r < 110

    def observe(self, arr, state):
        # type: (object, dict) -> dict
        frame = state.get("frame", 0)

        if not self._clue_visible(arr):
            self.skip_count += 1
            return {
                "verdict":         "SKIP",
                "reason":          "no_clue_visible",
                "frame":           frame,
                "layer":           "WATCHER",
                "can_take_action": False,
                "stats": {
                    "kept":    self.kept_count,
                    "skipped": self.skip_count,
                    "flagged": self.flag_count,
                },
            }

        crop = self._center_crop(arr)
        current_hash = self._clue_hash(crop)

        if current_hash == self.last_clue_hash:
            self.skip_count += 1
            return {
                "verdict":         "SKIP",
                "reason":          "same_clue",
                "frame":           frame,
                "clue_hash":       current_hash,
                "layer":           "WATCHER",
                "can_take_action": False,
            }

        # New clue detected
        prev_hash = self.last_clue_hash
        self.last_clue_hash = current_hash
        self.kept_count += 1

        verdict = "KEEP"
        reason  = "new_clue_detected"

        return {
            "verdict":         verdict,
            "reason":          reason,
            "frame":           frame,
            "clue_hash":       current_hash,
            "prev_hash":       prev_hash,
            "domain_map":      "JEOPARDYWATCHER: clue_interaction_detected",
            "layer":           "WATCHER",
            "can_take_action": False,
            "stats": {
                "kept":    self.kept_count,
                "skipped": self.skip_count,
                "flagged": self.flag_count,
            },
        }


# ══════════════════════════════════════════════════════════════
# LAYER 2 — PLAYER
# ══════════════════════════════════════════════════════════════

class JeopardyPlayer:
    """
    Layer 2 — Reads clue via Claude Vision API and answers in Jeopardy format.
    CANNOT_MUTATE: must_log_reasoning = True
    """

    _PROMPT = (
        "You are playing Jeopardy. Look at this screenshot carefully.\n\n"
        "Extract:\n"
        "1. The category name (usually displayed at the top)\n"
        "2. The dollar amount (e.g. $200, $400)\n"
        "3. The clue text (the full statement shown)\n\n"
        "Then answer the clue in proper Jeopardy format: "
        "\"What is [answer]?\" or \"Who is [person]?\"\n\n"
        "Return ONLY valid JSON, no markdown, no explanation:\n"
        "{\"category\": \"...\", \"amount\": \"...\", "
        "\"clue\": \"...\", \"answer\": \"What is ...?\", "
        "\"reasoning\": \"...\"}"
    )

    def _encode_frame(self, arr):
        # type: (object) -> str
        """Crop center region, encode as base64 PNG."""
        import base64
        import io
        import numpy as np
        from PIL import Image

        h, w = arr.shape[:2]
        crop = arr[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        img  = Image.fromarray(crop.astype("uint8"), "RGB")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.standard_b64encode(buf.getvalue()).decode("ascii")

    def _call_api(self, b64_img, model, retries=3):
        # type: (str, str, int) -> str
        import anthropic
        client = anthropic.Anthropic()

        for attempt in range(retries):
            try:
                msg = client.messages.create(
                    model=model,
                    max_tokens=512,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type":       "base64",
                                    "media_type": "image/png",
                                    "data":       b64_img,
                                },
                            },
                            {
                                "type": "text",
                                "text": self._PROMPT,
                            },
                        ],
                    }],
                )
                return msg.content[0].text.strip()
            except Exception as exc:
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    print("  [retry %d] %s — waiting %ds" % (attempt + 1, exc, wait))
                    time.sleep(wait)
                else:
                    return '{"category":"","amount":"","clue":"","answer":"","reasoning":"API error: %s"}' % str(exc)

    def _parse_response(self, raw):
        # type: (str) -> dict
        """Try to extract JSON from model response."""
        import re
        # Strip markdown fences if present
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        raw = raw.strip()

        try:
            return json.loads(raw)
        except (ValueError, KeyError):
            # Try to extract JSON object from mixed response
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except (ValueError, KeyError):
                    pass
        return {"category": "", "amount": "", "clue": raw[:200], "answer": "", "reasoning": "parse_error"}

    def _score_confidence(self, answer, reasoning):
        # type: (str, str) -> float
        if not answer:
            return 0.20
        ans_lower = answer.lower()
        if ans_lower.startswith("what is") or ans_lower.startswith("who is"):
            base = 0.85
        else:
            base = 0.55

        combined = (answer + " " + reasoning).lower()
        for word in HEDGE_WORDS:
            if word in combined:
                base -= 0.15
                break

        return max(0.20, min(0.85, base))

    def decide(self, arr, observation, model="claude-haiku-4-5-20251001"):
        # type: (object, dict, str) -> dict
        t0 = time.time()

        b64_img  = self._encode_frame(arr)
        raw      = self._call_api(b64_img, model)
        parsed   = self._parse_response(raw)

        category  = parsed.get("category", "")
        amount    = parsed.get("amount", "")
        clue      = parsed.get("clue", "")
        answer    = parsed.get("answer", "")
        reasoning = parsed.get("reasoning", "")

        confidence = self._score_confidence(answer, reasoning)
        latency_ms = int((time.time() - t0) * 1000)

        return {
            "category":           category,
            "amount":             amount,
            "clue_text":          clue,
            "answer":             answer,
            "reasoning":          reasoning,
            "confidence":         confidence,
            "latency_ms":         latency_ms,
            "model":              model,
            "raw_response":       raw[:400],
            "layer":              "PLAYER",
            "must_log_reasoning": True,
        }


# ══════════════════════════════════════════════════════════════
# LAYER 3 — EVALUATOR
# ══════════════════════════════════════════════════════════════

class JeopardyEvaluator:
    """
    Layer 3 — Evaluates answer quality.
    CANNOT_MUTATE: question_blindness = True
    CANNOT_MUTATE: rival_move_required = True
    CANNOT_MUTATE: uncertainty_floor = 0.15
    MAX_CONFIDENCE = 0.85
    """

    MAX_CONFIDENCE   = 0.85
    UNCERTAINTY_FLOOR = 0.15

    def _classify(self, answer, confidence):
        # type: (str, float) -> str
        if not answer or not answer.strip():
            return "NO_ANSWER"
        ans_lower = answer.lower().strip()
        if ans_lower.startswith("what is") or ans_lower.startswith("who is"):
            if confidence >= 0.70:
                return "CORRECT_FORMAT"
            return "HEDGED"
        for word in HEDGE_WORDS:
            if word in ans_lower:
                return "HEDGED"
        return "FORMAT_VIOLATION"

    def _rival_move(self, answer, category, clue_text):
        # type: (str, str, str) -> str
        """Always provide an alternative phrasing — CANNOT_MUTATE."""
        if not answer:
            return (
                "Fallback: 'What is %s?' (category topic used as guess)"
                % (category or "unknown")
            )
        ans_lower = answer.lower()
        if ans_lower.startswith("what is"):
            who_ver = "Who is" + answer[7:]
            return (
                "Alternative phrasing: '%s' "
                "(if answer is a person rather than a thing)"
                % who_ver
            )
        if ans_lower.startswith("who is"):
            what_ver = "What is" + answer[6:]
            return (
                "Alternative phrasing: '%s' "
                "(if answer is a thing rather than a person)"
                % what_ver
            )
        return (
            "Reformulated: 'What is %s?' "
            "(add Jeopardy format to raw answer)"
            % answer.strip("?").strip()
        )

    def evaluate(self, player_decision, observation):
        # type: (dict, dict) -> dict
        t0 = time.time()

        answer     = player_decision.get("answer", "")
        category   = player_decision.get("category", "")
        clue_text  = player_decision.get("clue_text", "")
        confidence = float(player_decision.get("confidence", 0.0))

        # Apply uncertainty floor and ceiling — CANNOT_MUTATE
        confidence = max(self.UNCERTAINTY_FLOOR, min(self.MAX_CONFIDENCE, confidence))

        classification = self._classify(answer, confidence)
        rival          = self._rival_move(answer, category, clue_text)

        domain_verdict = (
            "CORRECT_RESPONSE"   if classification == "CORRECT_FORMAT"
            else "HEDGED_RESPONSE"     if classification == "HEDGED"
            else "FORMAT_VIOLATION"    if classification == "FORMAT_VIOLATION"
            else "NO_ANSWER_PRODUCED"
        )

        manifest_id = "JW-JEO-%s-%s" % (
            datetime.now().strftime("%Y%m%d-%H%M%S"),
            str(uuid.uuid4())[:6],
        )

        manifest = {
            "manifest_id":              manifest_id,
            "manifest_version":         "1.0",
            "engine":                   "AXIOM GameWatcher — Jeopardy v1.0",
            "layer":                    "EVALUATOR",
            "timestamp":                datetime.now().isoformat() + "Z",
            "latency_ms":               int((time.time() - t0) * 1000),
            "question_blindness":       True,
            "player_reasoning_seen":    False,
            "clue_hash":                observation.get("clue_hash", ""),
            "frame":                    observation.get("frame", 0),
            "category":                 category,
            "amount":                   player_decision.get("amount", ""),
            "clue_text":                clue_text,
            "answer":                   answer,
            "confidence":               confidence,
            "classification":           classification,
            "domain_verdict":           domain_verdict,
            "domain_package":           "jeopardywatcher",
            "rival_move_required":      True,
            "rival_move":               rival,
            "uncertainty_floor_applied": True,
            "max_confidence_enforced":  self.MAX_CONFIDENCE,
        }

        manifest["signature"] = _sign(manifest)
        return manifest
