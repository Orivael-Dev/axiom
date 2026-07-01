#!/usr/bin/env python3
"""Local end-to-end test driver for the cognitive gating pipeline.

Self-contained: builds a temporary domain-pack store with two disjoint domains
(engineering + personal), sets the AXIOM_* environment variables exactly as a
deployment would, wires the pipeline through CognitiveGatingPipeline.from_env(),
and runs a batch of queries — printing what each gating layer did per query.

This exercises the SAME from_env() wiring the research server uses, so a green
run here means the server-side wiring is sound, without needing fastapi/uvicorn
or a live LLM backend.

Run:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    python3 local_test_cognitive_gating.py
    python3 local_test_cognitive_gating.py --sessions 3   # show cross-session promotion

What to look for:
    - engineering queries route to the engineering pack (personal noise excluded)
    - personal queries route to the personal pack
    - intent_filter is detected per query (procedure / definition / ...)
    - after enough sessions, fragments promote into hot_knowledge and appear in
      extra_context() — the block injected into every LLM call
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── synthetic two-domain corpus ─────────────────────────────────────────────────

_CORPUS: List[Tuple[str, str, str]] = [
    ("engineering", "srd.txt",
     "Stochastic Residual Dithering reduces precision loss during weight "
     "quantization by distributing error variance across the residual, "
     "mitigating quantization artefacts at low bits-per-weight."),
    ("engineering", "kvcache.txt",
     "The key-value cache stores attention keys and values. Paged attention and "
     "FP8 cache precision reduce the VRAM footprint during long-context inference."),
    ("engineering", "lora.txt",
     "A LoRA adapter injects low-rank matrices into frozen base model weights to "
     "fine-tune on domain data without updating the full parameter set."),
    ("personal", "roast.txt",
     "To roast the vegetables for dinner, preheat the oven to 200 degrees and "
     "toss the carrots and potatoes in olive oil. Roast for 40 minutes."),
    ("personal", "calendar.txt",
     "Calendar this week: dentist on Tuesday, dinner with Sarah on Thursday, "
     "pick up dry cleaning Friday. Best time to call mum is Sunday afternoon."),
    ("personal", "pasta.txt",
     "For the pasta sauce, fry garlic in olive oil, add crushed tomatoes and "
     "simmer to reduce for twenty minutes. Season with salt and fresh basil."),
]

_QUERIES: List[Tuple[str, str]] = [
    ("engineering", "how do I reduce error when quantizing model weights?"),
    ("engineering", "what lowers VRAM during long context inference?"),
    ("engineering", "how to fine-tune without updating all parameters?"),
    ("personal",    "how long do I roast the vegetables for dinner?"),
    ("personal",    "what appointments do I have this week?"),
    ("personal",    "how do I reduce the pasta sauce?"),
]


def _build_store(base: Path) -> Path:
    """Build + install the two domain packs; return the store base dir."""
    from axiom_domain_ingester import DomainIngester
    from axiom_domain_pack import DomainPackManifest, DomainPackStore, build_pack

    store_dir = base / "store"
    per_domain: dict[str, Path] = {}
    src = base / "src"
    src.mkdir(parents=True, exist_ok=True)

    for domain, fname, text in _CORPUS:
        idx = base / f"{domain}_index"
        per_domain.setdefault(domain, idx)
        p = src / fname
        p.write_text(text, encoding="utf-8")
        DomainIngester(domain=domain, index_dir=idx, session_id="build").ingest_file(p)

    for domain, idx in per_domain.items():
        m = DomainPackManifest(
            name=f"{domain}-pack", title=domain.title(), description=domain,
            version="1.0.0", author="local-test", license="Apache-2.0", domain=domain,
        )
        pack_dir = build_pack(manifest=m, index_dir=idx, output_dir=base / "packs")
        DomainPackStore(base_dir=store_dir).install(pack_dir)
    return store_dir


def _run_once(label: str) -> List[dict]:
    """Build a fresh pipeline from env and run all queries; return telemetry rows."""
    from axiom_cognitive_gating import CognitiveGatingPipeline

    gating = CognitiveGatingPipeline.from_env()
    print(f"\n[{label}]  layers={gating.layers()}  session={gating.session_id}")
    rows: List[dict] = []
    for want, query in _QUERIES:
        hits, tel = gating.retrieve(query, k=5)
        ok = "✓" if tel.routed_to == want else "✗"
        print(f"  {ok} {query[:46]:<48} → routed={tel.routed_to or '(none)':<12}"
              f" intent={tel.intent_filter or '-':<12} hits={tel.hits_returned}"
              f" rec={tel.fragments_recorded}")
        rows.append({**tel.to_dict(), "want": want})
    gating.promote()
    return rows


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=3,
                    help="number of sessions to simulate (cross-session promotion)")
    ap.add_argument("--hyde", action="store_true",
                    help="enable HyDE expansion (needs AXIOM_QUERY_REWRITE + backend)")
    args = ap.parse_args(argv)

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: export AXIOM_MASTER_KEY first "
              "(python3 -c \"import secrets; print(secrets.token_hex(32))\")",
              file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="axiom_gating_local_") as d:
        base = Path(d)
        store_dir = _build_store(base)

        # Wire the environment exactly as a deployment would.
        os.environ["AXIOM_DOMAIN_STORE"] = str(store_dir)
        os.environ["AXIOM_KNOWLEDGE_COOKIE"] = str(base / "knowledge.cookie.json")
        if args.hyde:
            os.environ["AXIOM_HYDE"] = "1"
            os.environ.setdefault("AXIOM_QUERY_REWRITE", "general")
        else:
            os.environ.pop("AXIOM_HYDE", None)

        print("=" * 72)
        print("Cognitive Gating — local end-to-end wiring test")
        print(f"  domain store : {store_dir}")
        print(f"  knowledge    : {base / 'knowledge.cookie.json'}")
        print(f"  HyDE         : {'on' if args.hyde else 'off'}")
        print("=" * 72)

        # Run N sessions; each is a fresh from_env() pipeline (new session id),
        # all writing to the same knowledge cookie so fragments accumulate and
        # promote across sessions.
        routing_correct = 0
        routing_total = 0
        for i in range(args.sessions):
            rows = _run_once(f"session {i + 1}/{args.sessions}")
            routing_correct += sum(1 for r in rows if r["routed_to"] == r["want"])
            routing_total += len(rows)

        # After N sessions, inspect promoted hot knowledge.
        from axiom_cognitive_gating import CognitiveGatingPipeline
        final = CognitiveGatingPipeline.from_env()
        final.promote()
        ctx = final.extra_context()
        from axiom_knowledge_cookie import KnowledgeCookieStore
        cookie = KnowledgeCookieStore(Path(os.environ["AXIOM_KNOWLEDGE_COOKIE"])).load()

        print("\n" + "=" * 72)
        print("Results")
        print("=" * 72)
        print(f"  routing accuracy   : {routing_correct}/{routing_total} "
              f"({100*routing_correct/max(routing_total,1):.0f}%)")
        if cookie is not None:
            promoted = [f for f in cookie.fragments.values() if f.promoted]
            print(f"  fragments stored   : {len(cookie.fragments)}")
            print(f"  promoted (hot)     : {len(promoted)} "
                  f"(threshold = {cookie.PROMOTE_THRESHOLD} sessions)")
        has_hot = "hot_knowledge" in ctx
        print(f"  hot_knowledge ready: {has_hot} "
              f"(this block is injected into every LLM call)")
        if has_hot:
            preview = ctx["hot_knowledge"][:120].replace("\n", " ")
            print(f"  hot preview        : {preview}…")

        all_routed = routing_correct == routing_total
        print("\n  " + ("PASS — every query routed to its correct domain, "
                         "knowledge promoted." if all_routed and has_hot
                         else "PARTIAL — see rows above."))
        return 0 if all_routed else 1


if __name__ == "__main__":
    raise SystemExit(main())
