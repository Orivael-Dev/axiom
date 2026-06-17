"""ORVL-022 CPI + local agent — a 3D object described through the
Constitutional Physical Intelligence pipeline.

examples/cpi_demo.py proves the CPI claims with fixed synthetic parameters.
This version drives a local Qwen3-1.7B SRD4 model to perceive the object
and produce real geometry + material estimates that flow into the full
4-layer physical-constitutional pipeline:

  Layer 0  (3D Perception)   — model assesses a described 3D object from a
                               scene: material class, vertex geometry features,
                               recommended grip force, stability concern
  Layer 1  (Material Sim)    — MaterialSimulator N-branch contact forecast;
                               fracture-branch probability = constitutional
                               distance (ORVL-014 World Model in physical space)
  Layer 2  (Vertex Gate)     — VertexClassifier maps geometry → grip skill +
                               CANNOT_MUTATE torque ceiling; FRAGILE ceiling
                               is CANNOT_EXCEED — planning-layer override test
  Layer 3  (Supervisor)      — SupervisoryGuard: PASS / SOFTEN / VETO based on
                               per-class competence × forecast vs min_safe
  Layer 4  (Stability Ticks) — model generates 5 stability scores for the
                               pickup trajectory; PhysicalMonotonicGate fires
                               L1–L4 reflexes on non-monotonic dips
  Claim 5  (Certificate)     — MotionExaminer issues a signed Certificate over
                               the 6-scenario sealed suite (teacher's key,
                               independent of agent key)

Model-assessed geometry feeds real physical parameters — not synthetic.
On a fragile object (thin glass) the model should output GLASS +
low_density_edges=1, triggering FRAGILE vertex class and the 0.2 Nm
CANNOT_EXCEED ceiling. On a stable metal cylinder it should output METAL +
vertical_clusters=2, CYLINDRICAL, 2.0 Nm ceiling.

Rocksi integration (--state / --state-file):
  Run tools/rocksi_bridge.js in the Rocksi browser console (F12) to capture
  live joint angles, TCP position samples, and SimObject geometry.  Pass the
  resulting JSON to bypass the model perception step and drive the CPI
  pipeline with real 3D simulator state.

  # 1. In Rocksi browser console:
  #    paste + run tools/rocksi_bridge.js → JSON copied to clipboard
  # 2. On the Jetson:
  python3 axiom_cpi_local_agent.py \\
    --state '{"robot_name":"franka_panda","arm_joints":[...],...}' \\
    --scene "Red cube on the table, roughly 8x8x8 cm"
  # or from a saved file:
  python3 axiom_cpi_local_agent.py \\
    --state-file tools/rocksi_state.json \\
    --scene "Red cube on the table"

Run (model-only, no Rocksi):
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_cpi_local_agent.py \\
    --scene "A tall wine glass: thin-walled borosilicate, 22 cm height, 8 cm base"
  python3 axiom_cpi_local_agent.py \\
    --scene "A steel thermos flask: 500 ml, 20 cm tall, 7 cm diameter, heavy cap"
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

if not os.environ.get("AXIOM_MASTER_KEY"):
    print("[WARN] AXIOM_MASTER_KEY not set — using ephemeral demo key", file=sys.stderr)
    os.environ["AXIOM_MASTER_KEY"] = "demo-key-" + __import__("secrets").token_hex(16)

from axiom_cpi import (
    HumanoidStabilityAgent, StabilityFrame, VertexClassifier, TorqueExceeded,
    PhysicalMonotonicGate, TORQUE_LIMIT_FRAGILE, STABILITY_FLOOR,
)
from axiom_motion_examiner import MotionExaminer

_DEFAULT_MODEL  = "models/qwen25_coder_0p5b_srd4_q4km.gguf"
_DEFAULT_BIN    = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP            = "─" * 66
_MATERIALS      = {"GLASS", "METAL", "WOOD", "SOFT", "UNKNOWN"}


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _call_model(prompt: str, model: str, binary: str,
                n_predict: int, temp: float) -> str:
    cmd = [binary, "-m", model, "-p", prompt,
           "-n", str(n_predict), "-c", "2048",
           "--temp", str(temp), "-ngl", "99", "-t", "6",
           "--no-display-prompt"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] model call failed: {e}")
        return ""
    err = proc.stderr.lower()
    if "out of memory" in err or "unable to create context" in err:
        print("  [ERROR] model load failed (CUDA OOM / context).")
        return ""
    out = proc.stdout.strip()
    if "</think>" in out:
        out = out.rsplit("</think>", 1)[1].strip()
    elif "<think>" in out:
        out = out.split("<think>", 1)[0].strip()
    return out


def _build_perception_prompt(scene: str) -> str:
    system = (
        "You are an AXIOM Constitutional Physical Intelligence perception agent. "
        "A robotic arm is about to grasp the described 3D object. "
        "Analyze the scene and output ONLY the 8 assessment fields below in "
        "EXACT format. /no_think\n\n"
        "Rules:\n"
        "  MATERIAL_CLASS must be one of: GLASS METAL WOOD SOFT UNKNOWN\n"
        "  vertical_clusters   : integer 0-4  (cylindrical features, tubes, stems)\n"
        "  horizontal_planes   : integer 0-3  (flat surfaces, bases, lids)\n"
        "  isolated_protrusions: integer 0-3  (handles, knobs, spouts)\n"
        "  low_density_edges   : integer 0-2  (thin rims, tapered walls, fragile edges)\n"
        "  shape_variance      : float 0.0-1.0 (0=regular, 1=irregular/deformable)\n"
        "  GRIP_FORCE_NM       : float 0.1-5.0 (recommended initial grip in Newton-meters)\n"
        "  STABILITY_CONCERN   : [one sentence describing the main grasp risk]\n\n"
        "Output ONLY these 8 lines, no preamble, no extra text."
    )
    user = (
        f"3D OBJECT SCENE: {scene}\n\n"
        "Assess this object for robotic grasp:"
    )
    # Prime the model with the first field so it stays on-format
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n"
            f"MATERIAL_CLASS: ")


def _build_stability_prompt(scene: str, material: str, vertex_class: str,
                             applied_nm: float, fracture_p: float) -> str:
    system = (
        "You are simulating a robotic arm's stability during a 5-tick pickup motion. "
        "Output ONLY 5 stability scores (one per line), each a float 0.00–1.00. "
        "/no_think\n\n"
        "Ticks represent: approach → contact → grip-close → lift → hold.\n"
        "A fragile glass at high force dips at grip-close; metal is stable.\n"
        "Output ONLY:\n"
        "TICK_0: 0.XX\n"
        "TICK_1: 0.XX\n"
        "TICK_2: 0.XX\n"
        "TICK_3: 0.XX\n"
        "TICK_4: 0.XX"
    )
    user = (
        f"Object   : {scene}\n"
        f"Material : {material}  |  Vertex class: {vertex_class}  |  "
        f"Applied grip: {applied_nm:.2f} Nm  |  Fracture risk: {fracture_p:.3f}\n\n"
        "5-tick pickup stability scores:"
    )
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n")


def _parse_perception(raw: str) -> Tuple[str, Dict, float, str]:
    # Model was primed with "MATERIAL_CLASS: " so prepend it
    text = "MATERIAL_CLASS: " + raw if not re.match(r"MATERIAL_CLASS", raw, re.I) else raw

    mat_m = re.search(r"MATERIAL_CLASS\s*:\s*(\w+)", text, re.I)
    material = mat_m.group(1).upper() if mat_m else "UNKNOWN"
    if material not in _MATERIALS:
        material = "UNKNOWN"

    features: Dict = {}
    for feat in ("vertical_clusters", "horizontal_planes",
                 "isolated_protrusions", "low_density_edges"):
        m = re.search(rf"{feat}\s*:\s*(\d+)", text, re.I)
        features[feat] = int(m.group(1)) if m else 0

    sv_m = re.search(r"shape_variance\s*:\s*([0-9]*\.?[0-9]+)", text, re.I)
    features["shape_variance"] = float(sv_m.group(1)) if sv_m else 0.0

    gf_m = re.search(r"GRIP_FORCE_NM\s*:\s*([0-9]*\.?[0-9]+)", text, re.I)
    grip = max(0.1, min(5.0, float(gf_m.group(1)))) if gf_m else 1.0

    sc_m = re.search(r"STABILITY_CONCERN\s*:\s*(.+)", text, re.I)
    concern = sc_m.group(1).strip() if sc_m else ""

    return material, features, grip, concern


def _parse_stability(text: str) -> List[float]:
    scores = []
    for i in range(5):
        m = re.search(rf"TICK_{i}\s*:\s*([0-9]*\.?[0-9]+)", text, re.I)
        if m:
            v = float(m.group(1))
            scores.append(max(0.0, min(1.0, v if v <= 1.0 else v / 100.0)))
    return scores


def _rocksi_state_to_cpi(state: dict, scene_hint: str
                         ) -> Tuple[str, Dict, float, str]:
    """Map a Rocksi JSON state blob → (material, features, grip_nm, scene_desc).

    SimObject shape/size/mass → vertex geometry features.
    Color hex → material heuristic (fallback: UNKNOWN + model uses scene_hint).
    Mass → grip force via F = m·g·μ (μ≈0.15 for typical dry-contact gripper).
    """
    obj = (state.get("sim_objects") or [{}])[0]
    shape  = (obj.get("shape") or "box").lower().replace("cube", "box")
    size   = obj.get("size") or {"x": 0.08, "y": 0.08, "z": 0.08}
    mass   = float(obj.get("mass") or 0.3)
    color  = (obj.get("color") or "").lower().strip()

    # Color → material heuristic
    material = "UNKNOWN"
    if color.startswith("#") and len(color) >= 7:
        try:
            r = int(color[1:3], 16) / 255.0
            g = int(color[3:5], 16) / 255.0
            b = int(color[5:7], 16) / 255.0
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum > 0.80:
                material = "GLASS"      # very bright / white → glass
            elif r > 0.45 and g > 0.30 and b < 0.25:
                material = "WOOD"       # brownish
            elif lum > 0.35:
                material = "METAL"      # mid-gray / silver
            else:
                material = "UNKNOWN"
        except ValueError:
            pass

    # Shape → vertex geometry features
    min_dim = min(size.get("x", 0.08), size.get("y", 0.08), size.get("z", 0.08))
    features: Dict = {
        "vertical_clusters":    2 if shape == "cylinder" else 0,
        "horizontal_planes":    1 if shape == "box" else 0,
        "isolated_protrusions": 0,
        "low_density_edges":    1 if min_dim < 0.04 else 0,   # thin = fragile
        "shape_variance":       0.5 if shape == "sphere" else 0.0,
    }

    # Grip force from mass: N = m·g·friction_factor, clamped to 0.1–5.0 Nm
    grip_nm = round(max(0.1, min(5.0, mass * 9.81 * 0.15)), 2)

    # Scene description for logs
    w, h, d = size.get("x", "?"), size.get("y", "?"), size.get("z", "?")
    robot   = state.get("robot_name", "unknown")
    scene_desc = (
        scene_hint
        or f"Rocksi {robot}: {shape} "
           f"{w:.3f}×{h:.3f}×{d:.3f} m, {mass:.3f} kg, color={color}"
    )
    return material, features, grip_nm, scene_desc


def _tcp_to_stability(tcp_samples: List[dict], fracture_p: float) -> List[float]:
    """Convert 5 Rocksi TCP position samples → normalized stability scores.

    Stability proxy: per-sample displacement from the previous sample,
    normalised by 0.05 m (5 cm at 100 ms = 0.5 m/s, a fast pickup move).
    Static arm (all same position) → near 1.0, reduced by fracture_p.
    Fast / jerky motion → lower scores, triggering MonotonicGate reflexes.
    """
    if len(tcp_samples) < 2:
        base = round(0.90 - fracture_p * 0.30, 3)
        return [round(max(0.05, base - fracture_p * 0.04 * i), 3) for i in range(5)]

    pts = [(s["tcp"]["x"], s["tcp"]["y"], s["tcp"]["z"]) for s in tcp_samples[:5]]
    scores: List[float] = []
    for i, pt in enumerate(pts):
        if i == 0:
            disp = 0.0
        else:
            prev = pts[i - 1]
            disp = math.sqrt(sum((a - b) ** 2 for a, b in zip(pt, prev)))
        instability = min(1.0, disp / 0.05 * 0.5 + fracture_p * 0.30)
        scores.append(round(max(0.05, 1.0 - instability), 3))

    while len(scores) < 5:
        scores.append(scores[-1])
    return scores[:5]


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-022 CPI + local agent")
    ap.add_argument("--scene",
                    default="A tall wine glass: thin-walled borosilicate, "
                            "22 cm height, 8 cm base diameter")
    ap.add_argument("--model",  default=_DEFAULT_MODEL)
    ap.add_argument("--bin",    dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=200)
    ap.add_argument("--temp",   type=float, default=0.3)
    # Rocksi bridge inputs (bypass model perception when provided)
    ap.add_argument("--state",      default=None,
                    help="Rocksi JSON state string from tools/rocksi_bridge.js")
    ap.add_argument("--state-file", default=None, dest="state_file",
                    help="Path to saved Rocksi JSON state file")
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first.", file=sys.stderr)
        return 2

    # ── Rocksi state (optional) — bypasses model perception ───────────────
    rocksi_state: Optional[dict] = None
    if args.state_file:
        try:
            rocksi_state = json.loads(Path(args.state_file).read_text())
        except Exception as e:
            print(f"[ERROR] could not load --state-file: {e}", file=sys.stderr)
            return 2
    elif args.state:
        try:
            rocksi_state = json.loads(args.state)
        except Exception as e:
            print(f"[ERROR] could not parse --state JSON: {e}", file=sys.stderr)
            return 2

    # ── Layer 0: perception — Rocksi state OR model ────────────────────────
    if rocksi_state is not None:
        _header("Layer 0 — 3D Perception: Rocksi live state (bridge mode)")
        robot_name = rocksi_state.get("robot_name", "unknown")
        n_joints   = len(rocksi_state.get("arm_joints", []))
        n_samples  = len(rocksi_state.get("tcp_samples", []))
        n_objects  = len(rocksi_state.get("sim_objects", []))
        print(f"  robot   : {robot_name}  ({n_joints} arm joints)")
        print(f"  tcp     : {n_samples} position samples")
        print(f"  objects : {n_objects} SimObject(s) detected")
        if rocksi_state.get("arm_joints"):
            print(f"\n  Joint angles (rad):")
            for j in rocksi_state["arm_joints"]:
                bar = "█" * int(abs(j["angle_rad"]) / 3.14 * 20)
                print(f"    {j['name']:<6} {j['angle_rad']:+.4f}  [{bar}]")
        material, features, grip_force, concern = _rocksi_state_to_cpi(
            rocksi_state, args.scene)
        print(f"\n  Derived from SimObject:")
        print(f"    material           : {material}")
        for k, v in features.items():
            print(f"    {k:<22} : {v}")
        print(f"    GRIP_FORCE_NM      : {grip_force:.2f}")
        print(f"    scene              : {concern}")
        rocksi_ticks: Optional[List[float]] = None   # filled after Layer 1
    else:
        _header("Layer 0 — 3D Perception: model assesses object from scene")
        print(f"  model : {args.model}")
        print(f"  scene : \"{args.scene}\"\n")

        raw_p = _call_model(_build_perception_prompt(args.scene),
                            args.model, args.binary, args.n_predict, args.temp)
        if not raw_p:
            return 1

        print("  --- model output ---")
        for line in raw_p.splitlines():
            print(f"  | {line}")

        material, features, grip_force, concern = _parse_perception(raw_p)
        print(f"\n  Parsed perception:")
        print(f"    material           : {material}")
        for k, v in features.items():
            print(f"    {k:<22} : {v}")
        print(f"    GRIP_FORCE_NM      : {grip_force:.2f}")
        if concern:
            print(f"    STABILITY_CONCERN  : {concern}")
        rocksi_ticks = None

    # ── Layer 1: Material Sim (N-branch contact forecast) ─────────────────
    _header("Layer 1 — Material Sim: N-branch contact forecast")
    agent = HumanoidStabilityAgent()
    plan = agent.perceive_and_plan(
        object_id="model-assessed-object",
        features=features,
        material_class=material,
        requested_grip_force_nm=grip_force,
    )
    sim = plan["material"]
    vtx = plan["vertex"]
    sup = plan["supervisory_review"]

    print(f"  MaterialSimulator ({material}, grip={grip_force:.2f} Nm):")
    for b in sim["branches"]:
        bar = "█" * int(b["probability"] * 30)
        print(f"    {b['label']:<14}  {b['probability']:.3f}  [{bar}]")
    print(f"  fracture_probability    : {sim['fracture_probability']:.4f}")
    print(f"  constitutional_distance : {sim['constitutional_distance']:.4f}  "
          f"(= 1.0 − fracture_p)")
    print(f"  cautious_approach       : {sim['cautious_approach']}")

    # ── Layer 2: Vertex Gate ───────────────────────────────────────────────
    _header("Layer 2 — Vertex Gate: geometry → constitutional torque ceiling")
    print(f"  vertex_class    : {vtx['vertex_class']}   "
          f"(confidence {vtx['confidence']:.2f})")
    print(f"  grip_skill      : {vtx['grip_skill']}")
    print(f"  torque_ceiling  : {vtx['torque_ceiling']} Nm  (CANNOT_MUTATE)")
    print(f"  requested_grip  : {plan['requested_grip_force']:.2f} Nm")
    print(f"  applied_grip    : {plan['applied_grip_force']:.2f} Nm  "
          f"(clamped: {plan['torque_clamped']})")

    if vtx["vertex_class"] == "FRAGILE":
        print(f"\n  CANNOT_EXCEED contract test (planning-layer override):")
        try:
            VertexClassifier.enforce_torque("FRAGILE", grip_force + 1.0)
            print("    [UNEXPECTED] no exception raised.")
        except TorqueExceeded as exc:
            print(f"    TorqueExceeded → {exc}")
            print(f"    FRAGILE ceiling {TORQUE_LIMIT_FRAGILE} Nm is CANNOT_EXCEED. ✓")

    # ── Layer 3: Supervisory Guard (PASS / SOFTEN / VETO) ─────────────────
    _header("Layer 3 — Supervisory Guard: competence × forecast → verdict")
    verdict = sup["verdict"]
    icon = {"PASS": "✓", "SOFTEN": "~", "VETO": "✗"}.get(verdict, "?")
    print(f"  [{icon}] {verdict}  — {sup['reason']}")
    print(f"  competence      : {sup['competence']:.2f}  (boots at 0.0 — untrusted)")
    print(f"  min_predicted   : {sup['min_predicted']:.3f}")
    print(f"  min_safe        : {sup['min_safe']:.3f}")
    if verdict == "SOFTEN":
        print(f"  softening ×{sup['softening_factor']:.2f}  → "
              f"supervised_grip = {plan['supervised_grip_force']:.3f} Nm")
    print(f"  HMAC            : {sup['signature'][:24]}...")

    # ── Layer 4: Stability Ticks — Rocksi TCP samples OR model ────────────
    _header("Layer 4 — Stability Ticks: 5-tick pickup motion")
    applied_nm = plan["applied_grip_force"]
    fracture_p = sim["fracture_probability"]
    vertex_cls = vtx["vertex_class"]

    if rocksi_state is not None:
        tcp_samples = rocksi_state.get("tcp_samples", [])
        ticks = _tcp_to_stability(tcp_samples, fracture_p)
        print(f"  source : Rocksi TCP samples ({len(tcp_samples)} points → 5 ticks)")
        if tcp_samples:
            print(f"  TCP trajectory:")
            for s in tcp_samples[:5]:
                t = s["tcp"]
                print(f"    t={s.get('t','?')}  "
                      f"({t['x']:+.4f}, {t['y']:+.4f}, {t['z']:+.4f})")
        print(f"  derived stability ticks: {ticks}")
    else:
        print(f"  source : Qwen3 model inference")
        raw_s = _call_model(
            _build_stability_prompt(args.scene, material, vertex_cls,
                                    applied_nm, fracture_p),
            args.model, args.binary, 80, args.temp)

        if raw_s:
            print("  --- model output ---")
            for line in raw_s.splitlines():
                print(f"  | {line}")

        ticks = _parse_stability(raw_s) if raw_s else []
        if len(ticks) < 5:
            base = round(0.90 - fracture_p * 0.30, 3)
            ticks = [round(max(0.0, base - fracture_p * 0.04 * i), 3)
                     for i in range(5)]
            print(f"\n  [fallback ticks from fracture_p={fracture_p:.3f}]: {ticks}")

    print(f"\n  PhysicalMonotonicGate — 5-tick pickup trajectory:")
    gate   = PhysicalMonotonicGate()
    events = []
    for i, score in enumerate(ticks):
        frame = StabilityFrame(
            timestamp_ms=i * 100,
            com_offset=round(0.02 + fracture_p * 0.03, 4),
            stability_score=score,
            joint_torques=(applied_nm,),
        )
        ev = gate.record(frame)
        events.append(ev)
        bar  = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        flag = ""
        if ev.fired:
            flag = f"  ← L{ev.level} REFLEX  ({ev.reason[:45]})"
        elif score < STABILITY_FLOOR:
            flag = "  ← FLOOR BREACH"
        print(f"    tick {i}  score={score:.3f}  [{bar}]{flag}")

    reflexes = [e for e in events if e.fired]
    print(f"\n  reflexes fired  : {gate.reflex_count}   "
          f"emergency stops : {gate.emergency_count}")
    if reflexes:
        worst = max(reflexes, key=lambda e: e.level)
        print(f"  worst event     : L{worst.level} — {worst.reason}")

    # ── Claim 5: MotionExaminer certificate (6 sealed scenarios) ──────────
    _header("Claim 5 — MotionExaminer: signed certificate (6 sealed scenarios)")
    fresh_agent = HumanoidStabilityAgent()
    examiner    = MotionExaminer()
    cert, results = examiner.evaluate(fresh_agent)
    verified    = examiner.verify_certificate(cert)

    icon_c = "✓" if cert.scenarios_failed == 0 else "✗"
    print(f"  [{icon_c}] suite: {cert.suite_id}  v{cert.suite_version}")
    print(f"  scenarios run   : {cert.scenarios_run}")
    print(f"  passed          : {cert.scenarios_passed}")
    print(f"  failed          : {cert.scenarios_failed}")
    for r in results:
        mark = "✓" if r.passed else "✗"
        print(f"    {mark} {r.scenario_id}")
        for reason in r.reasons:
            print(f"        {reason}")
    print(f"  issued_at       : {cert.issued_at}")
    print(f"  HMAC            : {cert.signature[:32]}...")
    print(f"  verify_certificate: {verified}  "
          f"(teacher key ≠ agent key — agent cannot forge this)")

    _header("Summary")
    print(f"  Scene           : {args.scene}")
    print(f"  Material        : {material}  (model-assessed)")
    print(f"  Vertex class    : {vertex_cls}  → {vtx['grip_skill']}")
    print(f"  Torque ceiling  : {vtx['torque_ceiling']} Nm  (CANNOT_MUTATE)")
    print(f"  Applied grip    : {applied_nm:.2f} Nm  "
          f"({'clamped' if plan['torque_clamped'] else 'within ceiling'})")
    print(f"  Fracture risk   : {fracture_p:.3f}  "
          f"(const. dist={sim['constitutional_distance']:.3f})")
    print(f"  Supervisor      : {verdict}")
    print(f"  Reflex events   : {gate.reflex_count}  "
          f"(worst L{max((e.level for e in reflexes), default=0)})")
    print(f"  Certificate     : {cert.scenarios_passed}/{cert.scenarios_run} passed  "
          f"HMAC {cert.signature[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
