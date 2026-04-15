"""
gen_spec_pdf.py
Generates: axiom_spec_v1.3.pdf
Axiom Language Specification v1.3 -- CONCEPT Construct and Language Validator
"""
from fpdf import FPDF, XPos, YPos

# ?? Colour palette ????????????????????????????????????????????????????????????
BLACK   = (15, 15, 15)
WHITE   = (255, 255, 255)
TEAL    = (0, 128, 128)
TEAL_LT = (220, 240, 240)
GREY    = (90, 90, 90)
GREY_LT = (245, 245, 245)
RED_LT  = (255, 235, 235)
RED     = (180, 0, 0)
GREEN   = (0, 120, 60)
GREEN_LT= (225, 245, 230)
YELLOW_LT=(255, 250, 220)
YELLOW  = (140, 110, 0)


class SpecPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)
        self._page_label = ""

    # ?? Header / Footer ???????????????????????????????????????????????????????
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GREY)
        self.cell(0, 6, "Axiom Language Specification v1.3", align="L")
        self.set_x(-40)
        self.cell(0, 6, f"Page {self.page_no()}", align="R")
        self.ln(2)
        self.set_draw_color(*TEAL)
        self.set_line_width(0.3)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*GREY)
        self.cell(0, 6,
                  "Copyright (c) 2026 Antonio Roberts -- AXIOM Project. All rights reserved.",
                  align="C")

    # ?? Cover page ????????????????????????????????????????????????????????????
    def cover(self):
        self.add_page()
        # Background band
        self.set_fill_color(*TEAL)
        self.rect(0, 0, 210, 80, style="F")

        self.set_y(18)
        self.set_font("Helvetica", "B", 28)
        self.set_text_color(*WHITE)
        self.cell(0, 12, "AXIOM", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 13)
        self.cell(0, 8, "Language Specification", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 10, "Version 1.3", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        self.set_y(85)
        self.set_font("Helvetica", "I", 11)
        self.set_text_color(*GREY)
        self.cell(0, 8, "CONCEPT Construct  .  Language Validator", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        self.ln(8)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        meta = [
            ("Release date",   "April 15, 2026"),
            ("Project",        "AXIOM -- An AI-Native Language for Self-Evolving Intelligence"),
            ("Author",         "Antonio Roberts"),
            ("Previous version", "v1.2 -- evaluator.axiom & rewriter.axiom upgrades"),
        ]
        for label, value in meta:
            self.set_x(40)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*TEAL)
            self.cell(50, 6, label + ":", new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*BLACK)
            self.cell(0, 6, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Divider
        self.ln(6)
        self.set_draw_color(*TEAL)
        self.set_line_width(0.5)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(6)

        # Abstract box
        self.set_fill_color(*TEAL_LT)
        self.set_draw_color(*TEAL)
        self.set_line_width(0.3)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*TEAL)
        self.cell(0, 7, "Abstract", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True, border=0)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        abstract = (
            "Version 1.3 introduces two foundational capabilities to the Axiom language. "
            "First, the CONCEPT construct enables language-native extensibility by defining "
            "structured, named ideas with PURPOSE, APPLIES WHEN, REQUIRES, and EFFECT fields. "
            "Concepts are automatically activated per-task based on keyword overlap, injecting "
            "relevant constraints into the agent's system prompt without modifying its base definition. "
            "Second, the Language Validator enforces structural, purity, and semantic constraints "
            "on any .axiom file through three validation phases, producing a STATUS (valid / warning / invalid) "
            "with actionable ISSUES and SUGGESTIONS. Together these features establish Axiom as a "
            "self-describing, self-enforcing AI-native language."
        )
        self.multi_cell(0, 6, abstract)

    # ?? Helpers ???????????????????????????????????????????????????????????????
    def h1(self, text):
        self.ln(4)
        self.set_fill_color(*TEAL)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 9, f"  {text}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        self.set_text_color(*BLACK)
        self.ln(2)

    def h2(self, text):
        self.ln(3)
        self.set_text_color(*TEAL)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*TEAL)
        self.set_line_width(0.2)
        self.line(20, self.get_y(), 190, self.get_y())
        self.set_text_color(*BLACK)
        self.ln(2)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def bullet(self, items, indent=6):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        for item in items:
            self.set_x(20 + indent)
            self.cell(4, 6, "-", new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.multi_cell(0, 6, item)

    def numbered(self, items, indent=6):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        for i, item in enumerate(items, 1):
            self.set_x(20 + indent)
            self.set_font("Helvetica", "B", 10)
            self.cell(6, 6, f"{i}.", new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.set_font("Helvetica", "", 10)
            self.multi_cell(0, 6, item)

    def code_block(self, text, bg=GREY_LT):
        self.ln(1)
        self.set_fill_color(*bg)
        self.set_draw_color(*GREY)
        self.set_line_width(0.2)
        self.set_font("Courier", "", 9)
        self.set_text_color(*BLACK)
        lines = text.strip().split("\n")
        # Draw background rect first (approximate height)
        line_h = 5
        total_h = len(lines) * line_h + 4
        x, y = self.get_x(), self.get_y()
        self.rect(20, y, 170, total_h, style="FD")
        self.set_xy(22, y + 2)
        for line in lines:
            self.set_x(22)
            self.cell(0, line_h, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)

    def kv_table(self, rows, col1_w=50):
        """Simple two-column key-value table."""
        self.set_font("Helvetica", "", 10)
        for label, value in rows:
            self.set_fill_color(*TEAL_LT)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*TEAL)
            self.cell(col1_w, 7, f"  {label}", fill=True, border=1)
            self.set_fill_color(*WHITE)
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*BLACK)
            self.cell(0, 7, f"  {value}", fill=False, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)

    def status_badge(self, status, count=None):
        colors = {
            "valid":   (GREEN,    GREEN_LT,  "OK  VALID"),
            "warning": (YELLOW,   YELLOW_LT, "!  WARNING"),
            "invalid": (RED,      RED_LT,    "X  INVALID"),
        }
        fg, bg, label = colors.get(status, (GREY, GREY_LT, status.upper()))
        if count is not None:
            label += f"  ({count} issue{'s' if count != 1 else ''})"
        self.set_fill_color(*bg)
        self.set_draw_color(*fg)
        self.set_line_width(0.4)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*fg)
        self.cell(60, 8, f"  {label}", fill=True, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*BLACK)
        self.ln(2)

    def two_col_concept(self, name, purpose, applies, requires, effect):
        """Render a CONCEPT definition in a styled box."""
        self.ln(2)
        self.set_fill_color(*TEAL)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 7, f"  CONCEPT  {name}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        rows = [
            ("PURPOSE",      purpose),
            ("APPLIES WHEN", applies),
            ("REQUIRES",     requires),
            ("EFFECT",       effect),
        ]
        for key, val in rows:
            self.set_fill_color(*TEAL_LT)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*TEAL)
            self.cell(38, 6, f"  {key}", fill=True, border=1)
            self.set_fill_color(*WHITE)
            self.set_font("Helvetica", "", 9)
            self.set_text_color(*BLACK)
            self.multi_cell(132, 6, f"  {val}", border=1)
        self.ln(3)


