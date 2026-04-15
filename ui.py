"""
AXIOM — Phase 1 UI
Run with: streamlit run ui.py
"""
import os
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import streamlit as st

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AXIOM",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
body, .stApp { background-color: #0d0d0d; color: #e0e0e0; }
h1, h2, h3 { color: #7ab648; }
.score-high  { color: #7ab648; font-weight: bold; }
.score-mid   { color: #f0c040; font-weight: bold; }
.score-low   { color: #e05050; font-weight: bold; }
.iteration-box {
    background: #1a1a1a;
    border-left: 3px solid #7ab648;
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
    border-radius: 4px;
}
.output-box {
    background: #111;
    border: 1px solid #333;
    padding: 1rem;
    border-radius: 6px;
    font-size: 0.9rem;
    white-space: pre-wrap;
}
.tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 0.75rem;
    font-weight: bold;
    margin-right: 4px;
}
.tag-worker    { background: #1a3a5c; color: #5ab4f0; }
.tag-evaluator { background: #3a2a10; color: #f0c040; }
.tag-rewriter  { background: #2a1a3a; color: #c084f0; }
.tag-converged { background: #1a3a1a; color: #7ab648; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# ⚡ AXIOM")
st.markdown("**An AI-Native Language for Building Self-Evolving Intelligence** — Phase 1")
st.divider()

# ── Mode tabs ─────────────────────────────────────────────────────────────────
tab_prompt, tab_dsl = st.tabs(["🔁 Prompt Evolution", "📄 AXIOM DSL (Language Test)"])

# ── Sidebar controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙ Run Configuration")

    max_iterations = st.slider("Max Iterations", 1, 15, 5)
    threshold = st.slider("Quality Threshold", 1.0, 10.0, 8.0, 0.1)
    enable_meta = st.toggle("Meta-Evolution", value=True,
                            help="Also evolve the Evaluator and Rewriter prompts")
    temperature = st.slider("Worker Temperature", 0.1, 1.0, 0.7, 0.05)

    st.divider()
    st.markdown("### 🔑 Model")
    model = st.text_input(
        "NVIDIA NIM Model",
        value=os.environ.get("AXIOM_MODEL", "meta/llama-3.3-70b-instruct"),
    )

    st.divider()
    st.markdown("### 💡 Example Tasks")
    examples = [
        "Explain what makes an AI agent capable of improving itself",
        "Write a Python function that validates email addresses without using regex",
        "List three ways self-improving agents drift from their original goal and a safeguard for each",
        "You are given a broken AI agent that keeps optimizing for the wrong goal. Write the exact system prompt that caused the failure, explain precisely why it failed, then write the corrected prompt and prove why it fixes it. Use a specific real-world scenario.",
        "Design a minimal constitutional AI safety layer for a self-rewriting agent",
    ]
    for ex in examples:
        if st.button(ex[:55] + ("…" if len(ex) > 55 else ""), use_container_width=True):
            st.session_state["task_input"] = ex

# ── Tab: Prompt Evolution ─────────────────────────────────────────────────────
with tab_prompt:

 # ── Task input ───────────────────────────────────────────────────────────────
 task = st.text_area(
    "Describe the task for the agent to master",
    value=st.session_state.get("task_input", ""),
    height=120,
    placeholder="e.g. Explain what makes an AI agent capable of improving itself",
    key="task_input",
 )

 col1, col2 = st.columns([1, 5])
 with col1:
    run_btn = st.button("▶  Run AXIOM", type="primary", use_container_width=True)

 # ── Validate API key ─────────────────────────────────────────────────────────
 api_key = os.environ.get("NVIDIA_API_KEY", "")
 if not api_key or api_key == "your_nvidia_api_key_here":
    st.error("NVIDIA_API_KEY not set. Edit `.env` with your key from https://build.nvidia.com")
    st.stop()

 # ── Run ──────────────────────────────────────────────────────────────────────
 if run_btn:
    if not task.strip():
        st.warning("Please enter a task.")
        st.stop()

    # Override model env var for this run
    os.environ["AXIOM_MODEL"] = model
    os.environ["AXIOM_MAX_ITERATIONS"] = str(max_iterations)
    os.environ["AXIOM_QUALITY_THRESHOLD"] = str(threshold)

    st.divider()
    st.markdown("## Evolution Run")

    # ── Rubric ───────────────────────────────────────────────────────────────
    with st.status("Generating scoring rubric…", expanded=False) as rubric_status:
        try:
            from axiom import rubric as rubric_module
            rubric = rubric_module.generate(task)
            rubric_status.update(label=f"✓ Rubric: {rubric.get('task_summary', '')[:80]}", state="complete")
        except Exception as e:
            rubric_status.update(label=f"Rubric failed: {e}", state="error")
            st.error(str(e))
            st.stop()

    with st.expander("View Scoring Rubric"):
        for d in rubric.get("dimensions", []):
            st.markdown(f"**{d['name']}** ({d['weight']:.0%}) — {d['description']}")
        st.caption(rubric.get("scoring_guide", ""))

    # ── Manual evolution loop (streaming into UI) ────────────────────────────
    from axiom.agents.worker import WorkerAgent
    from axiom.agents.evaluator import EvaluatorAgent
    from axiom.agents.rewriter import RewriterAgent
    from axiom.evolution import EvolutionResult, IterationResult, LOGS_DIR
    from axiom import store as prompt_store
    import uuid, json
    from datetime import datetime, timezone

    worker = WorkerAgent(task)
    # Apply UI temperature override
    _orig_execute = worker.execute
    def _execute_with_temp(t):
        from axiom import client as nim
        return nim.chat(worker.system_prompt, f"Task:\n{t}", temperature=temperature)
    worker.execute = _execute_with_temp

    evaluator = EvaluatorAgent(task)
    rewriter = RewriterAgent(task)

    run_id = uuid.uuid4().hex[:8]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{run_id}.jsonl"

    result = EvolutionResult(task_description=task, run_id=run_id)

    progress = st.progress(0, text="Starting…")
    iteration_container = st.container()
    converged = False

    for i in range(max_iterations):
        frac = i / max_iterations
        progress.progress(frac, text=f"Iteration {i+1}/{max_iterations}")

        with iteration_container:
            with st.status(f"Iteration {i+1} — Worker executing…", expanded=True) as iter_status:

                # Worker
                st.markdown('<span class="tag tag-worker">WORKER</span>', unsafe_allow_html=True)
                worker_output = worker.execute(task)
                st.markdown(f'<div class="output-box">{worker_output}</div>', unsafe_allow_html=True)

                # Evaluator
                st.markdown('<span class="tag tag-evaluator">EVALUATOR</span>', unsafe_allow_html=True)
                try:
                    evaluation = evaluator.score(task=task, output=worker_output, rubric=rubric)
                except Exception as e:
                    st.warning(f"Evaluator error: {e}")
                    iter_status.update(label=f"Iteration {i+1} — evaluator error", state="error")
                    continue

                score = float(evaluation.get("score", 0.0))
                reasoning = evaluation.get("reasoning", "")
                improvements = evaluation.get("improvements", [])

                color_cls = "score-high" if score >= threshold else ("score-mid" if score >= 5 else "score-low")
                st.markdown(
                    f'Score: <span class="{color_cls}">{score:.1f}/10</span>',
                    unsafe_allow_html=True,
                )
                st.caption(reasoning)

                if improvements:
                    with st.expander("Improvements identified"):
                        for imp in improvements:
                            st.markdown(f"- {imp}")

                # Dimension breakdown
                dim_scores = evaluation.get("dimension_scores", {})
                if dim_scores:
                    cols = st.columns(len(dim_scores))
                    for col, (dim, sc) in zip(cols, dim_scores.items()):
                        col.metric(dim, f"{sc:.1f}")

                # Track best
                iter_result = IterationResult(
                    iteration=i,
                    worker_prompt=worker.system_prompt,
                    worker_output=worker_output,
                    score=score,
                    reasoning=reasoning,
                    improvements=improvements,
                    dimension_scores=dim_scores,
                )
                result.iterations.append(iter_result)
                if score > result.best_score:
                    result.best_score = score
                    result.best_iteration = len(result.iterations) - 1

                prompt_store.save_iteration(task, "worker", worker.system_prompt, score)

                # Log
                with log_file.open("a") as lf:
                    lf.write(json.dumps({
                        "run_id": run_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "iteration": i,
                        "agent_role": "worker",
                        "score": score,
                        "output": worker_output,
                        "evaluation": evaluation,
                    }) + "\n")

                if score >= threshold:
                    converged = True
                    iter_status.update(
                        label=f"✓ Iteration {i+1} — Converged  {score:.1f}/10",
                        state="complete",
                        expanded=False,
                    )
                    break

                # Rewriter
                if i < max_iterations - 1:
                    st.markdown('<span class="tag tag-rewriter">REWRITER</span>', unsafe_allow_html=True)
                    new_prompt = rewriter.rewrite(
                        target_role="worker",
                        current_prompt=worker.system_prompt,
                        evaluation=evaluation,
                    )
                    worker.system_prompt = new_prompt
                    with st.expander("New Worker Prompt"):
                        st.code(new_prompt, language=None)

                    # Write version bump to worker.axiom
                    try:
                        from axiom_files.parser import load_axiom, save_axiom
                        _axiom = load_axiom("worker")
                        _ver = float(_axiom.get("version", "1.0")) + 0.1
                        _axiom["version"] = f"{_ver:.1f}"
                        _lines = new_prompt.strip().split("\n")
                        _new_c = [l.strip().lstrip("-").strip() for l in _lines
                                  if l.strip().startswith("-") or "constraint" in l.lower()]
                        if _new_c:
                            _axiom["constraints"] = _new_c
                        save_axiom("worker", _axiom)
                        st.caption(f"worker.axiom → v{_ver:.1f}")
                    except Exception as _e:
                        st.caption(f"axiom write error: {_e}")

                iter_status.update(
                    label=f"Iteration {i+1} — Score {score:.1f}/10",
                    state="complete" if score >= threshold else "running",
                    expanded=False,
                )

        if converged:
            break

    progress.progress(1.0, text="Done")

    # ── Summary ──────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("## Results")

    best = result.best
    badge = '<span class="tag tag-converged">CONVERGED</span>' if converged else '<span class="tag tag-rewriter">MAX ITER</span>'
    st.markdown(
        f'{badge} Best score: <span class="score-high">{result.best_score:.1f}/10</span> '
        f'(iteration {result.best_iteration + 1})',
        unsafe_allow_html=True,
    )

    st.markdown("### Best Output")
    st.markdown(f'<div class="output-box">{best.worker_output}</div>', unsafe_allow_html=True)

    # Score chart
    if len(result.iterations) > 1:
        import json as _json
        scores = [it.score for it in result.iterations]
        st.markdown("### Score Progression")
        st.line_chart({"Score": scores})

    # Meta-evolution
    if enable_meta and not converged:
        st.divider()
        with st.status("Running meta-evolution…", expanded=True) as meta_status:
            try:
                from axiom import meta_evolution
                meta_evolution.run_if_needed(result, rubric)
                meta_status.update(label="✓ Meta-evolution complete", state="complete")
                st.success("Evaluator and Rewriter prompts updated for future runs.")
            except Exception as e:
                meta_status.update(label=f"Meta-evolution error: {e}", state="error")

    st.caption(f"Run ID: `{run_id}` · Log: `logs/{run_id}.jsonl` · Prompts saved to `prompts/`")


# ── Tab: AXIOM DSL ────────────────────────────────────────────────────────────
with tab_dsl:
    st.markdown("### AXIOM DSL v0 — Language-Driven Evolution")
    st.markdown(
        "Agents load their behaviour from `.axiom` files. "
        "The Rewriter mutates `worker.axiom` on disk after each iteration — "
        "this is the language rewriting itself."
    )

    # Show current .axiom definitions
    with st.expander("Current .axiom definitions", expanded=False):
        for role in ("worker", "evaluator", "rewriter"):
            try:
                from axiom_files.parser import get_prompt
                st.markdown(f"**{role}.axiom**")
                st.code(get_prompt(role), language=None)
            except Exception as e:
                st.warning(f"{role}.axiom: {e}")

    dsl_task = st.text_area(
        "Task for the Worker agent",
        height=100,
        placeholder="e.g. Explain what AXIOM is and why it matters",
        key="dsl_task",
    )

    col_a, col_b = st.columns([1, 5])
    with col_a:
        dsl_run = st.button("▶  Run DSL Loop", type="primary", use_container_width=True)

    if dsl_run:
        if not dsl_task.strip():
            st.warning("Please enter a task.")
            st.stop()

        os.environ["AXIOM_MODEL"] = model
        os.environ["AXIOM_MAX_ITERATIONS"] = str(max_iterations)
        os.environ["AXIOM_QUALITY_THRESHOLD"] = str(threshold)

        import json, uuid
        from axiom_files.parser import load_axiom, save_axiom, to_system_prompt, get_prompt
        from axiom import client as nim
        from axiom import store as dsl_store
        from axiom import rubric as dsl_rubric_mod
        from axiom.rubric import format_for_prompt

        # Rubric
        with st.status("Generating rubric…", expanded=False) as rs:
            try:
                dsl_rubric = dsl_rubric_mod.generate(dsl_task)
                rs.update(label=f"✓ {dsl_rubric.get('task_summary','')[:80]}", state="complete")
            except Exception as e:
                rs.update(label=f"Rubric error: {e}", state="error")
                st.stop()

        rubric_txt = format_for_prompt(dsl_rubric)
        worker_p  = get_prompt("worker")
        eval_p    = get_prompt("evaluator")
        rewrite_p = get_prompt("rewriter")

        best_score_dsl = 0.0
        best_out_dsl   = ""
        scores_dsl     = []
        converged_dsl  = False
        dsl_run_id     = uuid.uuid4().hex[:8]

        prog = st.progress(0, text="Starting DSL loop…")

        for i in range(max_iterations):
            prog.progress(i / max_iterations, text=f"Iteration {i+1}/{max_iterations}")

            with st.status(f"Iteration {i+1} — Worker", expanded=True) as s:

                # Worker
                st.markdown('<span class="tag tag-worker">WORKER</span>', unsafe_allow_html=True)
                out = nim.chat(worker_p, f"Task:\n{dsl_task}", temperature=temperature)
                st.markdown(f'<div class="output-box">{out}</div>', unsafe_allow_html=True)

                # Evaluator
                st.markdown('<span class="tag tag-evaluator">EVALUATOR</span>', unsafe_allow_html=True)
                eval_msg = f"""RUBRIC:\n{rubric_txt}\n\nTASK:\n{dsl_task}\n\nWORKER OUTPUT:\n{out}\n\nReturn JSON: {{"score": <0-10>, "reasoning": "<str>", "failures": ["<str>"], "suggested_changes": ["<str>"]}}"""
                try:
                    ev = nim.chat_json(eval_p, eval_msg, temperature=0.2)
                except ValueError as e:
                    st.warning(f"Evaluator parse error: {e}")
                    s.update(label=f"Iteration {i+1} — parse error", state="error")
                    continue

                sc = float(ev.get("score", 0.0))
                scores_dsl.append(sc)
                color_cls = "score-high" if sc >= threshold else ("score-mid" if sc >= 5 else "score-low")
                st.markdown(f'Score: <span class="{color_cls}">{sc:.1f}/10</span>', unsafe_allow_html=True)
                st.caption(ev.get("reasoning", ""))

                failures  = ev.get("failures", [])
                suggested = ev.get("suggested_changes", [])
                if failures:
                    with st.expander("Failures"):
                        for f in failures: st.markdown(f"- {f}")

                dsl_store.save_iteration(dsl_task, "worker", worker_p, sc)

                if sc > best_score_dsl:
                    best_score_dsl = sc
                    best_out_dsl   = out

                if sc >= threshold:
                    converged_dsl = True
                    s.update(label=f"✓ Converged {sc:.1f}/10", state="complete", expanded=False)
                    break

                # Rewriter → mutates worker.axiom
                if i < max_iterations - 1:
                    st.markdown('<span class="tag tag-rewriter">REWRITER → worker.axiom</span>', unsafe_allow_html=True)
                    cur_axiom = load_axiom("worker")
                    rw_msg = f"""Current worker.axiom (parsed):\n{json.dumps(cur_axiom, indent=2)}\n\nFailures:\n{chr(10).join(f'- {f}' for f in failures)}\n\nSuggested changes:\n{chr(10).join(f'- {s_}' for s_ in suggested)}\n\nReturn updated axiom dict as JSON. Add mutations key: [{{"field":...,"cut":...,"added":...,"why":...}}]"""
                    try:
                        new_raw = nim.chat_json(rewrite_p, rw_msg, temperature=0.4)
                    except ValueError as e:
                        st.warning(f"Rewriter parse error: {e}")
                        s.update(label=f"Iteration {i+1} — rewriter error", state="error")
                        continue

                    mutations = new_raw.pop("mutations", [])
                    if mutations:
                        with st.expander(f"Mutations ({len(mutations)})"):
                            for m in mutations:
                                st.markdown(
                                    f"**{m.get('field','?')}** — "
                                    f"cut: `{m.get('cut','')}` → "
                                    f"added: `{m.get('added','')}` — "
                                    f"{m.get('why','')}"
                                )

                    valid_keys = set(cur_axiom.keys())
                    for k, v in new_raw.items():
                        if k in valid_keys:
                            cur_axiom[k] = v

                    _dsl_ver = float(cur_axiom.get("version", "1.0")) + 0.1
                    cur_axiom["version"] = f"{_dsl_ver:.1f}"

                    save_axiom("worker", cur_axiom)
                    worker_p = to_system_prompt(cur_axiom)

                    with st.expander("Updated worker.axiom prompt"):
                        st.code(worker_p, language=None)
                    st.caption(f"worker.axiom → v{_dsl_ver:.1f}")

                s.update(label=f"Iteration {i+1} — Score {sc:.1f}/10", state="complete", expanded=False)

            if converged_dsl:
                break

        prog.progress(1.0, text="Done")

        st.divider()
        st.markdown("### Best Output")
        st.markdown(f'<div class="output-box">{best_out_dsl}</div>', unsafe_allow_html=True)

        if len(scores_dsl) > 1:
            st.markdown("### Score Progression")
            st.line_chart({"Score": scores_dsl})

        badge_dsl = '<span class="tag tag-converged">CONVERGED</span>' if converged_dsl else '<span class="tag tag-rewriter">MAX ITER</span>'
        st.markdown(f'{badge_dsl} Best score: <span class="score-high">{best_score_dsl:.1f}/10</span>', unsafe_allow_html=True)
        st.caption(f"Run ID: `{dsl_run_id}` · worker.axiom updated in `axiom_files/`")

