"""
SmolVLM reader for The Impossible Quiz.

Two calls per turn:
  extract_state(pil_image)  →  {"question": str, "answers": [str×4]}
  pick_answer(question, answers)  →  {"choice": A-D, "reasoning": str, "confidence": float}

Model: HuggingFaceTB/SmolVLM-Instruct  (follows research/quant/bench_multimodal_srd.py pattern)
"""
from __future__ import annotations

import json
import re
from typing import Optional

_MODELS = {
    "smolvlm":      "HuggingFaceTB/SmolVLM-Instruct",
    "smolvlm-256m": "HuggingFaceTB/SmolVLM-256M-Instruct",
    "smolvlm-500m": "HuggingFaceTB/SmolVLM-Instruct",
}

# Two-shot examples showing the lateral-thinking pattern this game requires
_QRF_HINTS = """\
The Impossible Quiz uses trick questions. Rules to remember:
- The obvious, sensible answer is almost always wrong.
- Watch for wordplay: "a femur" might mean something literal about letters.
- Sometimes the answer is an absurd non-sequitur — pick it if the others are too obvious.
- Sometimes the question itself is the trick (e.g. "click the answer" means click the word "answer").
- Answers that reference the game, the UI, or previous questions are often correct.
"""

_EXTRACT_PROMPT = """\
This is a screenshot of The Impossible Quiz game.
Read the question text and all four answer button labels exactly as they appear.
Return ONLY valid JSON with this exact structure:
{"question": "<question text>", "answers": ["<A text>", "<B text>", "<C text>", "<D text>"]}
If you cannot read the question or answers, return:
{"question": "", "answers": []}
"""

_REASON_TMPL = """\
{hints}
Question: {question}
Answers:
  A) {a}
  B) {b}
  C) {c}
  D) {d}

Which answer is correct? Think step by step, then end your response with:
ANSWER: <A or B or C or D>
CONFIDENCE: <0.0–1.0>
"""

# QRF branch prompts — different reasoning angles
_QRF_BRANCHES_BASE = [
    # Branch 0: lateral / trick angle
    "The Impossible Quiz always has a trick. The obvious answer is wrong. "
    "Look for wordplay, puns, or a non-sequitur that only makes sense laterally. "
    "Question: {question}\nAnswers: A){a} B){b} C){c} D){d}\n"
    "What is the lateral-thinking trick here? End with: ANSWER: <A/B/C/D>",

    # Branch 1: meta-game / UI angle
    "The Impossible Quiz sometimes hides the answer in the question wording itself, "
    "the UI, or a reference to a previous question. "
    "Question: {question}\nAnswers: A){a} B){b} C){c} D){d}\n"
    "Is there a meta-game clue? End with: ANSWER: <A/B/C/D>",

    # Branch 2: elimination angle
    "For The Impossible Quiz, eliminate the three most obvious/sensible answers. "
    "The remaining one is probably correct. "
    "Question: {question}\nAnswers: A){a} B){b} C){c} D){d}\n"
    "Which answer is left after eliminating the obvious ones? End with: ANSWER: <A/B/C/D>",
]

# Branch 3: option-aware pun scan.
# Only fires when branches 0-2 all disagree (1-1-1 tie) — prevents it from
# manufacturing spurious "hidden meanings" on count/meta questions where the
# base branches already agree.
_QRF_PUN_SCAN = (
    "Question: {question}\n\n"
    "Check each answer option for a hidden pun, homophone, or double meaning:\n"
    "A) \"{a}\" — does this sound like something else? Any wordplay or alternate meaning?\n"
    "B) \"{b}\" — does this sound like something else? Any wordplay or alternate meaning?\n"
    "C) \"{c}\" — does this sound like something else? Any wordplay or alternate meaning?\n"
    "D) \"{d}\" — does this sound like something else? Any wordplay or alternate meaning?\n\n"
    "Which option's hidden meaning best answers the question?\n"
    "End with: ANSWER: <A/B/C/D>"
)


