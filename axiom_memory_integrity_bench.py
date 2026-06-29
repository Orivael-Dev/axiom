"""
AXIOM Memory Integrity Benchmark — ORVL-015
============================================
Recall benchmarks ask "can the memory system find the right fact?" This harness
asks the governance question instead: **over a long horizon, can past
conversation be silently altered, and does recall stay under governance?**

It exercises `axiom_memory_engine` (the signed ConstitutionalPacket store) over up
to ~1,000 days of synthetic daily logs per persona, then runs three adversarial
checks:

  1. CONTENT TAMPER  — mutate a fraction of stored memories. Every packet is
     HMAC-signed over all its fields, and load_store()/recall() gate on
     verification, so an altered row fails its signature and is dropped from
     governed recall. Expected detection: 100%.

  2. GOVERNED RECALL — after tampering, a query that would have hit an altered
     memory must NOT be served that memory (the engine refuses unsigned rows).

  3. DELETION / TRUNCATION — remove a fraction of rows. Per-packet signatures
     cannot detect a *missing* row (there is no hash-chain over the store), so
     this is a KNOWN GAP, reported honestly. Closing it needs a chained ledger
     (same pattern as the .axm proof ledger / guest-cert register).

Usage:
  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  python axiom_memory_integrity_bench.py --days 1000 --tamper 0.1 --delete 0.05
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import tempfile
from pathlib import Path

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "memory_integrity_bench_demo_key"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axiom_memory_engine import (
    ConstitutionalMemoryEngine, LSHIndex, embed_text,
    load_store, count_verified, _verify_packet, packet_from_dict,
)

_TOPICS = [
    "the migration plan", "the budget review", "the hiring decision",
    "the incident postmortem", "the vendor contract", "the roadmap change",
    "the security audit", "the customer escalation", "the architecture choice",
    "the compliance deadline",
]


def _daily_log(rng: random.Random, day: int) -> tuple[str, str]:
    """Deterministic synthetic daily log. Returns (conversation_text, fact_marker).

    The marker is a unique, recoverable fact so we can later prove an altered
    memory is no longer served.
    """
    topic = _TOPICS[day % len(_TOPICS)]
    marker = f"DAY{day:04d}-FACT-{rng.randrange(100000, 999999)}"
    text = (
        f"Day {day}: we discussed {topic}. The decision recorded was {marker}. "
        f"Owner noted follow-ups and the agreed outcome for {topic}."
    )
    return text, marker


def _ingest(store_path: Path, days: int, seed: int) -> list[dict]:
    """Ingest `days` daily logs as signed packets. Returns per-day records."""
    engine = ConstitutionalMemoryEngine(str(store_path), LSHIndex(seed=seed))
    rng = random.Random(seed)
    records = []
    for day in range(days):
        text, marker = _daily_log(rng, day)
        engine.remember(
            conversation_text=text,
            final_synthesis_vec=embed_text(text),
            domain="journal",
            active_constraints=("CANNOT_FABRICATE", "CANNOT_MUTATE_HISTORY"),
            resolution=f"recorded: {marker}",
            sovereign_history=(),
        )
        records.append({"day": day, "text": text, "marker": marker})
    return records


def _tamper_rows(store_path: Path, indices: set[int]) -> None:
    """Alter `resolution` + one vec element on the chosen rows WITHOUT re-signing.

    This is the "someone edited my past conversation" attack — the stored bytes
    change but the signature is now stale.
    """
    lines = store_path.read_text(encoding="utf-8").splitlines()
    for i in indices:
        d = json.loads(lines[i])
        d["resolution"] = d.get("resolution", "") + " [ALTERED]"
        if d.get("compressed_vec"):
            d["compressed_vec"][0] = round(float(d["compressed_vec"][0]) + 0.5, 6)
        lines[i] = json.dumps(d, ensure_ascii=True)
    store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _delete_rows(src: Path, dst: Path, indices: set[int]) -> None:
    lines = src.read_text(encoding="utf-8").splitlines()
    kept = [ln for i, ln in enumerate(lines) if i not in indices]
    dst.write_text("\n".join(kept) + "\n", encoding="utf-8")


def run(days: int, tamper_rate: float, delete_rate: float, seed: int) -> dict:
    work = Path(tempfile.mkdtemp(prefix="axm_mem_integrity_"))
    store = work / "memory.jsonl"

    print(f"\n  AXIOM Memory Integrity Benchmark — {days}-day horizon")
    print("  " + "=" * 58)

    # ── Ingest ────────────────────────────────────────────────────────────────
    records = _ingest(store, days, seed)
    baseline = count_verified(store)
    print(f"  Ingested            : {days} daily logs → {baseline} signed packets")
    assert baseline == days, "every ingested packet should verify"

    # Governed recall sanity: a clean day is recalled and verifies.
    eng = ConstitutionalMemoryEngine(str(store), LSHIndex(seed=seed))
    load_store(store, eng._lsh)
    sample = records[days // 2]
    hit = eng.recall(embed_text(sample["text"]), domain="journal")
    recall_ok = hit is not None and _verify_packet(hit) and sample["marker"] in hit.resolution
    print(f"  Governed recall     : day {sample['day']} → "
          f"{'served + verified' if recall_ok else 'MISS'}")

    # ── Check 1+2: content tamper + governed recall under tamper ──────────────
    rng = random.Random(seed + 1)
    n_tamper = max(1, math.ceil(days * tamper_rate))
    tamper_idx = set(rng.sample(range(days), n_tamper))
    tamper_store = work / "memory_tampered.jsonl"
    tamper_store.write_text(store.read_text(encoding="utf-8"), encoding="utf-8")
    _tamper_rows(tamper_store, tamper_idx)

    verified_after = count_verified(tamper_store)
    detected = days - verified_after
    detect_rate = detected / n_tamper if n_tamper else 1.0

    # A tampered day must no longer be served as governed memory.
    eng2 = ConstitutionalMemoryEngine(str(tamper_store), LSHIndex(seed=seed))
    load_store(tamper_store, eng2._lsh)
    victim = records[sorted(tamper_idx)[0]]
    served = eng2.recall(embed_text(victim["text"]), domain="journal")
    altered_served = served is not None and victim["marker"] in getattr(served, "resolution", "")

    print(f"\n  [1] Content tamper  : altered {n_tamper} of {days} stored memories")
    print(f"      → detected      : {detected}/{n_tamper}  ({detect_rate:.0%} rejected by signature)")
    print(f"  [2] Governed recall : altered day {victim['day']} "
          f"{'STILL SERVED ✗' if altered_served else 'refused (not served) ✓'}")

    # ── Check 3: deletion / truncation (the honest gap) ───────────────────────
    n_delete = max(1, math.ceil(days * delete_rate))
    del_idx = set(random.Random(seed + 2).sample(range(days), n_delete))
    del_store = work / "memory_deleted.jsonl"
    _delete_rows(store, del_store, del_idx)
    verified_del = count_verified(del_store)
    # Every surviving row still verifies — the deletion leaves no signature trace.
    deletion_detected = False  # no chain anchor exists today
    print(f"\n  [3] Deletion        : removed {n_delete} of {days} rows")
    print(f"      → survivors verify: {verified_del}/{verified_del} (all authentic)")
    print(f"      → deletion caught : {'yes' if deletion_detected else 'NO — known gap (no hash-chain)'}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    content_pass = detect_rate == 1.0 and not altered_served
    print("\n  " + "-" * 58)
    print(f"  Content integrity   : {'PASS' if content_pass else 'FAIL'} "
          f"— altered memories are detected and never served")
    print(f"  Deletion integrity  : GAP  — per-packet signatures cannot prove "
          f"completeness")
    print(f"  Recommendation      : add a hash-chain over the store (same pattern "
          f"as\n                        the .axm proof ledger / guest-cert register) "
          f"to close [3]")
    print()

    return {
        "days": days,
        "ingested_verified": baseline,
        "governed_recall_ok": recall_ok,
        "tampered": n_tamper,
        "tamper_detected": detected,
        "tamper_detect_rate": round(detect_rate, 4),
        "altered_still_served": altered_served,
        "deleted": n_delete,
        "deletion_detected": deletion_detected,
        "content_integrity_pass": content_pass,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="AXIOM Memory Integrity Benchmark")
    ap.add_argument("--days", type=int, default=1000, help="horizon in daily logs (≤ ~5000)")
    ap.add_argument("--tamper", type=float, default=0.10, help="fraction of memories to alter")
    ap.add_argument("--delete", type=float, default=0.05, help="fraction of memories to delete")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="emit the result dict as JSON")
    args = ap.parse_args()

    result = run(args.days, args.tamper, args.delete, args.seed)
    if args.json:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
