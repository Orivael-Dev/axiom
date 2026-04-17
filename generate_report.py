"""
AXIOM Progress Report Generator  v2
Produces: AXIOM_Progress_Report_April2026.pdf
Uses Arial TTF via fpdf2 for full Unicode support.
"""
from fpdf import FPDF

FONT_DIR = "C:/Windows/Fonts/"

BRAND  = (15,  23,  42)
ACCENT = (37,  99, 235)
GREEN  = (22, 163,  74)
ORANGE = (234, 88,  12)
GRAY   = (100, 116, 139)
LIGHT  = (241, 245, 249)
WHITE  = (255, 255, 255)
RED    = (185,  28,  28)
PURP   = (147,  51, 234)
TEAL   = (6,   148, 162)

# ---- text sanitiser: Latin-1 safe fallbacks -------------------------
_SAFE = {
    '\u2014': '--', '\u2013': '-',  '\u2192': '->',
    '\u2022': '-',  '\u2019': "'",  '\u2018': "'",
    '\u201c': '"',  '\u201d': '"',  '\u25cb': 'o',
    '\u2705': '[OK]','\u25ba': '>',
}
def s(text):
    for c, r in _SAFE.items():
        text = text.replace(c, r)
    return text


class Report(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 18, 18)
        # Register as "UA" (Unicode Arial) -- avoids fpdf2's "Arial->Helvetica" alias
        self.add_font("UA",  "",  FONT_DIR + "arial.ttf")
        self.add_font("UA",  "B", FONT_DIR + "arialbd.ttf")
        self.add_font("UA",  "I", FONT_DIR + "ariali.ttf")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*BRAND)
        self.rect(0, 0, 210, 10, "F")
        self.set_y(2)
        self.set_font("UA", "B", 7)
        self.set_text_color(*WHITE)
        self.cell(0, 6, "AXIOM -- Autonomous Cognitive Architecture   |   Progress Report   |   April 17, 2026", align="C")
        self.set_text_color(0, 0, 0)
        self.set_y(14)

    def footer(self):
        self.set_y(-12)
        self.set_font("UA", "", 7)
        self.set_text_color(*GRAY)
        self.cell(0, 8, f"Page {self.page_no()} -- Confidential", align="C")

    # ---- primitives ------------------------------------------------
    def h1(self, txt):
        self.set_font("UA", "B", 20)
        self.set_text_color(*BRAND)
        self.multi_cell(0, 9, s(txt))
        self.ln(2)

    def h2(self, txt):
        self.ln(4)
        self.set_fill_color(*ACCENT)
        self.rect(18, self.get_y(), 174, 0.5, "F")
        self.ln(3)
        self.set_font("UA", "B", 13)
        self.set_text_color(*ACCENT)
        self.multi_cell(0, 7, s(txt))
        self.ln(1)
        self.set_text_color(0, 0, 0)

    def h3(self, txt):
        self.ln(3)
        self.set_font("UA", "B", 10)
        self.set_text_color(*BRAND)
        self.multi_cell(0, 6, s(txt))
        self.set_text_color(0, 0, 0)

    def body(self, txt):
        self.set_font("UA", "", 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5, s(txt))
        self.ln(1)

    def kv(self, key, val, key_w=58):
        self.set_font("UA", "B", 9)
        self.set_text_color(*BRAND)
        self.cell(key_w, 5.5, s(key))
        self.set_font("UA", "", 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(174 - key_w, 5.5, s(str(val)))

    def table_header(self, cols, widths):
        self.set_fill_color(*BRAND)
        self.set_text_color(*WHITE)
        self.set_font("UA", "B", 8)
        for col, w in zip(cols, widths):
            self.cell(w, 7, s(col), border=0, fill=True, align="C")
        self.ln()
        self.set_text_color(0, 0, 0)

    def table_row(self, vals, widths, fill=False, aligns=None):
        aligns = aligns or ["C"] * len(vals)
        self.set_font("UA", "", 8)
        if fill:
            self.set_fill_color(*LIGHT)
        for val, w, align in zip(vals, widths, aligns):
            self.cell(w, 6.5, s(str(val)), border=0, fill=fill, align=align)
        self.ln()


# ==================================================================
def build():
    pdf = Report()

    # ---- COVER -------------------------------------------------------
    pdf.add_page()
    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 72, "F")

    pdf.set_y(18)
    pdf.set_font("UA", "B", 32)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 14, "AXIOM", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("UA", "", 14)
    pdf.cell(0, 8, "Autonomous Cognitive Architecture", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("UA", "", 10)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 6, "Progress Report  --  April 17, 2026", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(0, 0, 0)
    # stat boxes
    stats = [
        ("192 / 192", "Benchmark v1.6",       GREEN),
        ("100%",       "Test Suite Pass Rate", GREEN),
        ("+89.5%",     "vs Raw Baseline",      ACCENT),
        ("15.9/16",    "Chaos Suite Score",    ORANGE),
    ]
    cell_w = 174 / len(stats)
    x0 = 18
    for val, label, col in stats:
        pdf.set_xy(x0, 80)
        pdf.set_fill_color(*col)
        pdf.rect(x0, 80, cell_w - 2, 24, "F")
        pdf.set_xy(x0, 83)
        pdf.set_font("UA", "B", 14)
        pdf.set_text_color(*WHITE)
        pdf.cell(cell_w - 2, 8, val, align="C")
        pdf.set_xy(x0, 91)
        pdf.set_font("UA", "", 7)
        pdf.cell(cell_w - 2, 5, label, align="C")
        x0 += cell_w
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(110)

    pdf.h2("Executive Summary")
    pdf.body(
        "AXIOM is a self-governing agent architecture built on a domain-specific language (DSL) "
        "that governs how AI agents reason, evolve, and constrain themselves. Over six development "
        "cycles (v1.0-v1.6) the system has grown from a basic prompt-evolution loop into a fully "
        "validated, self-describing, adversarially-resistant cognitive framework -- with a live "
        "real-time visual perception pipeline demonstrated on a Pacman game environment.\n\n"
        "The architecture achieves 100% pass rates across all correctness, chaos, and security test "
        "suites, and outperforms a raw language-model baseline by +89.5% on structured benchmarks. "
        "The most recent milestone (v1.6 + GameWatcher) validates the entire pipeline end-to-end: "
        "screen capture -> vision analysis -> HISTORY construct -> PatternAgent -> "
        "SkillBuilder -> live skill promotion."
    )

    # ---- ARCHITECTURE ------------------------------------------------
    pdf.add_page()
    pdf.h1("Architecture Overview")
    pdf.body(
        "AXIOM is structured as a layered stack. Each layer was built and validated before the next "
        "was added, giving strong regression guarantees at every level."
    )

    layers = [
        ("Layer 1 -- DSL Foundation (v1.0)",
         "A purpose-built .axiom file format drives all agent behaviour. Agents declare GOAL, PERSONA, "
         "CONSTRAINTS, PROCESS, FAILURE, OUTPUT, and SUCCESS blocks. A parser converts these files into "
         "structured prompt payloads. A prompt store (SHA-keyed, versioned) records all evolved states.",
         ACCENT),
        ("Layer 2 -- Contract Declarations (v1.2)",
         "Agents formally declare RECEIVES (expected inputs), EMITS (produced outputs), MUTATES "
         "(mutable fields), and CANNOT_MUTATE (constitutional boundaries). The parser enforces these "
         "contracts at load and save time, raising AxiomConstitutionalViolation on any protected-field "
         "mutation attempt.",
         GREEN),
        ("Layer 3 -- Self-Describing Language (v1.3)",
         "CONCEPT blocks extend the DSL natively. Shared concepts (UncertaintyBound, RewardGuard, "
         "AmbiguityResolution, RecoveryMode) are defined once in concepts.axiom and activated "
         "automatically via keyword detection. A three-phase validator (syntax -> purity -> "
         "semantic) issues badges per agent.",
         ORANGE),
        ("Layer 4 -- Conditional Flow & Routing (v1.4)",
         "WHEN blocks provide declarative context-sensitive concept activation. DELEGATES blocks wire "
         "agent-to-agent routing (e.g. Worker -> Rewriter on RecoveryMode). A git-style HISTORY diff "
         "log tracks all .axiom mutations with before/after field snapshots.",
         PURP),
        ("Layer 5 -- HISTORY Construct (v1.6)",
         "Agents can now retain, decay, promote, and forget observations across time. A rolling "
         "HistoryStore buffers frames and decisions, promotes recurring patterns to skills, and decays "
         "stale low-confidence observations. The GameWatcher pipeline exercises this layer live.",
         TEAL),
        ("Layer 6 -- Live Perception Pipeline (GameWatcher)",
         "A real-time screen-capture loop feeds frames to a vision LLM (meta/llama-3.2-90b-vision-instruct). "
         "Extracted game state flows into HistoryStore. AxiomPatternAgent confirms recurring patterns; "
         "AxiomSkillBuilder names and categorises them. Skills are ranked by survival > routing > scoring > "
         "general, decayed when unseen, conflict-resolved, and chained into decision sequences.",
         RED),
    ]

    for title, desc, col in layers:
        y = pdf.get_y()
        pdf.set_fill_color(*col)
        pdf.rect(18, y, 3, 26, "F")
        pdf.set_xy(24, y)
        pdf.set_font("UA", "B", 10)
        pdf.set_text_color(*col)
        pdf.multi_cell(170, 5.5, s(title))
        pdf.set_xy(24, pdf.get_y())
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(170, 4.8, s(desc))
        pdf.ln(4)

    # ---- VERSION HISTORY --------------------------------------------
    pdf.add_page()
    pdf.h1("Development History")

    versions = [
        ("v1.0", "Apr 14",  "DSL Foundation",
         ["parser.py -- reads .axiom files",
          "worker, evaluator, rewriter agents",
          "Overlay system (reward_analysis.axiom)",
          "Evolution loop (Worker -> Evaluator -> Rewriter)",
          "SHA-keyed versioned prompt store",
          "Streamlit UI (Prompt Evolution tab)"], GREEN),
        ("v1.1", "Apr 14",  "Agent Upgrades",
         ["FAILURE block added to worker.axiom",
          "OUTPUT block added to worker.axiom",
          "Sharper CONSTRAINTS across all agents"], GREEN),
        ("v1.2", "Apr 14",  "Contract Declarations",
         ["RECEIVES / EMITS / MUTATES / CANNOT_MUTATE blocks",
          "evaluator.axiom & rewriter.axiom fully upgraded",
          "Constitutional boundary enforcement at save layer"], GREEN),
        ("v1.3", "Apr 14",  "Self-Describing Language",
         ["CONCEPT construct with keyword activation",
          "Three-phase language validator (syntax / purity / semantic)",
          "concepts.axiom shared library",
          "v1.3 Test Suite: 39/39 (100%)",
          "Chaos Suite v1.0: 23/23 (100%), 15.8/16 avg"], GREEN),
        ("v1.4", "Apr 15-16", "Conditional Flow & Agent Routing",
         ["WHEN block -- context-sensitive concept activation",
          "DELEGATES block -- declarative agent routing",
          "Version history -- git-style .axiom diff log",
          "GAP-01 fix: MUTATES/CANNOT_MUTATE overlap detection",
          "GAP-02 fix: AxiomConstitutionalViolation at save layer"], GREEN),
        ("v1.5", "Apr 15",  "Security Test Suites",
         ["injection_suite.json -- 5 tests",
          "hijack_suite.json -- 5 tests",
          "sandbox_suite.json -- 5 tests",
          "Security: 15/15 PASS (100%)",
          "Full suite: 40/40 tests total"], GREEN),
        ("v1.6", "Apr 17",  "HISTORY Construct + GameWatcher",
         ["HISTORY block parser + compile_history()",
          "HistoryStore: retain / decay / promote / forget",
          "Validator Phase 4 -- HISTORY validation",
          "Benchmark v1.6: 192/192 (100%), zero regressions",
          "GameWatcher: 4 axiom agents validated",
          "Live Pacman: first skill promoted (80% confidence, survival)",
          "Skill decay, conflict resolution, skill chaining added"], GREEN),
    ]

    for ver, date, title, items, col in versions:
        y = pdf.get_y()
        pdf.set_fill_color(*LIGHT)
        pdf.rect(18, y, 174, 5.5, "F")
        pdf.set_xy(18, y)
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*ACCENT)
        pdf.cell(16, 5.5, ver)
        pdf.set_font("UA", "", 8)
        pdf.set_text_color(*GRAY)
        pdf.cell(28, 5.5, date)
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*BRAND)
        pdf.cell(130, 5.5, title)
        pdf.ln(6)
        for item in items:
            pdf.set_x(24)
            pdf.set_font("UA", "", 8)
            pdf.set_text_color(40, 40, 40)
            pdf.cell(4, 4.5, "-")
            pdf.multi_cell(168, 4.5, s(item))
        pdf.ln(3)

    # ---- BENCHMARKS -------------------------------------------------
    pdf.add_page()
    pdf.h1("Benchmark & Test Results")

    pdf.h2("Benchmark v1.0 -- AXIOM vs Raw Model")
    pdf.body(
        "Seven structured reasoning tasks were run against both a bare language model "
        "(meta/llama-3.3-70b-instruct, no governance) and the full AXIOM stack. "
        "Each test was scored out of 16 by the AXIOM evaluator agent."
    )

    cols   = ["Test", "Category", "Raw", "AXIOM", "Delta", "Winner"]
    widths = [14, 62, 18, 18, 18, 44]
    pdf.table_header(cols, widths)
    rows = [
        ("B1",   "Ambiguity",              "8/16",  "15/16", "+7",  "AXIOM [OK]"),
        ("B3",   "Missing Evidence",       "10/16", "16/16", "+6",  "AXIOM [OK]"),
        ("B5",   "Adversarial Resistance", "5/16",  "14/16", "+9",  "AXIOM [OK]"),
        ("B8",   "Tone Under Pressure",    "10/16", "15/16", "+5",  "AXIOM [OK]"),
        ("B9",   "Language Purity",        "9/16",  "16/16", "+7",  "AXIOM [OK]"),
        ("B12",  "Adversarial Resistance", "5/16",  "16/16", "+11", "AXIOM [OK]"),
        ("BCC1", "Constraint Compliance",  "10/16", "16/16", "+6",  "AXIOM [OK]"),
    ]
    for i, row in enumerate(rows):
        pdf.table_row(row, widths, fill=(i % 2 == 0),
                      aligns=["C", "L", "C", "C", "C", "C"])

    pdf.ln(4)
    summary_stats = [
        ("Raw Average",   "8.1 / 16",  RED),
        ("AXIOM Average", "15.4 / 16", GREEN),
        ("Improvement",   "+89.5%",    ACCENT),
        ("Win Rate",      "7 / 7",     GREEN),
    ]
    x0 = 18
    bw = 174 / len(summary_stats)
    base_y = pdf.get_y()
    for lbl, val, col in summary_stats:
        pdf.set_fill_color(*col)
        pdf.rect(x0, base_y, bw - 2, 18, "F")
        pdf.set_xy(x0, base_y + 2)
        pdf.set_font("UA", "B", 13)
        pdf.set_text_color(*WHITE)
        pdf.cell(bw - 2, 8, val, align="C")
        pdf.set_xy(x0, base_y + 10)
        pdf.set_font("UA", "", 7)
        pdf.cell(bw - 2, 5, lbl, align="C")
        x0 += bw
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(base_y + 22)

    pdf.h2("Chaos Suite v1.0 -- Resilience Testing")
    pdf.body(
        "23 adversarial, ambiguous, and edge-case tests measuring system resilience. "
        "Scored on a 0-16 scale. Level 4 (Self-Governing) requires >= 15.5/16 average."
    )
    chaos_rows = [
        ("C2", "Contradiction Handling",  "16/16", "16/16", "Perfect"),
        ("O2", "Adversarial Override",    "16/16", "16/16", "Perfect"),
        ("A1", "Ambiguity Resolution",    "15/16", "15/16", "By design (1 pt reserved)"),
        ("S1", "Security Injection",      "15/15", "--",    "Security suite (separate)"),
    ]
    cols2   = ["Suite", "Category", "v1.3 Score", "v1.5 Score", "Notes"]
    widths2 = [16, 56, 30, 30, 42]
    pdf.table_header(cols2, widths2)
    for i, row in enumerate(chaos_rows):
        pdf.table_row(row, widths2, fill=(i % 2 == 0),
                      aligns=["C", "L", "C", "C", "L"])

    pdf.ln(4)
    pdf.h3("Summary")
    pdf.kv("Total tests:", "23/23 passed (100%)")
    pdf.kv("Average score:", "15.9 / 16  (Level 4: Self-Governing)")
    pdf.kv("Security (v1.5):", "15/15 passed -- injection, hijack, sandbox suites")
    pdf.kv("Benchmark v1.6:", "192/192 (100%) -- HISTORY construct, zero regressions")

    # ---- COMPARATIVE CONTEXT ----------------------------------------
    pdf.add_page()
    pdf.h1("Comparative Context")
    pdf.body(
        "AXIOM is a research prototype. The comparisons below are architectural and conceptual -- "
        "they situate AXIOM within the broader AI-agent ecosystem to clarify what is novel and "
        "what trade-offs have been made consciously."
    )

    pdf.h2("How AXIOM Differs From Common Approaches")
    comparisons = [
        ("RAG / Tool-Use Agents  (LangChain, AutoGPT, CrewAI)",
         "Most agent frameworks focus on retrieval and tool routing. AXIOM's differentiator is "
         "governance: agents cannot mutate their own constitutional fields, cannot bypass declared "
         "constraints, and must emit outputs matching their declared schema. Tool-use agents have "
         "no equivalent enforcement layer.",
         "Governance depth", "AXIOM"),
        ("Prompt-Engineering Frameworks  (DSPy, TextGrad)",
         "DSPy and TextGrad optimise prompt weights via gradient-like feedback. AXIOM uses a "
         "structured DSL instead of numerical weights -- every agent behaviour is human-readable "
         "and auditable. Trade-off: AXIOM is less auto-optimisable but fully interpretable and "
         "constitutionally safe.",
         "Interpretability", "AXIOM"),
        ("Constitutional AI  (Anthropic)",
         "Anthropic's Constitutional AI bakes principles into training. AXIOM applies equivalent "
         "constraints at inference time via the CANNOT_MUTATE block -- no retraining required. "
         "Deployable on any base model. Weakness: runtime enforcement is bypassable in ways "
         "RLHF-trained constraints are not.",
         "Model-agnostic", "AXIOM"),
        ("AgentIQ / Multi-Agent Orchestration",
         "AgentIQ and similar frameworks handle multi-agent composition graphs. AXIOM's DELEGATES "
         "construct provides lightweight declarative routing between agents. AXIOM does not yet "
         "support dynamic agent spawning or shared memory graphs -- planned for v1.7.",
         "Simplicity now", "AgentIQ (at scale)"),
        ("LLM + Feedback Loop  (RLHF / PPO)",
         "Reinforcement-learning approaches like PPO require thousands of game trajectories to "
         "learn stable policies. AXIOM's GameWatcher promoted its first skill from 20 frames "
         "(~20 seconds of play). Trade-off: AXIOM skills are heuristic, not optimally trained.",
         "Sample efficiency", "AXIOM"),
    ]

    for title, desc, edge_label, edge_winner in comparisons:
        y = pdf.get_y()
        pdf.set_fill_color(*LIGHT)
        pdf.rect(18, y, 174, 6, "F")
        pdf.set_xy(18, y + 0.5)
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*BRAND)
        pdf.cell(120, 5.5, s(title))
        pdf.set_font("UA", "I", 8)
        pdf.set_text_color(*GRAY)
        pdf.cell(54, 5.5, f"Edge: {s(edge_label)}", align="R")
        pdf.ln(7)
        pdf.set_x(22)
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(170, 4.8, s(desc))
        pdf.set_x(22)
        pdf.set_font("UA", "B", 8)
        pdf.set_text_color(*GREEN if edge_winner == "AXIOM" else GRAY)
        pdf.cell(0, 5, f"-> Advantage: {s(edge_winner)}")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(5)

    # ---- GAMEWATCHER ------------------------------------------------
    pdf.add_page()
    pdf.h1("GameWatcher Pipeline")
    pdf.body(
        "The GameWatcher is a live demonstration of the full AXIOM stack applied to a visual game "
        "environment (Pacman / Google Doodle). It shows that the architecture generalises beyond "
        "text tasks to real-time sensory input, pattern recognition, and skill formation."
    )

    pdf.h2("Pipeline Architecture")
    pipeline_steps = [
        ("1. Screen Capture",
         "mss captures the game window at 1 fps. Frames are JPEG-compressed and base64-encoded "
         "for the vision API."),
        ("2. Vision Analysis",
         "meta/llama-3.2-90b-vision-instruct extracts structured game state: player position, "
         "direction, threats, opportunities, recommended move, confidence. Retry logic handles "
         "empty responses (up to 2 retries per frame)."),
        ("3. HISTORY Store",
         "GameState is wrapped in an Observation and pushed to HistoryStore. The rolling buffer "
         "retains the last 50 frames and 10 decisions. Low-confidence observations decay after 20 frames."),
        ("4. PatternAgent  (every 10 good frames)",
         "AxiomPatternAgent sends recent frames to a text LLM (meta/llama-3.3-70b-instruct) for "
         "pattern analysis. Patterns confirmed >= 2 times are flagged for promotion. Skill decay "
         "runs every cycle: unseen patterns lose 0.12 confidence/cycle; stale skills are flagged "
         "at < 0.20 confidence."),
        ("5. SkillBuilder  (on promotion)",
         "AxiomSkillBuilder receives a confirmed pattern and produces a named skill with: "
         "skill_name, trigger, action, outcome, confidence (0-1), and "
         "category (survival / routing / scoring / general)."),
        ("6. Conflict Resolution",
         "When multiple skills match the current threat context, resolve_conflicts() ranks them by "
         "CATEGORY_PRIORITY x confidence: survival(4) > routing(3) > scoring(2) > general(1). "
         "Same-trigger conflicts are resolved by keeping the highest-weight skill and logging the conflict."),
        ("7. Skill Chaining",
         "build_chains() links skills where one skill's outcome keywords overlap the next skill's "
         "trigger keywords. Chains up to length 4 are ranked by average weight. get_best_chain() "
         "returns the highest-weight chain matching the current context."),
    ]

    for step, desc in pipeline_steps:
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*ACCENT)
        pdf.cell(0, 5.5, s(step), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(30, 30, 30)
        pdf.set_x(24)
        pdf.multi_cell(168, 4.8, s(desc))
        pdf.ln(2)

    pdf.h2("Live Session Results  (April 17, 2026)")
    session_data = [
        ("Total frames captured",   "29"),
        ("Good frames (vision OK)", "~21  (72% success rate after retries)"),
        ("PatternAgent cycles",     "2  (frame 10, frame 20)"),
        ("Patterns tracked",        "3"),
        ("Skills promoted",         "1"),
        ("First skill ID",          "ghost_approaching_left"),
        ("Skill name",              '"Avoid Ghosts from Left and Top"'),
        ("Confidence",              "80%"),
        ("Category",                "survival"),
        ("History saved/reloaded",  "Yes -- reloaded on restart (1 skill, 1 pattern)"),
    ]
    for k, v in session_data:
        pdf.kv(k + ":", v)

    pdf.ln(3)
    pdf.h2("Four GameWatcher Axiom Agents")
    agents = [
        ("game_watcher.axiom",      "Top-level orchestrator. Declares DELEGATES to PatternAgent and SkillBuilder."),
        ("pattern_agent.axiom",     "Receives recent frames, confirms recurring patterns, returns structured Pattern list."),
        ("skill_builder.axiom",     "Receives a confirmed pattern, outputs a named skill with trigger/action/outcome/category."),
        ("controller_mapper.axiom", "Maps recommended moves to controller inputs. Extensible to keyboard/gamepad output."),
    ]
    for name, desc in agents:
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*BRAND)
        pdf.cell(62, 5.5, name)
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(110, 5.5, s(desc))

    # ---- GAPS & ROADMAP --------------------------------------------
    pdf.add_page()
    pdf.h1("Known Gaps & Roadmap")

    pdf.h2("Open Items (v1.4 incomplete)")
    open_items = [
        ("Snapshot / restore",
         "Save best .axiom state; restore from snapshot if next evolution run degrades score. "
         "Partially designed -- no code written."),
        ("Export bundle",
         "Package a complete evolved run (worker.axiom + prompts + logs) as a .axiom.bundle zip."),
        ("Version history -- multi-agent coverage",
         "History diff logging works for single agents; extended edge cases across multiple agents "
         "not yet tested."),
        ("WHEN / DELEGATES extended edge cases",
         "Core implementation complete and validated; adversarial edge cases not yet covered by tests."),
    ]
    for title, desc in open_items:
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*ORANGE)
        pdf.cell(4, 5.5, "o")
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 5.5, s(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(22)
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(170, 4.8, s(desc))
        pdf.ln(2)

    pdf.h2("v1.7+ Roadmap")
    roadmap = [
        ("Agent Spawning",
         "Worker can spawn sub-agents for parallelisable subtasks. Each sub-agent inherits "
         "constitutional constraints from the spawner."),
        ("Shared Memory Graph",
         "Evolved prompts from one agent feed back as starting points for related agents. "
         "Enables cross-agent knowledge transfer without explicit wiring."),
        ("Multi-Agent Composition  (AgentIQ integration)",
         "Connect AXIOM's DELEGATES routing to a full composition graph engine "
         "for large-scale multi-agent deployments."),
        ("AXIOM Runtime Package",
         "Package axiom/ + axiom_files/ as an installable Python package "
         "(pip install axiom-runtime) with a CLI entry point and optional Streamlit UI."),
        ("GameWatcher Controller Output",
         "Connect controller_mapper.axiom to actual keyboard / gamepad output. "
         "Close the loop: observe -> reason -> act -> observe."),
        ("Continuous Skill Evaluation",
         "After each PatternAgent cycle, evaluate promoted skills against actual game outcomes. "
         "Skills that correlate with score increases gain confidence; others decay faster."),
    ]
    for title, desc in roadmap:
        pdf.set_font("UA", "B", 9)
        pdf.set_text_color(*ACCENT)
        pdf.cell(4, 5.5, ">")
        pdf.cell(0, 5.5, s(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(22)
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(170, 4.8, s(desc))
        pdf.ln(2)

    # ---- SCORECARD -------------------------------------------------
    pdf.add_page()
    pdf.h1("Summary Scorecard")

    pdf.h2("Test Coverage Matrix")
    cols3   = ["Suite", "Version", "Tests", "Passed", "Score", "Level"]
    widths3 = [52, 20, 22, 22, 30, 28]
    pdf.table_header(cols3, widths3)
    scorecard = [
        ("v1.3 Correctness Suite",   "v1.3", "39",  "39",  "39/39",    "Perfect"),
        ("Chaos Suite v1.0",         "v1.3", "23",  "23",  "15.9/16",  "Self-Governing"),
        ("Security -- Injection",    "v1.5", "5",   "5",   "5/5",      "Perfect"),
        ("Security -- Hijack",       "v1.5", "5",   "5",   "5/5",      "Perfect"),
        ("Security -- Sandbox",      "v1.5", "5",   "5",   "5/5",      "Perfect"),
        ("Benchmark v1.0 (AXIOM)",   "v1.0", "7",   "7",   "15.4/16",  "+89.5% vs raw"),
        ("Benchmark v1.6 (HISTORY)", "v1.6", "192", "192", "192/192",  "Zero regressions"),
    ]
    for i, row in enumerate(scorecard):
        pdf.table_row(row, widths3, fill=(i % 2 == 0),
                      aligns=["L", "C", "C", "C", "C", "L"])

    pdf.ln(6)
    pdf.h2("What Has Been Built -- One Line Per Milestone")
    milestones = [
        "DSL with parser, prompt store, and Streamlit UI",
        "Constitutional contracts (RECEIVES / EMITS / MUTATES / CANNOT_MUTATE)",
        "Self-describing CONCEPT blocks with three-phase validator",
        "WHEN conditional activation and DELEGATES agent routing",
        "Git-style version history for .axiom mutations",
        "Security suites: injection, hijack, sandbox (15/15)",
        "HISTORY construct: retain / decay / promote / forget",
        "GameWatcher: real-time screen capture -> vision -> pattern -> skill pipeline",
        "Skill decay (confidence -0.12/cycle), conflict resolution (survival > routing > scoring), chaining",
        'Live first skill promoted: "Avoid Ghosts from Left and Top" (80% confidence, survival)',
    ]
    for i, m in enumerate(milestones, 1):
        pdf.set_font("UA", "B", 8)
        pdf.set_text_color(*ACCENT)
        pdf.cell(8, 5.5, f"{i:02d}.")
        pdf.set_font("UA", "", 8.5)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(166, 5.5, s(m))

    pdf.ln(6)
    base_y2 = pdf.get_y()
    pdf.set_fill_color(*BRAND)
    pdf.rect(18, base_y2, 174, 20, "F")
    pdf.set_xy(18, base_y2 + 4)
    pdf.set_font("UA", "B", 11)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 6,
             "AXIOM: from blank .axiom file to a live self-governing perception agent",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("UA", "", 9)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 5,
             "Built in 3 days  |  100% test pass rate  |  +89.5% over raw model baseline",
             align="C")
    pdf.set_text_color(0, 0, 0)

    return pdf


if __name__ == "__main__":
    out = "i:/vsCode/promt-agent/AXIOM_Progress_Report_April2026.pdf"
    pdf = build()
    pdf.output(out)
    print(f"PDF written: {out}")