class SmolVLMReader:
    """Wraps SmolVLM for screenshot reading and quiz reasoning."""

    def __init__(self, model_id: Optional[str] = None, device: str = "cpu"):
        mid = _MODELS.get(model_id or "smolvlm", model_id or _MODELS["smolvlm"])
        try:
            import torch
            from transformers import AutoModelForVision2Seq, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "transformers and torch required: pip install transformers torch accelerate"
            ) from exc

        self.device    = device
        self.model_id  = mid
        self.processor = AutoProcessor.from_pretrained(mid)
        self.model     = AutoModelForVision2Seq.from_pretrained(
            mid, torch_dtype="auto", low_cpu_mem_usage=True
        ).to(device)
        self.model.eval()

    def _generate(self, pil_images: list, text_prompt: str, max_new_tokens: int = 256) -> str:
        """Run one inference call. Returns decoded text after the prompt."""
        import torch

        content = []
        for _ in pil_images:
            content.append({"type": "image"})
        content.append({"type": "text", "text": text_prompt})

        messages = [{"role": "user", "content": content}]
        prompt   = self.processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs   = self.processor(
            text=prompt,
            images=pil_images if pil_images else None,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        # Decode only the generated tokens (skip the prompt)
        new_tokens = output_ids[0][inputs["input_ids"].shape[-1]:]
        return self.processor.decode(new_tokens, skip_special_tokens=True).strip()

    def _generate_text_only(self, prompt: str, max_new_tokens: int = 128) -> str:
        """Run inference on text only (no image) for reasoning branches."""
        import torch

        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        text     = self.processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs   = self.processor(text=text, return_tensors="pt").to(self.device)

        with torch.no_grad():
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        new_tokens = output_ids[0][inputs["input_ids"].shape[-1]:]
        return self.processor.decode(new_tokens, skip_special_tokens=True).strip()

    def extract_state(self, pil_image) -> dict:
        """Screenshot → {"question": str, "answers": [str, str, str, str]}."""
        try:
            raw = self._generate([pil_image], _EXTRACT_PROMPT, max_new_tokens=128)
            # Find JSON block in response
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                data    = json.loads(match.group())
                answers = data.get("answers", [])
                # Pad / truncate to exactly 4
                answers = (list(answers) + ["", "", "", ""])[:4]
                return {"question": str(data.get("question", "")), "answers": answers}
        except Exception:
            pass
        return {"question": "", "answers": ["", "", "", ""]}

    def pick_answer(self, question: str, answers: list[str], pil_image=None) -> dict:
        """Reason about the best answer using the screenshot if available."""
        if not question:
            return {"choice": "A", "reasoning": "no question extracted", "confidence": 0.0}

        a, b, c, d = (answers + ["", "", "", ""])[:4]
        prompt = _REASON_TMPL.format(
            hints=_QRF_HINTS, question=question, a=a, b=b, c=c, d=d
        )
        try:
            if pil_image is not None:
                raw = self._generate([pil_image], prompt, max_new_tokens=180)
            else:
                raw = self._generate_text_only(prompt, max_new_tokens=180)
            return _parse_answer_response(raw, question)
        except Exception as exc:
            return {"choice": "A", "reasoning": f"inference error: {exc}", "confidence": 0.0}

    def qrf_branches(self, question: str, answers: list[str], pil_image=None) -> list[dict]:
        """Run QRF branches with gated pun-scan.

        Phase 1: run the 3 base branches (lateral / meta / elimination).
        If they reach a 2/3 majority, return immediately — pun-scan never fires.
        Phase 2: only on a 1-1-1 tie do we run the pun-scan branch to break the
        deadlock. This prevents pun-scan from manufacturing spurious hidden-meaning
        votes on count or meta questions where the base branches already agree.
        """
        a, b, c, d = (answers + ["", "", "", ""])[:4]

        results = []
        for branch_prompt in _QRF_BRANCHES_BASE:
            prompt = branch_prompt.format(question=question, a=a, b=b, c=c, d=d)
            try:
                if pil_image is not None:
                    raw = self._generate([pil_image], prompt, max_new_tokens=100)
                else:
                    raw = self._generate_text_only(prompt, max_new_tokens=100)
                choice = _extract_choice(raw)
                results.append({"choice": choice, "reasoning": raw[:120]})
            except Exception:
                results.append({"choice": "A", "reasoning": "branch error"})

        # Check for early-exit majority (≥ 2 of 3)
        votes: dict[str, int] = {}
        for r in results:
            c = r.get("choice", "A")
            votes[c] = votes.get(c, 0) + 1
        if max(votes.values()) >= 2:
            return results  # majority found — pun-scan not needed

        # 1-1-1 tie: run pun-scan as the deadlock-breaker
        prompt = _QRF_PUN_SCAN.format(question=question, a=a, b=b, c=c, d=d)
        try:
            if pil_image is not None:
                raw = self._generate([pil_image], prompt, max_new_tokens=120)
            else:
                raw = self._generate_text_only(prompt, max_new_tokens=120)
            choice = _extract_choice(raw)
            results.append({"choice": choice, "reasoning": "[pun-scan tiebreak] " + raw[:120]})
        except Exception:
            results.append({"choice": "A", "reasoning": "[pun-scan tiebreak] branch error"})

        return results


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _extract_choice(text: str) -> str:
    """Extract A/B/C/D from model output."""
    m = re.search(r'ANSWER\s*:\s*([ABCD])', text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # Fallback: last standalone A/B/C/D letter
    letters = re.findall(r'\b([ABCD])\b', text.upper())
    return letters[-1] if letters else "A"


def _parse_answer_response(raw: str, question: str) -> dict:
    choice     = _extract_choice(raw)
    conf_match = re.search(r'CONFIDENCE\s*:\s*([0-9.]+)', raw, re.IGNORECASE)
    confidence = float(conf_match.group(1)) if conf_match else 0.5
    confidence = max(0.0, min(1.0, confidence))
    # Extract reasoning: everything before ANSWER:
    reasoning  = re.split(r'ANSWER\s*:', raw, flags=re.IGNORECASE)[0].strip()
    return {"choice": choice, "reasoning": reasoning[:300], "confidence": confidence}
