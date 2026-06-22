"""Axiom Blueprint Compiler — `.axiom` v1.4 → StateMachineIR pipeline.

Compilation stages:
    source (.axiom text)
        → Lexer          : token stream with line/col
        → Parser         : BlueprintAST (typed, frozen nodes)
        → Analyzer       : DiagnosticReport (dead states, budget, transitions)
        → Optimizer      : 4 passes, token-budget-aware
        → Codegen        : StateMachineIR (JSON-serialisable, runtime-ready)

The StateMachineIR feeds BlueprintStateMachine in axiom_blueprint.py via
``BlueprintStateMachine.from_ir(ir)``.

Layer mapping:
  Lexer / Parser / Analyzer → Layer 0 (Intent Kernel) — compile-time policy
  Optimizer                 → Layer 2 (Memory + EventToken Cache) — token budget
  Codegen / AgentSpec       → Layer 1 (Inference Router) — agent partitioning
  StateMachineIR.inv_hooks  → Layer 4 (Governance Guard) — immutable gates

CANNOT_MUTATE sentinels:
  COMPILER_VERSION  — spec version this compiler targets
  MAX_AGENT_STATES  — states per agent before partition is split
"""
from __future__ import annotations

import enum
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple

# ── CANNOT_MUTATE sentinels ──────────────────────────────────────────────────
_COMPILER_VERSION: str = "1.4"
_MAX_AGENT_STATES: int = 8   # split into new agent if exceeded

COMPILER_VERSION  = _COMPILER_VERSION
MAX_AGENT_STATES  = _MAX_AGENT_STATES

import sys as _sys
_mod = _sys.modules[__name__]

class _FrozenMod(type(_mod)):
    _LOCKED: FrozenSet[str] = frozenset({"COMPILER_VERSION", "MAX_AGENT_STATES"})
    def __setattr__(self, name: str, value: object) -> None:
        if name in self._LOCKED:
            raise AttributeError(f"{name} is CANNOT_MUTATE")
        super().__setattr__(name, value)

_mod.__class__ = _FrozenMod


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Lexer
# ══════════════════════════════════════════════════════════════════════════════

class TK(enum.Enum):
    """Token kinds."""
    KW_MODULE    = "module"
    KW_IMPORT    = "import"
    KW_RUNTIME   = "runtime"
    KW_INVARIANT = "invariant"
    KW_STATE     = "state"
    KW_FINALIZE  = "finalize"
    KW_EVALUATE  = "evaluate"
    KW_RETRY     = "retry_loop"
    KW_ON        = "on"
    IDENT        = "IDENT"
    STRING       = "STRING"
    NUMBER       = "NUMBER"
    LBRACE       = "{"
    RBRACE       = "}"
    LPAREN       = "("
    RPAREN       = ")"
    LBRACKET     = "["
    RBRACKET     = "]"
    ARROW        = "=>"
    DCOLON       = "::"
    SEMI         = ";"
    COLON        = ":"
    COMMA        = ","
    PLUS_EQ      = "+="
    MINUS_EQ     = "-="
    DOT          = "."
    EQ           = "="
    GT           = ">"
    LT           = "<"
    MINUS        = "MINUS"
    EOF          = "EOF"


_KW_MAP = {
    "module": TK.KW_MODULE,
    "import": TK.KW_IMPORT,
    "runtime": TK.KW_RUNTIME,
    "invariant": TK.KW_INVARIANT,
    "state": TK.KW_STATE,
    "finalize": TK.KW_FINALIZE,
    "evaluate": TK.KW_EVALUATE,
    "retry_loop": TK.KW_RETRY,
}


@dataclass(frozen=True)
class Token:
    kind:  TK
    value: str
    line:  int
    col:   int

    def __repr__(self) -> str:
        return f"Token({self.kind.name}, {self.value!r}, {self.line}:{self.col})"


class LexError(SyntaxError):
    pass


class BlueprintLexer:
    """Tokenise `.axiom` v1.4 source into a ``Token`` list."""

    def tokenise(self, source: str) -> List[Token]:
        tokens: List[Token] = []
        i, line, col = 0, 1, 1
        n = len(source)

        def advance(count: int = 1) -> str:
            nonlocal i, col
            ch = source[i : i + count]
            i += count
            col += count
            return ch

        while i < n:
            # newline
            if source[i] == "\n":
                advance()
                line += 1
                col = 1
                continue

            # whitespace
            if source[i] in " \t\r":
                advance()
                continue

            # comment
            if source[i] == "#":
                while i < n and source[i] != "\n":
                    advance()
                continue

            start_line, start_col = line, col

            # two-char tokens
            two = source[i : i + 2]
            if two == "=>":
                tokens.append(Token(TK.ARROW, "=>", start_line, start_col))
                advance(2); continue
            if two == "::":
                tokens.append(Token(TK.DCOLON, "::", start_line, start_col))
                advance(2); continue
            if two == "+=":
                tokens.append(Token(TK.PLUS_EQ, "+=", start_line, start_col))
                advance(2); continue
            if two == "-=":
                tokens.append(Token(TK.MINUS_EQ, "-=", start_line, start_col))
                advance(2); continue

            # number (checked before single_map so that -0.2 stays a single NUMBER token)
            if source[i].isdigit() or (source[i] == "-" and i + 1 < n and source[i+1].isdigit()):
                buf = []
                if source[i] == "-":
                    buf.append(advance())
                while i < n and (source[i].isdigit() or source[i] == "."):
                    buf.append(advance())
                tokens.append(Token(TK.NUMBER, "".join(buf), start_line, start_col))
                continue

            # one-char tokens (after number check so -0.2 is not split into MINUS + NUMBER)
            single = source[i]
            single_map = {
                "{": TK.LBRACE, "}": TK.RBRACE,
                "(": TK.LPAREN, ")": TK.RPAREN,
                "[": TK.LBRACKET, "]": TK.RBRACKET,
                ";": TK.SEMI, ":": TK.COLON,
                ",": TK.COMMA, ".": TK.DOT,
                "=": TK.EQ, ">": TK.GT, "<": TK.LT,
                "-": TK.MINUS,
            }
            if single in single_map:
                tokens.append(Token(single_map[single], single, start_line, start_col))
                advance(); continue

            # string literal — supports \" escapes
            if single in ('"', "'"):
                quote = advance()
                buf = []
                while i < n and source[i] != quote:
                    if source[i] == "\\" and i + 1 < n:
                        advance()  # skip backslash
                        buf.append(advance())
                    else:
                        buf.append(advance())
                if i < n:
                    advance()  # consume closing quote
                tokens.append(Token(TK.STRING, "".join(buf), start_line, start_col))
                continue

            # identifier / keyword (allow underscore and alphanumeric)
            if source[i].isalpha() or source[i] == "_":
                # raw string prefix: r"..." or r'...' — skip 'r', next iter handles string
                if source[i] == "r" and i + 1 < n and source[i + 1] in ('"', "'"):
                    advance()  # skip 'r'
                    continue
                buf = []
                while i < n and (source[i].isalnum() or source[i] == "_"):
                    buf.append(advance())
                word = "".join(buf)
                kind = _KW_MAP.get(word, TK.IDENT)
                tokens.append(Token(kind, word, start_line, start_col))
                continue

            raise LexError(f"Unexpected character {source[i]!r} at {line}:{col}")

        tokens.append(Token(TK.EOF, "", line, col))
        return tokens


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — AST nodes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ASTRuntimeConfig:
    max_context_tokens:      int   = 8192
    reserve_response_tokens: int   = 1024
    execution_mode:          str   = "local_first"
    temperature_min:         float = 0.1
    temperature_max:         float = 0.7