# ?????????????????????????????????????????????????????????????????????????????
def build():
    pdf = SpecPDF()

    # ?? Cover ?????????????????????????????????????????????????????????????????
    pdf.cover()

    # ?? Page 2: Overview + Version History ???????????????????????????????????
    pdf.add_page()
    pdf.h1("1  Overview")
    pdf.body(
        "Version 1.3 extends the Axiom language along two independent but complementary axes. "
        "The CONCEPT construct provides a structured mechanism for adding new native ideas to "
        "the language without modifying agent definitions or overloading existing constraints. "
        "The Language Validator enforces the purity and structure of all .axiom files, catching "
        "drift toward external programming paradigms and flagging vague or procedural language "
        "before it reaches the model."
    )

    pdf.numbered([
        "Introduces the CONCEPT construct for language-native extensibility.",
        "Adds a three-phase validation layer (syntax, purity, semantic).",
        "Separates language growth (CONCEPT) from constraint enforcement (Validator).",
        "Provides a shared concept library (concepts.axiom) seeded with UncertaintyBound and RewardGuard.",
        "Wires validation output into both the CLI (run_axiom.py) and the Streamlit UI (ui.py).",
    ])

    pdf.h2("Version History")
    pdf.kv_table([
        ("v1.3  (current)", "CONCEPT construct, Language Validator, concepts.axiom seed library"),
        ("v1.2",            "evaluator.axiom + rewriter.axiom upgrades -- GOAL, PROCESS, FAILURE, SUCCESS, sharper CONSTRAINTS"),
        ("v1.1",            "worker.axiom upgrades -- FAILURE block, OUTPUT block, sharper CONSTRAINTS"),
        ("v1.0",            "DSL v0 -- parser.py, worker/evaluator/rewriter.axiom, overlay system"),
    ], col1_w=42)

    # ?? Page 3: CONCEPT Construct ?????????????????????????????????????????????
    pdf.add_page()
    pdf.h1("2  CONCEPT Construct")

    pdf.body(
        "The CONCEPT block is a new top-level construct in the Axiom language. It defines a "
        "named, structured idea that can be injected into an agent's system prompt when the "
        "task context matches. Concepts live in any .axiom file or in the shared concepts.axiom "
        "library and are activated automatically -- agents never need to be modified to benefit "
        "from new concepts."
    )

    pdf.h2("2.1  Syntax")
    pdf.code_block(
        "CONCEPT <Name>\n"
        "PURPOSE    <one-line description of what this concept expresses>\n"
        "APPLIES WHEN <space-separated keywords triggering this concept>\n"
        "REQUIRES   <what the agent must include when this concept is active>\n"
        "EFFECT     <how the agent's behaviour changes>"
    )

    pdf.h2("2.2  Fields")
    pdf.kv_table([
        ("PURPOSE",      "Declares the intent of this concept in one line."),
        ("APPLIES WHEN", "Space-separated keywords. If any appear in the task, the concept activates."),
        ("REQUIRES",     "The specific output requirement the agent must satisfy."),
        ("EFFECT",       "The behavioural constraint applied to the agent's response."),
    ])

    pdf.h2("2.3  Seed Concepts (concepts.axiom)")

    pdf.two_col_concept(
        name    = "UncertaintyBound",
        purpose = "Express uncertainty in measurable form",
        applies = "evidence  incomplete  probabilistic  confidence  unknown  estimate",
        requires= "Confidence range or explicit limitation statement",
        effect  = "Forces explicit uncertainty expression -- agent must state bounds, not just conclusions",
    )

    pdf.two_col_concept(
        name    = "RewardGuard",
        purpose = "Prevent reward hacking or proxy gaming",
        applies = "reward  optimization  metric  objective  incentive  maximize  minimize  utility  proxy",
        requires= "Objective metric, side-effect metric, and abuse-case check",
        effect  = "Rejects harmful optimization strategies -- agent must verify the optimized metric "
                  "cannot be gamed without achieving the real goal",
    )

    pdf.h2("2.4  Runtime Behaviour")
    pdf.numbered([
        "get_prompt_with_concepts(agent_name, task) loads the agent .axiom and the shared concepts.axiom library.",
        "detect_concepts(task, parsed) tokenises each concept's APPLIES WHEN field and checks for overlap "
        "with the task string (min token length 4 to skip stop words).",
        "Only matching concepts are retained; non-matching concepts are removed before rendering.",
        "Active concepts are appended to the system prompt under an 'Active Concepts' section.",
        "Concepts that do not match the task are completely invisible to the model -- zero noise.",
    ])

    pdf.h2("2.5  Example -- Active Concept in System Prompt")
    pdf.body("Task: \"Design a reward function that maximizes user engagement\"")
    pdf.code_block(
        "Active Concepts:\n"
        "  - CONCEPT RewardGuard: Rejects harmful optimization strategies --\n"
        "    agent must verify the optimized metric cannot be gamed without\n"
        "    achieving the real goal\n"
        "    (applies when: reward optimization metric objective incentive\n"
        "    maximize minimize utility proxy)"
    )
    pdf.body("Task: \"Write a summary of this article\"  ->  no active concepts (prompt unchanged)")

    # ?? Page 4: Language Validator ?????????????????????????????????????????????
    pdf.add_page()
    pdf.h1("3  Language Validator")

    pdf.body(
        "The Language Validator (axiom_files/validator.py) inspects any parsed .axiom dict and "
        "returns a structured result indicating whether the definition is safe to use. "
        "Validation is always non-blocking -- it produces warnings and errors for the operator "
        "to review, but never halts execution."
    )

    pdf.h2("3.1  Output Schema")
    pdf.code_block(
        "{\n"
        '  "status":      "valid" | "warning" | "invalid",\n'
        '  "issues":      [\n'
        '    {\n'
        '      "phase":   "syntax" | "purity" | "semantic",\n'
        '      "level":   "error" | "warning",\n'
        '      "field":   "<field name>",\n'
        '      "message": "<description>"\n'
        "    }\n"
        "  ],\n"
        '  "suggestions": ["<actionable correction>", ...]\n'
        "}"
    )

    pdf.h2("3.2  Phase 1 -- Syntax Validation")
    pdf.bullet([
        "AGENT field present and non-empty.",
        "At least one of PURPOSE or GOAL must be defined.",
        "VERSION matches the pattern N.N (e.g. 1.3).",
        "SUCCESS weights sum to 1.0 +/- 0.01 tolerance.",
        "MUTATES and CANNOT_MUTATE reference only known Axiom fields.",
        "Every CONCEPT block has all four sub-fields (PURPOSE, APPLIES WHEN, REQUIRES, EFFECT) populated.",
    ])

    pdf.h2("3.3  Phase 2 -- Purity Validation")
    pdf.body(
        "Scans every string value in the parsed dict for patterns that indicate drift into "
        "external programming paradigms. Any match is an error."
    )
    patterns = [
        ("def <name>(", "Python function definition"),
        ("class <name>:", "Python class definition"),
        ("for <x> in", "Procedural for-loop"),
        ("while ...:", "Procedural while-loop"),
        ("import <module>", "Import statement"),
        ("return", "return keyword"),
        ("print(...)", "print() call"),
        (":=", "Walrus operator"),
        ("lambda", "Lambda expression"),
    ]
    pdf.kv_table([(p, d) for p, d in patterns], col1_w=55)

    pdf.h2("3.4  Phase 3 -- Semantic Validation")
    pdf.bullet([
        "Vague qualifier detection in CONSTRAINTS and RULES: flags entries containing "
        "'try to', 'consider', 'if possible', 'when needed', 'appropriate', 'reasonable', "
        "'as needed', 'maybe', 'perhaps', 'generally', 'typically', 'usually' -- unless the "
        "entry also contains a numeric threshold (e.g. '>= 7 points'). Level: warning.",
        "Procedural drift in PROCESS: entries containing 'if', 'else', 'while', 'loop', "
        "or 'return' violate the declarative requirement. Level: error.",
        "Constraint/rule overlap: exact duplicate text appearing in both CONSTRAINT and RULES "
        "sections. Level: warning.",
    ])

    pdf.h2("3.5  Status Determination")
    pdf.body(
        "The overall STATUS is set to 'invalid' if any issue has level 'error'. "
        "If all issues are 'warning', STATUS is 'warning'. "
        "If there are no issues, STATUS is 'valid'."
    )
    pdf.status_badge("valid")
    pdf.status_badge("warning", count=1)
    pdf.status_badge("invalid", count=3)

    # ?? Page 5: Integration ????????????????????????????????????????????????????
    pdf.add_page()
    pdf.h1("4  Integration")

    pdf.h2("4.1  CLI -- run_axiom.py")
    pdf.body(
        "At startup, after loading each agent's system prompt preview, run_axiom.py calls "
        "validate_file() and prints a coloured STATUS badge. Issues are listed inline. "
        "Execution continues regardless of status."
    )
    pdf.code_block(
        "WORKER.axiom   -> system prompt preview: ...\n"
        "  Validator: ! WARNING  (1 issue)\n"
        "    [warn] [semantic] rules: Vague qualifier 'appropriate' ...\n\n"
        "EVALUATOR.axiom -> system prompt preview: ...\n"
        "  Validator: OK VALID\n\n"
        "REWRITER.axiom  -> system prompt preview: ...\n"
        "  Validator: OK VALID"
    )

    pdf.h2("4.2  Streamlit UI -- DSL Tab")
    pdf.body(
        "In the 'Current .axiom definitions' expander, each agent file now shows a "
        "colour-coded badge (? VALID / ? WARNING / ? INVALID) next to its name. "
        "When issues exist, a collapsible 'N validator issue(s)' expander shows the "
        "full issue list and suggestions."
    )

    pdf.h2("4.3  Public API")
    pdf.kv_table([
        ("validate(parsed)",                "Validate a pre-loaded parsed dict. Returns status/issues/suggestions."),
        ("validate_file(agent_name)",        "Load a .axiom file by agent name and validate it."),
        ("detect_concepts(task, parsed)",    "Return concept names whose APPLIES WHEN matches the task string."),
        ("get_prompt_with_concepts(a, task)","Load agent + concept library, filter by task, return system prompt."),
        ("get_prompt(agent_name)",           "Existing: load .axiom and return full system prompt."),
        ("get_prompt_with_overlays(a, lst)", "Existing: load base + overlay files, merge, return system prompt."),
    ], col1_w=72)

    # ?? Page 6: Validation Rules + File Reference ?????????????????????????????
    pdf.add_page()
    pdf.h1("5  Validation Rules Reference")

    pdf.body("The following table lists all validation rules in v1.3.")

    rules = [
        ("AGENT present",            "syntax",   "error",   "AGENT field missing or empty."),
        ("PURPOSE or GOAL",          "syntax",   "error",   "Neither PURPOSE nor GOAL defined."),
        ("VERSION format",           "syntax",   "warning", "VERSION does not match N.N pattern."),
        ("SUCCESS sums to 1.0",      "syntax",   "warning", "SUCCESS weights sum != 1.0 (+/-0.01)."),
        ("Valid MUTATES fields",      "syntax",   "warning", "MUTATES/CANNOT_MUTATE refs unknown field."),
        ("CONCEPT completeness",      "syntax",   "error",   "CONCEPT missing one or more of 4 sub-fields."),
        ("No def/class/for/while",    "purity",   "error",   "External code pattern found in any field."),
        ("No import/return/print",    "purity",   "error",   "Import, return, or print() found in any field."),
        ("No := or lambda",           "purity",   "error",   "Walrus operator or lambda found."),
        ("No vague qualifiers",       "semantic", "warning", "Vague term without numeric threshold in CONSTRAINTS/RULES."),
        ("Declarative PROCESS",       "semantic", "error",   "Conditional or loop construct in PROCESS."),
        ("No CONSTRAINT/RULE overlap","semantic", "warning", "Exact duplicate in both CONSTRAINT and RULES."),
    ]

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*TEAL)
    pdf.set_text_color(*WHITE)
    for header, w in [("Rule", 52), ("Phase", 22), ("Level", 22), ("Trigger", 74)]:
        pdf.cell(w, 7, f"  {header}", fill=True, border=1)
    pdf.ln()

    for i, (rule, phase, level, trigger) in enumerate(rules):
        bg = GREY_LT if i % 2 == 0 else WHITE
        pdf.set_fill_color(*bg)
        level_color = RED if level == "error" else YELLOW
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*BLACK)
        pdf.cell(52, 6, f"  {rule}", fill=True, border=1)
        pdf.cell(22, 6, f"  {phase}", fill=True, border=1)
        pdf.set_text_color(*level_color)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(22, 6, f"  {level}", fill=True, border=1)
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(74, 6, f"  {trigger}", fill=True, border=1)
        pdf.ln()
    pdf.ln(4)

    pdf.h1("6  File Reference")
    pdf.kv_table([
        ("axiom_files/parser.py",    "Extended: CONCEPT parse/save/render, detect_concepts(), get_prompt_with_concepts()"),
        ("axiom_files/validator.py", "New: Language Validator -- validate(), validate_file()"),
        ("axiom_files/concepts.axiom","New: Shared concept library -- UncertaintyBound, RewardGuard"),
        ("run_axiom.py",             "Updated: validator badges printed at startup per agent"),
        ("ui.py",                    "Updated: validator badges + issue expander in DSL tab"),
        ("CHECKLIST.txt",            "Updated: all v1.3 items marked done"),
        ("axiom_files/worker.axiom",  "v1.2 -- upgraded FAILURE, OUTPUT, sharper CONSTRAINTS"),
        ("axiom_files/evaluator.axiom","v1.2 -- GOAL, RULES, PROCESS, FAILURE, SUCCESS"),
        ("axiom_files/rewriter.axiom", "v1.2 -- GOAL, PROCESS, CHECK, FAILURE, OUTPUT, SUCCESS"),
    ])

    # ?? Page 7: Testing + Conclusion ??????????????????????????????????????????
    pdf.add_page()
    pdf.h1("7  Testing")

    pdf.h2("7.1  Parser / CONCEPT")
    pdf.numbered([
        "python axiom_files/parser.py  -- prints all 3 agent prompts. Confirms no errors from CONCEPT additions.",
        "python -c \"from axiom_files.parser import load_axiom; p=load_axiom('concepts'); "
        "print([c['name'] for c in p['concepts']])\"  -- confirms ['UncertaintyBound','RewardGuard'].",
        "Call get_prompt_with_concepts('worker', 'reward function') -- confirm RewardGuard appears in Active Concepts.",
        "Call get_prompt_with_concepts('worker', 'Write a summary') -- confirm no Active Concepts section.",
    ])

    pdf.h2("7.2  Validator")
    pdf.numbered([
        "python axiom_files/validator.py  -- prints STATUS for worker (! WARNING), evaluator (? VALID), "
        "rewriter (? VALID).",
        "Inject 'def foo():' into any constraint, re-run -- confirm ? INVALID with purity error.",
        "Inject 'try to be helpful' into a rule, re-run -- confirm ! WARNING with semantic warning.",
        "Inject 'if task is unclear: ask' into PROCESS, re-run -- confirm ? INVALID with procedural-drift error.",
        "Restore file after each test.",
    ])

    pdf.h2("7.3  Integration")
    pdf.numbered([
        "python run_axiom.py  -- coloured validator badges appear before the loop prompt.",
        "streamlit run ui.py  -- DSL tab shows ? WARNING for worker.axiom, ? VALID for evaluator and rewriter.",
    ])

    pdf.h1("8  Conclusion")
    pdf.body(
        "Axiom v1.3 delivers two pillars of a self-describing AI language. "
        "The CONCEPT construct gives the language a native mechanism for growth -- new ideas can be "
        "added to the concept library without touching any agent definition, and they activate "
        "automatically when relevant. "
        "The Language Validator gives the language a native mechanism for self-enforcement -- "
        "structural errors, purity violations, and vague language are caught before they reach "
        "the model, maintaining the integrity of the DSL as it evolves. "
        "Together they form the foundation described in the v1.3 specification: "
        "a language that can grow and a language that can police itself."
    )

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(0, 6,
        "Next planned work: .axiom version history (git-style diff log), "
        "export best run as standalone .axiom bundle, evaluator mutation loop, "
        "snapshot/restore at convergence."
    )

    # ?? Save ??????????????????????????????????????????????????????????????????
    out = "axiom_spec_v1.3.pdf"
    pdf.output(out)
    print(f"OK Generated: {out}")


if __name__ == "__main__":
    build()
