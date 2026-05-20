"""
AXIOM — Phase 1 UI
Run with: streamlit run ui.py

Author:  Antonio Roberts
Project: AXIOM — An AI-Native Language for Self-Evolving Intelligence
License: Copyright (c) 2026 Antonio Roberts. All rights reserved.
"""
import os
import time
import warnings
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Suppress Streamlit's internal use_container_width self-deprecation warning
# (Streamlit 1.56 triggers this on its own internal code, not our code)
warnings.filterwarnings(
    "ignore",
    message=".*use_container_width.*",
    category=DeprecationWarning,
)

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
(tab_prompt, tab_dsl, tab_growth,
 tab_exo, tab_audio, tab_dev) = st.tabs([
    "🔁 Prompt Evolution",
    "📄 AXIOM DSL (Language Test)",
    "📈 Growth Dashboard",
    "🦾 Exoskeleton",
    "🎙️ Audio",
    "🛠️ Dev Agent",
])

# ── Sidebar controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙ Run Configuration")

    max_iterations = st.slider("Max Iterations", 1, 15, 5)
    threshold = st.slider("Quality Threshold", 1.0, 10.0, 8.0, 0.1)
    force_rewrite = st.toggle("Force Rewrite Every Iteration", value=False,
                              help="Rewriter runs after every iteration regardless of score. Use this to force evolution even when scores are high.")
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
        if st.button(ex[:55] + ("…" if len(ex) > 55 else ""), width='stretch'):
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
    run_btn = st.button("▶  Run AXIOM", type="primary", width='stretch')

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
            from axiom_constitutional import rubric as rubric_module
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
    from axiom_constitutional.agents.worker import WorkerAgent
    from axiom_constitutional.agents.evaluator import EvaluatorAgent
    from axiom_constitutional.agents.rewriter import RewriterAgent
    from axiom_constitutional.evolution import EvolutionResult, IterationResult, LOGS_DIR
    from axiom_constitutional import store as prompt_store
    from axiom_files.parser import get_prompt_with_overlays, detect_overlays
    import uuid, json
    from datetime import datetime, timezone

    detected = detect_overlays(task)
    if detected:
        st.caption(f"Overlays detected: {', '.join(detected)}")

    worker = WorkerAgent(task)
    if detected:
        worker.system_prompt = get_prompt_with_overlays("worker", detected)

    # Apply UI temperature override
    def _execute_with_temp(t):
        from axiom_constitutional import client as nim
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
                if i < max_iterations - 1 and (force_rewrite or score < threshold):
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
                        _skip = [
                            "understand task", "identify missing", "produce answer",
                            "relevance", "accuracy", "completeness", "tone", "wording",
                            "empathy", "clarity:", "helpfulness:", "check answer",
                        ]
                        _new_c = [
                            l.strip().lstrip("-").strip()
                            for l in _lines
                            if "constraint" in l.lower()
                            and not any(skip in l.lower() for skip in _skip)
                        ]
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
                from axiom_constitutional import meta_evolution
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
        from axiom_files.validator import validate_file
        from axiom_files.parser import get_prompt
        status_icon = {"valid": "✅", "warning": "⚠️", "invalid": "❌"}
        for role in ("worker", "evaluator", "rewriter"):
            try:
                result = validate_file(role)
                icon = status_icon.get(result["status"], "?")
                st.markdown(f"**{icon} {role}.axiom** — `{result['status'].upper()}`")
                if result["issues"]:
                    with st.expander(f"{len(result['issues'])} validator issue(s)"):
                        for issue in result["issues"]:
                            level_color = "red" if issue["level"] == "error" else "orange"
                            st.markdown(
                                f":{level_color}[{issue['level'].upper()}] "
                                f"`{issue['phase']}` · **{issue['field']}** — {issue['message']}"
                            )
                        st.divider()
                        for s in result["suggestions"]:
                            st.caption(f"→ {s}")
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
        dsl_run = st.button("▶  Run DSL Loop", type="primary", width='stretch')

    if dsl_run:
        if not dsl_task.strip():
            st.warning("Please enter a task.")
            st.stop()

        os.environ["AXIOM_MODEL"] = model
        os.environ["AXIOM_MAX_ITERATIONS"] = str(max_iterations)
        os.environ["AXIOM_QUALITY_THRESHOLD"] = str(threshold)

        import json, uuid
        from axiom_files.parser import load_axiom, save_axiom, to_system_prompt, get_prompt
        from axiom_constitutional import client as nim
        from axiom_constitutional import store as dsl_store
        from axiom_constitutional import rubric as dsl_rubric_mod
        from axiom_constitutional.rubric import format_for_prompt

        # Rubric
        with st.status("Generating rubric…", expanded=False) as rs:
            try:
                dsl_rubric = dsl_rubric_mod.generate(dsl_task)
                rs.update(label=f"✓ {dsl_rubric.get('task_summary','')[:80]}", state="complete")
            except Exception as e:
                rs.update(label=f"Rubric error: {e}", state="error")
                st.stop()

        rubric_txt = format_for_prompt(dsl_rubric)
        from axiom_files.parser import get_prompt_with_overlays, detect_overlays
        worker_p = get_prompt_with_overlays("worker", detect_overlays(dsl_task))
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
                if i < max_iterations - 1 and (force_rewrite or sc < threshold):
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

        # ── Tab: Growth Dashboard ─────────────────────────────────────────────────────
