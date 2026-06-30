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
 tab_exo, tab_audio, tab_dev, tab_med,
 tab_twitter, tab_codeagent) = st.tabs([
    "🔁 Prompt Evolution",
    "📄 AXIOM DSL (Language Test)",
    "📈 Growth Dashboard",
    "🦾 Exoskeleton",
    "🎙️ Audio",
    "🛠️ Dev Agent",
    "🧬 Medical Research",
    "🐦 Twitter Reply",
    "🤖 Code Agent",
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
    use_memory = st.toggle("Prompt Memory (RAG)", value=True,
                           help="Recall the best prompts from similar PAST runs to warm-start "
                                "this one, and remember this run's results. Evolution learns "
                                "across tasks and compounds over time.")
    memory_floor = st.slider("Memory: min score to reuse", 1.0, 10.0, 8.0, 0.5,
                             help="Only warm-start from past prompts that scored at least this high.")
    use_knowledge = st.toggle("Knowledge RAG (AXIOM docs)", value=True,
                              help="Retrieve relevant AXIOM spec / docs / .axiom examples for the "
                                   "task and ground the Worker in them — so it can answer what it "
                                   "doesn't know (e.g. how to build an AXIOM agent) instead of guessing.")
    temperature = st.slider("Worker Temperature", 0.1, 1.0, 0.7, 0.05)

    st.divider()
    st.markdown("### 🤖 LLM / SLM Backend")
    # One picker drives BOTH:
    #   axiom_event_token.backends.default_backend (Exoskeleton tab)
    #   axiom_constitutional.client                (Prompt Evolution + DSL)
    # by setting AXIOM_BACKEND / OLLAMA_* / NIM_* / AXIOM_API_KEY /
    # AXIOM_BASE_URL each rerender. Save-to-.env makes the choice
    # persist across sessions.

    _BACKEND_OPTIONS = [
        "local,nim (auto-fallback)",      # local first, NIM fallback
        "local,deepseek (auto-fallback)", # local first, DeepSeek fallback
        "deepseek",                       # DeepSeek API only
        "local",                          # Ollama only
        "nim",                            # NVIDIA NIM only
        "nim,local (NIM-first fallback)",
        "custom (OpenAI-compatible)",     # Bring-your-own endpoint
    ]
    _BACKEND_LABEL_TO_ENV = {
        "local,nim (auto-fallback)":       "local,nim",
        "local,deepseek (auto-fallback)":  "local,deepseek",
        "nim,local (NIM-first fallback)":  "nim,local",
        "deepseek":                         "deepseek",
        "local":                            "local",
        "nim":                              "nim",
        "custom (OpenAI-compatible)":      "custom",
    }
    _BACKEND_ENV_TO_LABEL = {
        v: k for k, v in _BACKEND_LABEL_TO_ENV.items()
    }
    _backend_default = _BACKEND_ENV_TO_LABEL.get(
        os.environ.get("AXIOM_BACKEND", "local,nim"),
        "local,nim (auto-fallback)",
    )

    backend_choice = st.selectbox(
        "Backend",
        _BACKEND_OPTIONS,
        index=_BACKEND_OPTIONS.index(_backend_default),
        key="sb-backend",
        help=(
            "All tabs use this. 'local' = Ollama at OLLAMA_URL. "
            "'nim' = NVIDIA NIM. 'local,nim' tries local first and "
            "falls back to NIM (safest if you sometimes have Ollama "
            "running and sometimes don't)."
        ),
    )
    # Map dropdown label → AXIOM_BACKEND env value.
    _backend_env = _BACKEND_LABEL_TO_ENV[backend_choice]

    # Known-good local models for the GTX 1660 Ti / 6 GB VRAM tier
    # (Q4_K_M quantization). Picking one writes it to the
    # local_model text field. "(custom)" leaves the field editable.
    _LOCAL_MODEL_PRESETS = [
        "(custom — type below)",
        "qwen2.5:7b           — best all-rounder, Apache 2.0",
        "qwen2.5-coder:7b     — code patches, dev-agent flows",
        "qwen2.5-coder:3b     — fast code-gen for low-VRAM / laptop dev",
        "deepseek-r1:7b       — reasoning-tuned distill",
        "deepseek-r1:8b       — slightly larger distill",
        "mistral:7b-instruct  — sales / outreach delegates",
        "phi3.5               — 3.8B, fast Evaluator + light tasks",
        "gemma2:9b            — tight VRAM fit (Q4_K_S), best quality",
        "llama3.2:3b          — tiny + fast for smoke tests",
    ]
    _local_preset = st.selectbox(
        "Local model preset",
        _LOCAL_MODEL_PRESETS,
        index=0,
        key="sb-local-preset",
        help=(
            "Picks fit into 6 GB VRAM at Q4_K_M (except gemma2:9b "
            "which needs Q4_K_S). Choosing one populates the text "
            "field below. Stays on '(custom)' if you're typing your "
            "own model name."
        ),
    )
    # If the user picked a preset, prefill the text field with just
    # the model name (strip the "— description" trailer).
    _preset_default = None
    if not _local_preset.startswith("(custom"):
        _preset_default = _local_preset.split("—")[0].strip()
    local_model = st.text_input(
        "Local (Ollama) model",
        value=(_preset_default
               or os.environ.get("OLLAMA_MODEL", "llama3.2:3b")),
        key="sb-local-model",
        help=(
            "Pull first with `ollama pull <model>`. "
            "Set OLLAMA_URL too if Ollama isn't on localhost:11434."
        ),
    )
    nim_model = st.text_input(
        "NIM model",
        value=os.environ.get(
            "NIM_MODEL",
            os.environ.get("AXIOM_MODEL", "meta/llama-3.3-70b-instruct"),
        ),
        key="sb-nim-model",
        help="Free tier list at https://build.nvidia.com",
    )
    ollama_url = st.text_input(
        "OLLAMA_URL",
        value=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        key="sb-ollama-url",
        help="Where Ollama serves. Leave default for local install.",
    )
    deepseek_model = st.text_input(
        "DeepSeek model",
        value=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        key="sb-deepseek-model",
        help=(
            "'deepseek-chat' = DeepSeek-V3 (general). "
            "'deepseek-reasoner' = DeepSeek-R1 (slower, deeper "
            "reasoning). Used only by Prompt Evolution / "
            "Exoskeleton / Medical Research when 'deepseek' is in "
            "the backend chain."
        ),
    )
    deepseek_key = st.text_input(
        "DeepSeek API key",
        value=os.environ.get("DEEPSEEK_API_KEY", ""),
        type="password",
        key="sb-deepseek-key",
        help=(
            "Get one at platform.deepseek.com. "
            "Pricing ~$0.14/M input + $0.28/M output for V3 — "
            "usually cheaper than running distilled models locally "
            "for solo-founder volume."
        ),
    )

    # Bring-your-own OpenAI-compatible endpoint. Shown unconditionally
    # so the env vars stay visible even when not in use; only matters
    # when AXIOM_BACKEND='custom' is in the chain.
    custom_base_url = st.text_input(
        "Custom BASE_URL (OpenAI-compatible)",
        value=os.environ.get("AXIOM_BASE_URL", ""),
        key="sb-custom-base-url",
        help=(
            "Endpoint that speaks OpenAI's /v1/chat/completions shape. "
            "Examples: https://openrouter.ai/api/v1 · "
            "https://api.together.xyz/v1 · http://localhost:1234/v1 "
            "(LM Studio) · your own vLLM. Only used when the Backend "
            "dropdown is set to 'custom (OpenAI-compatible)' or a "
            "chain containing 'custom'."
        ),
    )
    custom_model = st.text_input(
        "Custom model name",
        value=os.environ.get(
            "AXIOM_MODEL_CUSTOM",
            os.environ.get("AXIOM_MODEL", ""),
        ),
        key="sb-custom-model",
        help="Model identifier your endpoint expects "
             "(e.g. 'anthropic/claude-3.5-sonnet' on OpenRouter).",
    )
    custom_api_key = st.text_input(
        "Custom API key",
        value=os.environ.get("AXIOM_API_KEY_CUSTOM", ""),
        type="password",
        key="sb-custom-api-key",
        help="Use any non-empty string for endpoints that don't "
             "validate the key (LM Studio, vLLM).",
    )

    # The Prompt Evolution / DSL tabs use the chosen model under the
    # legacy AXIOM_MODEL / AXIOM_BASE_URL / AXIOM_API_KEY names —
    # axiom_constitutional.client._build_client reads those. Route
    # them to whatever the sidebar Backend points at, with the
    # FIRST element of a chain winning (consistent with how
    # ChainedBackend picks its target).
    _primary = _backend_env.split(",")[0].strip()
    if _primary == "local":
        _legacy_model    = local_model
        _legacy_base_url = f"{ollama_url.rstrip('/')}/v1"
        _legacy_api_key  = "ollama"
    elif _primary == "deepseek":
        _legacy_model    = deepseek_model
        _legacy_base_url = os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1",
        )
        _legacy_api_key  = deepseek_key.strip()
    elif _primary == "custom":
        _legacy_model    = custom_model.strip() or "model"
        _legacy_base_url = custom_base_url.strip() or os.environ.get(
            "AXIOM_BASE_URL", ""
        )
        _legacy_api_key  = custom_api_key.strip() or os.environ.get(
            "AXIOM_API_KEY", ""
        )
    else:   # 'nim' (or any unrecognised value falls back to NIM)
        _legacy_model    = nim_model
        _legacy_base_url = os.environ.get(
            "NVIDIA_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        )
        _legacy_api_key  = (
            os.environ.get("AXIOM_API_KEY")
            or os.environ.get("NVIDIA_API_KEY", "")
        )

    # Apply on every rerun so downstream modules see the choice.
    os.environ["AXIOM_BACKEND"]  = _backend_env
    os.environ["OLLAMA_MODEL"]   = local_model
    os.environ["OLLAMA_URL"]     = ollama_url
    os.environ["NIM_MODEL"]      = nim_model
    os.environ["DEEPSEEK_MODEL"] = deepseek_model
    if deepseek_key.strip():
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key.strip()
    # Stash the custom-endpoint trio separately so the user's
    # explicit picks don't get clobbered by the legacy-routing
    # block when a NIM/DeepSeek/local backend is active.
    if custom_base_url.strip():
        os.environ["AXIOM_BASE_URL_CUSTOM"] = custom_base_url.strip()
    if custom_model.strip():
        os.environ["AXIOM_MODEL_CUSTOM"]    = custom_model.strip()
    if custom_api_key.strip():
        os.environ["AXIOM_API_KEY_CUSTOM"]  = custom_api_key.strip()
    os.environ["AXIOM_MODEL"]    = _legacy_model
    os.environ["AXIOM_BASE_URL"] = _legacy_base_url
    if _legacy_api_key:
        os.environ["AXIOM_API_KEY"] = _legacy_api_key

    # Preflight: which backends look reachable?
    _be_status: list[str] = []
    if "local" in _backend_env:
        _be_status.append("✓ local target set")
    if "deepseek" in _backend_env:
        if deepseek_key.strip() or os.environ.get("DEEPSEEK_API_KEY"):
            _be_status.append("✓ DeepSeek key set")
        else:
            _be_status.append("✗ DeepSeek key missing")
    if "custom" in _backend_env:
        has_url = bool(custom_base_url.strip()
                       or os.environ.get("AXIOM_BASE_URL_CUSTOM"))
        has_key = bool(custom_api_key.strip()
                       or os.environ.get("AXIOM_API_KEY_CUSTOM"))
        if has_url and has_key:
            _be_status.append("✓ custom endpoint set")
        else:
            _be_status.append(
                "✗ custom needs BASE_URL + API_KEY"
            )
    # Match 'nim' but not 'nim' inside 'deepseek'-only strings.
    _has_nim = any(
        tok.strip() == "nim" for tok in _backend_env.split(",")
    )
    if _has_nim:
        if os.environ.get("NVIDIA_NIM_API_KEY") or \
                os.environ.get("NVIDIA_API_KEY"):
            _be_status.append("✓ NIM key set")
        else:
            _be_status.append("✗ NIM key missing")
    st.caption("  ·  ".join(_be_status))

    # Save the chosen values to .env (creates the file if missing,
    # preserves any unrelated keys already there).
    if st.button("💾 Save to .env", key="sb-save-env",
                  width="stretch",
                  help="Writes AXIOM_BACKEND, OLLAMA_MODEL, "
                       "OLLAMA_URL, NIM_MODEL, AXIOM_MODEL, "
                       "AXIOM_BASE_URL into .env so the choice "
                       "persists across sessions. Other keys in "
                       ".env are left untouched."):
        _env_path = Path(".env")
        _managed = {
            "AXIOM_BACKEND":  _backend_env,
            "OLLAMA_MODEL":   local_model,
            "OLLAMA_URL":     ollama_url,
            "NIM_MODEL":      nim_model,
            "DEEPSEEK_MODEL": deepseek_model,
            "AXIOM_MODEL":    _legacy_model,
            "AXIOM_BASE_URL": _legacy_base_url,
        }
        # Persist the DeepSeek key only when explicitly set in the
        # sidebar (don't blow away an existing .env entry).
        if deepseek_key.strip():
            _managed["DEEPSEEK_API_KEY"] = deepseek_key.strip()
        existing: list[str] = []
        if _env_path.exists():
            existing = _env_path.read_text(encoding="utf-8").splitlines()
        # Drop existing lines for managed keys; keep everything else.
        kept = [
            line for line in existing
            if not any(line.lstrip().startswith(f"{k}=")
                       for k in _managed)
        ]
        new_lines = kept + [f"{k}={v}" for k, v in _managed.items()]
        _env_path.write_text("\n".join(new_lines) + "\n",
                              encoding="utf-8")
        st.success(f"Saved {len(_managed)} keys to {_env_path.resolve()}")

    # Legacy alias so the Prompt Evolution + DSL tabs (which read
    # `model` from this sidebar) keep working without rewriting.
    model = _legacy_model

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

 # ── Initial prompts: show + (optionally) override what each agent starts with
 with st.expander(
    "🔍 Initial prompts (Worker / Evaluator / Rewriter)",
    expanded=False,
 ):
    st.caption(
        "The TASK above is automatically shared with all three agents. "
        "Each agent has its own SYSTEM PROMPT (auto-loaded from "
        "prompt_store for this task; falls back to the seed). Override "
        "below to prime evolution from a specific starting prompt."
    )

    # ── Seed-from-prior-run picker ────────────────────────────────────
    # Lower the chance of restarting evolution from scratch when a
    # prior run already produced a strong Worker prompt for this task
    # (or a similar one). Pick the run from the dropdown, hit Load,
    # the override field below auto-populates.
    st.markdown("**📚 Seed from a prior run** *(lower mistake risk while re-evolving)*")
    import json as _seed_json
    _seed_runs: list = []
    try:
        _prompts_root = Path(
            os.environ.get("AXIOM_PROMPTS_DIR", "prompts")
        )
        if _prompts_root.is_dir():
            for _d in sorted(_prompts_root.iterdir()):
                _wf = _d / "worker.json"
                if not _wf.is_file():
                    continue
                try:
                    _data = _seed_json.loads(_wf.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                _iters = _data.get("iterations", []) or []
                if not _iters:
                    continue
                _best_idx = int(_data.get("best_version", 0) or 0)
                _best_score = float(_data.get("best_score", 0.0) or 0.0)
                _preview = (
                    (_data.get("task_description") or "")
                    .replace("\n", " ")
                    .strip()[:60]
                )
                _seed_runs.append({
                    "task_id":         _d.name,
                    "best_score":      _best_score,
                    "iters":           len(_iters),
                    "best_prompt":     _iters[_best_idx]["prompt"],
                    "task_description": _data.get("task_description", ""),
                    "label": (
                        f"{_d.name}  ·  best={_best_score:.1f}  ·  "
                        f"iters={len(_iters)}  ·  "
                        f"{(_preview or '(no task)')!r}"
                    ),
                })
            # Sort by best_score desc so the strongest seeds are first.
            _seed_runs.sort(key=lambda r: r["best_score"], reverse=True)
    except Exception as e:  # noqa: BLE001
        st.caption(f"(could not scan prompts dir: {e})")

    if _seed_runs:
        _options = ["(don't seed from a prior run)"] + [r["label"] for r in _seed_runs]
        _seed_choice = st.selectbox(
            f"Pick a prior run ({len(_seed_runs)} available, sorted by best score)",
            _options,
            index=0,
            key="seed-from-run",
        )
        if _seed_choice != _options[0]:
            _chosen = next(r for r in _seed_runs if r["label"] == _seed_choice)
            _c1, _c2 = st.columns([1, 3])
            with _c1:
                if st.button("Load this seed →", key="load-seed",
                             width="stretch"):
                    st.session_state["worker-prompt-override"] = \
                        _chosen["best_prompt"]
                    st.success(
                        f"Loaded best prompt from {_chosen['task_id']} "
                        f"(score={_chosen['best_score']:.1f}) into the "
                        f"override field below."
                    )
                    st.rerun()
            with _c2:
                st.caption(
                    f"task: {_chosen['task_description'][:120]!r}"
                )
    else:
        st.caption(
            "(no prior runs in `prompts/` yet — run a Prompt Evolution "
            "session first and the best Worker prompt will land here)"
        )

    # Escape hatch: paste a task_id directly.
    _seed_id_paste = st.text_input(
        "...or paste a task_id (16 hex chars)",
        value="",
        key="seed-id-paste",
        help=(
            "task_id is the 16-char SHA-256 prefix of the task "
            "description. Find it in the directory names under "
            "prompts/ or in the 'Run ID' line printed after a run."
        ),
    )
    if _seed_id_paste.strip() and st.button(
        "Load by task_id →", key="load-seed-id",
    ):
        _tid = _seed_id_paste.strip()
        _match = next(
            (r for r in _seed_runs if r["task_id"] == _tid),
            None,
        )
        if _match:
            st.session_state["worker-prompt-override"] = _match["best_prompt"]
            st.success(
                f"Loaded best prompt from {_tid} "
                f"(score={_match['best_score']:.1f})."
            )
            st.rerun()
        else:
            st.warning(
                f"No task_id {_tid!r} found in {_prompts_root}. "
                f"Available: {[r['task_id'] for r in _seed_runs[:5]]}…"
            )

    st.divider()
    try:
        from axiom_constitutional.agents.worker import WorkerAgent as _W
        from axiom_constitutional.agents.evaluator import EvaluatorAgent as _E
        from axiom_constitutional.agents.rewriter import RewriterAgent as _R
        _task_for_preview = (task or "(no task entered yet)").strip()[:120]
        _wp = _W(_task_for_preview).system_prompt
        _ep = _E(_task_for_preview).system_prompt
        _rp = _R(_task_for_preview).system_prompt
    except Exception as e:  # noqa: BLE001
        _wp = _ep = _rp = f"(could not load: {e})"

    st.markdown("**Worker system prompt** (evolves every iteration)")
    worker_prompt_override = st.text_area(
        "Override Worker prompt (leave blank = auto-load)",
        value="",
        height=120,
        key="worker-prompt-override",
        placeholder=_wp[:400] + ("…" if len(_wp) > 400 else ""),
        help="Pasted text becomes the Worker's STARTING system prompt. "
             "Subsequent iterations evolve from there.",
    )

    st.markdown("**Evaluator system prompt** (seed; evolves only with Meta-Evolution toggle)")
    st.code(_ep[:600] + ("…" if len(_ep) > 600 else ""), language=None)

    st.markdown("**Rewriter system prompt** (seed; evolves only with Meta-Evolution toggle)")
    st.code(_rp[:600] + ("…" if len(_rp) > 600 else ""), language=None)

    st.caption(
        "Cross-agent threading: the Evaluator sees the TASK + the "
        "Worker's OUTPUT each iteration (its user message is "
        "`TASK GIVEN TO WORKER: …` + `WORKER OUTPUT: …`). "
        "The Rewriter sees the TASK + the Worker's CURRENT prompt "
        "+ the Evaluator's score/reasoning/improvements."
    )

 col1, col2 = st.columns([1, 5])
 with col1:
    run_btn = st.button("▶  Run AXIOM", type="primary", width='stretch')

 # ── Run ──────────────────────────────────────────────────────────────────────
 if run_btn:
    if not task.strip():
        st.warning("Please enter a task.")
        st.stop()

    # ── Validate the picked backend has what it needs ───────────────────────
    _be = os.environ.get("AXIOM_BACKEND", "")
    if _be.startswith("local"):
        # Local target: Ollama must be reachable. We don't ping here —
        # the constitutional client will raise a clear error if not.
        pass
    else:
        # NIM-first chains still need a NIM key to function.
        _nim_key = (
            os.environ.get("NVIDIA_NIM_API_KEY")
            or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("AXIOM_API_KEY")
        )
        if not _nim_key or _nim_key == "your_nvidia_api_key_here":
            st.error(
                "NIM backend selected but no API key found. Either "
                "set NVIDIA_NIM_API_KEY in `.env` (get one at "
                "https://build.nvidia.com) or switch the sidebar "
                "Backend to 'local' / 'local,nim'."
            )
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
    from axiom_constitutional.evolution import (
        EvolutionResult, IterationResult, LOGS_DIR,
    )
    try:
        from axiom_constitutional.evolution import detect_score_pegging
    except ImportError:
        # Older evolution.py without this helper — minimal inline fallback:
        # flag when the last `window` scores are identical (evaluator pegging).
        def detect_score_pegging(scores, window=3):
            if len(scores) >= window and len(set(scores[-window:])) == 1:
                return {"pegged_at": scores[-1], "window": window}
            return None
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
    # Honour the "Initial prompts" expander's override — pasted text
    # becomes the Worker's STARTING system prompt for this run.
    _override = (st.session_state.get("worker-prompt-override") or "").strip()
    if _override:
        worker.system_prompt = _override
        st.caption(
            f"Using overridden Worker prompt "
            f"({len(_override)} chars) from the Initial Prompts expander."
        )

    # ── Experience RAG: warm-start from the best prompt of a similar past run ──
    mem_hits = []
    if use_memory and not _override:
        try:
            from axiom_constitutional import prompt_memory
            mem_hits = prompt_memory.recall(task, k=3, min_score=memory_floor, role="worker")
            if mem_hits:
                worker.system_prompt = mem_hits[0]["prompt"]
                st.caption(
                    f"🧠 Prompt memory: warm-started from a similar past task "
                    f"(scored {mem_hits[0]['score']:.1f}/10) · {prompt_memory.stats()} iterations remembered."
                )
        except Exception as _e:
            st.caption(f"prompt memory unavailable: {_e}")

    # ── Knowledge RAG: ground the Worker in retrieved AXIOM docs/spec/examples ──
    _kctx = ""
    if use_knowledge:
        try:
            from axiom_constitutional import knowledge_rag
            _kctx = knowledge_rag.context_for(task)
            if _kctx:
                st.caption(f"📚 Knowledge RAG: grounded with AXIOM reference "
                           f"({knowledge_rag.stats()} chunks indexed).")
        except Exception as _e:
            st.caption(f"knowledge rag unavailable: {_e}")

    # Apply UI temperature override (+ retrieved AXIOM reference, if any)
    def _execute_with_temp(t):
        from axiom_constitutional import client as nim
        user = (_kctx + "\n\nTask:\n" + t) if _kctx else f"Task:\n{t}"
        return nim.chat(worker.system_prompt, user, temperature=temperature)
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

                # Experience RAG: remember this iteration for future runs
                if use_memory:
                    try:
                        from axiom_constitutional import prompt_memory
                        prompt_memory.index_iteration(
                            task, worker.system_prompt, score, evaluation, role="worker")
                    except Exception:
                        pass

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

                # Anti-pegging check — bail out if the Evaluator emits
                # the same score N iterations in a row (it isn't really
                # evaluating). See evolution.detect_score_pegging.
                peg = detect_score_pegging(
                    [it.score for it in result.iterations]
                )
                if peg:
                    iter_status.update(
                        label=(
                            f"⚠ Iteration {i+1} — Evaluator pegging at "
                            f"{peg['pegged_at']:.1f} ({peg['window']} in a row)"
                        ),
                        state="error",
                        expanded=True,
                    )
                    st.error(
                        f"**Evaluator pegging detected.** The Evaluator "
                        f"returned the same score ({peg['pegged_at']:.1f}) for "
                        f"{peg['window']} iterations running — that means it's "
                        f"defaulting rather than evaluating. Inspect the "
                        f"Evaluator's reasoning above; common causes are a "
                        f"too-vague rubric, an over-evolved Evaluator prompt "
                        f"in `prompts/`, or a model that's too agreeable. "
                        f"Run aborted to avoid promoting a falsely high-scoring "
                        f"Worker prompt."
                    )
                    if "evaluation" in evaluation:
                        pass  # marker for grep
                    break

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

        # Knowledge RAG: retrieve AXIOM reference for this DSL task
        _dsl_kctx = ""
        if use_knowledge:
            try:
                from axiom_constitutional import knowledge_rag
                _dsl_kctx = knowledge_rag.context_for(dsl_task)
            except Exception:
                _dsl_kctx = ""

        for i in range(max_iterations):
            prog.progress(i / max_iterations, text=f"Iteration {i+1}/{max_iterations}")

            with st.status(f"Iteration {i+1} — Worker", expanded=True) as s:

                # Worker
                st.markdown('<span class="tag tag-worker">WORKER</span>', unsafe_allow_html=True)
                out = nim.chat(worker_p, (_dsl_kctx + "\n\nTask:\n" + dsl_task) if _dsl_kctx else f"Task:\n{dsl_task}", temperature=temperature)
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
    else:
        # NOTE: previously called st.stop() in the empty-logs branch,
        # which halted the entire script — the playground tabs below
        # never rendered. The rest of the dashboard now lives inside
        # this else branch instead.
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
            _sidebar_backend = os.environ.get(
                "AXIOM_BACKEND", "local,nim",
            )
            backend_label = st.selectbox(
                "Backend (this tab)",
                [f"use sidebar choice ({_sidebar_backend})",
                 "local",
                 "nim",
                 "deepseek",
                 "local,nim",
                 "local,deepseek",
                 "nim,local"],
                index=0,
                key="pg-exo-backend",
                help=(
                    "Defaults to whatever the sidebar Backend "
                    "is set to. Override per-run only if you want "
                    "to A/B test something specific."
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
            # axiom_event_token.backends.default_backend understands
            # AXIOM_BACKEND="local,nim" as a ChainedBackend that tries
            # local first then NIM — the safer playground default.
            previous_backend = os.environ.get("AXIOM_BACKEND")
            backend_env_value = None
            if backend_label.startswith("use sidebar"):
                # Inherit from the sidebar — already set in os.environ.
                pass
            elif backend_label in (
                "local", "nim", "deepseek",
                "local,nim", "local,deepseek", "nim,local",
            ):
                backend_env_value = backend_label
            if backend_env_value is not None:
                os.environ["AXIOM_BACKEND"] = backend_env_value
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
                if backend_env_value is not None:
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


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Medical Research — AXM container + per-layer signed event tokens
# ─────────────────────────────────────────────────────────────────────────────


with tab_med:
    st.markdown("### 🧬 Medical research instrument")
    st.markdown(
        "Signed, replayable medical-research session. Each question "
        "fans out across the layer delegates in the chosen profile "
        "(`source` / `claim` / `data` / `bio` / **`physics`** / "
        "`governance`); each layer is its own **signed EventToken**; "
        "a **MedicalCoordinatorToken** binds them under "
        "`axiom-medical-coord-v1`. The bracketed Token Descriptor "
        "is what you'd paste into any plain LLM for synthesis."
    )

    key_ok = _pg_key_panel("med")
    imports = _pg_check_imports()
    # axiom_medical_agent isn't in _pg_check_imports yet; probe ad-hoc.
    try:
        import axiom_medical_agent as _med_mod  # noqa: F401
        med_import_ok = True
        med_import_err = ""
    except Exception as e:  # noqa: BLE001
        med_import_ok = False
        med_import_err = f"{type(e).__name__}: {e}"

    if not med_import_ok:
        st.error(f"axiom_medical_agent import failed: {med_import_err}")

    if key_ok and med_import_ok:
        from axiom_medical_agent import (
            LAYER_ACTIVATION_PROFILES,
        )

        col_l, col_r = st.columns([1, 2])
        with col_l:
            med_profile = st.selectbox(
                "Activation profile",
                sorted(LAYER_ACTIVATION_PROFILES),
                index=sorted(LAYER_ACTIVATION_PROFILES).index(
                    "mechanism"
                    if "mechanism" in LAYER_ACTIVATION_PROFILES
                    else sorted(LAYER_ACTIVATION_PROFILES)[0]
                ),
                key="pg-med-profile",
                help=(
                    "Picks which layer delegates fire. 'mechanism' "
                    "is the one with the physics / world-model "
                    "layer. 'patient_apply' is the most restrictive "
                    "(governance-only)."
                ),
            )
            st.caption(
                "active layers: "
                + ", ".join(LAYER_ACTIVATION_PROFILES[med_profile])
            )
            write_med_ledger = st.checkbox(
                "Append to medical ledger",
                value=False,
                key="pg-med-ledger",
                help=(
                    "Off by default so the playground doesn't pollute "
                    "~/.axiom/medical-ledger.jsonl. Flip on for "
                    "audit-worthy runs."
                ),
            )

        with col_r:
            med_question = st.text_area(
                "Research question",
                value=(
                    "What mechanisms link GLP-1 drugs to reduced "
                    "inflammation?"
                ),
                height=100,
                key="pg-med-question",
            )
            med_sources_text = st.text_area(
                "Sources (optional, one JSON object per line)",
                value="",
                height=110,
                key="pg-med-sources",
                placeholder=(
                    '{"name": "Cochrane 2023 systematic review", '
                    '"source_type": "systematic_review", '
                    '"text": "abstract here..."}'
                ),
                help=(
                    "Each line is a JSON dict with at minimum "
                    "`name` and `text`. Leave blank for a "
                    "session-default placeholder source."
                ),
            )

        if st.button(
            "▶  Run medical session",
            type="primary",
            key="pg-med-run",
            width="stretch",
            disabled=not med_question.strip(),
        ):
            from axiom_medical_agent import (
                MedicalResearchAgent, MedicalAgentError,
            )
            from axiom_medical_container import (
                MedicalContainerSpec, MedicalContainerError,
            )
            from axiom_medical_ledger import (
                LedgerWriter as _MedLedgerWriter,
                default_ledger_path as _med_default_ledger_path,
            )

            # Parse sources lines into dicts.
            sources: list = []
            for line in (med_sources_text or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    sources.append(_pg_json.loads(line))
                except _pg_json.JSONDecodeError as e:
                    st.warning(f"skipping unparseable source line: {e}")

            ledger_obj = (
                _MedLedgerWriter(_med_default_ledger_path())
                if write_med_ledger else None
            )

            try:
                spec = MedicalContainerSpec(
                    container_id="axm-med-pg-" + _pg_secrets.token_hex(4),
                    research_question=med_question.strip(),
                )
                agent = MedicalResearchAgent.from_default_pack(
                    ledger=ledger_obj, spec=spec,
                    research_question=med_question.strip(),
                )
            except MedicalContainerError as e:
                st.error(f"container spec rejected: {e}")
                agent = None
            except Exception as e:  # noqa: BLE001
                st.error(f"setup failed: {type(e).__name__}: {e}")
                agent = None

            result = None
            if agent is not None:
                with st.spinner(f"Running medical profile '{med_profile}' …"):
                    try:
                        result = agent.research(
                            med_question.strip(),
                            sources=(sources or None),
                            profile=med_profile,
                        )
                    except MedicalAgentError as e:
                        st.error(f"research failed: {e}")
                    except Exception as e:  # noqa: BLE001
                        st.error(
                            f"backend error: {type(e).__name__}: {e}"
                        )

            if result is not None:
                # Header — verify badge + a 1-line summary.
                all_verified = all(
                    c.verify() for c in result.coordinator_tokens
                ) and all(t.verify() for t in result.event_tokens)
                st.markdown(_pg_signed_badge(all_verified),
                            unsafe_allow_html=True)
                st.markdown(
                    f"<div class='iteration-box'>"
                    f"<strong>container</strong>: "
                    f"<code>{result.container_id}</code>  ·  "
                    f"<strong>profile</strong>: "
                    f"<code>{result.profile}</code>  ·  "
                    f"event tokens: {len(result.event_tokens)}  ·  "
                    f"coordinators: {len(result.coordinator_tokens)}<br>"
                    f"<strong>manifest_root</strong>: "
                    f"<code>{(result.manifest_root or '')[:80]}…</code>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # Human-review banner.
                if result.requires_human_review:
                    st.warning(
                        "⚠ **Human review required.** The governance "
                        "layer flagged this session — review the "
                        "descriptor before acting. (Tier-5 / PHI / "
                        "clinical-advice / emergency triggers.)"
                    )

                # Tier distribution.
                tiers = result.tier_distribution or {}
                tcols = st.columns(5)
                for i, tk in enumerate(("1", "2", "3", "4", "5")):
                    tcols[i].metric(f"Tier {tk}", int(tiers.get(tk, 0)))

                # Bracketed descriptor — the canonical handoff to a
                # plain LLM. Show it expanded by default; this is the
                # primary artifact users will copy.
                st.markdown("**Bracketed Token Descriptor**")
                st.markdown(
                    "<small style='color: var(--muted)'>"
                    "Copy this into any plain LLM with a "
                    "medical-research-only system prompt."
                    "</small>",
                    unsafe_allow_html=True,
                )
                st.code(result.descriptor, language=None)

                # Per-coordinator + per-event-token JSON expanders.
                for i, coord in enumerate(result.coordinator_tokens):
                    with st.expander(
                        f"Coordinator token {i+1} — "
                        f"links: {', '.join(sorted(coord.layer_links))}"
                    ):
                        st.code(coord.to_json(indent=2),
                                 language="json")
                with st.expander(
                    f"Raw EventToken JSON ({len(result.event_tokens)})"
                ):
                    for t in result.event_tokens:
                        st.code(t.to_json(indent=2), language="json")

                if write_med_ledger:
                    st.caption(
                        f"appended to "
                        f"{_med_default_ledger_path()}"
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Twitter Reply — halt-at-gate, paste-for-send (no API posting)
# ─────────────────────────────────────────────────────────────────────────────


with tab_twitter:
    st.markdown("### 🐦 Twitter reply drafter")
    st.markdown(
        "Paste a tweet you'd like to engage with. The agent drafts "
        "**N=3 candidate replies** under three framings "
        "(acknowledge · counter · artifact), each scanned by the "
        "same honesty post-scan that gates the exoskeleton. Pick "
        "one, approve, **copy the text, paste into Twitter**. "
        "Nothing in this module posts to the X API — approval just "
        "signs the chosen draft into the ledger so the audit trail "
        "shows which candidate you ran with."
    )

    tw_key_ok = _pg_key_panel("twitter")
    try:
        import axiom_twitter_agent as _tw_mod  # noqa: F401
        import axiom_twitter_agent_ledger as _tw_ledger  # noqa: F401
        tw_import_ok = True
        tw_import_err = ""
    except Exception as e:  # noqa: BLE001
        tw_import_ok = False
        tw_import_err = f"{type(e).__name__}: {e}"

    if not tw_import_ok:
        st.error(f"axiom_twitter_agent import failed: {tw_import_err}")

    if tw_key_ok and tw_import_ok:
        from axiom_twitter_agent import (
            TwitterAgent, TweetInput, MAX_REPLY_CHARS,
            HonestyRefusal, TwitterAgentError,
        )
        from axiom_twitter_agent_ledger import (
            LedgerWriter as _TwLedger,
            default_ledger_path as _tw_default_ledger,
        )

        def _twitter_agent() -> TwitterAgent:
            return TwitterAgent(ledger=_TwLedger(_tw_default_ledger()))

        st.markdown("#### 1. Ingest a tweet")
        with st.form("twitter-ingest-form", clear_on_submit=False):
            c1, c2 = st.columns([1, 1])
            with c1:
                tw_tweet_id = st.text_input(
                    "tweet_id (the trailing number in the URL)",
                    key="pg-tw-tweet-id",
                )
                tw_author = st.text_input(
                    "author handle (@ optional)",
                    key="pg-tw-author",
                )
            with c2:
                tw_url = st.text_input(
                    "full tweet URL",
                    key="pg-tw-url",
                )
            tw_text = st.text_area(
                "tweet body text",
                height=80,
                key="pg-tw-text",
                help=(
                    "Paste the exact body of the tweet you want to "
                    "reply to. The text is hashed at ingest so the "
                    "EventToken pins what was scraped."
                ),
            )
            ingest_btn = st.form_submit_button("Ingest + draft (N=3)")

        if ingest_btn:
            try:
                tw = TweetInput.new(
                    tweet_id=tw_tweet_id, author_handle=tw_author,
                    url=tw_url, text=tw_text,
                )
            except TwitterAgentError as e:
                st.error(str(e))
            else:
                agent = _twitter_agent()
                agent.ingest(tw)
                with st.spinner("Drafting 3 candidates…"):
                    drafts = agent.draft(tw.input_id, candidates=3)
                st.success(
                    f"Ingested {tw.input_id} + drafted "
                    f"{len(drafts)} candidate(s). Scroll down to "
                    f"the pending list."
                )

        st.divider()
        st.markdown("#### 2. Pending drafts — pick one, approve, copy")

        agent = _twitter_agent()
        pending = agent.list_pending()
        if not pending:
            st.caption(
                "No pending drafts. Ingest a tweet above to draft "
                "three candidates."
            )
        for d in pending:
            blocked = d.honesty_block_count > 0
            over    = d.over_limit
            badge = (
                ":red[BLOCKED — honesty]" if blocked
                else (":orange[OVER LIMIT]" if over
                      else ":green[ready]")
            )
            with st.container(border=True):
                st.markdown(
                    f"**{d.draft_id}**  ·  framing=`{d.framing}`  ·  "
                    f"chars={d.char_count}/{MAX_REPLY_CHARS}  ·  "
                    f"backend={d.backend}/{d.model}  ·  {badge}"
                )
                st.markdown(f"> reply to **@{d.parent_author_handle}** — "
                            f"<{d.parent_url}>")
                st.code(d.reply_text, language=None)
                if d.honesty_findings:
                    with st.expander(
                        f"Honesty findings "
                        f"({d.honesty_block_count} block / "
                        f"{d.honesty_flag_count} flag)"
                    ):
                        for f in d.honesty_findings:
                            sev = f.get("severity", "?").upper()
                            st.write(
                                f"- **[{sev}]** "
                                f"{f.get('category', '?')}: "
                                f"`{f.get('matched', '')}`"
                            )
                cols = st.columns([1, 1, 2])
                with cols[0]:
                    reviewer = st.text_input(
                        "reviewer email",
                        key=f"pg-tw-rev-{d.draft_id}",
                    )
                with cols[1]:
                    if st.button(
                        "✓ Approve",
                        key=f"pg-tw-app-{d.draft_id}",
                        disabled=(blocked or over),
                        type="primary",
                    ):
                        if not reviewer.strip():
                            st.error("reviewer email required")
                        else:
                            try:
                                token = agent.approve(
                                    d.draft_id,
                                    reviewer_principal=reviewer.strip(),
                                )
                                st.success(
                                    f"approved — signed event "
                                    f"`{token.id}` "
                                    f"(verified={token.verify()})"
                                )
                                st.rerun()
                            except HonestyRefusal as he:
                                st.error(f"HONESTY REFUSED: {he}")
                            except TwitterAgentError as te:
                                st.error(str(te))
                with cols[2]:
                    reject_reason = st.text_input(
                        "reject reason",
                        key=f"pg-tw-rej-reason-{d.draft_id}",
                    )
                    if st.button(
                        "✗ Reject",
                        key=f"pg-tw-rej-{d.draft_id}",
                    ):
                        if not reviewer.strip():
                            st.error("reviewer email required")
                        elif not reject_reason.strip():
                            st.error("reason required")
                        else:
                            agent.reject(
                                d.draft_id,
                                reviewer_principal=reviewer.strip(),
                                reason=reject_reason.strip(),
                            )
                            st.success(
                                "rejected + improvement record "
                                "appended to "
                                "dev_agent_improvements.jsonl"
                            )
                            st.rerun()

        st.divider()
        st.markdown("#### 3. Approved drafts — copy + mark sent")

        # Walk the drafts directory for approved-but-not-yet-sent items.
        from pathlib import Path as _TwPath
        drafts_root = (
            _TwPath(os.environ.get("AXIOM_TWITTER_DRAFTS")
                    or (_TwPath.home() / ".axiom" / "twitter"))
            / "drafts"
        )
        approved = []
        if drafts_root.is_dir():
            for entry in sorted(drafts_root.iterdir()):
                if not entry.is_dir():
                    continue
                try:
                    di = agent.get_draft(entry.name)
                except Exception:  # noqa: BLE001
                    continue
                if di.status == "approved":
                    approved.append(di)
        if not approved:
            st.caption(
                "No approved-but-unsent drafts. Approve one above "
                "first."
            )
        for d in approved:
            with st.container(border=True):
                st.markdown(
                    f"**{d.draft_id}** — reply to "
                    f"**@{d.parent_author_handle}** at "
                    f"<{d.parent_url}>"
                )
                st.markdown("**Copy this and paste into Twitter:**")
                st.code(d.reply_text, language=None)
                if st.button(
                    "I pasted it — mark sent",
                    key=f"pg-tw-sent-{d.draft_id}",
                ):
                    agent.mark_sent(d.draft_id)
                    st.success(
                        f"marked sent — signed into ledger "
                        f"({_tw_default_ledger()})"
                    )
                    st.rerun()

        st.caption(
            f"Ledger: `{_tw_default_ledger()}`  ·  "
            f"namespace `axiom-twitter-ledger-v1`  ·  "
            f"no API posting — paste-for-send only."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Code Agent (axiom_dev_agent_v2 — generate + constitutionally review code)
# ─────────────────────────────────────────────────────────────────────────────
with tab_codeagent:
    st.markdown("### 🤖 Code Agent")
    st.markdown(
        "Describe a **new agent or feature**. The Code Agent generates the "
        "implementation, then runs it through `axiom_dev_agent_v2`'s four-layer "
        "constitutional pipeline — **Reflex** (static safety) → **Reviewer** "
        "(competence forecast) → **Examiner** (sealed CI) — and returns a merge "
        "verdict. Unlike the Dev Agent tab (which *records* signed dev cycles), "
        "this one *writes and governs* the code."
    )

    ca_key_ok = _pg_key_panel("codeagent")
    ca_imports = _pg_check_imports()
    ca_dev_ok = ca_imports.get("axiom_dev_agent_v2", "ok") == "ok"

    try:
        import axiom_dev_agent_v2 as _ca_mod  # noqa: F401
    except Exception as _ca_e:
        ca_dev_ok = False
        st.error(f"axiom_dev_agent_v2 import failed: {type(_ca_e).__name__}: {_ca_e}")

    ca_desc = st.text_area(
        "What should the agent build?",
        value=st.session_state.get("ca_desc", ""),
        height=140,
        placeholder="e.g. A Python agent that watches a JSONL ledger and raises an "
                    "alert when constitutional_distance exceeds a threshold.",
        key="ca_desc",
    )
    ca_c1, ca_c2 = st.columns([2, 1])
    with ca_c1:
        ca_path = st.text_input("Target artifact path", value="axiom_new_feature.py",
                                key="ca_path")
    with ca_c2:
        ca_class = st.selectbox(
            "Task class",
            ["FEATURE", "BUG_FIX", "SPEC_WRITING", "EFFICIENCY", "DOCUMENTATION"],
            key="ca_class",
        )

    if st.button("⚙ Generate & Review", type="primary", width="stretch",
                 disabled=not (ca_key_ok and ca_dev_ok)):
        if not ca_desc.strip():
            st.warning("Describe the agent or feature first.")
        else:
            import uuid as _ca_uuid
            from axiom_constitutional import client as _ca_nim

            ca_code = ""
            with st.spinner("Generating implementation…"):
                ca_sys = (
                    "You are a senior Python engineer building components for the "
                    "AXIOM constitutional AI framework. Given a feature or agent "
                    "request, output ONLY the complete, runnable Python source for "
                    "the target file — no markdown fences, no commentary, no prose. "
                    "Use clear docstrings and type hints. Never include destructive "
                    "shell calls, eval/exec on untrusted input, or hardcoded secrets."
                )
                ca_user = (f"Target file: {ca_path}\nTask class: {ca_class}\n\n"
                           f"Request:\n{ca_desc}")
                try:
                    ca_code = _ca_nim.chat(ca_sys, ca_user, model=model,
                                           temperature=temperature)
                except Exception as e:
                    st.error(f"Generation failed: {type(e).__name__}: {e}")

            # Strip accidental markdown fences.
            if ca_code.strip().startswith("```"):
                _parts = ca_code.split("```")
                ca_code = _parts[1] if len(_parts) > 1 else ca_code
                if ca_code.lstrip().lower().startswith("python"):
                    ca_code = ca_code.split("\n", 1)[1] if "\n" in ca_code else ca_code

            if ca_code.strip():
                st.markdown("#### Generated implementation")
                st.code(ca_code, language="python")

                ca_outcome = None
                ca_agent = None
                with st.spinner("Constitutional review (Reflex → Reviewer → Examiner)…"):
                    try:
                        from axiom_dev_agent_v2 import AxiomDevAgentV2, DevTask
                        _ca_state = str(
                            Path(os.environ.get("AXIOM_PROMPTS_DIR", ".")) / "dev_agent_v2.json"
                        )
                        ca_agent = AxiomDevAgentV2(persistence_path=_ca_state)
                        ca_task = DevTask(
                            id=_ca_uuid.uuid4().hex[:8],
                            description=ca_desc,
                            task_class=ca_class,
                            artifact_path=ca_path,
                            proposed_diff=ca_code,
                            cited_patterns=(),
                        )
                        ca_outcome = ca_agent.handle_task(ca_task)
                    except Exception as e:
                        st.error(f"Review failed: {type(e).__name__}: {e}")

                if ca_outcome is not None:
                    _verdict = ca_outcome.final_verdict
                    _colour = {
                        "MERGED": "#7ab648",
                        "SOFTEN_REQUESTED": "#f0c040",
                        "VETO": "#e05050",
                        "REFLEX_REFUSED": "#e05050",
                    }.get(_verdict, "#e0e0e0")
                    st.markdown(
                        f"#### Verdict: <span style='color:{_colour};"
                        f"font-weight:bold'>{_verdict}</span>",
                        unsafe_allow_html=True,
                    )

                    _r = ca_outcome.reflex
                    with st.expander(f"Layer 0 · Reflex — {'OK' if _r.ok else 'REFUSED'}",
                                     expanded=not _r.ok):
                        if _r.reasons:
                            for _reason in _r.reasons:
                                st.markdown(f"- {_reason}")
                        else:
                            st.markdown("- No static-safety issues "
                                        "(AST + forbidden-pattern checks passed).")

                    if ca_outcome.review is not None:
                        _rv = ca_outcome.review
                        with st.expander(f"Layer 1 · Reviewer — {_rv.verdict}", expanded=True):
                            _m1, _m2, _m3 = st.columns(3)
                            _m1.metric("Competence", f"{_rv.competence:.2f}")
                            _m2.metric("Forecast passing", f"{_rv.forecast_passing:.2f}")
                            _m3.metric("Min safe", f"{_rv.min_safe:.2f}")
                            for _reason in _rv.reasons:
                                st.markdown(f"- {_reason}")
                            if _rv.softening_advice:
                                st.markdown("**Softening advice:**")
                                for _adv in _rv.softening_advice:
                                    st.markdown(f"- {_adv}")

                    if ca_outcome.ci is not None:
                        _ci = ca_outcome.ci
                        with st.expander(
                            f"Layer 3 · Examiner — {_ci.checks_passed}/{_ci.checks_run} "
                            f"checks passed",
                            expanded=_ci.checks_failed > 0,
                        ):
                            for _fail in _ci.failure_summary:
                                st.markdown(f"- ❌ {_fail}")
                            if _ci.checks_failed == 0:
                                st.markdown("- ✅ Sealed CI suite passed.")

                    if ca_agent is not None:
                        with st.expander("Agent status (curriculum / competence)"):
                            st.json(ca_agent.status())
