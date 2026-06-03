"""
AUI rendering — adaptive layout for an assembled workspace.
==========================================================
The interface reshapes around the result: a refusal panel for unsafe
goals, a recalled-context panel when prior local context exists, or a
fresh-workspace panel otherwise. Pure string rendering (no I/O) so it is
trivially testable.
"""
from __future__ import annotations

from workspace.assembler import AssembledWorkspace

_WIDTH = 64


def _line(label: str = "") -> str:
    if not label:
        return "+" + "-" * (_WIDTH - 2) + "+"
    label = f" {label} "
    return "+" + label + "-" * (_WIDTH - 2 - len(label)) + "+"


def _row(text: str) -> str:
    text = text[: _WIDTH - 4]
    return "| " + text.ljust(_WIDTH - 4) + " |"


def render(ws: AssembledWorkspace) -> str:
    """Render an AssembledWorkspace as an adaptive terminal panel."""
    out = [_line("AX OS workspace"), _row(f"goal: {ws.goal}")]

    if not ws.allowed:
        out += [
            _line("refused"),
            _row(f"the intent gate refused this goal ({ws.intent_class})"),
            _row(f"reason: {ws.refusal or 'blocked'}"),
            _row("no workspace assembled, no context gathered"),
        ]
    else:
        out.append(_row(f"safety: ALLOWED  ({ws.intent_class}, conf {ws.confidence:.2f})"))
        if ws.has_context and ws.context:
            c = ws.context
            constraints = ", ".join(c.get("active_constraints") or []) or "(none)"
            out += [
                _line("recalled context"),
                _row(f"domain:      {c.get('domain', '')}"),
                _row(f"constraints: {constraints}"),
                _row(f"resolution:  {c.get('resolution', '')}"),
                _row(f"packet:      {str(c.get('packet_signature', ''))[:16]}…"),
            ]
        else:
            out += [
                _line("fresh workspace"),
                _row("no prior local context for this goal yet"),
                _row("(remember some context, then re-open to recall it)"),
            ]

    out.append(_line(f"signed {ws.signature[:16]}…"))
    return "\n".join(out)


def render_branch(ws, branch, trail) -> str:
    """Render a branch-aware dev workspace plus its signed audit trail.

    ``ws`` is an AssembledWorkspace, ``branch`` a BranchContext (or None),
    ``trail`` the axiom_ledger 'list' result for the recent events.
    """
    out = [render(ws)]

    if branch is not None and getattr(branch, "available", False):
        out += [
            _line("branch workspace"),
            _row(f"branch:  {branch.branch}"),
            _row(f"readme:  {'yes' if branch.has_readme else 'no'}   "
                 f"docs: {branch.docs_count}   tests: {branch.tests_count}"),
        ]
        for c in branch.recent_commits[:3]:
            out.append(_row(f"  {c}"))

    if trail:
        events = trail.get("events", [])
        verified = "all verified" if trail.get("all_verified") else "TAMPER DETECTED"
        out.append(_line(f"audit trail ({trail.get('count', 0)}, {verified})"))
        for e in events[-4:]:
            line = f"{e.get('event_type','')}: {e.get('outcome','') or '-'}"
            out.append(_row(line[: _WIDTH - 4]))

    return "\n".join(out)


def render_install_review(review) -> str:
    """Render the human approval screen for a sandboxed agent."""
    out = [_line("AX Store — install review"),
           _row(f"agent:   {review.agent}  v{review.version}")]
    if not review.valid_signature:
        out += [_line("rejected"),
                _row("signature did not verify — install refused"),
                _row(f"reason: {review.error or 'bad signature'}")]
        out.append(_line("not installed"))
        return "\n".join(out)

    acc = review.requested_access or {}
    out += [
        _row(f"signature: VALID    installed: sandbox (pair {str(review.pair_id)[:14]}…)"),
        _line("requested access"),
        _row(f"block patterns added: {acc.get('additional_block_patterns', 0)}"),
        _row(f"disabled classes:     {', '.join(acc.get('disabled_default_classes') or []) or '(none)'}"),
        _row(f"allow-only classes:   {acc.get('allow_only_classes') if acc.get('allow_only_classes') is not None else '(unrestricted)'}"),
        _row(f"tags:                 {', '.join(acc.get('tags') or []) or '(none)'}"),
        _line("authority"),
        _row(f"authorized to act: {'YES' if review.authorized else 'NO — awaiting human approval'}"),
    ]
    return "\n".join(out)


def render_authority(label: str, auth: dict) -> str:
    """Render an authority-state line (after approve / revoke / act-check)."""
    return "\n".join([
        _line(f"authority — {label}"),
        _row(f"state:      {auth.get('state', '?')}"),
        _row(f"authorized: {'YES' if auth.get('authorized') else 'NO'}"),
    ])