with tab_growth:
    import json
    import glob
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    st.markdown("### AXIOM Growth Dashboard")
    st.markdown("Evolution history across all runs — how the language and agents improve over time.")

    # ── Load all log files ────────────────────────────────────────────────────
    log_dir = Path("logs")
    log_files = sorted(log_dir.glob("*.jsonl")) if log_dir.exists() else []

    if not log_files:
        st.info("No evolution runs yet. Run a task first.")
        st.stop()

    # Parse all entries
    all_entries = []
    for lf in log_files:
        with open(lf) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    entry["log_file"] = lf.stem
                    all_entries.append(entry)
                except:
                    pass

    df = pd.DataFrame(all_entries)
    df = df[df["agent_role"] == "worker"].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    df["run_label"] = df["run_id"] + " i" + df["iteration"].astype(str)

    # ── Top metrics ───────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Runs", df["run_id"].nunique())
    col2.metric("Total Iterations", len(df))
    col3.metric("Best Score Ever", f"{df['score'].max():.1f}/10")
    col4.metric("Avg Score", f"{df['score'].mean():.2f}/10")

    st.divider()

    # ── Score trajectory across all runs ─────────────────────────────────────
    st.markdown("#### Score Trajectory — All Runs")

    fig = go.Figure()
    for run_id, group in df.groupby("run_id"):
        group = group.sort_values("iteration")
        fig.add_trace(go.Scatter(
            x=list(range(len(group))),
            y=group["score"].tolist(),
            mode="lines+markers",
            name=run_id,
            line=dict(width=2),
            marker=dict(size=8),
            hovertemplate=f"Run: {run_id}<br>Iter: %{{x}}<br>Score: %{{y:.1f}}<extra></extra>"
        ))

    fig.add_hline(
        y=threshold, line_dash="dash",
        line_color="#f0c040",
        annotation_text=f"Threshold ({threshold})",
        annotation_position="right"
    )
    fig.update_layout(
        plot_bgcolor="#0d0d0d",
        paper_bgcolor="#0d0d0d",
        font_color="#e0e0e0",
        xaxis=dict(title="Iteration", gridcolor="#222"),
        yaxis=dict(title="Score", gridcolor="#222", range=[0, 10]),
        legend=dict(bgcolor="#1a1a1a", bordercolor="#333"),
        height=400,
    )
    st.plotly_chart(fig, width='stretch')

    # ── Score distribution ────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### Score Distribution")
        fig2 = go.Figure()
        fig2.add_trace(go.Histogram(
            x=df["score"].tolist(),
            nbinsx=20,
            marker_color="#7ab648",
            opacity=0.8,
            name="Scores"
        ))
        fig2.update_layout(
            plot_bgcolor="#0d0d0d",
            paper_bgcolor="#0d0d0d",
            font_color="#e0e0e0",
            xaxis=dict(title="Score", gridcolor="#222"),
            yaxis=dict(title="Count", gridcolor="#222"),
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig2, width='stretch')

    with col_b:
        st.markdown("#### Avg Score Per Run")
        run_avgs = df.groupby("run_id")["score"].mean().reset_index()
        run_avgs.columns = ["run_id", "avg_score"]
        run_avgs = run_avgs.sort_values("avg_score")

        fig3 = go.Figure()
        fig3.add_trace(go.Bar(
            x=run_avgs["avg_score"].tolist(),
            y=run_avgs["run_id"].tolist(),
            orientation="h",
            marker_color="#5ab4f0",
        ))
        fig3.update_layout(
            plot_bgcolor="#0d0d0d",
            paper_bgcolor="#0d0d0d",
            font_color="#e0e0e0",
            xaxis=dict(title="Avg Score", gridcolor="#222", range=[0, 10]),
            yaxis=dict(gridcolor="#222"),
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig3, width='stretch')

    # ── Score improvement within runs ─────────────────────────────────────────
    st.markdown("#### Score Improvement Within Runs")
    improvements = []
    for run_id, group in df.groupby("run_id"):
        group = group.sort_values("iteration")
        scores = group["score"].tolist()
        if len(scores) > 1:
            improvements.append({
                "run_id": run_id,
                "start_score": scores[0],
                "end_score": scores[-1],
                "delta": scores[-1] - scores[0],
                "iterations": len(scores)
            })

    if improvements:
        imp_df = pd.DataFrame(improvements).sort_values("delta", ascending=False)
        fig4 = go.Figure()
        fig4.add_trace(go.Bar(
            x=imp_df["run_id"].tolist(),
            y=imp_df["delta"].tolist(),
            marker_color=[
                "#7ab648" if d >= 0 else "#e05050"
                for d in imp_df["delta"].tolist()
            ],
            name="Score Delta"
        ))
        fig4.update_layout(
            plot_bgcolor="#0d0d0d",
            paper_bgcolor="#0d0d0d",
            font_color="#e0e0e0",
            xaxis=dict(title="Run", gridcolor="#222"),
            yaxis=dict(title="Score Change", gridcolor="#222"),
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig4, width='stretch')

    # ── worker.axiom version history ──────────────────────────────────────────
    st.markdown("#### worker.axiom Version History")
    axiom_path = Path("axiom_files/worker.axiom")
    if axiom_path.exists():
        with open(axiom_path) as f:
            content = f.read()
        st.code(content, language=None)
        lines = content.split("\n")
        constraints = [l for l in lines if l.startswith("CONSTRAINT")]
        rules = [l for l in lines if l.startswith("-")]
        col_x, col_y, col_z = st.columns(3)
        col_x.metric("Current Version",
            next((l.split()[-1] for l in lines if l.startswith("VERSION")), "?"))
        col_y.metric("Constraints", len(constraints))
        col_z.metric("Rules", len(rules))

    # ── Raw run data ──────────────────────────────────────────────────────────
    with st.expander("Raw Run Data"):
        st.dataframe(
            df[["run_id", "iteration", "score", "timestamp"]].sort_values("timestamp"),
            width='stretch'
        )


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL AGENT PLAYGROUND — Exoskeleton / Audio / Dev Agent
# ═════════════════════════════════════════════════════════════════════════════
# Why these tabs exist: a safer alternative to the CLIs. Every action that
# touches a signing key, writes to a ledger, or fires a real backend is
# gated behind a confirmation, a preflight check, or an explicit toggle —
# so mistakes that would silently corrupt a training corpus or pollute the
# default ledger are hard to make.
# ─────────────────────────────────────────────────────────────────────────────