@dataclass(frozen=True)
class ASTWeightedCategory:
    label:  str
    weight: float


@dataclass(frozen=True)
class ASTPrivacyFilter:
    pattern:  str   # raw regex string
    is_regex: bool = True


@dataclass(frozen=True)
class ASTInvariant:
    name:            str
    line:            int
    allow_network:   bool                    = False
    allow_fs_write:  Tuple[str, ...]        = ()
    privacy_filters: Tuple[ASTPrivacyFilter, ...] = ()
    validate_via:    Optional[str]           = None
    validate_kwargs: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ASTTransition:
    condition:    str   # "on_success" | "on_failure"
    target_state: str
    line:         int


@dataclass(frozen=True)
class ASTWeightAdjustment:
    category: str
    delta:    float  # positive or negative


@dataclass(frozen=True)
class ASTRetrySpec:
    max_attempts:       int
    on_violation:       str
    feedback_template:  str
    weight_adjustments: Tuple[ASTWeightAdjustment, ...] = ()
    temperature_delta:  float = -0.1


@dataclass(frozen=True)
class ASTState:
    name:                  str
    line:                  int
    prompt:                str  = ""
    action:                str  = ""
    probabilistic_weights: Tuple[ASTWeightedCategory, ...] = ()
    transition_threshold:  float = 0.8
    transitions:           Tuple[ASTTransition, ...] = ()
    output_schema_fields:  Tuple[str, ...] = ()
    retry_spec:            Optional[ASTRetrySpec] = None


@dataclass(frozen=True)
class ASTFinalize:
    name:          str
    line:          int
    log_telemetry: Tuple[str, ...] = ()
    sign_payload:  str = "hmac.sha256"
    destination:   str = "memory"


@dataclass(frozen=True)
class BlueprintAST:
    module:         str
    imports:        Tuple[str, ...]
    source_hash:    str          # sha256 of source — for cache invalidation
    runtime:        ASTRuntimeConfig
    invariants:     Tuple[ASTInvariant, ...]
    states:         Tuple[ASTState, ...]
    finalize:       Optional[ASTFinalize]


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Parser (recursive-descent, token-stream based)
# ══════════════════════════════════════════════════════════════════════════════

class ParseError(SyntaxError):
    pass


class _TokenStream:
    def __init__(self, tokens: List[Token]) -> None:
        self._tokens = tokens
        self._pos    = 0

    def peek(self) -> Token:
        return self._tokens[self._pos]

    def consume(self, *kinds: TK) -> Token:
        tok = self._tokens[self._pos]
        if kinds and tok.kind not in kinds:
            expected = " or ".join(k.name for k in kinds)
            raise ParseError(
                f"Expected {expected}, got {tok.kind.name} {tok.value!r} "
                f"at {tok.line}:{tok.col}"
            )
        self._pos += 1
        return tok

    def try_consume(self, *kinds: TK) -> Optional[Token]:
        if self._tokens[self._pos].kind in kinds:
            return self.consume()
        return None

    def skip_to(self, *kinds: TK) -> None:
        while self._tokens[self._pos].kind not in kinds and \
              self._tokens[self._pos].kind != TK.EOF:
            self._pos += 1

    def at(self, *kinds: TK) -> bool:
        return self._tokens[self._pos].kind in kinds


