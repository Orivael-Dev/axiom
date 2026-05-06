"""
AXIOM Scientific Research Pipeline
====================================
9-agent constitutional research workflow with efficiency layer routing.

Pipeline flow:
  1. Hypothesis  → Generate testable hypothesis + null hypothesis
  2. Literature  → Search/verify existing research
  3. Simulation  → Model the hypothesis before lab work
  4. Critic      → Find flaws (question blindness enforced)
  5. Safety      → Risk assessment — CAN HALT PIPELINE
  6. Ethics      → Ethical evaluation — CAN HALT PIPELINE
  7. Data        → Collect/validate experimental data plan
  8. Experiment  → Design the experiment protocol
  9. Report      → Write constitutional findings

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python axiom_research_pipeline.py "Does intermittent fasting reduce inflammation?"
  python axiom_research_pipeline.py --steps 3 "Can vitamin D improve sleep quality?"
  python axiom_research_pipeline.py --model claude-sonnet-4-6 "Your question here"

Requirements:
  pip install anthropic
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
from axiom_signing import derive_key
SIGNING_KEY = derive_key(b"axiom-research-pipeline-v1")
MANIFEST_FILE = "research_manifests.json"
AXIOM_FILES_DIR = Path(__file__).parent / "axiom_files"

# ASCII box-draw (Windows cp1252 safe)
BOX_DOUBLE = "=" * 58
BOX_SINGLE = "-" * 58
BOX_TOP = "+" + "=" * 56 + "+"
BOX_BTM = "+" + "=" * 56 + "+"
BOX_MID = "+" + "-" * 56 + "+"

# Agent definitions: name, axiom_file, task_class, step_number
AGENTS = [
    {"name": "hypothesis",  "axiom": "research/research_hypothesis.axiom",  "task_class": "medium",   "step": 1, "label": "HYPOTHESIS AGENT",  "max_tokens": 2000},
    {"name": "literature",  "axiom": "research/research_literature.axiom",  "task_class": "hard",     "step": 2, "label": "LITERATURE AGENT",  "max_tokens": 4000},
    {"name": "simulation",  "axiom": "research/research_simulation.axiom",  "task_class": "hard",     "step": 3, "label": "SIMULATION AGENT",  "max_tokens": 6000},
    {"name": "critic",      "axiom": "research/research_critic.axiom",      "task_class": "hard",     "step": 4, "label": "CRITIC AGENT (question blindness)", "max_tokens": 3000},
    {"name": "safety",      "axiom": "research/research_safety.axiom",      "task_class": "critical", "step": 5, "label": "SAFETY AGENT",      "max_tokens": 4000},
    {"name": "ethics",      "axiom": "research/research_ethics.axiom",      "task_class": "critical", "step": 6, "label": "ETHICS AGENT",      "max_tokens": 4000},
    {"name": "data",        "axiom": "research/research_data.axiom",        "task_class": "medium",   "step": 7, "label": "DATA AGENT",        "max_tokens": 2000},
    {"name": "experiment",  "axiom": "research/research_experiment.axiom",  "task_class": "hard",     "step": 8, "label": "EXPERIMENT AGENT",  "max_tokens": 4000},
    {"name": "report",      "axiom": "research/research_report.axiom",      "task_class": "hard",     "step": 9, "label": "REPORT AGENT",      "max_tokens": 5000},
]


# ── Signing ──────────────────────────────────────────────────────────────────

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


# ── Load .axiom spec as system prompt ────────────────────────────────────────

def _load_axiom_system(axiom_file):
    """Read .axiom file and use it as the system prompt."""
    path = AXIOM_FILES_DIR / axiom_file
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "You are a research assistant. Respond with valid JSON only."


# ── LLM call ─────────────────────────────────────────────────────────────────

def _call_llm(system_prompt, user_message, model, max_tokens, temperature=0.3):
    """Call LLM. Prefers Anthropic API; falls back to NIM/OpenAI-compatible client.
    Returns (response_text, latency_ms).
    """
    t0 = time.time()

    # ── Path A: Anthropic API ─────────────────────────────────────────────────
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = msg.content[0].text.strip()
            latency = int((time.time() - t0) * 1000)
            return text, latency
        except Exception as exc:
            latency = int((time.time() - t0) * 1000)
            return '{"error": "%s"}' % str(exc).replace('"', "'"), latency

    # ── Path B: NIM / OpenAI-compatible (axiom_constitutional client) ─────────
    try:
        from axiom_constitutional.client import chat as _axchat
        # Use AXIOM_MODEL if set; claude model IDs don't resolve on NIM
        nim_model = os.environ.get("AXIOM_MODEL", "meta/llama-3.3-70b-instruct")
        text = _axchat(
            system_prompt=system_prompt,
            user_message=user_message,
            model=nim_model,
            temperature=temperature,
            _skip_validation=True,
            caller="research_pipeline",
        )
        latency = int((time.time() - t0) * 1000)
        return text, latency
    except Exception as exc:
        latency = int((time.time() - t0) * 1000)
        return '{"error": "%s"}' % str(exc).replace('"', "'"), latency


# ── Parse JSON from LLM response ────────────────────────────────────────────

def _parse_json(raw):
    """Extract JSON from model response, handling markdown fences and truncation."""
    import re

    # Strip markdown fences
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()

    # 1. Clean parse
    try:
        return json.loads(raw)
    except (ValueError, KeyError):
        pass

    # 2. Find first {...} block and try clean parse
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, KeyError):
            pass

    # 3. Truncation recovery — walk backward adding "}" until valid JSON
    #    Handles cases where token budget cut the stream mid-string or mid-array.
    candidate = m.group(0) if m else raw
    # Close any open arrays first, then objects
    depth_obj = 0
    depth_arr = 0
    in_string = False
    escape = False
    for ch in candidate:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not in_string:
            in_string = True
        elif ch == '"' and in_string:
            in_string = False
        elif not in_string:
            if ch == "{":
                depth_obj += 1
            elif ch == "}":
                depth_obj -= 1
            elif ch == "[":
                depth_arr += 1
            elif ch == "]":
                depth_arr -= 1

    # Trim to last clean token boundary (avoid broken mid-string)
    trimmed = candidate.rstrip()
    # Remove any trailing incomplete string value
    if in_string:
        last_quote = trimmed.rfind('"')
        if last_quote > 0:
            trimmed = trimmed[:last_quote]

    # Remove trailing commas that break JSON
    trimmed = re.sub(r",\s*$", "", trimmed)
    # Close open arrays then objects
    closing = "]" * max(0, depth_arr) + "}" * max(0, depth_obj)
    repaired = trimmed + closing
    try:
        return json.loads(repaired)
    except (ValueError, KeyError):
        pass

    # 4. Final fallback
    return {"raw_response": raw[:500], "parse_error": True}


# ── Model routing ────────────────────────────────────────────────────────────

MODEL_LADDER = {
    "simple":   "claude-haiku-4-5-20251001",
    "medium":   "claude-sonnet-4-6",
    "hard":     "claude-sonnet-4-6",
    "critical": "claude-opus-4-6",
}

TOKEN_BUDGETS = {
    "simple":   300,
    "medium":   1500,
    "hard":     4000,
    "critical": 8000,
}


# ── Terminal output ──────────────────────────────────────────────────────────

def _format_value(value):
    """Format a value for terminal display."""
    if isinstance(value, list):
        if len(value) == 0:
            return "(none)"
        items = []
        for v in value[:4]:
            if isinstance(v, dict):
                # Try to pull a name/title/description from sub-objects
                label = v.get("title") or v.get("name") or v.get("description") or v.get("flaw") or str(v)
                items.append(str(label)[:70])
            else:
                items.append(str(v)[:70])
        out = "; ".join(items)
        if len(value) > 4:
            out += " (+%d more)" % (len(value) - 4)
        return out
    if isinstance(value, dict):
        # Try to pull a meaningful summary
        for key in ("value", "summary", "description", "text"):
            if key in value:
                return str(value[key])[:100]
        return json.dumps(value)[:100]
    if isinstance(value, bool):
        check = "YES" if value else "NO"
        return check
    return str(value)[:100]


def _print_step(step, total, label, model, task_class, result, manifest_id, latency_ms, agent_name=""):
    confidence = _extract_confidence(result.get("confidence", 0.70))

    print()
    print(BOX_TOP)
    print("  STEP %d/%d %s %s" % (step, total, BOX_SINGLE[:2], label))
    print("  Model    : %s (%s task)  %dms" % (model, task_class, latency_ms))
    print(BOX_MID)

    # Use per-agent display fields if available
    fields = DISPLAY_FIELDS.get(agent_name, [])
    if fields:
        for key in fields:
            if key == "confidence":
                print("  %-20s: %.0f%%" % ("Confidence", confidence * 100))
            elif key in result:
                print("  %-20s: %s" % (key, _format_value(result[key])))
    else:
        # Fallback: print first 8 non-meta fields
        skip_keys = {"raw_response", "parse_error", "error"}
        printed = 0
        for key, value in result.items():
            if key in skip_keys:
                continue
            print("  %-20s: %s" % (key, _format_value(value)))
            printed += 1
            if printed >= 8:
                break

    print(BOX_MID)
    print("  Manifest : %s" % manifest_id)
    print(BOX_BTM)


def _print_halt(step, total, label, reason):
    print()
    print(BOX_TOP)
    halt_marker = "!! PIPELINE HALTED !!"
    print("  %s" % halt_marker)
    print("  Step %d/%d %s %s" % (step, total, BOX_SINGLE[:2], label))
    print("  Reason   : %s" % reason)
    print(BOX_BTM)


# ── Confidence extraction ────────────────────────────────────────────────────

def _extract_confidence(raw):
    """Handle any confidence shape the model returns."""
    if isinstance(raw, float):
        return max(0.15, min(0.85, raw))
    if isinstance(raw, int):
        return max(0.15, min(0.85, float(raw)))
    if isinstance(raw, dict):
        # Try common keys
        for key in ("value", "score", "level", "rating"):
            if key in raw and isinstance(raw[key], (int, float)):
                return max(0.15, min(0.85, float(raw[key])))
        # First numeric value found
        for v in raw.values():
            if isinstance(v, (int, float)):
                return max(0.15, min(0.85, float(v)))
    if isinstance(raw, str):
        try:
            return max(0.15, min(0.85, float(raw)))
        except (ValueError, TypeError):
            pass
    # Default — uncertain
    return 0.70


# ── Per-agent display fields ─────────────────────────────────────────────────

DISPLAY_FIELDS = {
    "hypothesis":  ["hypothesis", "null_hypothesis", "falsifiable", "assumptions", "confidence"],
    "literature":  ["sources", "supporting_evidence", "contradicting_evidence", "consensus_level", "gaps", "confidence"],
    "simulation":  ["model_description", "predicted_outcomes", "assumptions", "sensitivity_parameters", "limitations", "confidence"],
    "critic":      ["severity", "flaws", "rival_hypothesis", "recommendation", "confidence"],
    "safety":      ["verdict", "risk_level", "risks", "mitigations", "halt_reason", "confidence"],
    "ethics":      ["verdict", "classification", "concerns", "mitigations_required", "halt_reason", "confidence"],
    "data":        ["data_requirements", "methodology", "sample_size", "validation_criteria", "confidence"],
    "experiment":  ["protocol", "control_groups", "endpoints", "analysis_plan", "success_criteria", "confidence"],
    "report":      ["title", "summary", "findings", "negative_results", "rival_hypothesis", "limitations", "conclusions", "confidence"],
}


# ── Build manifest ───────────────────────────────────────────────────────────

def _build_manifest(agent_name, step, result, model, task_class, latency_ms, halted=False):
    prefix_map = {
        "hypothesis": "HYP", "literature": "LIT", "simulation": "SIM",
        "critic": "CRT", "safety": "SAF", "ethics": "ETH",
        "data": "DAT", "experiment": "EXP", "report": "RPT",
    }
    prefix = prefix_map.get(agent_name, "UNK")
    manifest_id = "RP-%s-%s-%s" % (
        prefix,
        datetime.now().strftime("%Y%m%d-%H%M%S"),
        str(uuid.uuid4())[:6],
    )

    confidence = _extract_confidence(result.get("confidence", 0.70))

    verdict = result.get("verdict", "COMPLETE")
    if agent_name == "critic":
        verdict = result.get("severity", "REVIEWED")
    elif agent_name == "safety":
        verdict = result.get("verdict", "PROCEED")
    elif agent_name == "ethics":
        verdict = result.get("verdict", "PROCEED")

    manifest = {
        "manifest_id":           manifest_id,
        "manifest_version":      "1.0",
        "engine":                "AXIOM Research Pipeline v1.0",
        "agent":                 agent_name,
        "step":                  step,
        "layer":                 "RESEARCH_PIPELINE",
        "timestamp":             datetime.now().isoformat() + "Z",
        "model":                 model,
        "task_class":            task_class,
        "latency_ms":            latency_ms,
        "verdict":               verdict,
        "confidence":            confidence,
        "halted":                halted,
        "question_blindness":    agent_name == "critic",
        "can_halt_pipeline":     agent_name in ("safety", "ethics"),
        "rival_required":        agent_name in ("critic", "report"),
        "agent_response":        result,
    }

    manifest["signature"] = _sign(manifest)
    return manifest


# ── Pipeline ─────────────────────────────────────────────────────────────────

class ResearchPipeline:
    """Orchestrates the 9-agent constitutional research pipeline."""

    def __init__(self, model_override=None):
        self.model_override = model_override
        self.manifests = []
        self.halted = False
        self.halt_reason = None
        self.halt_step = None

    def _get_model(self, task_class):
        if self.model_override:
            return self.model_override
        return MODEL_LADDER.get(task_class, "claude-sonnet-4-6")

    def _get_budget(self, task_class):
        return TOKEN_BUDGETS.get(task_class, 1500)

    def _run_agent(self, agent_def, user_prompt, total_steps):
        """Run one agent: load spec, call LLM, parse, sign manifest, print."""
        name       = agent_def["name"]
        axiom_file = agent_def["axiom"]
        task_class = agent_def["task_class"]
        step       = agent_def["step"]
        label      = agent_def["label"]

        system_prompt = _load_axiom_system(axiom_file)
        system_prompt += (
            "\n\nIMPORTANT: Your response MUST be valid JSON only. "
            "No prose, no markdown fences, no explanation outside the JSON object. "
            "Keep string values under 300 characters. "
            "Keep arrays to a maximum of 5 items. "
            "Do not include wrapper keys like 'agent' or 'version' — return the data fields directly."
        )

        model      = self._get_model(task_class)
        # Use per-agent token budget, fall back to task_class default
        max_tokens = agent_def.get("max_tokens") or self._get_budget(task_class)

        response, latency_ms = _call_llm(system_prompt, user_prompt, model, max_tokens)
        result = _parse_json(response)

        manifest = _build_manifest(name, step, result, model, task_class, latency_ms)
        self.manifests.append(manifest)

        _print_step(step, total_steps, label, model, task_class, result, manifest["manifest_id"], latency_ms, agent_name=name)

        return result, manifest

    def run(self, research_question, max_steps=9):
        """Run the full pipeline. Returns final report dict."""
        total_steps = min(max_steps, 9)
        context = {"question": research_question}

        print()
        print("  AXIOM Scientific Research Pipeline v1.0")
        print("  " + BOX_DOUBLE)
        print("  Question: %s" % research_question)
        print("  Steps   : %d" % total_steps)
        print("  " + BOX_DOUBLE)

        # ── Step 1: Hypothesis ────────────────────────────────────────
        agent = AGENTS[0]
        prompt = (
            "Research question: %s\n\n"
            "Generate a testable hypothesis with null hypothesis. "
            "Return JSON with: hypothesis, null_hypothesis, falsifiable, "
            "falsification_criteria, variables, assumptions, confidence"
        ) % research_question

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["hypothesis"] = result
        if total_steps <= 1:
            return self._final_report(context)

        # ── Step 2: Literature ────────────────────────────────────────
        agent = AGENTS[1]
        hypothesis_text = result.get("hypothesis", research_question)
        prompt = (
            "Research question: %s\n"
            "Hypothesis: %s\n\n"
            "Search existing literature for evidence supporting or "
            "contradicting this hypothesis. Return JSON with: sources[], "
            "supporting_evidence, contradicting_evidence, gaps[], "
            "consensus_level, confidence"
        ) % (research_question, hypothesis_text)

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["literature"] = result
        if total_steps <= 2:
            return self._final_report(context)

        # ── Step 3: Simulation ────────────────────────────────────────
        agent = AGENTS[2]
        prompt = (
            "Research question: %s\n"
            "Hypothesis: %s\n"
            "Literature summary: %s\n\n"
            "Model this hypothesis. Predict outcomes, list assumptions, "
            "identify key parameters. Return JSON with: model_description, "
            "assumptions[], predicted_outcomes[], sensitivity_parameters[], "
            "limitations[], confidence"
        ) % (
            research_question,
            context["hypothesis"].get("hypothesis", ""),
            json.dumps(context["literature"].get("supporting_evidence", ""))[:300],
        )

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["simulation"] = result
        if total_steps <= 3:
            return self._final_report(context)

        # ── Step 4: Critic (QUESTION BLINDNESS) ──────────────────────
        # Critic gets claim + evidence only, NOT hypothesis reasoning
        agent = AGENTS[3]
        prompt = (
            "CLAIM: %s\n\n"
            "EVIDENCE:\n"
            "- Simulation predicted: %s\n"
            "- Literature says: %s\n\n"
            "NOTE: You have NOT been shown the reasoning behind this claim. "
            "Evaluate the claim against the evidence only. "
            "Find flaws. Provide a rival hypothesis. "
            "Return JSON with: flaws[], rival_hypothesis, severity, "
            "recommendation, question_blindness_enforced, confidence"
        ) % (
            context["hypothesis"].get("hypothesis", ""),
            json.dumps(context["simulation"].get("predicted_outcomes", []))[:200],
            context["literature"].get("supporting_evidence", "")[:200] if isinstance(context["literature"].get("supporting_evidence"), str) else json.dumps(context["literature"].get("supporting_evidence", ""))[:200],
        )

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["critique"] = result
        if total_steps <= 4:
            return self._final_report(context)

        # ── Step 5: Safety (CAN HALT) ────────────────────────────────
        agent = AGENTS[4]
        prompt = (
            "Research question: %s\n"
            "Hypothesis: %s\n"
            "Critic severity: %s\n"
            "Critic flaws: %s\n\n"
            "Assess safety risks. Can this research proceed safely? "
            "Return JSON with: verdict (PROCEED or CRITICAL_RISK), "
            "risk_level, risks[], mitigations[], halt_reason, confidence"
        ) % (
            research_question,
            context["hypothesis"].get("hypothesis", ""),
            context["critique"].get("severity", "UNKNOWN"),
            json.dumps(context["critique"].get("flaws", []))[:300],
        )

        result, manifest = self._run_agent(agent, prompt, total_steps)
        context["safety"] = result

        if result.get("verdict") == "CRITICAL_RISK":
            self.halted = True
            self.halt_reason = result.get("halt_reason", "Critical safety risk detected")
            self.halt_step = 5
            _print_halt(5, total_steps, "SAFETY AGENT", self.halt_reason)
            return self._final_report(context, halted=True)

        if total_steps <= 5:
            return self._final_report(context)

        # ── Step 6: Ethics (CAN HALT) ────────────────────────────────
        agent = AGENTS[5]
        prompt = (
            "Research question: %s\n"
            "Hypothesis: %s\n"
            "Safety verdict: %s\n\n"
            "Evaluate ethical implications. Are there concerns about "
            "participants, consent, equity, or societal impact? "
            "Return JSON with: verdict (PROCEED or ETHICS_VIOLATION), "
            "classification, concerns[], mitigations_required[], "
            "informed_consent_needed, halt_reason, confidence"
        ) % (
            research_question,
            context["hypothesis"].get("hypothesis", ""),
            context["safety"].get("verdict", "PROCEED"),
        )

        result, manifest = self._run_agent(agent, prompt, total_steps)
        context["ethics"] = result

        if result.get("verdict") == "ETHICS_VIOLATION":
            self.halted = True
            self.halt_reason = result.get("halt_reason", "Ethics violation detected")
            self.halt_step = 6
            _print_halt(6, total_steps, "ETHICS AGENT", self.halt_reason)
            return self._final_report(context, halted=True)

        if total_steps <= 6:
            return self._final_report(context)

        # ── Step 7: Data ──────────────────────────────────────────────
        agent = AGENTS[6]
        prompt = (
            "Research question: %s\n"
            "Hypothesis: %s\n"
            "Simulation parameters: %s\n\n"
            "Define data collection requirements. "
            "Return JSON with: data_requirements[], methodology, "
            "sample_size, validation_criteria[], provenance, "
            "integrity_hash, confidence"
        ) % (
            research_question,
            context["hypothesis"].get("hypothesis", ""),
            json.dumps(context["simulation"].get("sensitivity_parameters", []))[:200],
        )

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["data"] = result
        if total_steps <= 7:
            return self._final_report(context)

        # ── Step 8: Experiment ────────────────────────────────────────
        agent = AGENTS[7]
        prompt = (
            "Research question: %s\n"
            "Hypothesis: %s\n"
            "Data requirements: %s\n"
            "Critic recommendation: %s\n\n"
            "Design the experimental protocol. Include controls, "
            "endpoints, analysis plan. "
            "Return JSON with: protocol, control_groups[], endpoints[], "
            "analysis_plan, success_criteria, failure_criteria, "
            "reproducibility_notes, confidence"
        ) % (
            research_question,
            context["hypothesis"].get("hypothesis", ""),
            json.dumps(context["data"].get("data_requirements", []))[:200],
            context["critique"].get("recommendation", ""),
        )

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["experiment"] = result
        if total_steps <= 8:
            return self._final_report(context)

        # ── Step 9: Report ────────────────────────────────────────────
        agent = AGENTS[8]
        prompt = (
            "Research question: %s\n\n"
            "PIPELINE RESULTS:\n"
            "Hypothesis: %s\n"
            "Null hypothesis: %s\n"
            "Literature consensus: %s\n"
            "Simulation prediction: %s\n"
            "Critic rival hypothesis: %s\n"
            "Critic severity: %s\n"
            "Safety verdict: %s\n"
            "Ethics verdict: %s\n"
            "Experiment protocol summary: %s\n\n"
            "Write the constitutional research report. "
            "Include what was found AND what was NOT found. "
            "Use 'shows promise' not 'cures'. "
            "Return JSON with: title, summary, findings[], "
            "negative_results[], rival_hypothesis, limitations[], "
            "conclusions, pipeline_provenance, confidence"
        ) % (
            research_question,
            context["hypothesis"].get("hypothesis", ""),
            context["hypothesis"].get("null_hypothesis", ""),
            context["literature"].get("consensus_level", "unknown"),
            json.dumps(context["simulation"].get("predicted_outcomes", []))[:150],
            context["critique"].get("rival_hypothesis", ""),
            context["critique"].get("severity", ""),
            context["safety"].get("verdict", ""),
            context["ethics"].get("verdict", ""),
            context["experiment"].get("protocol", "")[:150] if isinstance(context["experiment"].get("protocol"), str) else json.dumps(context["experiment"].get("protocol", ""))[:150],
        )

        result, _ = self._run_agent(agent, prompt, total_steps)
        context["report"] = result

        return self._final_report(context)

    def _final_report(self, context, halted=False):
        """Print summary and save manifests."""
        print()
        print("  " + BOX_DOUBLE)
        if halted:
            print("  PIPELINE HALTED at step %d" % (self.halt_step or 0))
            print("  Reason: %s" % self.halt_reason)
        else:
            print("  PIPELINE COMPLETE — %d steps executed" % len(self.manifests))
        print("  Manifests: %d signed" % len(self.manifests))
        print("  " + BOX_DOUBLE)

        # Save manifests
        try:
            with open(MANIFEST_FILE, "w") as f:
                json.dump(self.manifests, f, indent=2)
            print("  Saved %s" % MANIFEST_FILE)
        except IOError as exc:
            print("  [warning] Could not save manifests: %s" % exc)

        return {
            "question": context.get("question", ""),
            "steps_completed": len(self.manifests),
            "halted": halted,
            "halt_reason": self.halt_reason,
            "manifests": self.manifests,
            "context": context,
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AXIOM Scientific Research Pipeline — 9-agent constitutional workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python axiom_research_pipeline.py "Does intermittent fasting reduce inflammation?"\n'
            '  python axiom_research_pipeline.py --steps 3 "Can vitamin D improve sleep?"\n'
            '  python axiom_research_pipeline.py --model claude-sonnet-4-6 "Your question"\n'
        ),
    )
    parser.add_argument(
        "question",
        help="The research question to investigate",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=9,
        help="Number of pipeline steps to run (1-9, default: 9)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override model for all agents (bypasses efficiency routing)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Manifest output path (default: research_manifests.json)",
    )
    args = parser.parse_args()

    global MANIFEST_FILE
    if args.output:
        MANIFEST_FILE = args.output

    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_nim = bool(
        os.environ.get("AXIOM_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not has_anthropic and not has_nim:
        print("[error] No API key configured. Set one of:")
        print("  $env:ANTHROPIC_API_KEY  = 'sk-ant-...'   (Anthropic — preferred)")
        print("  $env:AXIOM_API_KEY      = 'nvapi-...'    (NVIDIA NIM)")
        print("  $env:NVIDIA_API_KEY     = 'nvapi-...'    (NVIDIA NIM legacy)")
        sys.exit(1)

    if has_anthropic:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            print("[error] anthropic package not installed.")
            print("  pip install anthropic")
            sys.exit(1)

    pipeline = ResearchPipeline(model_override=args.model)
    pipeline.run(args.question, max_steps=args.steps)


if __name__ == "__main__":
    main()
