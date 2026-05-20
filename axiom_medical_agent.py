"""MedicalResearchAgent — high-level orchestrator for the medical
research instrument.

Composes:
    - axiom_medical_container.build_medical_container
    - examples/medical_pack.MEDICAL_DELEGATES (6 layer delegates)
    - axiom_event_token.Coordinator.compose_from_delegates (per layer)
    - axiom_medical_coordinator.MedicalCoordinatorToken (cross-layer)
    - axiom_medical_governance.MedicalGovernanceCheck (deterministic
      post-processing on the governance delegate)
    - axiom_medical_descriptor.render + wrap_for_llm_prompt (synth)
    - axiom_medical_ledger (audit)

Per PDF section 6, the activation profile selects which layer
delegates fire for the current question. Sibling of
`ExoskeletonAgent` — NOT a subclass, because the workflow differs.

Programmatic:
    from axiom_medical_agent import MedicalResearchAgent
    agent = MedicalResearchAgent.from_default_pack(backend=...)
    result = agent.research(
        research_question="What mechanisms link GLP-1 drugs to "
                          "reduced inflammation?",
        sources=[{"name": "Cochrane 2023 systematic review",
                  "source_type": "systematic_review",
                  "text": "..."}],
        profile="mechanism",
    )
    print(result.descriptor)
    print(result.coordinator_tokens[0].to_json(indent=2))

CLI:
    python3 -m axiom_medical_agent \\
        --question "What mechanisms link GLP-1 drugs to inflammation?" \\
        --profile mechanism --backend local
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from axiom_medical_container import (
    CANNOT_MUTATE_FIELDS,
    MedicalContainerError,
    MedicalContainerSpec,
    build_medical_container,
    load_medical_container,
    verify_cannot_mutate,
)
from axiom_medical_coordinator import (
    MedicalCoordinatorToken,
    MEDICAL_LAYERS,
)
from axiom_medical_descriptor import (
    DEFAULT_MEDICAL_SYSTEM,
    render as render_descriptor,
    wrap_for_llm_prompt,
)
from axiom_medical_governance import MedicalGovernanceCheck
from axiom_medical_safety import classify_source


# ── Activation profiles (PDF section 6) ─────────────────────────────


# Each profile maps a question-type to the set of medical layer
# delegates that should fire. The orchestrator fires only the
# matching subset.
LAYER_ACTIVATION_PROFILES: dict[str, tuple[str, ...]] = {
    "summarize":     ("source", "claim", "data", "governance"),
    "mechanism":     ("bio", "physics", "data", "governance"),
    "compare":       ("source", "data", "claim", "governance"),
    "patient_apply": ("governance",),
    "hypothesize":   ("bio", "source", "governance"),
}


_LAYER_TO_DELEGATE: dict[str, str] = {
    "source":      "medical_source",
    "claim":       "medical_claim",
    "text":        "medical_claim",       # alias per PDF
    "data":        "medical_data",
    "bio":         "medical_bio",
    "physics":     "medical_physics",
    "governance":  "medical_governance",
}


# ── Result dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class ResearchResult:
    research_question:      str
    profile:                str
    container_id:           str
    event_tokens:           tuple = ()        # tuple[EventToken, ...]
    coordinator_tokens:     tuple = ()        # tuple[MedicalCoordinatorToken, ...]
    descriptor:             str = ""
    manifest_root:          str = ""
    requires_human_review:  bool = False
    tier_distribution:      dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "research_question":     self.research_question,
            "profile":               self.profile,
            "container_id":          self.container_id,
            "event_tokens": [t.to_dict() for t in self.event_tokens],
            "coordinator_tokens": [
                c.to_dict() for c in self.coordinator_tokens
            ],
            "descriptor":            self.descriptor,
            "manifest_root":         self.manifest_root,
            "requires_human_review": self.requires_human_review,
            "tier_distribution":     dict(self.tier_distribution),
        }


class MedicalAgentError(RuntimeError):
    """Container mismatch, missing delegate, or backend failure."""


# ── Agent ───────────────────────────────────────────────────────────


class MedicalResearchAgent:
    """Pick-a-profile orchestrator over the 6-delegate medical pack."""

    def __init__(
        self,
        container,                                  # AXMContainer
        *,
        backend=None,
        ledger=None,
        spec: Optional[MedicalContainerSpec] = None,
        core_dict: Optional[dict] = None,
    ) -> None:
        if not getattr(container, "delegates", None):
            raise MedicalAgentError("AXM container has no delegates")
        self._container = container
        self._backend   = backend
        self._ledger    = ledger
        self._spec      = spec
        self._core      = core_dict or {}
        self._by_name   = {d.name: d for d in container.delegates}
        self._governance = MedicalGovernanceCheck()

    # ── construction ────────────────────────────────────────────────

    @classmethod
    def from_default_pack(
        cls,
        *,
        backend=None,
        ledger=None,
        research_question: str = "general medical research session",
        spec: Optional[MedicalContainerSpec] = None,
    ) -> "MedicalResearchAgent":
        """Build a medical AXM container in a tempdir + load it.

        Useful for one-off CLI invocations. The container is owned
        by the instance for the process lifetime.
        """
        tmp = Path(tempfile.mkdtemp(prefix="axiom_med_pack_"))
        if spec is None:
            spec = MedicalContainerSpec(
                container_id="axm-med-" + uuid.uuid4().hex[:8],
                research_question=research_question,
            )
        container = build_medical_container(spec, tmp / "medical.axm")
        _, core = load_medical_container(tmp / "medical.axm")
        instance = cls(container, backend=backend, ledger=ledger,
                       spec=spec, core_dict=core)
        instance._owned_tmpdir = tmp  # keep alive
        return instance

    @classmethod
    def from_path(
        cls,
        axm_path,
        *,
        backend=None,
        ledger=None,
    ) -> "MedicalResearchAgent":
        container, core = load_medical_container(axm_path)
        return cls(container, backend=backend, ledger=ledger,
                   core_dict=core)

    # ── public API ──────────────────────────────────────────────────

    def list_profiles(self) -> list[str]:
        return sorted(LAYER_ACTIVATION_PROFILES)

    def research(
        self,
        research_question: str,
        *,
        sources: Optional[list[dict]] = None,
        profile: str = "summarize",
    ) -> ResearchResult:
        """Run the activation profile across each source.

        - For each source, fire the active delegates (one per layer
          in the profile) → collect signed EventTokens.
        - Wrap the per-source bundle in a MedicalCoordinatorToken.
        - Render the bracketed descriptor for downstream LLM use.
        - Optionally append a signed ledger entry.

        Raises MedicalAgentError if any CANNOT_MUTATE field has
        been silently changed since the container was sealed.
        """
        if profile not in LAYER_ACTIVATION_PROFILES:
            raise MedicalAgentError(
                f"unknown profile {profile!r}. Known: "
                f"{sorted(LAYER_ACTIVATION_PROFILES)}"
            )
        if not isinstance(research_question, str) or \
                not research_question.strip():
            raise MedicalAgentError(
                "research_question must be a non-empty string"
            )
        self._enforce_cannot_mutate(research_question)

        active_layers = LAYER_ACTIVATION_PROFILES[profile]
        sources = sources or [{
            "name": "session-default",
            "text": research_question,
        }]

        event_tokens: list = []
        coord_tokens: list = []
        tier_counts: dict[str, int] = {str(i): 0 for i in range(1, 6)}
        any_human_review = False

        for src in sources:
            per_source_tokens = self._fire_layers(
                research_question, src, active_layers,
            )
            event_tokens.extend(per_source_tokens)

            # Tier accounting from any source delegate that ran.
            for tok in per_source_tokens:
                payload = self._primary_payload(tok)
                tier = (payload.get("evidence_tier") or
                        payload.get("source_tier"))
                if tier is None and "source_type" in payload:
                    tier = classify_source(payload)
                if tier is not None:
                    try:
                        key = str(int(tier))
                        tier_counts[key] = tier_counts.get(key, 0) + 1
                    except (TypeError, ValueError):
                        pass

            # Governance verdict drives requires_human_review.
            gov_block_reason = self._governance_verdict_for(
                per_source_tokens, research_question, src,
            )
            requires_review = bool(gov_block_reason)
            any_human_review = any_human_review or requires_review

            # Build a coordinator token per source.
            layer_assignments = self._layer_assignments(per_source_tokens)
            if not layer_assignments:
                continue
            primary = ("text" if "text" in layer_assignments
                       else next(iter(layer_assignments)))
            coord = MedicalCoordinatorToken.bind(
                event_tokens=per_source_tokens,
                layer_assignments=layer_assignments,
                summary=str(src.get("name") or research_question)[:160],
                primary_layer=primary,
                contradictions=(),
                requires_human_review=requires_review,
            )
            coord_tokens.append(coord)

        descriptor = render_descriptor(
            event_tokens,
            coord=coord_tokens[0] if coord_tokens else None,
        )
        manifest_root = self._manifest_root(event_tokens, coord_tokens)
        container_id = (
            self._spec.container_id if self._spec else
            self._core.get("container_id", "unknown")
        )

        if self._ledger is not None and coord_tokens:
            try:
                self._ledger.append(
                    coord_token=coord_tokens[0],
                    event_tokens=event_tokens,
                    research_question=research_question,
                    profile=profile,
                    container_id=container_id,
                    manifest_root=manifest_root,
                    tier_distribution=tier_counts,
                )
            except Exception:
                # Audit is best-effort; do not fail the session.
                pass

        return ResearchResult(
            research_question=research_question,
            profile=profile,
            container_id=container_id,
            event_tokens=tuple(event_tokens),
            coordinator_tokens=tuple(coord_tokens),
            descriptor=descriptor,
            manifest_root=manifest_root,
            requires_human_review=any_human_review,
            tier_distribution=tier_counts,
        )

    def synthesize(
        self,
        coord_tokens: list,
        *,
        question: str,
        event_tokens: Optional[list] = None,
        backend=None,
    ) -> str:
        """Render descriptor → wrap → call backend → return synthesis.

        Synthesis is NOT a signed event token (it's a derived
        artifact). Its hash gets folded into `manifest_root`.
        """
        from axiom_event_token.backends import default_backend
        be = backend or self._backend or default_backend()
        if event_tokens is None:
            event_tokens = []
            for c in coord_tokens:
                event_tokens.extend([
                    t for t in self._all_event_tokens()
                    if t.id in set(c.layer_links.values())
                ])
        descriptor = render_descriptor(
            event_tokens,
            coord=coord_tokens[0] if coord_tokens else None,
        )
        prompt = wrap_for_llm_prompt(
            [descriptor], user_question=question,
        )
        result = be.generate(
            system=DEFAULT_MEDICAL_SYSTEM,
            prompt=prompt,
            max_output_tokens=600,
        )
        return result.text

    # ── internals ───────────────────────────────────────────────────

    def _fire_layers(
        self,
        research_question: str,
        source: dict,
        active_layers: Iterable[str],
    ) -> list:
        """Fire one delegate per active layer; return the EventTokens."""
        from axiom_event_token.coordinator import Coordinator
        from axiom_event_token.backends import default_backend
        from axiom_event_token.router import RoutingDecision

        backend = self._backend or default_backend()
        input_text = self._compose_layer_input(research_question, source)

        out: list = []
        for layer in active_layers:
            dname = _LAYER_TO_DELEGATE.get(layer)
            if not dname or dname not in self._by_name:
                continue
            target = self._by_name[dname]

            class _OneShotRouter:
                def route(self, *, delegates, text=None,
                          audio_transcript=None):
                    names = tuple(d.name for d in delegates
                                  if d.name == target.name)
                    return RoutingDecision(
                        intent_class="EXPLICIT",
                        confidence=1.0,
                        delegate_names=names,
                        matched_on="text" if names else "empty",
                    )

            coord = Coordinator()
            token = coord.compose_from_delegates(
                axm_container=self._container,
                text=input_text,
                backend=backend,
                router=_OneShotRouter(),
                token_id=f"medevt_{uuid.uuid4().hex[:10]}",
            )
            out.append(token)
        return out

    def _governance_verdict_for(
        self,
        tokens: list,
        research_question: str,
        source: dict,
    ) -> Optional[str]:
        """Re-run deterministic governance over the inputs + outputs.

        Returns block_reason if anything trips, else None.
        """
        # Scan: the question itself, the source text, and every
        # delegate output payload.
        blob_parts = [research_question, source.get("text", ""),
                      source.get("name", "")]
        for tok in tokens:
            blob_parts.append(json.dumps(
                self._primary_payload(tok), sort_keys=True,
            ))
        v = self._governance.evaluate("\n".join(blob_parts))
        if v.requires_human_review:
            return v.block_reason or "human_review_threshold_reached"
        return None

    def _layer_assignments(self, tokens: list) -> dict[str, str]:
        """Map each medical layer name to the EventToken ID that
        supplies it. Uses each token's payload `delegate` field
        (set by DelegateAgent) to determine which layer it covers."""
        out: dict[str, str] = {}
        delegate_to_layer = {v: k for k, v in _LAYER_TO_DELEGATE.items()
                             if k != "text"}  # text is alias for claim
        delegate_to_layer["medical_claim"] = "text"
        for tok in tokens:
            payload = self._primary_payload(tok)
            dname = payload.get("delegate", "")
            layer = delegate_to_layer.get(dname)
            if layer and layer not in out:
                out[layer] = tok.id
        return out

    def _compose_layer_input(
        self, research_question: str, source: dict,
    ) -> str:
        """Bundle the question + source context into one prompt the
        delegate's system prompt can chew on."""
        parts = [f"RESEARCH QUESTION: {research_question.strip()}"]
        name = source.get("name") or source.get("title")
        if name:
            parts.append(f"SOURCE NAME: {name}")
        text = source.get("text") or source.get("abstract") or ""
        if text:
            parts.append(f"SOURCE TEXT: {text}")
        for k in ("doi", "pmid", "year", "publication_venue"):
            v = source.get(k)
            if v:
                parts.append(f"{k.upper()}: {v}")
        return "\n".join(parts)

    def _primary_payload(self, tok) -> dict:
        for slot in ("text", "governance", "physics", "qrf"):
            layer = getattr(tok, slot, None)
            if layer is not None and isinstance(layer.payload, dict):
                return layer.payload
        return {}

    def _all_event_tokens(self):
        # placeholder for future caching; today we only see fresh tokens
        return []

    def _enforce_cannot_mutate(self, research_question: str) -> None:
        """Guard against silent CANNOT_MUTATE changes mid-session.

        Special case: when the container was sealed with the default
        placeholder research_question, the first concrete question
        through `.research(...)` is accepted and the in-memory core
        is updated to match. Subsequent attempts to change the
        research_question hit a hard refusal.
        """
        if not self._core:
            return
        placeholder = "general medical research session"
        current_q = self._core.get("research_question")
        if current_q != research_question:
            if current_q == placeholder:
                self._core["research_question"] = research_question
                return
            raise MedicalAgentError(
                "CANNOT_MUTATE violation on fields: ['research_question']. "
                "Container must be re-packed to change the research question."
            )

    def _manifest_root(self, event_tokens, coord_tokens) -> str:
        """SHA-256 root over all signatures in the session."""
        import hashlib
        sig_list: list[str] = []
        for t in event_tokens:
            if getattr(t, "signature", ""):
                sig_list.append(t.signature)
        for c in coord_tokens:
            if getattr(c, "fusion_signature", ""):
                sig_list.append(c.fusion_signature)
        if not sig_list:
            return ""
        joined = "|".join(sig_list).encode("utf-8")
        return "sha256:" + hashlib.sha256(joined).hexdigest()


