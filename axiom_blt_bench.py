"""BLT (Bloat / Latency / Tokens) benchmark for KnowledgeCookie — measures the
three-way trade-off of always-injecting RAG fragments into LLM calls.

Axes
----
Bloat   — file size growth as fragments accumulate (bytes → KB)
Latency — time cost to load cookie, inject fragments into call, serialize back (ms)
Tokens  — added tokens per LLM call from hot_knowledge injection (4 chars/token)

Usage
-----
    python3 -m axiom_blt_bench                          # defaults, table output
    python3 -m axiom_blt_bench --json                   # JSON output
    python3 -m axiom_blt_bench --fragment-counts 1,5,50 # custom fragment counts
    python3 -m axiom_blt_bench --reps 5                 # measurement repetitions
    python3 -m axiom_blt_bench --token-cost 0.003       # USD per 1k tokens
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

# ── import guard ─────────────────────────────────────────────────────────────

try:
    from axiom_knowledge_cookie import (
        KnowledgeFragment,
        KnowledgeCookie,
        KnowledgeCookieStore,
    )
except ImportError as _import_err:
    print(
        "ERROR: axiom_knowledge_cookie is not available.\n"
        f"       ({_import_err})\n\n"
        "       This benchmark requires the KnowledgeCookie system to be installed.\n"
        "       Build or install axiom_knowledge_cookie and re-run.\n",
        file=sys.stderr,
    )
    sys.exit(1)

# ── constants ────────────────────────────────────────────────────────────────

RELEVANCE_RATE = 0.20  # fraction of sessions where a given fragment is relevant


# ── BLTConfig ────────────────────────────────────────────────────────────────

@dataclass
class BLTConfig:
    """Configuration for a BLT benchmark run.

    Attributes
    ----------
    fragment_counts:
        List of fragment counts to benchmark.
    sessions_per_fragment_count:
        Unused directly in timing math but included for report context —
        represents the realistic session-volume axis callers may want to plot.
    calls_per_session:
        LLM calls made in a single user session (used for break-even math).
    avg_fragment_chars:
        Target character length for each synthetic fragment (~300 tokens at
        4 chars/token).
    token_cost_per_1k:
        USD cost per 1 000 tokens (e.g. 0.002 for a mid-tier API).
    tokens_saved_per_hit:
        Tokens saved by *not* re-fetching the fragment from RAG when the
        model already has it in context from the cookie.
    reps:
        Measurement repetitions; timings are averaged for stability.
    """
    fragment_counts: List[int] = field(
        default_factory=lambda: [1, 5, 10, 25, 50, 100]
    )
    sessions_per_fragment_count: List[int] = field(
        default_factory=lambda: [1, 3, 5, 10, 25]
    )
    calls_per_session: int = 10
    avg_fragment_chars: int = 1200   # ~300 tokens at 4 chars/token
    token_cost_per_1k: float = 0.002
    tokens_saved_per_hit: int = 200
    reps: int = 3


# ── BLTResult ────────────────────────────────────────────────────────────────

@dataclass
class BLTResult:
    """Measurements for a single fragment-count level.

    Attributes
    ----------
    fragment_count:
        Number of fragments in the cookie.
    cookie_bytes:
        Serialised cookie file size in bytes.
    load_ms:
        Average time to load + deserialise the cookie from disk (ms).
    inject_ms:
        Average time to call ``cookie.to_extra_context(max_fragments=n)`` (ms).
    save_ms:
        Average time to sign + serialise + write the cookie to disk (ms).
    tokens_added:
        Total tokens added to each LLM call from hot_knowledge content.
    break_even_sessions:
        Sessions before net token savings exceed the injection cost.
    net_cost_per_1m_calls:
        USD cost (or saving, if negative) across 1 million LLM calls when
        always injecting these fragments.
    """
    fragment_count: int
    cookie_bytes: int
    load_ms: float
    inject_ms: float
    save_ms: float
    tokens_added: int
    # Derived
    break_even_sessions: float
    net_cost_per_1m_calls: float


# ── synthetic data helpers ────────────────────────────────────────────────────

def _make_synthetic_fragment(idx: int, avg_chars: int) -> "KnowledgeFragment":
    """Return a synthetic KnowledgeFragment suitable for benchmarking."""
    raw = "legal precedent text "
    body = (raw * ((avg_chars // len(raw)) + 1))[:avg_chars]
    content = f"Fragment {idx}: {body}"
    source_uri = f"bench://synthetic/fragment_{idx}"
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    return KnowledgeFragment(
        content=content,
        source_uri=source_uri,
        content_hash=content_hash,
        hit_count=5,
        sessions_seen=3,
        promoted=True,
        first_seen="2026-06-17T00:00:00Z",
        last_seen="2026-06-17T00:00:00Z",
    )


def _make_synthetic_cookie(
    fragment_count: int,
    avg_chars: int,
) -> "KnowledgeCookie":
    """Build a KnowledgeCookie populated with *fragment_count* synthetic fragments."""
    fragments: Dict[str, "KnowledgeFragment"] = {}
    hot_knowledge: List["KnowledgeFragment"] = []

    for i in range(fragment_count):
        frag = _make_synthetic_fragment(i, avg_chars)
        fragments[frag.content_hash] = frag
        hot_knowledge.append(frag)

    return KnowledgeCookie(
        fragments=fragments,
        hot_knowledge=hot_knowledge,
    )


# ── BLTBenchmark ─────────────────────────────────────────────────────────────

class BLTBenchmark:
    """Run the BLT benchmark and report results.

    Parameters
    ----------
    config:
        Benchmark parameters.  Defaults to :class:`BLTConfig` with stock values.
    tmp_dir:
        Directory for temporary cookie files.  When *None* a
        :class:`tempfile.TemporaryDirectory` is created and cleaned up after
        :meth:`run` returns.
    """

    def __init__(
        self,
        config: Optional[BLTConfig] = None,
        tmp_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or BLTConfig()
        self._tmp_dir = tmp_dir
        self._owned_tmp: Optional[tempfile.TemporaryDirectory] = None  # type: ignore[type-arg]

    # ── public API ────────────────────────────────────────────────────────────

    def run(self) -> List[BLTResult]:
        """Execute the benchmark and return one :class:`BLTResult` per fragment count."""
        cfg = self.config

        # Ensure we have a temp directory
        if self._tmp_dir is not None:
            work_dir = self._tmp_dir
        else:
            self._owned_tmp = tempfile.TemporaryDirectory(prefix="axiom_blt_")
            work_dir = Path(self._owned_tmp.name)

        results: List[BLTResult] = []

        try:
            for frag_count in cfg.fragment_counts:
                result = self._measure(frag_count, work_dir)
                results.append(result)
        finally:
            if self._owned_tmp is not None:
                self._owned_tmp.cleanup()
                self._owned_tmp = None

        return results

    def print_table(self, results: List[BLTResult]) -> None:
        """Print a formatted ASCII table of benchmark results to stdout."""
        header1 = (
            "Fragment│ Cookie  │Load  │Inject│Tokens│"
            "Break-even│Net cost/1M"
        )
        header2 = (
            "count   │ bytes   │ms    │ms    │added "
            "│sessions  │calls (USD)"
        )
        sep = (
            "─" * 8
            + "┼"
            + "─" * 9
            + "┼"
            + "─" * 6
            + "┼"
            + "─" * 6
            + "┼"
            + "─" * 6
            + "┼"
            + "─" * 10
            + "┼"
            + "─" * 11
        )

        print("\nBLT Benchmark — KnowledgeCookie injection cost")
        print(header1)
        print(header2)
        print(sep)

        for r in results:
            kb = r.cookie_bytes / 1024
            be_ok = r.break_even_sessions < 10
            marker = "✓" if be_ok else "✗"
            net_sign = "-" if r.net_cost_per_1m_calls < 0 else "+"
            net_abs = abs(r.net_cost_per_1m_calls)

            # Format cost column
            if net_abs >= 1000:
                cost_str = f"{net_sign}${net_abs:,.0f}"
            elif net_abs >= 1:
                cost_str = f"{net_sign}${net_abs:,.2f}"
            else:
                cost_str = f"{net_sign}${net_abs:.4f}"

            print(
                f"{r.fragment_count:>8d}"
                f"│{kb:>6.0f} KB "
                f"│{r.load_ms:>5.1f}ms"
                f"│{r.inject_ms:>5.1f}ms"
                f"│{r.tokens_added:>6,d}"
                f"│{r.break_even_sessions:>10.1f} "
                f"│{cost_str:>8s} {marker}"
            )

        print(
            "\nNote: ✓ = break-even < 10 sessions (profitable for regular users),"
            " ✗ = not recommended for general use."
        )

    def to_json(self, results: List[BLTResult]) -> str:
        """Return a JSON-serialisable list of result dicts."""
        return json.dumps(
            [asdict(r) for r in results],
            indent=2,
            ensure_ascii=False,
        )

    # ── internal ─────────────────────────────────────────────────────────────

    def _measure(self, frag_count: int, work_dir: Path) -> BLTResult:
        """Run *reps* measurement passes for *frag_count* fragments and average."""
        cfg = self.config
        cookie_path = work_dir / f"bench_{frag_count}.json"
        store = KnowledgeCookieStore(path=cookie_path)

        save_times: List[float] = []
        load_times: List[float] = []
        inject_times: List[float] = []
        tokens_added_samples: List[int] = []

        for _ in range(cfg.reps):
            # 1. Build a fresh synthetic cookie
            cookie = _make_synthetic_cookie(frag_count, cfg.avg_fragment_chars)

            # 2. Save — measure sign + serialise + write
            t0 = time.perf_counter()
            store.save(cookie)
            save_times.append((time.perf_counter() - t0) * 1000)

            # 3. Load — measure read + deserialise
            t0 = time.perf_counter()
            loaded = store.load()
            load_times.append((time.perf_counter() - t0) * 1000)

            if loaded is None:
                loaded = cookie  # fallback if store.load() returns None (pre-sign path)

            # 4. Inject — measure to_extra_context call
            t0 = time.perf_counter()
            ctx = loaded.to_extra_context(max_fragments=frag_count)
            inject_times.append((time.perf_counter() - t0) * 1000)

            # 5. Count tokens from hot_knowledge in the returned context
            hot = ctx.get("hot_knowledge", [])
            if isinstance(hot, list):
                total_chars = sum(
                    len(item if isinstance(item, str) else str(item))
                    for item in hot
                )
            elif isinstance(hot, str):
                total_chars = len(hot)
            else:
                # Fallback: count directly from the cookie object's hot_knowledge
                total_chars = sum(
                    len(f.content)
                    for f in loaded.hot_knowledge[:frag_count]
                )
            tokens_added_samples.append(total_chars // 4)

        # Average across reps
        save_ms = sum(save_times) / cfg.reps
        load_ms = sum(load_times) / cfg.reps
        inject_ms = sum(inject_times) / cfg.reps
        tokens_added = int(sum(tokens_added_samples) / cfg.reps)

        cookie_bytes = cookie_path.stat().st_size if cookie_path.exists() else 0

        # Derived metrics
        break_even, net_cost = _compute_derived(
            tokens_added=tokens_added,
            calls_per_session=cfg.calls_per_session,
            tokens_saved_per_hit=cfg.tokens_saved_per_hit,
            token_cost_per_1k=cfg.token_cost_per_1k,
        )

        return BLTResult(
            fragment_count=frag_count,
            cookie_bytes=cookie_bytes,
            load_ms=load_ms,
            inject_ms=inject_ms,
            save_ms=save_ms,
            tokens_added=tokens_added,
            break_even_sessions=break_even,
            net_cost_per_1m_calls=net_cost,
        )


# ── derived-metric formulas ───────────────────────────────────────────────────

def _compute_derived(
    *,
    tokens_added: int,
    calls_per_session: int,
    tokens_saved_per_hit: int,
    token_cost_per_1k: float,
) -> tuple[float, float]:
    """Return (break_even_sessions, net_cost_per_1m_calls).

    Assumptions
    -----------
    - RELEVANCE_RATE (20 %) of sessions benefit from a given hot fragment.
    - tokens_added is the cost paid on every call.
    - tokens_saved_per_hit is the saving when the fragment is relevant.

    Formulas
    --------
    break_even_sessions = tokens_added * calls_per_session
                          / max(tokens_saved_per_hit * RELEVANCE_RATE
                                * calls_per_session, 1)

    net_cost_per_1m_calls = (tokens_added / 1000) * token_cost_per_1k * 1_000_000
                            - (tokens_saved_per_hit * RELEVANCE_RATE / 1000)
                              * token_cost_per_1k * 1_000_000
    """
    savings_per_session = tokens_saved_per_hit * RELEVANCE_RATE * calls_per_session
    cost_per_session = tokens_added * calls_per_session

    break_even = cost_per_session / max(savings_per_session, 1.0)

    net_cost = (
        (tokens_added / 1000) * token_cost_per_1k * 1_000_000
        - (tokens_saved_per_hit * RELEVANCE_RATE / 1000)
        * token_cost_per_1k
        * 1_000_000
    )

    return break_even, net_cost


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="axiom_blt_bench",
        description=(
            "BLT (Bloat/Latency/Tokens) benchmark — measures the cost of "
            "always-injecting KnowledgeCookie RAG fragments into LLM calls."
        ),
    )
    ap.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON instead of an ASCII table.",
    )
    ap.add_argument(
        "--fragment-counts",
        default=None,
        metavar="N,N,...",
        help="Comma-separated list of fragment counts to benchmark (e.g. 1,5,10,50).",
    )
    ap.add_argument(
        "--reps",
        type=int,
        default=3,
        metavar="N",
        help="Measurement repetitions for timing stability (default: 3).",
    )
    ap.add_argument(
        "--token-cost",
        type=float,
        default=0.002,
        metavar="USD",
        help="Cost per 1 000 tokens in USD (default: 0.002).",
    )
    ap.add_argument(
        "--avg-fragment-chars",
        type=int,
        default=1200,
        metavar="N",
        help="Average character length of each synthetic fragment (default: 1200).",
    )
    ap.add_argument(
        "--calls-per-session",
        type=int,
        default=10,
        metavar="N",
        help="LLM calls per session for break-even math (default: 10).",
    )
    ap.add_argument(
        "--tokens-saved-per-hit",
        type=int,
        default=200,
        metavar="N",
        help="Tokens saved by not re-fetching from RAG on a cache hit (default: 200).",
    )
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    cfg = BLTConfig(
        reps=args.reps,
        token_cost_per_1k=args.token_cost,
        avg_fragment_chars=args.avg_fragment_chars,
        calls_per_session=args.calls_per_session,
        tokens_saved_per_hit=args.tokens_saved_per_hit,
    )

    if args.fragment_counts is not None:
        try:
            cfg.fragment_counts = [
                int(x.strip()) for x in args.fragment_counts.split(",") if x.strip()
            ]
        except ValueError:
            print(
                "ERROR: --fragment-counts must be a comma-separated list of integers "
                f"(got: {args.fragment_counts!r})",
                file=sys.stderr,
            )
            return 1

    bench = BLTBenchmark(config=cfg)
    results = bench.run()

    if args.json:
        print(bench.to_json(results))
    else:
        bench.print_table(results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
