"""Company exoskeleton agent — explicit-invocation orchestrator for the
9 founder-workflow delegates in §9 of the 5-month roadmap.

Wraps the modular SLM-delegate runtime from `axiom_event_token` with an
explicit-invocation API. The caller picks which delegate runs by name —
no intent-class routing, no router decision to interpret. Reuses
`DelegateAgent` + `SLMBackend` + `EventToken` signing — this module
adds no new crypto, just orchestration + a CLI.

Each invocation returns a signed `EventToken` so the founder gets an
audit trail of every workflow run.

Programmatic usage:
    from axiom_exoskeleton import ExoskeletonAgent

    exo = ExoskeletonAgent.from_default_pack()
    token = exo.invoke(
        "outreach_personalization",
        "Buyer: CISO at a 1500-person fintech. Signal: posted job for "
        "AI Governance Lead three days ago.",
    )
    print(token.text.payload["output"])

CLI:
    python3 -m axiom_exoskeleton --list
    python3 -m axiom_exoskeleton outreach_personalization --input "..."
    python3 -m axiom_exoskeleton customer_discovery --input-file call.txt
    echo "..." | python3 -m axiom_exoskeleton sales_objection_handling
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Iterable, Optional


class ExoskeletonError(RuntimeError):
    """Use case unknown, container malformed, or backend setup failed."""


class ExoskeletonAgent:
    """Pick-a-delegate-by-name orchestrator.

    Holds one AXMContainer (the 9-delegate exoskeleton pack) and one
    SLMBackend. Each `invoke(...)` builds a fresh DelegateAgent and
    fires it; the resulting LayerReport is wrapped into a signed
    EventToken via the existing event-token Coordinator path.
    """

    def __init__(self, axm_container, backend=None) -> None:
        if not getattr(axm_container, "delegates", None):
            raise ExoskeletonError("AXM container has no delegates")
        self._container = axm_container
        self._backend   = backend
        self._by_name   = {d.name: d for d in axm_container.delegates}

    # ── construction ─────────────────────────────────────────────────

    @classmethod
    def from_path(cls, axm_path, backend=None) -> "ExoskeletonAgent":
        """Load an exoskeleton AXM container from a path on disk."""
        from axiom_axm import AXMContainer
        container = AXMContainer.from_path(str(axm_path))
        return cls(container, backend=backend)

    @classmethod
    def from_default_pack(cls, backend=None) -> "ExoskeletonAgent":
        """Build the 9-delegate pack in a tempdir + load it.

        The tempdir is owned by this instance for the process lifetime.
        Useful for one-off CLI invocations that don't need a persistent
        on-disk pack.
        """
        from examples.exoskeleton_pack import build_exoskeleton_pack
        tmp = Path(tempfile.mkdtemp(prefix="axiom_exo_pack_"))
        container = build_exoskeleton_pack(tmp / "exoskeleton.axm")
        instance = cls(container, backend=backend)
        instance._owned_tmpdir = tmp  # keep alive
        return instance

    # ── public API ───────────────────────────────────────────────────

    def use_cases(self) -> tuple[str, ...]:
        """Names of all packed delegates, in container order."""
        return tuple(d.name for d in self._container.delegates)

    def describe(self, use_case: str) -> dict:
        """Return a small description dict for one use case."""
        d = self._find(use_case)
        return {
            "name":           d.name,
            "intent_classes": list(d.intent_classes),
            "prompt_budget":  d.prompt_budget,
            "output_budget":  d.output_budget,
            "backend_chain":  list(d.backend_chain),
        }

    def invoke(
        self,
        use_case: str,
        input_text: str,
        *,
        extra_context: Optional[dict] = None,
    ):
        """Run the named delegate against `input_text`.

        Returns a signed `EventToken` whose `text` layer carries the
        delegate's structured output, token counts, backend identity,
        and latency.
        """
        if not isinstance(input_text, str) or not input_text.strip():
            raise ExoskeletonError("input_text must be a non-empty string")
        delegate = self._find(use_case)
        from axiom_event_token.coordinator import Coordinator
        from axiom_event_token.backends import default_backend
        from axiom_event_token.router import RoutingDecision

        backend = self._backend or default_backend()

        class _ExplicitRouter:
            """One-delegate router — ignores intent classification."""
            def route(self, *, delegates, text=None, audio_transcript=None):
                names = tuple(d.name for d in delegates
                               if d.name == delegate.name)
                if not names:
                    return RoutingDecision(
                        intent_class="EXPLICIT", confidence=1.0,
                        delegate_names=(), matched_on="empty",
                    )
                return RoutingDecision(
                    intent_class="EXPLICIT", confidence=1.0,
                    delegate_names=names, matched_on="text",
                )

        coord = Coordinator()
        token = coord.compose_from_delegates(
            axm_container=self._container,
            text=input_text,
            backend=backend,
            router=_ExplicitRouter(),
            token_id=f"exo_{uuid.uuid4().hex[:12]}",
        )
        return token

    # ── internals ────────────────────────────────────────────────────

    def _find(self, use_case: str):
        if use_case not in self._by_name:
            raise ExoskeletonError(
                f"unknown use case: {use_case!r}. "
                f"Available: {', '.join(sorted(self._by_name))}"
            )
        return self._by_name[use_case]


# ── Output formatting helpers ─────────────────────────────────────────────


def render_human(token) -> str:
    """Format an EventToken from `invoke(...)` for human reading."""
    if token.text is None:
        return "(no delegate fired)"
    p = token.text.payload
    lines = [
        f"# {p.get('delegate', '?')}",
        f"  backend={p.get('backend')}  model={p.get('model')}",
        f"  tokens in/out={p.get('input_tokens')}/{p.get('output_tokens')}  "
        f"latency={p.get('latency_ms')}ms",
        f"  signed_event_id={token.id}  verified={token.verify()}",
        "",
    ]
    if p.get("error"):
        lines.append(f"ERROR: {p['error']}")
    else:
        lines.append(p.get("output", "").rstrip())
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────


def _read_input(args) -> Optional[str]:
    if args.input is not None:
        return args.input
    if args.input_file:
        return Path(args.input_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        return data if data.strip() else None
    return None


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-exoskeleton",
        description="Run a founder-workflow delegate against an input.",
    )
    ap.add_argument("use_case", nargs="?",
                    help="delegate name (see --list)")
    ap.add_argument("--list", action="store_true",
                    help="list available use cases")
    ap.add_argument("--describe", action="store_true",
                    help="print delegate metadata instead of running it")
    ap.add_argument("--input", "-i",
                    help="inline input text")
    ap.add_argument("--input-file", "-f",
                    help="read input from a file")
    ap.add_argument("--pack",
                    help="path to a prebuilt exoskeleton AXM container "
                         "(default: build a fresh one in a tempdir)")
    ap.add_argument("--backend", choices=["local", "nim", "chain"],
                    default=None, help="force backend (default: env)")
    ap.add_argument("--save-token",
                    help="write the signed EventToken JSON to this path")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32 bytes hex).",
              file=sys.stderr)
        return 2

    if args.backend == "chain":
        os.environ["AXIOM_BACKEND"] = "local,nim"
    elif args.backend:
        os.environ["AXIOM_BACKEND"] = args.backend

    exo = (ExoskeletonAgent.from_path(args.pack) if args.pack
           else ExoskeletonAgent.from_default_pack())

    if args.list:
        for name in exo.use_cases():
            print(name)
        return 0

    if not args.use_case:
        ap.error("use_case is required (or pass --list).")

    if args.describe:
        import json
        print(json.dumps(exo.describe(args.use_case), indent=2))
        return 0

    text = _read_input(args)
    if not text:
        ap.error("provide input via --input, --input-file, or stdin.")

    try:
        token = exo.invoke(args.use_case, text)
    except ExoskeletonError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(render_human(token))

    if args.save_token:
        Path(args.save_token).write_text(token.to_json(indent=2),
                                          encoding="utf-8")
        print(f"\n# signed token written to {args.save_token}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