import json as _pg_json
import secrets as _pg_secrets
import subprocess as _pg_subprocess
import tempfile as _pg_tempfile
from pathlib import Path as _PgPath


# ── Master-key bootstrap (shared across all playground tabs) ─────────────────


def _pg_master_key_state() -> tuple[bool, str]:
    """Return (set?, status-line) for the AXIOM_MASTER_KEY env var.

    A 32-byte hex string is required by every signing-aware module
    (event-token, AXM container, exoskeleton ledger, dev-cycle
    recorder, audio report). Missing key = every signed-output call
    will crash at first use.
    """
    raw = os.environ.get("AXIOM_MASTER_KEY", "")
    if not raw:
        return False, "✗ AXIOM_MASTER_KEY not set — generate one below."
    if len(raw) < 64:
        return False, (
            f"⚠ AXIOM_MASTER_KEY present but only {len(raw)} chars "
            f"(want 64 hex). Modules may reject it."
        )
    return True, f"✓ AXIOM_MASTER_KEY set ({len(raw)} chars)."


def _pg_key_panel(tab_name: str) -> bool:
    """Render the key status + ephemeral-key button. Returns True iff
    a usable key is in place after rendering."""
    ok, line = _pg_master_key_state()
    cols = st.columns([3, 1])
    with cols[0]:
        (st.success if ok else st.warning)(line)
    with cols[1]:
        if st.button(
            "🔑 Generate ephemeral key",
            key=f"pg-genkey-{tab_name}",
            help=(
                "Sets AXIOM_MASTER_KEY in THIS process only. "
                "Records signed under an ephemeral key cannot be "
                "verified by anyone who doesn't have it. Fine for "
                "local playground experiments — bad for shared "
                "ledgers."
            ),
            width="stretch",
        ):
            os.environ["AXIOM_MASTER_KEY"] = _pg_secrets.token_hex(32)
            st.rerun()
    return ok


