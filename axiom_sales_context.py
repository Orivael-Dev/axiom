"""Sales knowledge store + delegate-aware context injector.

A small, hand-edited body of sales knowledge (companies, buyers,
objections, competitors, call notes) lives under
`docs/internal/sales/`. This module loads it and produces a
**delegate-specific** context snippet that gets auto-injected
into the five sales-related exoskeleton delegates:

    sales_objection_handling
    outreach_personalization
    enterprise_targeting
    competitive_analysis
    customer_discovery

The injected snippet rides as an `extra_context` block in the
delegate prompt — it is NOT in the system prompt, so it does
not bloat the AXM container or change the signature.

Programmatic usage:
    from axiom_sales_context import SalesContext
    ctx = SalesContext.from_default()
    snippet = ctx.relevant_for(
        "sales_objection_handling",
        query="buyer says no budget right now",
    )

CLI:
    python3 -m axiom_sales_context list buyers
    python3 -m axiom_sales_context add objection \\
        '{"class":"BUDGET","source":"Acme CTO","text":"...","response":"..."}'
    python3 -m axiom_sales_context show buyer "Jane Doe"
    python3 -m axiom_sales_context relevant outreach_personalization \\
        --query "Jane Doe at Acme"

JSONL schemas (one record per line):

    companies.jsonl
        {name, industry, size, region, signal, status, notes, created_utc}

    buyers.jsonl
        {name, role, company, email, signal, last_contact_utc, notes}

    objections.jsonl
        {class, source, text, response, outcome, created_utc}
        class ∈ {TOO_EARLY, TOO_TECHNICAL, COMPLIANCE_RISK,
                BUDGET, INTEGRATION_FRICTION, OTHER}

    competitors.jsonl
        {name, category, their_strength, their_gap,
         axiom_wedge, honest_concession, last_reviewed_utc}

Plus free-form workspace files: `calls/<date>.md` and `notes.md`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


SALES_USE_CASES: tuple[str, ...] = (
    "sales_objection_handling",
    "outreach_personalization",
    "enterprise_targeting",
    "competitive_analysis",
    "customer_discovery",
)


_STORE_FILES: dict[str, str] = {
    "company":    "companies.jsonl",
    "buyer":      "buyers.jsonl",
    "objection":  "objections.jsonl",
    "competitor": "competitors.jsonl",
}

_PLURAL: dict[str, str] = {
    "company":    "companies",
    "buyer":      "buyers",
    "objection":  "objections",
    "competitor": "competitors",
}

_OBJECTION_CLASSES: frozenset[str] = frozenset({
    "TOO_EARLY", "TOO_TECHNICAL", "COMPLIANCE_RISK",
    "BUDGET", "INTEGRATION_FRICTION", "OTHER",
})


def default_context_root() -> Path:
    """Where the sales context lives by default.

    Resolution order:
        1. AXIOM_SALES_CONTEXT_ROOT env var.
        2. `docs/internal/sales/` next to this module.
        3. `docs/internal/sales/` under cwd (last-resort fallback).
    """
    env = os.environ.get("AXIOM_SALES_CONTEXT_ROOT")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent
    return here / "docs" / "internal" / "sales"


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _tokenize(s: str) -> set[str]:
    """Lowercase alphanumeric word-set for keyword overlap scoring."""
    if not s:
        return set()
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            # Skip malformed lines so a typo doesn't break the runtime.
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")


@dataclass
class SalesContext:
    """In-memory snapshot of the on-disk sales store."""

    root:        Path
    companies:   list[dict] = field(default_factory=list)
    buyers:      list[dict] = field(default_factory=list)
    objections:  list[dict] = field(default_factory=list)
    competitors: list[dict] = field(default_factory=list)
    notes_text:  str        = ""
    call_files:  list[Path] = field(default_factory=list)

    # ── construction ─────────────────────────────────────────────────

    @classmethod
    def from_default(cls) -> "SalesContext":
        return cls.load(default_context_root())

    @classmethod
    def load(cls, root: Path | str) -> "SalesContext":
        root = Path(root)
        companies   = _read_jsonl(root / _STORE_FILES["company"])
        buyers      = _read_jsonl(root / _STORE_FILES["buyer"])
        objections  = _read_jsonl(root / _STORE_FILES["objection"])
        competitors = _read_jsonl(root / _STORE_FILES["competitor"])
        notes_path = root / "notes.md"
        notes_text = (
            notes_path.read_text(encoding="utf-8")
            if notes_path.exists() else ""
        )
        calls_dir = root / "calls"
        call_files: list[Path] = []
        if calls_dir.is_dir():
            for p in sorted(calls_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in (".md", ".txt"):
                    call_files.append(p)
        return cls(
            root=root,
            companies=companies, buyers=buyers,
            objections=objections, competitors=competitors,
            notes_text=notes_text, call_files=call_files,
        )

    # ── core retrieval ───────────────────────────────────────────────

    def relevant_for(
        self,
        use_case: str,
        query: str,
        *,
        token_budget: int = 250,
    ) -> str:
        """Return a compact context block tailored to `use_case`.

        Empty string if there's nothing relevant — the caller treats
        that as "skip injection."

        `token_budget` is enforced approximately (~4 chars per token).
        """
        if use_case not in SALES_USE_CASES:
            return ""
        query_tokens = _tokenize(query)
        if use_case == "sales_objection_handling":
            block = self._block_objections(query_tokens)
        elif use_case == "outreach_personalization":
            block = self._block_outreach(query_tokens, query)
        elif use_case == "enterprise_targeting":
            block = self._block_targeting(query_tokens)
        elif use_case == "competitive_analysis":
            block = self._block_competitive(query_tokens, query)
        elif use_case == "customer_discovery":
            block = self._block_discovery(query_tokens, query)
        else:
            block = ""
        return _truncate_chars(block, max(8, int(token_budget)) * 4)

    # ── per-use-case selectors ───────────────────────────────────────

    def _block_objections(self, qtoks: set[str]) -> str:
        if not self.objections:
            return ""
        scored = sorted(
            self.objections,
            key=lambda o: (
                _score_record(o, qtoks, ("text", "class", "source")),
                o.get("created_utc", ""),
            ),
            reverse=True,
        )
        lines = ["PRIOR OBJECTIONS (most relevant first):"]
        for o in scored[:3]:
            cls = str(o.get("class", "OTHER")).strip() or "OTHER"
            src = str(o.get("source", "")).strip()
            txt = _one_line(o.get("text", ""))
            rsp = _one_line(o.get("response", ""))
            outcome = _one_line(o.get("outcome", ""))
            head = f"- [{cls}]"
            if src:
                head += f" from {src}:"
            lines.append(f"{head} {txt}")
            if rsp:
                lines.append(f"  prior response: {rsp}")
            if outcome:
                lines.append(f"  outcome: {outcome}")
        return "\n".join(lines)

    def _block_outreach(self, qtoks: set[str], query: str) -> str:
        if not self.buyers and not self.companies:
            return ""
        buyer = _best_match(
            self.buyers, qtoks,
            ("name", "role", "company", "signal"),
        )
        company = None
        if buyer is not None and buyer.get("company"):
            company = next(
                (c for c in self.companies
                 if str(c.get("name", "")).lower()
                 == str(buyer["company"]).lower()),
                None,
            )
        if company is None:
            company = _best_match(
                self.companies, qtoks,
                ("name", "industry", "signal", "notes"),
            )
        if buyer is None and company is None:
            return ""
        lines = ["BUYER + COMPANY CONTEXT:"]
        if buyer is not None:
            lines.append(
                f"- buyer: {buyer.get('name', '?')} "
                f"({buyer.get('role', 'role?')}) "
                f"at {buyer.get('company', '?')}"
            )
            if buyer.get("signal"):
                lines.append(f"  signal: {_one_line(buyer['signal'])}")
            if buyer.get("last_contact_utc"):
                lines.append(
                    f"  last contact: {buyer['last_contact_utc']}"
                )
        if company is not None:
            lines.append(
                f"- company: {company.get('name', '?')} "
                f"(industry={company.get('industry', '?')}, "
                f"size={company.get('size', '?')})"
            )
            if company.get("signal"):
                lines.append(f"  signal: {_one_line(company['signal'])}")
            if company.get("status"):
                lines.append(f"  status: {company['status']}")
        # Look for a prior objection from the same company.
        same_co = None
        if buyer is not None:
            target = str(buyer.get("company", "")).lower()
            same_co = next(
                (o for o in reversed(self.objections)
                 if target
                 and target in str(o.get("source", "")).lower()),
                None,
            )
        if same_co is not None:
            lines.append(
                f"- prior objection from this company: "
                f"[{same_co.get('class', 'OTHER')}] "
                f"{_one_line(same_co.get('text', ''))}"
            )
        return "\n".join(lines)

    def _block_targeting(self, qtoks: set[str]) -> str:
        if not self.companies:
            return ""
        scored = sorted(
            self.companies,
            key=lambda c: _score_record(
                c, qtoks, ("industry", "name", "signal", "notes")
            ),
            reverse=True,
        )
        top = [c for c in scored if _score_record(
            c, qtoks, ("industry", "name", "signal", "notes")) > 0]
        if not top:
            top = scored[:5]
        else:
            top = top[:5]
        lines = ["TARGET-ACCOUNT POOL (most relevant first):"]
        for c in top:
            name = c.get("name", "?")
            ind  = c.get("industry", "?")
            size = c.get("size", "?")
            sig  = _one_line(c.get("signal", ""))
            line = f"- {name} ({ind}, {size})"
            if sig:
                line += f" — signal: {sig}"
            lines.append(line)
        return "\n".join(lines)

    def _block_competitive(self, qtoks: set[str], query: str) -> str:
        if not self.competitors:
            return ""
        named = _best_match(
            self.competitors, qtoks,
            ("name", "category"),
        )
        lines = ["COMPETITOR INTEL:"]
        if named is not None:
            lines.append(
                f"- named: {named.get('name', '?')} "
                f"({named.get('category', '?')})"
            )
            for k, label in (
                ("their_strength",    "their strength"),
                ("their_gap",         "their gap"),
                ("axiom_wedge",       "AXIOM wedge"),
                ("honest_concession", "honest concession"),
            ):
                v = _one_line(named.get(k, ""))
                if v:
                    lines.append(f"  {label}: {v}")
        # One-line gap reminders for the others — supports the
        # honest-concession habit in the delegate's system prompt.
        others = [c for c in self.competitors if c is not named]
        if others:
            lines.append("- other competitors (gap reminders):")
            for c in others[:4]:
                gap = _one_line(c.get("their_gap", ""))
                if c.get("name") and gap:
                    lines.append(f"    {c['name']}: gap = {gap}")
        return "\n".join(lines)

    def _block_discovery(self, qtoks: set[str], query: str) -> str:
        lines: list[str] = []
        # Past call headers from the same company, if any are named.
        if self.call_files:
            qlower = query.lower()
            matching: list[Path] = []
            for p in self.call_files:
                head = _read_head(p)
                if any(t and t in head.lower() for t in qtoks):
                    matching.append(p)
                elif any(t and t in p.name.lower() for t in qtoks):
                    matching.append(p)
            if not matching:
                matching = self.call_files[-3:]
            if matching:
                lines.append("PAST CALLS:")
                for p in matching[-3:]:
                    header = _read_head(p).splitlines()[0:1]
                    summary = header[0] if header else p.stem
                    lines.append(f"- {p.name}: {_one_line(summary)}")
        # Pain themes pulled from recent objections (last 5).
        if self.objections:
            recent = sorted(
                self.objections,
                key=lambda o: o.get("created_utc", ""),
            )[-5:]
            classes = sorted({
                str(o.get("class", "OTHER")) for o in recent
            })
            if classes:
                lines.append(
                    "RECENT PAIN THEMES (from objection log): "
                    + ", ".join(classes)
                )
        return "\n".join(lines)

    # ── mutation ─────────────────────────────────────────────────────

    def add(self, kind: str, record: dict) -> dict:
        """Validate + persist a single record. Returns the stored record
        (with `created_utc` filled in if absent)."""
        if kind not in _STORE_FILES:
            raise ValueError(
                f"unknown kind {kind!r}; expected one of "
                f"{sorted(_STORE_FILES)}"
            )
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        record = dict(record)
        if kind == "objection":
            cls = str(record.get("class", "OTHER")).upper()
            if cls not in _OBJECTION_CLASSES:
                cls = "OTHER"
            record["class"] = cls
        record.setdefault("created_utc", _utc_now_iso())
        path = self.root / _STORE_FILES[kind]
        _append_jsonl(path, record)
        # Reflect in the in-memory snapshot so callers see it.
        getattr(self, _PLURAL[kind]).append(record)
        return record


# ── helpers (module-private) ─────────────────────────────────────────


def _one_line(s: Any) -> str:
    if s is None:
        return ""
    return " ".join(str(s).split())


def _truncate_chars(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rstrip()
    return cut + " …"


def _score_record(rec: dict, qtoks: set[str], fields: tuple[str, ...]) -> int:
    if not qtoks:
        return 0
    blob = " ".join(str(rec.get(f, "") or "") for f in fields)
    return len(qtoks & _tokenize(blob))


def _best_match(
    records: list[dict],
    qtoks: set[str],
    fields: tuple[str, ...],
) -> Optional[dict]:
    if not records:
        return None
    best, best_score = None, 0
    for r in records:
        s = _score_record(r, qtoks, fields)
        if s > best_score:
            best, best_score = r, s
    return best


def _read_head(path: Path, n_lines: int = 5) -> str:
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = []
            for _ in range(n_lines):
                line = fh.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
            return "\n".join(lines)
    except OSError:
        return ""


# ── CLI ──────────────────────────────────────────────────────────────


def _cli_list(args, ctx: SalesContext) -> int:
    pool = _resolve_pool(ctx, args.kind)
    if pool is None:
        print(f"error: unknown kind {args.kind!r}", file=sys.stderr)
        return 2
    if not pool:
        print(f"(no {args.kind} records)")
        return 0
    for r in pool:
        print(json.dumps(r, ensure_ascii=True))
    return 0


def _cli_show(args, ctx: SalesContext) -> int:
    pool = _resolve_pool(ctx, args.kind)
    if pool is None:
        print(f"error: unknown kind {args.kind!r}", file=sys.stderr)
        return 2
    needle = args.name.lower()
    for r in pool:
        if str(r.get("name", "")).lower() == needle:
            print(json.dumps(r, indent=2, ensure_ascii=True))
            return 0
    print(f"(no {args.kind} matched {args.name!r})", file=sys.stderr)
    return 1


def _cli_add(args, ctx: SalesContext) -> int:
    if args.kind not in _STORE_FILES:
        print(f"error: unknown kind {args.kind!r}", file=sys.stderr)
        return 2
    raw = args.record
    if raw == "-" or raw is None:
        raw = sys.stdin.read()
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: record must be JSON: {e}", file=sys.stderr)
        return 2
    try:
        stored = ctx.add(args.kind, record)
    except (TypeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(stored, indent=2, ensure_ascii=True))
    return 0


def _cli_relevant(args, ctx: SalesContext) -> int:
    if args.use_case not in SALES_USE_CASES:
        print(
            f"error: use_case must be one of {sorted(SALES_USE_CASES)}",
            file=sys.stderr,
        )
        return 2
    snippet = ctx.relevant_for(
        args.use_case, args.query or "",
        token_budget=args.token_budget,
    )
    if not snippet:
        print(f"(no sales context relevant to {args.use_case})")
        return 0
    print(snippet)
    return 0


def _resolve_pool(ctx: SalesContext, kind: str) -> Optional[list[dict]]:
    # Accept singular or plural for ergonomics.
    if kind in _STORE_FILES:
        return getattr(ctx, _PLURAL[kind])
    inv = {v: k for k, v in _PLURAL.items()}
    if kind in inv:
        return getattr(ctx, kind)
    return None


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-sales-context",
        description="Sales knowledge store + delegate context injector.",
    )
    ap.add_argument(
        "--root",
        help="path to the sales store dir "
             "(default: docs/internal/sales/ or "
             "$AXIOM_SALES_CONTEXT_ROOT)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="dump records of a kind")
    p_list.add_argument(
        "kind",
        help="company|buyer|objection|competitor "
             "(plurals accepted)",
    )
    p_list.set_defaults(func=_cli_list)

    p_show = sub.add_parser("show", help="show one record by name")
    p_show.add_argument("kind")
    p_show.add_argument("name")
    p_show.set_defaults(func=_cli_show)

    p_add = sub.add_parser("add", help="append a record (JSON dict)")
    p_add.add_argument(
        "kind",
        help="company|buyer|objection|competitor",
    )
    p_add.add_argument(
        "record", nargs="?", default=None,
        help="JSON dict; '-' or omitted reads from stdin",
    )
    p_add.set_defaults(func=_cli_add)

    p_rel = sub.add_parser(
        "relevant",
        help="preview the context block that would be auto-injected",
    )
    p_rel.add_argument("use_case")
    p_rel.add_argument("--query", "-q", default="",
                       help="the input the delegate would receive")
    p_rel.add_argument("--token-budget", type=int, default=250)
    p_rel.set_defaults(func=_cli_relevant)

    args = ap.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root) if args.root else default_context_root()
    ctx = SalesContext.load(root)
    return args.func(args, ctx)


if __name__ == "__main__":
    raise SystemExit(main())