# ── CLI ─────────────────────────────────────────────────────────────


def _read_sources(args) -> Optional[list[dict]]:
    if args.source_file:
        path = Path(args.source_file)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines()
                    if line.strip()]
        d = json.loads(text)
        return d if isinstance(d, list) else [d]
    return None


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-medical-agent",
        description="Run the AXM medical research instrument.",
    )
    ap.add_argument("--question", "-q",
                    help="research question text")
    ap.add_argument("--profile", default="summarize",
                    choices=sorted(LAYER_ACTIVATION_PROFILES),
                    help="layer activation profile (default: summarize)")
    ap.add_argument("--list-profiles", action="store_true",
                    help="list available profiles + their layer sets")
    ap.add_argument("--source-file",
                    help="JSON or JSONL file of source dicts")
    ap.add_argument("--backend", choices=["local", "nim", "chain"],
                    default=None, help="force backend (default: env)")
    ap.add_argument("--save-tokens",
                    help="dir to write per-EventToken JSON + "
                         "_coordinator.json")
    ap.add_argument("--no-ledger", action="store_true",
                    help="skip medical-ledger append")
    ap.add_argument("--ledger",
                    help="medical-ledger JSONL path "
                         "(default: ~/.axiom/medical-ledger.jsonl, "
                         "override env AXIOM_MEDICAL_LEDGER)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.list_profiles:
        for name, layers in sorted(LAYER_ACTIVATION_PROFILES.items()):
            print(f"{name}: {', '.join(layers)}")
        return 0

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32 bytes hex).",
              file=sys.stderr)
        return 2

    if not args.question:
        ap.error("--question is required (or pass --list-profiles).")

    if args.backend == "chain":
        os.environ["AXIOM_BACKEND"] = "local,nim"
    elif args.backend:
        os.environ["AXIOM_BACKEND"] = args.backend

    ledger = None
    if not args.no_ledger:
        from axiom_medical_ledger import LedgerWriter, default_ledger_path
        ledger = LedgerWriter(
            Path(args.ledger) if args.ledger else default_ledger_path()
        )

    spec = MedicalContainerSpec(
        container_id="axm-med-" + uuid.uuid4().hex[:8],
        research_question=args.question,
    )
    try:
        agent = MedicalResearchAgent.from_default_pack(
            ledger=ledger, spec=spec,
            research_question=args.question,
        )
    except MedicalContainerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    sources = _read_sources(args)
    try:
        result = agent.research(
            args.question, sources=sources, profile=args.profile,
        )
    except MedicalAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Human-friendly output.
    print(f"# medical research session  profile={result.profile}  "
          f"container={result.container_id}")
    print(f"# event_tokens={len(result.event_tokens)}  "
          f"coordinators={len(result.coordinator_tokens)}  "
          f"requires_human_review={result.requires_human_review}")
    print(f"# manifest_root={result.manifest_root}")
    print()
    print(result.descriptor)

    if args.save_tokens:
        out_dir = Path(args.save_tokens)
        out_dir.mkdir(parents=True, exist_ok=True)
        for t in result.event_tokens:
            (out_dir / f"{t.id}.json").write_text(
                t.to_json(indent=2), encoding="utf-8",
            )
        for i, c in enumerate(result.coordinator_tokens):
            name = "_coordinator.json" if i == 0 \
                else f"_coordinator_{i}.json"
            (out_dir / name).write_text(
                c.to_json(indent=2), encoding="utf-8",
            )
        print(f"\n# tokens written to {args.save_tokens}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
