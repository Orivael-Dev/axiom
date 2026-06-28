"""
AXIOM Web UI — a plain website (FastAPI + SSE), no Streamlit.

Same engine as ui.py: the constitutional evolution loop (Worker → Evaluator →
Rewriter) over NVIDIA NIM, the AXIOM DSL loop that mutates worker.axiom, and a
growth dashboard over logs/. Streams every iteration live to the browser.

Run:
  pip install fastapi uvicorn python-dotenv      # axiom_constitutional already in repo
  # NVIDIA_API_KEY in .env (or env)
  python web_ui.py        →  http://localhost:8010
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
load_dotenv(HERE / ".env")

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

DEFAULT_MODEL = os.environ.get("AXIOM_MODEL", "meta/llama-3.3-70b-instruct")
EXAMPLES = [
    "Explain what makes an AI agent capable of improving itself",
    "Write a Python function that validates email addresses without using regex",
    "List three ways self-improving agents drift from their original goal and a safeguard for each",
    "Design a minimal constitutional AI safety layer for a self-rewriting agent",
]

app = FastAPI(title="AXIOM Web UI")
_runs: dict[str, dict] = {}


# ── Prompt-evolution run ────────────────────────────────────────────────────────

async def run_prompt(run_id: str, cfg: dict) -> None:
    run = _runs[run_id]
    async def emit(t, **d): await run["queue"].put({"type": t, **d})

    task = cfg["task"]; model = cfg["model"]
    max_it = cfg["max_iterations"]; thr = cfg["threshold"]; temp = cfg["temperature"]
    force = cfg["force_rewrite"]; meta = cfg["enable_meta"]
    os.environ["AXIOM_MODEL"] = model
    os.environ["AXIOM_MAX_ITERATIONS"] = str(max_it)
    os.environ["AXIOM_QUALITY_THRESHOLD"] = str(thr)

    try:
        await emit("status", message="Generating scoring rubric…")
        from axiom_constitutional import rubric as rubric_module
        rubric = await asyncio.to_thread(rubric_module.generate, task)
        await emit("rubric", rubric=rubric)

        from axiom_constitutional.agents.worker import WorkerAgent
        from axiom_constitutional.agents.evaluator import EvaluatorAgent
        from axiom_constitutional.agents.rewriter import RewriterAgent
        from axiom_constitutional.evolution import EvolutionResult, IterationResult, LOGS_DIR
        from axiom_constitutional import store as prompt_store, client as nim
        from axiom_files.parser import get_prompt_with_overlays, detect_overlays

        detected = detect_overlays(task)
        if detected:
            await emit("status", message="Overlays detected: " + ", ".join(detected))

        worker = WorkerAgent(task)
        if detected:
            worker.system_prompt = get_prompt_with_overlays("worker", detected)
        evaluator = EvaluatorAgent(task)
        rewriter = RewriterAgent(task)

        short = uuid.uuid4().hex[:8]
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOGS_DIR / f"{short}.jsonl"
        result = EvolutionResult(task_description=task, run_id=short)
        scores: list[float] = []
        converged = False

        for i in range(max_it):
            await emit("iter_start", i=i, total=max_it)

            worker_output = await asyncio.to_thread(
                lambda: nim.chat(worker.system_prompt, f"Task:\n{task}", temperature=temp))
            await emit("worker", i=i, output=worker_output)

            try:
                evaluation = await asyncio.to_thread(
                    lambda: evaluator.score(task=task, output=worker_output, rubric=rubric))
            except Exception as e:
                await emit("warn", message=f"Evaluator error: {e}")
                continue

            score = float(evaluation.get("score", 0.0)); scores.append(score)
            await emit("evaluator", i=i, score=score, threshold=thr,
                       reasoning=evaluation.get("reasoning", ""),
                       improvements=evaluation.get("improvements", []),
                       dimension_scores=evaluation.get("dimension_scores", {}))

            it = IterationResult(
                iteration=i, worker_prompt=worker.system_prompt, worker_output=worker_output,
                score=score, reasoning=evaluation.get("reasoning", ""),
                improvements=evaluation.get("improvements", []),
                dimension_scores=evaluation.get("dimension_scores", {}))
            result.iterations.append(it)
            if score > result.best_score:
                result.best_score = score; result.best_iteration = len(result.iterations) - 1

            await asyncio.to_thread(prompt_store.save_iteration, task, "worker", worker.system_prompt, score)
            with log_file.open("a", encoding="utf-8") as lf:
                lf.write(json.dumps({
                    "run_id": short, "timestamp": datetime.now(timezone.utc).isoformat(),
                    "iteration": i, "agent_role": "worker", "score": score,
                    "output": worker_output, "evaluation": evaluation,
                }) + "\n")

            if score >= thr:
                converged = True
                await emit("converged", i=i, score=score)
                break

            if i < max_it - 1 and (force or score < thr):
                new_prompt = await asyncio.to_thread(
                    lambda: rewriter.rewrite(target_role="worker",
                                             current_prompt=worker.system_prompt,
                                             evaluation=evaluation))
                worker.system_prompt = new_prompt
                ver_note = _bump_worker_axiom(new_prompt)
                await emit("rewriter", i=i, new_prompt=new_prompt, version=ver_note)

        if meta and not converged:
            try:
                from axiom_constitutional import meta_evolution
                await emit("status", message="Running meta-evolution…")
                await asyncio.to_thread(meta_evolution.run_if_needed, result, rubric)
                await emit("meta", message="Evaluator and Rewriter prompts updated for future runs.")
            except Exception as e:
                await emit("warn", message=f"Meta-evolution error: {e}")

        best = result.best
        await emit("summary", converged=converged, best_score=result.best_score,
                   best_iteration=result.best_iteration,
                   best_output=best.worker_output, scores=scores, run_id=short)
    except Exception as e:
        await emit("error", message=str(e))
    finally:
        await emit("complete")


def _bump_worker_axiom(new_prompt: str) -> str:
    """Mirror ui.py: bump worker.axiom version + lift constraints from the new prompt."""
    try:
        from axiom_files.parser import load_axiom, save_axiom
        ax = load_axiom("worker")
        ver = float(ax.get("version", "1.0")) + 0.1
        ax["version"] = f"{ver:.1f}"
        skip = ["understand task", "identify missing", "produce answer", "relevance",
                "accuracy", "completeness", "tone", "wording", "empathy", "clarity:",
                "helpfulness:", "check answer"]
        new_c = [l.strip().lstrip("-").strip() for l in new_prompt.strip().split("\n")
                 if "constraint" in l.lower() and not any(s in l.lower() for s in skip)]
        if new_c:
            ax["constraints"] = new_c
        save_axiom("worker", ax)
        return f"worker.axiom → v{ver:.1f}"
    except Exception as e:
        return f"axiom write error: {e}"


# ── DSL run (mutates worker.axiom on disk) ──────────────────────────────────────

async def run_dsl(run_id: str, cfg: dict) -> None:
    run = _runs[run_id]
    async def emit(t, **d): await run["queue"].put({"type": t, **d})

    task = cfg["task"]; model = cfg["model"]
    max_it = cfg["max_iterations"]; thr = cfg["threshold"]; temp = cfg["temperature"]
    force = cfg["force_rewrite"]
    os.environ["AXIOM_MODEL"] = model

    try:
        import json as _json
        from axiom_files.parser import (load_axiom, save_axiom, to_system_prompt,
                                         get_prompt, get_prompt_with_overlays, detect_overlays)
        from axiom_constitutional import client as nim, store as dsl_store
        from axiom_constitutional import rubric as rubric_mod
        from axiom_constitutional.rubric import format_for_prompt

        await emit("status", message="Generating rubric…")
        rubric = await asyncio.to_thread(rubric_mod.generate, task)
        await emit("rubric", rubric=rubric)
        rubric_txt = format_for_prompt(rubric)

        worker_p = get_prompt_with_overlays("worker", detect_overlays(task))
        eval_p = get_prompt("evaluator"); rewrite_p = get_prompt("rewriter")
        best_score = 0.0; best_out = ""; scores: list[float] = []
        converged = False; short = uuid.uuid4().hex[:8]

        for i in range(max_it):
            await emit("iter_start", i=i, total=max_it)
            out = await asyncio.to_thread(lambda: nim.chat(worker_p, f"Task:\n{task}", temperature=temp))
            await emit("worker", i=i, output=out)

            eval_msg = (f"RUBRIC:\n{rubric_txt}\n\nTASK:\n{task}\n\nWORKER OUTPUT:\n{out}\n\n"
                        'Return JSON: {"score": <0-10>, "reasoning": "<str>", '
                        '"failures": ["<str>"], "suggested_changes": ["<str>"]}')
            try:
                ev = await asyncio.to_thread(lambda: nim.chat_json(eval_p, eval_msg, temperature=0.2))
            except ValueError as e:
                await emit("warn", message=f"Evaluator parse error: {e}")
                continue

            sc = float(ev.get("score", 0.0)); scores.append(sc)
            await emit("evaluator", i=i, score=sc, threshold=thr,
                       reasoning=ev.get("reasoning", ""), improvements=ev.get("failures", []),
                       dimension_scores={})
            await asyncio.to_thread(dsl_store.save_iteration, task, "worker", worker_p, sc)
            if sc > best_score:
                best_score = sc; best_out = out
            if sc >= thr:
                converged = True
                await emit("converged", i=i, score=sc)
                break

            if i < max_it - 1 and (force or sc < thr):
                cur = load_axiom("worker")
                failures = ev.get("failures", []); suggested = ev.get("suggested_changes", [])
                rw_msg = (f"Current worker.axiom (parsed):\n{_json.dumps(cur, indent=2)}\n\n"
                          f"Failures:\n" + "\n".join(f"- {f}" for f in failures) + "\n\n"
                          f"Suggested changes:\n" + "\n".join(f"- {s}" for s in suggested) + "\n\n"
                          'Return updated axiom dict as JSON. Add mutations key: '
                          '[{"field":...,"cut":...,"added":...,"why":...}]')
                try:
                    new_raw = await asyncio.to_thread(lambda: nim.chat_json(rewrite_p, rw_msg, temperature=0.4))
                except ValueError as e:
                    await emit("warn", message=f"Rewriter parse error: {e}")
                    continue
                mutations = new_raw.pop("mutations", [])
                for k, v in list(new_raw.items()):
                    if k in set(cur.keys()):
                        cur[k] = v
                ver = float(cur.get("version", "1.0")) + 0.1
                cur["version"] = f"{ver:.1f}"
                await asyncio.to_thread(save_axiom, "worker", cur)
                worker_p = to_system_prompt(cur)
                await emit("rewriter", i=i, new_prompt=worker_p,
                           version=f"worker.axiom → v{ver:.1f}", mutations=mutations)

        await emit("summary", converged=converged, best_score=best_score,
                   best_iteration=0, best_output=best_out, scores=scores, run_id=short)
    except Exception as e:
        await emit("error", message=str(e))
    finally:
        await emit("complete")


# ── Routes ──────────────────────────────────────────────────────────────────────

@app.post("/api/start")
async def start(body: dict, background_tasks: BackgroundTasks) -> dict:
    task = (body.get("task") or "").strip()
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)
    if not (os.environ.get("NVIDIA_API_KEY") or "").startswith("nvapi-"):
        return JSONResponse({"error": "NVIDIA_API_KEY not set (put it in .env)"}, status_code=400)

    cfg = {
        "task": task[:4000],
        "model": body.get("model") or DEFAULT_MODEL,
        "max_iterations": int(body.get("max_iterations", 5)),
        "threshold": float(body.get("threshold", 8.0)),
        "temperature": float(body.get("temperature", 0.7)),
        "force_rewrite": bool(body.get("force_rewrite", False)),
        "enable_meta": bool(body.get("enable_meta", True)),
    }
    mode = body.get("mode", "prompt")
    run_id = uuid.uuid4().hex[:8]
    _runs[run_id] = {"queue": asyncio.Queue()}
    background_tasks.add_task(run_dsl if mode == "dsl" else run_prompt, run_id, cfg)
    return {"run_id": run_id}


@app.get("/api/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    async def gen():
        run = _runs.get(run_id)
        if not run:
            yield f"data: {json.dumps({'type':'error','message':'run not found'})}\n\n"
            return
        while True:
            try:
                ev = await asyncio.wait_for(run["queue"].get(), timeout=60.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"; continue
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") == "complete":
                _runs.pop(run_id, None); break
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/meta")
async def meta() -> dict:
    return {"examples": EXAMPLES, "default_model": DEFAULT_MODEL,
            "key_set": (os.environ.get("NVIDIA_API_KEY") or "").startswith("nvapi-")}


@app.get("/api/axiom")
async def axiom_defs() -> dict:
    from axiom_files.validator import validate_file
    from axiom_files.parser import get_prompt
    out = {}
    for role in ("worker", "evaluator", "rewriter"):
        try:
            v = validate_file(role)
            out[role] = {"status": v.get("status"), "issues": v.get("issues", []),
                         "prompt": get_prompt(role)}
        except Exception as e:
            out[role] = {"status": "error", "error": str(e), "prompt": ""}
    return out


@app.get("/api/growth")
async def growth() -> dict:
    log_dir = HERE / "logs"
    files = sorted(log_dir.glob("*.jsonl")) if log_dir.exists() else []
    entries = []
    for lf in files:
        for line in lf.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
                if e.get("agent_role") == "worker":
                    entries.append(e)
            except Exception:
                pass
    runs: dict[str, list] = {}
    for e in entries:
        runs.setdefault(e.get("run_id", "?"), []).append(e)
    series = []
    for rid, es in runs.items():
        es = sorted(es, key=lambda x: x.get("iteration", 0))
        series.append({"run_id": rid, "scores": [float(x.get("score", 0)) for x in es]})
    all_scores = [float(e.get("score", 0)) for e in entries]
    worker_axiom = ""
    ap = HERE / "axiom_files" / "worker.axiom"
    if ap.exists():
        worker_axiom = ap.read_text(encoding="utf-8", errors="replace")
    return {
        "total_runs": len(runs), "total_iterations": len(entries),
        "best_score": max(all_scores) if all_scores else 0,
        "avg_score": (sum(all_scores) / len(all_scores)) if all_scores else 0,
        "series": series, "all_scores": all_scores, "worker_axiom": worker_axiom,
    }


@app.get("/", response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse((HERE / "web_ui.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8010, log_level="info")