def _pg_signed_badge(verified: bool) -> str:
    if verified:
        return (
            "<span class='tag tag-converged'>SIGNED · VERIFIED</span>"
        )
    return "<span class='tag tag-rewriter'>SIGNATURE FAILED</span>"


# ── Module-availability cache ─────────────────────────────────────────────────


@st.cache_resource(show_spinner=False)
def _pg_check_imports() -> dict:
    """One-shot import probe so each tab can show a green/red light
    without retrying on every rerender."""
    status: dict[str, str] = {}
    for label, modname in (
        ("axiom_exoskeleton",        "axiom_exoskeleton"),
        ("axiom_exoskeleton_ledger", "axiom_exoskeleton_ledger"),
        ("examples.exoskeleton_pack", "examples.exoskeleton_pack"),
        ("axiom_audio",              "axiom_audio"),
        ("axiom_dev_loop",           "axiom_dev_loop"),
    ):
        try:
            __import__(modname)
            status[label] = "ok"
        except Exception as e:  # noqa: BLE001
            status[label] = f"err: {type(e).__name__}: {e}"
    return status


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Exoskeleton (9 founder workflows)
# ─────────────────────────────────────────────────────────────────────────────


with tab_exo:
    st.markdown("### 🦾 Exoskeleton playground")
    st.markdown(
        "Pick one of the 9 founder-workflow delegates, paste an "
        "input, run it against the configured backend. Output is a "
        "**signed EventToken** with a verify badge."
    )

    key_ok = _pg_key_panel("exo")
    imports = _pg_check_imports()
    exo_import_ok = (
        imports["axiom_exoskeleton"] == "ok"
        and imports["examples.exoskeleton_pack"] == "ok"
    )
    if not exo_import_ok:
        st.error(
            "Imports failed — cannot run the exoskeleton:\n"
            f"- axiom_exoskeleton: {imports['axiom_exoskeleton']}\n"
            f"- examples.exoskeleton_pack: "
            f"{imports['examples.exoskeleton_pack']}"
        )

    USE_CASE_HINTS: dict = {
        "investor_research":        "AI governance thesis; round size $3-8M",
        "enterprise_targeting":     "AI Governance Lead role at fintechs >1000 staff",
        "outreach_personalization": "Buyer: CISO at 1500-person fintech. Signal: posted job for AI Governance Lead three days ago.",
        "demo_scripts":             "Feature: signed event token with QRF reasoning paths",
        "sales_objection_handling": "Buyer says: 'Not in this year's budget.'",
        "competitive_analysis":     "Lakera",
        "grant_application":        "YC, AI safety + audit infra",
        "patent_counsel_packet":    "Signed multimodal event-token with selectively-activated agent layers",
        "customer_discovery":       "(paste call notes here)",
    }

    @st.cache_resource(show_spinner=False)
    def _pg_exo_agent() -> object:
        """One ExoskeletonAgent per process — building the pack does a
        tempdir + delegate signing pass, so we cache it."""
        from axiom_exoskeleton import ExoskeletonAgent
        return ExoskeletonAgent.from_default_pack()

    if key_ok and exo_import_ok:
        agent = _pg_exo_agent()
        use_cases = list(agent.use_cases())
        col_l, col_r = st.columns([1, 2])
        with col_l:
            use_case = st.selectbox(
                "Delegate",
                use_cases,
                index=use_cases.index("outreach_personalization")
                    if "outreach_personalization" in use_cases else 0,
                key="pg-exo-usecase",
            )
            backend_label = st.selectbox(
                "Backend",
                ["(default / env)", "local", "nim"],
                key="pg-exo-backend",
                help=(
                    "Defers to AXIOM_BACKEND env var when '(default / "
                    "env)'. Forces the backend otherwise."
                ),
            )
            write_ledger = st.checkbox(
                "Append to ledger",
                value=False,
                key="pg-exo-ledger",
                help=(
                    "Off by default — keeps the playground from "
                    "polluting ~/.axiom/exoskeleton-ledger.jsonl. "
                    "Flip on to record genuine training-worthy runs."
                ),
            )
        with col_r:
            try:
                desc = agent.describe(use_case)
                st.caption(
                    f"intent={','.join(desc['intent_classes'])}  ·  "
                    f"prompt_budget={desc['prompt_budget']}  ·  "
                    f"output_budget={desc['output_budget']}"
                )
            except Exception:
                pass
            default_hint = USE_CASE_HINTS.get(use_case, "")
            exo_input = st.text_area(
                "Input",
                value=default_hint,
                height=140,
                key=f"pg-exo-input-{use_case}",
            )

        if st.button(
            "▶  Run delegate",
            type="primary",
            key="pg-exo-run",
            width="stretch",
            disabled=not exo_input.strip(),
        ):
            # Apply backend override per click (don't persist).
            previous_backend = os.environ.get("AXIOM_BACKEND")
            if backend_label != "(default / env)":
                os.environ["AXIOM_BACKEND"] = backend_label
            try:
                ledger = None
                if write_ledger:
                    from axiom_exoskeleton_ledger import (
                        LedgerWriter, default_ledger_path,
                    )
                    ledger = LedgerWriter(default_ledger_path())
                # ExoskeletonAgent is cached without a ledger; build a
                # one-shot copy that holds the ledger if requested.
                if ledger is not None:
                    from axiom_exoskeleton import ExoskeletonAgent
                    runner = ExoskeletonAgent(
                        agent._container, backend=agent._backend,
                        ledger=ledger,
                    )
                else:
                    runner = agent
                with st.spinner(
                    f"Running {use_case} …",
                ):
                    token = runner.invoke(use_case, exo_input)
            except Exception as e:  # noqa: BLE001
                st.error(f"Run failed: {type(e).__name__}: {e}")
                token = None
            finally:
                if backend_label != "(default / env)":
                    if previous_backend is None:
                        os.environ.pop("AXIOM_BACKEND", None)
                    else:
                        os.environ["AXIOM_BACKEND"] = previous_backend

            if token is not None:
                payload = token.text.payload if token.text else {}
                verified = bool(token.verify())
                st.markdown(_pg_signed_badge(verified),
                            unsafe_allow_html=True)
                st.markdown(
                    f"<div class='iteration-box'>"
                    f"<strong>{payload.get('delegate', use_case)}</strong>  "
                    f"backend={payload.get('backend', '?')}  ·  "
                    f"model={payload.get('model', '?')}  ·  "
                    f"in/out tokens={payload.get('input_tokens', '?')}/"
                    f"{payload.get('output_tokens', '?')}  ·  "
                    f"latency={payload.get('latency_ms', '?')}ms<br>"
                    f"signed_event_id={token.id}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if payload.get("error"):
                    st.error(f"Delegate error: {payload['error']}")
                else:
                    st.markdown("**Output**")
                    st.markdown(
                        f"<div class='output-box'>"
                        f"{(payload.get('output', '') or '').strip()}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with st.expander("Raw EventToken JSON"):
                    st.code(token.to_json(indent=2), language="json")
                if write_ledger:
                    from axiom_exoskeleton_ledger import default_ledger_path
                    st.caption(
                        f"appended to {default_ledger_path()}"
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Audio (Ambient classifier)
# ─────────────────────────────────────────────────────────────────────────────


with tab_audio:
    st.markdown("### 🎙️ Audio playground")
    st.markdown(
        "Upload a short mono WAV. The `AmbientAudioAgent` returns a "
        "**signed AudioReport** with six fields the 3D-event-token "
        "Audio layer adopts: impact_profile, material_signature, "
        "decay_pattern, depth, width, rhythm."
    )

    key_ok = _pg_key_panel("audio")
    imports = _pg_check_imports()
    audio_ok = imports["axiom_audio"] == "ok"
    if not audio_ok:
        st.error(f"axiom_audio import failed: {imports['axiom_audio']}")

    if key_ok and audio_ok:
        from axiom_audio import AmbientAudioAgent
        from axiom_audio.features import load_wav

        wav_upload = st.file_uploader(
            "WAV file (mono PCM, 8-48 kHz, ≤30s recommended)",
            type=["wav"],
            key="pg-audio-upload",
            help="Saved to a tempdir; not persisted across reruns.",
        )

        if wav_upload is not None and st.button(
            "▶  Classify clip",
            type="primary",
            key="pg-audio-run",
            width="stretch",
        ):
            tmp = _PgPath(_pg_tempfile.mkdtemp(prefix="pg_audio_"))
            wav_path = tmp / wav_upload.name
            wav_path.write_bytes(wav_upload.getvalue())
            try:
                samples, sr = load_wav(str(wav_path))
                with st.spinner("Classifying …"):
                    report = AmbientAudioAgent().classify(samples, sr)
            except Exception as e:  # noqa: BLE001
                st.error(f"Classification failed: "
                         f"{type(e).__name__}: {e}")
                report = None

            if report is not None:
                verified = bool(report.verify())
                st.markdown(_pg_signed_badge(verified),
                            unsafe_allow_html=True)
                p = dict(report.payload or {})
                debug = p.pop("debug", {}) if isinstance(p, dict) else {}
                st.caption(f"confidence={report.confidence:.2f}")
                cols = st.columns(3)
                cols[0].metric("impact_profile",
                               str(p.get("impact_profile", "?")))
                cols[1].metric("material_signature",
                               str(p.get("material_signature", "?")))
                cols[2].metric("decay_pattern",
                               str(p.get("decay_pattern", "?")))
                cols = st.columns(3)
                cols[0].metric("depth", f"{p.get('depth', 0):.3f}"
                               if isinstance(p.get("depth"), (int, float))
                               else "?")
                cols[1].metric("width", f"{p.get('width', 0):.3f}"
                               if isinstance(p.get("width"), (int, float))
                               else "?")
                cols[2].metric("rhythm", str(p.get("rhythm", "?")))
                if debug:
                    with st.expander("Trace / telemetry"):
                        st.json(debug)
                with st.expander("Raw AudioReport JSON"):
                    st.code(report.to_json(indent=2), language="json")


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Dev Agent (signed dev cycle records)
# ─────────────────────────────────────────────────────────────────────────────


def _pg_git_head_sha() -> str:
    try:
        out = _pg_subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            check=False, capture_output=True, text=True,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def _pg_git_changed_files() -> list:
    try:
        out = _pg_subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            check=False, capture_output=True, text=True,
        )
        if out.returncode != 0:
            return []
        return [l for l in out.stdout.splitlines() if l.strip()]
    except FileNotFoundError:
        return []


with tab_dev:
    st.markdown("### 🛠️ Dev Agent playground")
    st.markdown(
        "Record a **signed dev-cycle record** for what you just shipped. "
        "Each record fans out to three JSONL sinks "
        "(`axiom_dev_training.jsonl`, `dev_agent_improvements.jsonl`, "
        "`axiom_crl_reward_log.jsonl`) so the training pipeline picks "
        "it up automatically. Verify badge proves the HMAC checks out."
    )

    key_ok = _pg_key_panel("dev")
    imports = _pg_check_imports()
    dev_ok = imports["axiom_dev_loop"] == "ok"
    if not dev_ok:
        st.error(f"axiom_dev_loop import failed: "
                 f"{imports['axiom_dev_loop']}")

    if key_ok and dev_ok:
        # Smart defaults pulled from the live git repo.
        default_sha = _pg_git_head_sha()
        default_files = _pg_git_changed_files()

        col_a, col_b = st.columns(2)
        with col_a:
            dev_sha = st.text_input(
                "commit_sha",
                value=default_sha,
                key="pg-dev-sha",
                help="Defaults to current HEAD short SHA.",
            )
            dev_task = st.text_input(
                "task (one-line)",
                value="",
                key="pg-dev-task",
                placeholder="Add medical event-token instrument",
            )
            dev_pass = st.number_input(
                "test_pass", min_value=0, value=0,
                step=1, key="pg-dev-pass",
            )
            dev_fail = st.number_input(
                "test_fail", min_value=0, value=0,
                step=1, key="pg-dev-fail",
            )
            dev_signal = st.selectbox(
                "retrospect_signal",
                ["neutral", "positive", "negative"],
                index=0, key="pg-dev-signal",
            )
        with col_b:
            dev_files_text = st.text_area(
                "changed_files (one per line)",
                value="\n".join(default_files),
                height=140,
                key="pg-dev-files",
                help="Auto-populated from `git diff HEAD~1 HEAD`.",
            )
            dev_diff = st.text_area(
                "diff_summary",
                value="",
                height=140,
                key="pg-dev-diff",
                placeholder="One-paragraph summary of what changed.",
            )

        target_root = st.text_input(
            "repo_root (where the 3 JSONL sinks live)",
            value=str(_PgPath.cwd()),
            key="pg-dev-root",
        )

        # Cap-on-rails: dry-run preview by default; second confirm
        # writes the records.
        dry_run = st.checkbox(
            "Dry run (sign + show, do NOT write JSONL sinks)",
            value=True,
            key="pg-dev-dry",
            help=(
                "When checked, the record is signed and rendered but "
                "the three JSONL files are not touched. Uncheck and "
                "re-run to actually append."
            ),
        )
        consent = False
        if not dry_run:
            consent = st.checkbox(
                "I understand this appends to "
                "axiom_dev_training.jsonl + dev_agent_improvements.jsonl + "
                "axiom_crl_reward_log.jsonl.",
                value=False,
                key="pg-dev-consent",
            )

        run_disabled = (
            not dev_sha or not dev_task.strip() or
            (not dry_run and not consent)
        )
        if st.button(
            "▶  Record dev cycle",
            type="primary",
            key="pg-dev-run",
            width="stretch",
            disabled=run_disabled,
        ):
            from axiom_dev_loop import (
                DevCycleRecorder, _sign as _dev_sign,
                _signing_key as _dev_signing_key,
            )
            changed = [
                line.strip() for line in dev_files_text.splitlines()
                if line.strip()
            ]
            if dry_run:
                # Build + sign WITHOUT touching the sinks. Mirror the
                # internal payload shape from DevCycleRecorder.record.
                from datetime import datetime, timezone
                timestamp = datetime.now(timezone.utc).isoformat()
                rating = (
                    "good" if (dev_fail == 0 and dev_pass > 0)
                    else "bad"
                )
                payload = {
                    "commit_sha":        dev_sha,
                    "task":              dev_task.strip(),
                    "changed_files":     changed,
                    "diff_summary":      dev_diff,
                    "test_pass":         int(dev_pass),
                    "test_fail":         int(dev_fail),
                    "retrospect_signal": dev_signal,
                    "rating":            rating,
                    "timestamp":         timestamp,
                }
                try:
                    sig = _dev_sign(_dev_signing_key(), payload)
                except Exception as e:  # noqa: BLE001
                    st.error(f"Signing failed: "
                             f"{type(e).__name__}: {e}")
                    sig = ""
                if sig:
                    record_d = {**payload, "signature": sig}
                    st.markdown(
                        _pg_signed_badge(True),
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        f"DRY RUN — no JSONL writes  ·  rating={rating}"
                    )
                    with st.expander("Signed record (preview)"):
                        st.code(
                            _pg_json.dumps(record_d, indent=2),
                            language="json",
                        )
            else:
                try:
                    rec = DevCycleRecorder(
                        repo_root=_PgPath(target_root),
                    ).record(
                        commit_sha=dev_sha,
                        task=dev_task.strip(),
                        changed_files=changed,
                        diff_summary=dev_diff,
                        test_pass=int(dev_pass),
                        test_fail=int(dev_fail),
                        retrospect_signal=dev_signal,
                    )
                except Exception as e:  # noqa: BLE001
                    st.error(f"Record failed: "
                             f"{type(e).__name__}: {e}")
                    rec = None
                if rec is not None:
                    from axiom_dev_loop import verify as _dev_verify
                    st.markdown(
                        _pg_signed_badge(_dev_verify(rec)),
                        unsafe_allow_html=True,
                    )
                    st.success(
                        f"rating={rec.rating}  ·  "
                        f"signature={rec.signature[:24]}…"
                    )
                    st.caption(
                        "appended to: "
                        "axiom_dev_training.jsonl, "
                        "dev_agent_improvements.jsonl, "
                        "axiom_crl_reward_log.jsonl"
                    )
                    with st.expander("Raw DevCycleRecord JSON"):
                        st.code(
                            _pg_json.dumps(
                                {
                                    "commit_sha":   rec.commit_sha,
                                    "task":         rec.task,
                                    "changed_files": list(
                                        rec.changed_files),
                                    "diff_summary": rec.diff_summary,
                                    "test_pass":    rec.test_pass,
                                    "test_fail":    rec.test_fail,
                                    "retrospect_signal":
                                        rec.retrospect_signal,
                                    "rating":       rec.rating,
                                    "timestamp":    rec.timestamp,
                                    "signature":    rec.signature,
                                },
                                indent=2,
                            ),
                            language="json",
                        )

        # ── Verifier panel — load existing records and verify each ──
        st.divider()
        with st.expander("Verify previously-recorded dev cycles"):
            training_path = _PgPath(target_root) / "axiom_dev_training.jsonl"
            if not training_path.exists():
                st.info(f"No record file at {training_path}")
            else:
                from axiom_dev_loop import (
                    DevCycleRecord, verify as _dev_verify,
                )
                lines = training_path.read_text(
                    encoding="utf-8",
                ).splitlines()
                if not lines:
                    st.info("Record file is empty.")
                else:
                    st.caption(
                        f"{len(lines)} record(s) in {training_path}"
                    )
                    show_n = st.slider(
                        "Show last N",
                        min_value=1, max_value=min(50, len(lines)),
                        value=min(5, len(lines)),
                        key="pg-dev-verify-n",
                    )
                    for line in lines[-show_n:]:
                        try:
                            d = _pg_json.loads(line)
                        except _pg_json.JSONDecodeError:
                            st.warning(f"unparseable: {line[:80]}")
                            continue
                        try:
                            rec = DevCycleRecord(
                                commit_sha=d.get("commit_sha", ""),
                                task=d.get("task", ""),
                                changed_files=tuple(
                                    d.get("changed_files", ())),
                                diff_summary=d.get("diff_summary", ""),
                                test_pass=int(d.get("test_pass", 0)),
                                test_fail=int(d.get("test_fail", 0)),
                                retrospect_signal=d.get(
                                    "retrospect_signal", "neutral"),
                                rating=d.get("rating", "bad"),
                                timestamp=d.get("timestamp", ""),
                                signature=d.get("signature", ""),
                            )
                            ok = _dev_verify(rec)
                        except Exception as e:  # noqa: BLE001
                            ok = False
                            st.warning(f"could not reconstruct: {e}")
                            continue
                        st.markdown(
                            f"{_pg_signed_badge(ok)} "
                            f"<code>{d.get('commit_sha', '')[:12]}</code> "
                            f"· {d.get('task', '')[:60]} "
                            f"· rating={d.get('rating', '?')}",
                            unsafe_allow_html=True,
                        )