class BlueprintASTParser:
    """Recursive-descent parser: ``List[Token]`` → ``BlueprintAST``."""

    def parse(self, source: str) -> BlueprintAST:
        src_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
        tokens   = BlueprintLexer().tokenise(source)
        ts       = _TokenStream(tokens)

        module  = "Unnamed"
        imports: List[str] = []
        runtime = ASTRuntimeConfig()
        invs:    List[ASTInvariant] = []
        states:  List[ASTState]    = []
        finalize: Optional[ASTFinalize] = None

        while not ts.at(TK.EOF):
            tok = ts.peek()
            if tok.kind == TK.KW_MODULE:
                ts.consume(TK.KW_MODULE)
                module = self._parse_dotted_name(ts)
                ts.try_consume(TK.SEMI)
            elif tok.kind == TK.KW_IMPORT:
                ts.consume(TK.KW_IMPORT)
                imports.append(self._parse_dotted_name(ts))
                ts.try_consume(TK.SEMI)
            elif tok.kind == TK.KW_RUNTIME:
                ts.consume(TK.KW_RUNTIME)
                _name = self._consume_ident(ts)
                runtime = self._parse_runtime_block(ts)
            elif tok.kind == TK.KW_INVARIANT:
                ts.consume(TK.KW_INVARIANT)
                name = self._consume_ident(ts)
                line = ts.peek().line
                invs.append(self._parse_invariant_block(ts, name, line))
            elif tok.kind == TK.KW_STATE:
                ts.consume(TK.KW_STATE)
                name = self._consume_ident(ts)
                line = tok.line
                states.append(self._parse_state_block(ts, name, line))
            elif tok.kind == TK.KW_FINALIZE:
                ts.consume(TK.KW_FINALIZE)
                name = self._consume_ident(ts)
                line = tok.line
                finalize = self._parse_finalize_block(ts, name, line)
            else:
                ts.consume()  # skip unknown tokens

        return BlueprintAST(
            module      = module,
            imports     = tuple(imports),
            source_hash = src_hash,
            runtime     = runtime,
            invariants  = tuple(invs),
            states      = tuple(states),
            finalize    = finalize,
        )

    def parse_file(self, path: Path) -> BlueprintAST:
        return self.parse(Path(path).read_text(encoding="utf-8"))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _parse_dotted_name(self, ts: _TokenStream) -> str:
        parts = [self._consume_ident(ts)]
        while ts.try_consume(TK.DOT):
            parts.append(self._consume_ident(ts))
        return ".".join(parts)

    def _consume_ident(self, ts: _TokenStream) -> str:
        tok = ts.peek()
        # keywords can appear as identifiers in name positions
        if tok.kind in (TK.IDENT, *_KW_MAP.values()):
            ts.consume()
            return tok.value
        raise ParseError(f"Expected identifier, got {tok!r}")

    def _parse_runtime_block(self, ts: _TokenStream) -> ASTRuntimeConfig:
        ts.consume(TK.LBRACE)
        kw: Dict[str, object] = {}
        while not ts.at(TK.RBRACE, TK.EOF):
            key_tok = ts.peek()
            if key_tok.kind == TK.EOF:
                break
            key = self._consume_ident(ts)
            ts.consume(TK.COLON)
            if key == "temperature_profile":
                # dynamic(min, max) or static value
                if ts.peek().value == "dynamic":
                    self._consume_ident(ts)
                    ts.consume(TK.LPAREN)
                    lo = float(ts.consume(TK.NUMBER).value)
                    ts.try_consume(TK.COMMA)
                    hi = float(ts.consume(TK.NUMBER).value)
                    ts.consume(TK.RPAREN)
                    kw["temperature_min"] = lo
                    kw["temperature_max"] = hi
                else:
                    v = float(ts.consume(TK.NUMBER, TK.STRING).value)
                    kw["temperature_min"] = v
                    kw["temperature_max"] = v
            elif key == "max_context_tokens":
                kw[key] = int(ts.consume(TK.NUMBER).value)
            elif key == "reserve_response_tokens":
                kw[key] = int(ts.consume(TK.NUMBER).value)
            elif key == "execution_mode":
                kw[key] = ts.consume(TK.STRING).value
            else:
                ts.skip_to(TK.SEMI, TK.RBRACE)
            ts.try_consume(TK.SEMI)
        ts.try_consume(TK.RBRACE)
        return ASTRuntimeConfig(**{k: v for k, v in kw.items()  # type: ignore[arg-type]
                                   if k in ASTRuntimeConfig.__dataclass_fields__})

    def _parse_invariant_block(
        self, ts: _TokenStream, name: str, line: int
    ) -> ASTInvariant:
        ts.consume(TK.LBRACE)
        allow_network   = False
        allow_fs_write: List[str] = []
        filters:        List[ASTPrivacyFilter] = []
        validate_via:   Optional[str] = None
        validate_kwargs: List[Tuple[str, str]] = []

        while not ts.at(TK.RBRACE, TK.EOF):
            key = self._consume_ident(ts)
            ts.consume(TK.COLON)
            if key == "allow_network":
                v = self._consume_ident(ts)
                allow_network = v.lower() in ("true", "yes", "1")
            elif key == "allow_fs_write":
                ts.consume(TK.LBRACKET)
                while not ts.at(TK.RBRACKET, TK.EOF):
                    if ts.at(TK.STRING):
                        allow_fs_write.append(ts.consume(TK.STRING).value)
                    ts.try_consume(TK.COMMA)
                ts.try_consume(TK.RBRACKET)
            elif key == "privacy_filter":
                ts.consume(TK.LBRACKET)
                while not ts.at(TK.RBRACKET, TK.EOF):
                    if ts.peek().value == "regex":
                        self._consume_ident(ts)
                        ts.consume(TK.LPAREN)
                        pat = ts.consume(TK.STRING).value
                        ts.consume(TK.RPAREN)
                        filters.append(ASTPrivacyFilter(pattern=pat, is_regex=True))
                    ts.try_consume(TK.COMMA)
                ts.try_consume(TK.RBRACKET)
            elif key == "validate_via":
                validate_via = self._parse_dotted_name(ts)
                if ts.try_consume(TK.LPAREN):
                    while not ts.at(TK.RPAREN, TK.EOF):
                        k2 = self._consume_ident(ts)
                        ts.try_consume(TK.COLON, TK.IDENT, TK.EQ)  # : or = separator
                        v2 = ts.consume(TK.STRING).value
                        validate_kwargs.append((k2, v2))
                        ts.try_consume(TK.COMMA)
                    ts.try_consume(TK.RPAREN)
            else:
                ts.skip_to(TK.SEMI, TK.RBRACE)
            ts.try_consume(TK.SEMI)

        ts.try_consume(TK.RBRACE)
        return ASTInvariant(
            name            = name,
            line            = line,
            allow_network   = allow_network,
            allow_fs_write  = tuple(allow_fs_write),
            privacy_filters = tuple(filters),
            validate_via    = validate_via,
            validate_kwargs = tuple(validate_kwargs),
        )

    def _parse_state_block(
        self, ts: _TokenStream, name: str, line: int
    ) -> ASTState:
        ts.consume(TK.LBRACE)
        prompt     = ""
        action     = ""
        weights:   List[ASTWeightedCategory] = []
        threshold  = 0.8
        trans:     List[ASTTransition] = []
        schema_fields: List[str] = []
        retry_spec: Optional[ASTRetrySpec] = None

        while not ts.at(TK.RBRACE, TK.EOF):
            key_tok = ts.peek()
            if key_tok.kind == TK.EOF:
                break

            # on_success / on_failure transitions
            if key_tok.kind == TK.IDENT and key_tok.value in ("on_success", "on_failure"):
                cond = key_tok.value
                ts.consume()
                ts.consume(TK.LPAREN)
                ts.skip_to(TK.RPAREN)
                ts.try_consume(TK.RPAREN)
                ts.try_consume(TK.ARROW)
                ts.consume(TK.KW_STATE, TK.IDENT)  # "state"
                ts.try_consume(TK.DCOLON)
                target = self._consume_ident(ts)
                ts.try_consume(TK.SEMI)
                trans.append(ASTTransition(condition=cond, target_state=target, line=key_tok.line))
                continue

            if key_tok.kind == TK.KW_EVALUATE:
                ts.consume(TK.KW_EVALUATE)
                # consume name tokens before the opening brace
                while not ts.at(TK.LBRACE, TK.EOF):
                    ts.consume()
                ts.consume(TK.LBRACE)
                while not ts.at(TK.RBRACE, TK.EOF):
                    label = ts.consume(TK.STRING).value
                    ts.try_consume(TK.ARROW)
                    self._consume_ident(ts)  # "weight"
                    ts.consume(TK.LPAREN)
                    w = float(ts.consume(TK.NUMBER).value)
                    ts.consume(TK.RPAREN)
                    ts.try_consume(TK.COMMA)
                    weights.append(ASTWeightedCategory(label=label, weight=w))
                ts.try_consume(TK.RBRACE)
                continue

            if key_tok.kind == TK.KW_RETRY:
                retry_spec = self._parse_retry_block(ts)
                continue

            key = self._consume_ident(ts)
            ts.consume(TK.COLON)

            if key == "prompt":
                prompt = ts.consume(TK.STRING).value
            elif key == "action":
                action = ts.consume(TK.STRING).value
            elif key == "transition_threshold":
                threshold = float(ts.consume(TK.NUMBER).value)
            elif key == "output_format":
                # Schema({field: Type, ...}) — extract field names
                self._consume_ident(ts)  # "Schema"
                ts.consume(TK.LPAREN)
                ts.consume(TK.LBRACE)
                while not ts.at(TK.RBRACE, TK.EOF):
                    fname = self._consume_ident(ts)
                    ts.try_consume(TK.COLON)
                    self._consume_ident(ts)  # type name
                    if ts.try_consume(TK.LPAREN):  # generic args: List(String)
                        ts.skip_to(TK.RPAREN)
                        ts.try_consume(TK.RPAREN)
                    ts.try_consume(TK.COMMA)
                    schema_fields.append(fname)
                ts.try_consume(TK.RBRACE)
                ts.try_consume(TK.RPAREN)
            else:
                ts.skip_to(TK.SEMI, TK.RBRACE)

            ts.try_consume(TK.SEMI)

        ts.try_consume(TK.RBRACE)

        # Normalise weights to sum to 1.0
        total = sum(w.weight for w in weights) or 1.0
        norm  = tuple(
            ASTWeightedCategory(w.label, round(w.weight / total, 6))
            for w in weights
        )

        return ASTState(
            name                  = name,
            line                  = line,
            prompt                = prompt,
            action                = action,
            probabilistic_weights = norm,
            transition_threshold  = threshold,
            transitions           = tuple(trans),
            output_schema_fields  = tuple(schema_fields),
            retry_spec            = retry_spec,
        )

    def _parse_retry_block(self, ts: _TokenStream) -> ASTRetrySpec:
        ts.consume(TK.KW_RETRY)
        self._consume_ident(ts)  # "MaxAttempts"
        ts.consume(TK.LPAREN)
        max_att = int(ts.consume(TK.NUMBER).value)
        ts.consume(TK.RPAREN)
        ts.consume(TK.LBRACE)

        on_viol    = ""
        feedback   = ""
        adjs:      List[ASTWeightAdjustment] = []
        temp_delta = -0.1

        while not ts.at(TK.RBRACE, TK.EOF):
            if ts.peek().value == "on_violation":
                self._consume_ident(ts)
                ts.consume(TK.LPAREN)
                on_viol_raw = self._consume_ident(ts)
                ts.try_consume(TK.DOT)
                ts.skip_to(TK.RPAREN)
                ts.try_consume(TK.RPAREN)
                ts.consume(TK.LBRACE)
                on_viol = on_viol_raw

                # inner block
                while not ts.at(TK.RBRACE, TK.EOF):
                    key = self._consume_ident(ts)
                    ts.consume(TK.COLON)
                    if key == "feedback":
                        feedback = ts.consume(TK.STRING).value
                    elif key == "adjust_weights":
                        ts.consume(TK.LBRACKET)
                        while not ts.at(TK.RBRACKET, TK.EOF):
                            cat = ts.consume(TK.STRING).value
                            op  = ts.consume(TK.PLUS_EQ, TK.MINUS_EQ)
                            val = float(ts.consume(TK.NUMBER).value)
                            delta = val if op.kind == TK.PLUS_EQ else -val
                            adjs.append(ASTWeightAdjustment(cat, delta))
                            ts.try_consume(TK.COMMA)
                        ts.try_consume(TK.RBRACKET)
                    elif key == "temperature":
                        # temperature: runtime.context.temperature - 0.2
                        ts.skip_to(TK.MINUS_EQ, TK.SEMI, TK.RBRACE, TK.EOF)
                        # look for a number that is the delta
                        if ts.at(TK.SEMI, TK.RBRACE):
                            pass
                        else:
                            # consume tokens until we hit a NUMBER
                            while not ts.at(TK.NUMBER, TK.SEMI, TK.RBRACE, TK.EOF):
                                ts.consume()
                            if ts.at(TK.NUMBER):
                                temp_delta = -float(ts.consume(TK.NUMBER).value)
                    else:
                        ts.skip_to(TK.SEMI, TK.RBRACE)
                    ts.try_consume(TK.SEMI)

                ts.try_consume(TK.RBRACE)
            else:
                ts.skip_to(TK.SEMI, TK.RBRACE)
                ts.try_consume(TK.SEMI)

        ts.try_consume(TK.RBRACE)
        return ASTRetrySpec(
            max_attempts       = max_att,
            on_violation       = on_viol,
            feedback_template  = feedback,
            weight_adjustments = tuple(adjs),
            temperature_delta  = temp_delta,
        )

    def _parse_finalize_block(
        self, ts: _TokenStream, name: str, line: int
    ) -> ASTFinalize:
        ts.consume(TK.LBRACE)
        items:    List[str] = []
        sign     = "hmac.sha256"
        dest     = "memory"

        while not ts.at(TK.RBRACE, TK.EOF):
            key = self._consume_ident(ts)
            ts.consume(TK.COLON)
            if key == "log_telemetry":
                ts.consume(TK.LBRACKET)
                while not ts.at(TK.RBRACKET, TK.EOF):
                    items.append(self._parse_dotted_name(ts))
                    ts.try_consume(TK.COMMA)
                ts.try_consume(TK.RBRACKET)
            elif key == "sign_payload":
                sign = self._parse_dotted_name(ts)
                if ts.try_consume(TK.LPAREN):
                    ts.skip_to(TK.RPAREN)
                    ts.try_consume(TK.RPAREN)
            elif key == "destination":
                dest = ts.consume(TK.STRING).value
            else:
                ts.skip_to(TK.SEMI, TK.RBRACE)
            ts.try_consume(TK.SEMI)

        ts.try_consume(TK.RBRACE)
        return ASTFinalize(
            name=name, line=line,
            log_telemetry=tuple(items), sign_payload=sign, destination=dest,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 — Semantic Analyzer
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Diagnostic:
    severity: str   # "error" | "warning" | "info"
    code:     str
    message:  str
    line:     int   # 0 = file-level


@dataclass(frozen=True)
class DiagnosticReport:
    diagnostics: Tuple[Diagnostic, ...]
    has_errors:  bool

    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


class BlueprintAnalyzer:
    """Validate a ``BlueprintAST`` and return a ``DiagnosticReport``.

    Checks:
      A1 — all transition targets refer to a declared state
      A2 — no state is unreachable from the first state (dead-state detection)
      A3 — budget: reserve_response_tokens < max_context_tokens
      A4 — weight sums are within 0.001 of 1.0 after normalisation
      A5 — no duplicate state names
      A6 — retry on_violation names a declared invariant
      A7 — at least one state defined
    """

    def analyze(self, ast: BlueprintAST) -> DiagnosticReport:
        diags: List[Diagnostic] = []
        state_names = {s.name for s in ast.states}

        # A7
        if not ast.states:
            diags.append(Diagnostic("error", "A7", "No states defined", 0))

        # A5
        seen: Dict[str, int] = {}
        for s in ast.states:
            if s.name in seen:
                diags.append(Diagnostic(
                    "error", "A5",
                    f"Duplicate state name '{s.name}' (first at line {seen[s.name]})",
                    s.line,
                ))
            seen[s.name] = s.line

        # A1
        inv_names = {inv.name for inv in ast.invariants}
        for s in ast.states:
            for t in s.transitions:
                if t.target_state not in state_names:
                    diags.append(Diagnostic(
                        "error", "A1",
                        f"State '{s.name}' transitions to undeclared state '{t.target_state}'",
                        t.line,
                    ))
            if s.retry_spec and s.retry_spec.on_violation not in inv_names:
                diags.append(Diagnostic(
                    "warning", "A6",
                    f"State '{s.name}' retry references unknown invariant '{s.retry_spec.on_violation}'",
                    s.line,
                ))

        # A2 — reachability from first state (DFS)
        if ast.states:
            reachable: set = set()
            stack = [ast.states[0].name]
            idx   = {s.name: s for s in ast.states}
            while stack:
                n = stack.pop()
                if n in reachable or n not in idx:
                    continue
                reachable.add(n)
                st = idx[n]
                for t in st.transitions:
                    stack.append(t.target_state)
                # linear successor
                names_list = [s.name for s in ast.states]
                si = names_list.index(n) if n in names_list else -1
                if si + 1 < len(names_list):
                    stack.append(names_list[si + 1])

            for s in ast.states:
                if s.name not in reachable:
                    diags.append(Diagnostic(
                        "warning", "A2",
                        f"State '{s.name}' is unreachable from the initial state",
                        s.line,
                    ))

        # A3
        rc = ast.runtime
        if rc.reserve_response_tokens >= rc.max_context_tokens:
            diags.append(Diagnostic(
                "error", "A3",
                f"reserve_response_tokens ({rc.reserve_response_tokens}) >= "
                f"max_context_tokens ({rc.max_context_tokens})",
                0,
            ))

        # A4 — weight normalisation check
        for s in ast.states:
            if s.probabilistic_weights:
                total = sum(w.weight for w in s.probabilistic_weights)
                if abs(total - 1.0) > 0.01:
                    diags.append(Diagnostic(
                        "warning", "A4",
                        f"State '{s.name}' weights sum to {total:.4f} (expected 1.0)",
                        s.line,
                    ))

        return DiagnosticReport(
            diagnostics = tuple(diags),
            has_errors  = any(d.severity == "error" for d in diags),
        )


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4 — Optimizer
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OptimizationReport:
    passes_applied:          Tuple[str, ...]
    states_before:           int
    states_after:            int
    tokens_before:           int
    tokens_after:            int
    token_reduction_pct:     float


class BlueprintOptimizer:
    """Token-budget-aware multi-pass optimizer.

    Passes (in order):
      P1 — dead-state elimination: remove states unreachable from first state
      P2 — weight compression: merge categories with weight < min_weight into _other_
      P3 — invariant hoisting: deduplicate identical invariants across states
      P4 — prompt compression: strip filler words from prompt/action strings
           (saves ~10-25 % of prompt tokens)
    """

    MIN_WEIGHT: float = 0.04  # categories below this are merged into _other_
    _FILLER = re.compile(
        r"\b(please|kindly|simply|just|basically|essentially|"
        r"now|then|next|always|make sure to|be sure to)\b\s*",
        re.IGNORECASE,
    )
    _MULTI_SPACE = re.compile(r"  +")

    def optimize(
        self,
        ast: BlueprintAST,
        token_budget: Optional[int] = None,
    ) -> Tuple[BlueprintAST, OptimizationReport]:
        budget = token_budget or (
            ast.runtime.max_context_tokens - ast.runtime.reserve_response_tokens
        )
        tokens_before = self._estimate_tokens(ast)
        passes: List[str] = []

        ast, p1 = self._p1_dead_state(ast)
        if p1:
            passes.append("P1:dead-state-elimination")

        ast, p2 = self._p2_weight_compression(ast)
        if p2:
            passes.append("P2:weight-compression")

        ast, p3 = self._p3_invariant_hoist(ast)
        if p3:
            passes.append("P3:invariant-hoist")

        ast, p4 = self._p4_prompt_compress(ast, budget)
        if p4:
            passes.append("P4:prompt-compression")

        tokens_after = self._estimate_tokens(ast)

        report = OptimizationReport(
            passes_applied      = tuple(passes),
            states_before       = len(ast.states),  # after elimination
            states_after        = len(ast.states),
            tokens_before       = tokens_before,
            tokens_after        = tokens_after,
            token_reduction_pct = round(
                100 * (tokens_before - tokens_after) / max(tokens_before, 1), 1
            ),
        )
        return ast, report

    def _p1_dead_state(self, ast: BlueprintAST) -> Tuple[BlueprintAST, bool]:
        if not ast.states:
            return ast, False
        reachable: set = set()
        idx = {s.name: s for s in ast.states}
        stack = [ast.states[0].name]
        while stack:
            n = stack.pop()
            if n in reachable or n not in idx:
                continue
            reachable.add(n)
            st = idx[n]
            for t in st.transitions:
                stack.append(t.target_state)
            names = [s.name for s in ast.states]
            si = names.index(n) if n in names else -1
            if si + 1 < len(names):
                stack.append(names[si + 1])
        new_states = tuple(s for s in ast.states if s.name in reachable)
        changed = len(new_states) != len(ast.states)
        if changed:
            ast = _replace_ast(ast, states=new_states)
        return ast, changed

    def _p2_weight_compression(self, ast: BlueprintAST) -> Tuple[BlueprintAST, bool]:
        changed = False
        new_states: List[ASTState] = []
        for s in ast.states:
            if not s.probabilistic_weights:
                new_states.append(s)
                continue
            keep  = [w for w in s.probabilistic_weights if w.weight >= self.MIN_WEIGHT]
            other = sum(w.weight for w in s.probabilistic_weights if w.weight < self.MIN_WEIGHT)
            if other > 0:
                keep.append(ASTWeightedCategory("_other_", round(other, 6)))
                changed = True
            new_states.append(_replace_state(s, probabilistic_weights=tuple(keep)))
        if changed:
            ast = _replace_ast(ast, states=tuple(new_states))
        return ast, changed

    def _p3_invariant_hoist(self, ast: BlueprintAST) -> Tuple[BlueprintAST, bool]:
        # Currently invariants are global already in the AST — deduplicate by name
        seen: Dict[str, ASTInvariant] = {}
        for inv in ast.invariants:
            if inv.name not in seen:
                seen[inv.name] = inv
        new_invs = tuple(seen.values())
        changed  = len(new_invs) != len(ast.invariants)
        if changed:
            ast = _replace_ast(ast, invariants=new_invs)
        return ast, changed

    def _p4_prompt_compress(
        self, ast: BlueprintAST, _budget: int
    ) -> Tuple[BlueprintAST, bool]:
        changed = False
        new_states: List[ASTState] = []
        for s in ast.states:
            np = self._compress(s.prompt)
            na = self._compress(s.action)
            if np != s.prompt or na != s.action:
                changed = True
                new_states.append(_replace_state(s, prompt=np, action=na))
            else:
                new_states.append(s)
        if changed:
            ast = _replace_ast(ast, states=tuple(new_states))
        return ast, changed

    def _compress(self, text: str) -> str:
        if not text:
            return text
        out = self._FILLER.sub(" ", text)
        out = self._MULTI_SPACE.sub(" ", out).strip()
        return out

    def _estimate_tokens(self, ast: BlueprintAST) -> int:
        chars = sum(len(s.prompt) + len(s.action) for s in ast.states)
        chars += sum(len(inv.name) * 4 for inv in ast.invariants)
        return chars // 4 + 30 * len(ast.states)


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5 — Code generation → StateMachineIR
# ══════════════════════════════════════════════════════════════════════════════

class AgentRole(enum.Enum):
    ANALYZER  = "analyzer"   # states with probabilistic_weights (classification/analysis)
    EXECUTOR  = "executor"   # states with action + output_schema
    RESPONDER = "responder"  # states with only prompt (dialogue / generation)
    AUDITOR   = "auditor"    # finalize block


@dataclass(frozen=True)
class AgentSpec:
    """One agent in the multi-agent partition."""
    agent_id:     str
    role:         AgentRole
    state_names:  Tuple[str, ...]
    system_prompt: str   # token-optimised, ready for LLM context injection
    token_budget:  int
    can_parallel:  bool  # True if no data dependency on prior states


@dataclass
class StateMachineIR:
    """Serialisable, runtime-ready intermediate representation.

    Load into BlueprintStateMachine via ``BlueprintStateMachine.from_ir(ir)``.
    """
    module:           str
    compiler_version: str
    source_hash:      str
    agent_specs:      List[AgentSpec]
    transition_table: Dict[str, Dict[str, str]]  # state → {cond → target}
    inv_hooks:        Dict[str, List[str]]        # state → [invariant_names]
    all_invariants:   Dict[str, dict]             # name → serialised ASTInvariant
    token_budget:     int
    reserve_tokens:   int
    state_order:      List[str]                   # canonical execution order
    ir_hash:          str = ""

    def to_dict(self) -> dict:
        return {
            "module":           self.module,
            "compiler_version": self.compiler_version,
            "source_hash":      self.source_hash,
            "agent_specs": [
                {
                    "agent_id":     a.agent_id,
                    "role":         a.role.value,
                    "state_names":  list(a.state_names),
                    "system_prompt": a.system_prompt,
                    "token_budget": a.token_budget,
                    "can_parallel": a.can_parallel,
                }
                for a in self.agent_specs
            ],
            "transition_table": self.transition_table,
            "inv_hooks":        self.inv_hooks,
            "all_invariants":   self.all_invariants,
            "token_budget":     self.token_budget,
            "reserve_tokens":   self.reserve_tokens,
            "state_order":      self.state_order,
            "ir_hash":          self.ir_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StateMachineIR":
        specs = [
            AgentSpec(
                agent_id     = a["agent_id"],
                role         = AgentRole(a["role"]),
                state_names  = tuple(a["state_names"]),
                system_prompt= a["system_prompt"],
                token_budget = a["token_budget"],
                can_parallel = a["can_parallel"],
            )
            for a in d.get("agent_specs", [])
        ]
        return cls(
            module           = d["module"],
            compiler_version = d["compiler_version"],
            source_hash      = d["source_hash"],
            agent_specs      = specs,
            transition_table = d["transition_table"],
            inv_hooks        = d["inv_hooks"],
            all_invariants   = d["all_invariants"],
            token_budget     = d["token_budget"],
            reserve_tokens   = d["reserve_tokens"],
            state_order      = d["state_order"],
            ir_hash          = d.get("ir_hash", ""),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "StateMachineIR":
        return cls.from_dict(json.loads(text))


class BlueprintCodegen:
    """Generate a ``StateMachineIR`` from an optimised ``BlueprintAST``.

    Agent partitioning rules:
      - States are grouped into agents by role (ANALYZER / EXECUTOR / RESPONDER)
      - If a group exceeds MAX_AGENT_STATES it is split into numbered sub-agents
      - States with no data dependency on prior states are marked can_parallel=True
      - The finalize block always becomes a singleton AUDITOR agent

    Token-optimised system prompt format per agent:
      [AXIOM:{module}::{agent_id}|role:{role}]
      INV:{inv1},{inv2}
      STATES:{name1}>{name2}>...
      W:{label}={w:.2f}[,...]  (for ANALYZER only)
      SCHEMA:{field1},{field2}  (for EXECUTOR only)
    """

    def emit(self, ast: BlueprintAST) -> StateMachineIR:
        inv_names = [inv.name for inv in ast.invariants]
        all_inv   = {
            inv.name: {
                "allow_network": inv.allow_network,
                "allow_fs_write": list(inv.allow_fs_write),
                "privacy_filters": [
                    {"pattern": f.pattern, "is_regex": f.is_regex}
                    for f in inv.privacy_filters
                ],
                "validate_via": inv.validate_via,
            }
            for inv in ast.invariants
        }

        # Build transition table
        tt: Dict[str, Dict[str, str]] = {}
        for s in ast.states:
            tt[s.name] = {t.condition: t.target_state for t in s.transitions}

        # All states get all invariants as hooks (global governance)
        inv_hooks = {s.name: inv_names for s in ast.states}

        # Partition states into agents
        agent_specs = self._partition_agents(ast)

        # Token budget per agent (split evenly across agents)
        budget_per_agent = max(
            64,
            (ast.runtime.max_context_tokens - ast.runtime.reserve_response_tokens)
            // max(1, len(agent_specs)),
        )
        agent_specs = [
            AgentSpec(
                a.agent_id, a.role, a.state_names,
                a.system_prompt, budget_per_agent, a.can_parallel,
            )
            for a in agent_specs
        ]

        ir = StateMachineIR(
            module           = ast.module,
            compiler_version = COMPILER_VERSION,
            source_hash      = ast.source_hash,
            agent_specs      = agent_specs,
            transition_table = tt,
            inv_hooks        = inv_hooks,
            all_invariants   = all_inv,
            token_budget     = ast.runtime.max_context_tokens,
            reserve_tokens   = ast.runtime.reserve_response_tokens,
            state_order      = [s.name for s in ast.states],
        )
        ir.ir_hash = hashlib.sha256(ir.to_json().encode()).hexdigest()[:16]
        return ir

    def _partition_agents(self, ast: BlueprintAST) -> List[AgentSpec]:
        analyzers:  List[ASTState] = []
        executors:  List[ASTState] = []
        responders: List[ASTState] = []

        for s in ast.states:
            if s.probabilistic_weights:
                analyzers.append(s)
            elif s.output_schema_fields or s.action:
                executors.append(s)
            else:
                responders.append(s)

        agents: List[AgentSpec] = []

        def _add_group(
            states: List[ASTState], role: AgentRole, prefix: str,
        ) -> None:
            for chunk_idx, chunk in enumerate(_chunked(states, MAX_AGENT_STATES)):
                agent_id = f"{prefix}_{chunk_idx}" if chunk_idx else prefix
                # can_parallel: True if none of these states depend on a prior
                # analyzer result (simple heuristic: executor/responder can be
                # parallel only if there are no analyzers, or they are the first group)
                parallel = (role != AgentRole.EXECUTOR) or not analyzers
                prompt   = self._build_system_prompt(ast, chunk, role)
                agents.append(AgentSpec(
                    agent_id      = agent_id,
                    role          = role,
                    state_names   = tuple(s.name for s in chunk),
                    system_prompt = prompt,
                    token_budget  = 0,   # filled in by emit()
                    can_parallel  = parallel,
                ))

        _add_group(analyzers,  AgentRole.ANALYZER,  "analyzer")
        _add_group(executors,  AgentRole.EXECUTOR,  "executor")
        _add_group(responders, AgentRole.RESPONDER, "responder")

        # Finalize → auditor
        if ast.finalize:
            agents.append(AgentSpec(
                agent_id      = "auditor",
                role          = AgentRole.AUDITOR,
                state_names   = (ast.finalize.name,),
                system_prompt = (
                    f"[AXIOM:{ast.module}::auditor|role:auditor]\n"
                    f"LOG:{','.join(ast.finalize.log_telemetry)}\n"
                    f"SIGN:{ast.finalize.sign_payload}\n"
                    f"DEST:{ast.finalize.destination}"
                ),
                token_budget  = 0,
                can_parallel  = False,
            ))

        return agents

    def _build_system_prompt(
        self,
        ast:    BlueprintAST,
        states: List[ASTState],
        role:   AgentRole,
    ) -> str:
        """Compact, token-optimised system prompt for one agent."""
        agent_tag = f"[AXIOM:{ast.module}::{role.value}|role:{role.value}]"
        inv_line  = "INV:" + ",".join(inv.name for inv in ast.invariants) \
                    if ast.invariants else ""
        state_line = "STATES:" + ">".join(s.name for s in states)

        # ANALYZER: include weight vectors
        weight_lines = []
        if role == AgentRole.ANALYZER:
            for s in states:
                if s.probabilistic_weights:
                    wstr = ",".join(
                        f"{w.label}={w.weight:.2f}" for w in s.probabilistic_weights
                    )
                    weight_lines.append(f"W[{s.name}]:{wstr}|thr:{s.transition_threshold:.2f}")

        # EXECUTOR: include schema fields
        schema_lines = []
        if role == AgentRole.EXECUTOR:
            for s in states:
                if s.output_schema_fields:
                    schema_lines.append(f"SCHEMA[{s.name}]:{','.join(s.output_schema_fields)}")

        # RESPONDER: include prompt snippets (first 80 chars)
        prompt_lines = []
        if role == AgentRole.RESPONDER:
            for s in states:
                if s.prompt:
                    prompt_lines.append(f"TASK[{s.name}]:{s.prompt[:80]}")

        parts = [agent_tag]
        if inv_line:
            parts.append(inv_line)
        parts.append(state_line)
        parts.extend(weight_lines)
        parts.extend(schema_lines)
        parts.extend(prompt_lines)
        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Compiler façade
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CompileResult:
    ir:           Optional[StateMachineIR]
    diagnostics:  DiagnosticReport
    opt_report:   Optional[OptimizationReport]
    success:      bool


class AxiomCompiler:
    """End-to-end compiler: source → ``StateMachineIR``.

    Usage::

        result = AxiomCompiler().compile_file("refactor_agent.axiom")
        if result.success:
            ir = result.ir
            # ir.to_json() → store / cache
            sm = BlueprintStateMachine.from_ir(ir)
            tel = sm.run()
    """

    def __init__(
        self,
        strict:           bool = False,
        min_weight:       float = 0.04,
        skip_optimizer:   bool = False,
    ) -> None:
        self._strict       = strict
        self._skip_opt     = skip_optimizer
        self._opt          = BlueprintOptimizer()
        self._opt.MIN_WEIGHT = min_weight

    def compile(self, source: str) -> CompileResult:
        # Stage 1+2: lex + parse
        try:
            ast = BlueprintASTParser().parse(source)
        except (LexError, ParseError) as exc:
            diag = DiagnosticReport(
                diagnostics=(Diagnostic("error", "PARSE", str(exc), 0),),
                has_errors=True,
            )
            return CompileResult(None, diag, None, False)

        # Stage 3: analyze
        report = BlueprintAnalyzer().analyze(ast)
        if report.has_errors and self._strict:
            return CompileResult(None, report, None, False)

        # Stage 4: optimize
        opt_report: Optional[OptimizationReport] = None
        if not self._skip_opt:
            ast, opt_report = self._opt.optimize(ast)

        # Stage 5: codegen
        ir = BlueprintCodegen().emit(ast)
        return CompileResult(ir, report, opt_report, True)

    def compile_file(self, path: str | Path) -> CompileResult:
        return self.compile(Path(path).read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _chunked(lst: List, n: int) -> Iterator[List]:
    for i in range(0, max(len(lst), 1), n):
        chunk = lst[i : i + n]
        if chunk:
            yield chunk


def _replace_ast(ast: BlueprintAST, **kw) -> BlueprintAST:
    d = {
        "module": ast.module, "imports": ast.imports,
        "source_hash": ast.source_hash, "runtime": ast.runtime,
        "invariants": ast.invariants, "states": ast.states, "finalize": ast.finalize,
    }
    d.update(kw)
    return BlueprintAST(**d)


def _replace_state(s: ASTState, **kw) -> ASTState:
    d = {
        "name": s.name, "line": s.line, "prompt": s.prompt,
        "action": s.action, "probabilistic_weights": s.probabilistic_weights,
        "transition_threshold": s.transition_threshold, "transitions": s.transitions,
        "output_schema_fields": s.output_schema_fields, "retry_spec": s.retry_spec,
    }
    d.update(kw)
    return ASTState(**d)
