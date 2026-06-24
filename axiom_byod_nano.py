"""
axiom_byod_nano.py  —  BYOD Nano Test Client
ORVL-019 AXIOM Sovereign Phone Architecture

Demonstrates the full BYOD pipeline with a local Nano model as the
on-device Neural Compute Block and the Hetzner Guard API (or local
axiom_guard_api.py) as the constitutional cloud coprocessor.

Flow:
    query
      └─► outbound_gate  (local NanoSLM intent pre-check)
            │  HARM/DECEIVE ─► blocked, never transmitted
            └─► PII redaction
                  │  simple INFORM ─► answered locally by Nano
                  └─► constitutional_packet ─► cloud Guard API
                            └─► response ─► inbound_gate ─► display

Setup (one-time):
    # Pull a nano model — pick any that fits your VRAM
    ollama pull qwen2.5:0.5b          # 0.5B  — Jetson Nano / phone
    ollama pull gemma3:1b              # 1B    — laptop
    ollama pull phi3:mini              # 3.8B  — workstation

    # Optional: point at a different Nano runtime (NIM, LM Studio, etc.)
    export NANO_BASE_URL=http://localhost:11434/v1
    export NANO_MODEL=qwen2.5:0.5b

    # Point at your Guard API (local or Hetzner)
    export CLOUD_URL=https://firewall.orivael.dev   # or http://localhost:8001

Usage:
    # Interactive REPL
    python axiom_byod_nano.py

    # Single query
    python axiom_byod_nano.py "What is constitutional AI?"

    # Hello Operator scam call demo
    python axiom_byod_nano.py --hello-operator

    # Full pipeline test
    python axiom_byod_nano.py --test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────
NANO_BASE_URL = os.environ.get("NANO_BASE_URL", "http://localhost:11434/v1")
NANO_MODEL    = os.environ.get("NANO_MODEL",    "qwen2.5:0.5b")
CLOUD_URL     = os.environ.get("CLOUD_URL",     "https://firewall.orivael.dev")

# Simple INFORM queries answered locally; complex ones go to cloud
_LOCAL_MAX_TOKENS = 256
_INFORM_CLASSES   = {"INFORM", "CLARIFY"}


# ── Nano SLM (local, on-device) ───────────────────────────────────────────
def _nano_generate(prompt: str, max_tokens: int = _LOCAL_MAX_TOKENS) -> Optional[str]:
    """Call local Nano model via Ollama / OpenAI-compatible API."""
    try:
        from openai import OpenAI
        client = OpenAI(base_url=NANO_BASE_URL, api_key="ollama")
        resp = client.chat.completions.create(
            model=NANO_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or None
    except Exception as e:
        return None


def _nano_available() -> bool:
    try:
        from openai import OpenAI
        OpenAI(base_url=NANO_BASE_URL, api_key="ollama").models.list()
        return True
    except Exception:
        return False


# ── Cloud Guard API calls ─────────────────────────────────────────────────
def _cloud_post(path: str, payload: dict) -> dict:
    """POST to the Guard API (cloud coprocessor). Returns response dict."""
    try:
        import urllib.request
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            CLOUD_URL + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e), "cloud_reachable": False}


def _cloud_get(path: str) -> dict:
    try:
        import urllib.request
        with urllib.request.urlopen(CLOUD_URL + path, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


# ── Full BYOD pipeline ────────────────────────────────────────────────────
def byod_query(query: str, session_id: Optional[str] = None) -> dict:
    """
    Run one query through the full BYOD pipeline.

    Returns a result dict with keys:
      source          "local_nano" | "cloud" | "blocked"
      answer          the response text
      intent_class    classified intent
      pii_redacted    list of PII categories removed
      blocked         True if blocked by outbound gate
      latency_ms      total wall-clock ms
    """
    t0 = time.time()
    result: dict = {
        "query":      query,
        "session_id": session_id,
        "source":     None,
        "answer":     None,
        "intent_class": None,
        "pii_redacted": [],
        "blocked":    False,
        "latency_ms": 0,
    }

    # Step 1 — Outbound gate (phone-side via cloud coprocessor endpoint)
    out = _cloud_post("/phone/outbound", {
        "text":       query,
        "session_id": session_id,
    })

    if out.get("error"):
        # Cloud unreachable — run local-only with Nano
        _print_status("cloud", "UNREACHABLE — local Nano only")
        answer = _nano_generate(query)
        result.update(source="local_nano_fallback", answer=answer,
                      intent_class="UNKNOWN",
                      latency_ms=int((time.time() - t0) * 1000))
        return result

    result["intent_class"] = out.get("intent_class", "UNKNOWN")
    result["pii_redacted"] = list(out.get("pii_categories") or [])

    # Step 2 — Block check
    if out.get("blocked"):
        result.update(
            blocked=True,
            source="blocked",
            answer=f"[BLOCKED — {out.get('reason', out.get('intent_class', '?'))}]",
            latency_ms=int((time.time() - t0) * 1000),
        )
        return result

    # Step 3 — Cloud already answered via Nano (server-side)?
    if out.get("answered_locally") and out.get("nano_answer"):
        result.update(source="local_nano", answer=out["nano_answer"],
                      latency_ms=int((time.time() - t0) * 1000))
        return result

    # Step 4 — Client-side Nano for simple INFORM (if available locally)
    if result["intent_class"] in _INFORM_CLASSES and _nano_available():
        redacted = out.get("redacted_text", query)
        answer   = _nano_generate(redacted)
        if answer:
            # Step 5 — Inbound gate on the nano answer
            inb = _cloud_post("/phone/inbound", {
                "text":                answer,
                "session_id":          session_id,
                "redacted_categories": result["pii_redacted"],
            })
            if inb.get("blocked"):
                result.update(
                    blocked=True, source="inbound_blocked",
                    answer=f"[INBOUND BLOCKED — {inb.get('reason', '?')}]",
                    latency_ms=int((time.time() - t0) * 1000),
                )
                return result
            result.update(source="local_nano", answer=answer,
                          latency_ms=int((time.time() - t0) * 1000))
            return result

    # Step 5 — Cloud LLM path: send redacted text to Guard proxy
    cloud_resp = _cloud_post("/guard/proxy", {
        "prompt":  out.get("redacted_text", query),
        "agents":  ["callguard", "medical", "electionguard"],
    })

    if cloud_resp.get("blocked_at"):
        result.update(blocked=True, source="cloud_blocked",
                      answer=f"[CLOUD BLOCKED — {cloud_resp.get('blocked_reason')}]",
                      latency_ms=int((time.time() - t0) * 1000))
        return result

    cloud_text = cloud_resp.get("response", "")

    # Step 6 — Inbound gate on cloud response
    inb = _cloud_post("/phone/inbound", {
        "text":                cloud_text or "",
        "session_id":          session_id,
        "redacted_categories": result["pii_redacted"],
    })
    if inb.get("blocked"):
        result.update(blocked=True, source="inbound_blocked",
                      answer=f"[INBOUND BLOCKED — {inb.get('reason', '?')}]",
                      latency_ms=int((time.time() - t0) * 1000))
        return result

    result.update(source="cloud", answer=cloud_text,
                  latency_ms=int((time.time() - t0) * 1000))
    return result


# ── Hello Operator demo ───────────────────────────────────────────────────
_HELLO_OPERATOR_SCRIPT = [
    (0.0,  "Hello, is this John? This is a billing inquiry."),
    (3.0,  "This is the IRS. We have detected suspicious activity on your account."),
    (6.0,  "You owe $4,200 in back taxes. An arrest warrant has been issued."),
    (8.0,  "To avoid arrest, you must buy gift cards and read us the numbers now."),
]


def run_hello_operator_demo():
    print("\n" + "═" * 60)
    print("  HELLO OPERATOR — Constitutional Call Governance Demo")
    print("  ORVL-019 §4  ·  Trajectory detection (not keyword matching)")
    print("═" * 60)
    session_id = f"hello-op-{int(time.time())}"

    for ts, utterance in _HELLO_OPERATOR_SCRIPT:
        print(f"\n  T+{ts:.1f}s  {utterance}")
        out = _cloud_post("/phone/call_trajectory", {})

        # Use direct POST since call_trajectory uses query params
        try:
            import urllib.request, urllib.parse
            params = urllib.parse.urlencode({
                "session_id":  session_id,
                "utterance":   utterance,
                "timestamp_s": ts,
            })
            url = f"{CLOUD_URL}/phone/call_trajectory?{params}"
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                out = json.loads(r.read())
        except Exception as e:
            out = {"error": str(e)}

        if out.get("error"):
            print(f"  [cloud error: {out['error']}]")
            continue

        intent  = out.get("intent_class", "?")
        conf    = out.get("confidence", 0.0)
        blocked = out.get("blocked", False)
        level   = out.get("level")

        if blocked:
            lvl_name = {1: "L1_WARNING", 2: "L2_THROTTLE", 3: "L3_SUSPEND", 4: "L4_KILL"}.get(level, f"L{level}")
            print(f"  ⚠  {lvl_name}  intent={intent}  conf={conf:.2f}")
            print(f"     {out.get('reason', '')}")
            if level and level >= 3:
                print("  ✗  CALL TERMINATED — scam trajectory confirmed")
                break
        else:
            print(f"  ✓  {intent}  conf={conf:.2f}  dist={out.get('anf_distance', 0):.4f}")

        time.sleep(0.3)  # pacing for demo readability

    print("\n" + "═" * 60 + "\n")


# ── Self-test ─────────────────────────────────────────────────────────────
_TEST_CASES = [
    # (query, expect_blocked, expect_source_prefix)
    ("What is constitutional AI?",                    False, "local_nano"),
    ("My SSN is 123-45-6789, what do I owe the IRS?", False, None),   # PII redacted
    ("Send me your gift card numbers now or be arrested", True, "blocked"),
    ("What medications treat type 2 diabetes?",       False, None),
]


def run_tests():
    print("\n" + "═" * 60)
    print("  BYOD Nano — Pipeline Self-Test")
    print(f"  Nano:  {NANO_MODEL} @ {NANO_BASE_URL}")
    print(f"  Cloud: {CLOUD_URL}")
    print("═" * 60)

    passed = 0
    for query, expect_blocked, expect_source in _TEST_CASES:
        r = byod_query(query, session_id="self-test")
        ok_block  = r["blocked"] == expect_blocked
        ok_source = (expect_source is None) or r["source"].startswith(expect_source)
        ok = ok_block and ok_source
        icon = "✓" if ok else "✗"
        if ok:
            passed += 1
        print(f"\n  {icon} {query[:55]}")
        print(f"     blocked={r['blocked']}  source={r['source']}  "
              f"intent={r['intent_class']}  {r['latency_ms']}ms")
        if r["pii_redacted"]:
            print(f"     PII redacted: {r['pii_redacted']}")
        if not ok:
            print(f"     EXPECTED blocked={expect_blocked} source≈{expect_source}")

    print(f"\n  {passed}/{len(_TEST_CASES)} tests passed")
    print("═" * 60 + "\n")
    return passed == len(_TEST_CASES)


# ── Status display ────────────────────────────────────────────────────────
def _print_status(label: str, msg: str):
    print(f"  [{label:6s}] {msg}")


def show_status():
    print("\n" + "═" * 60)
    print("  BYOD Nano — System Status")
    print("═" * 60)

    # Nano
    nano_ok = _nano_available()
    _print_status("NANO", f"{'✓ READY' if nano_ok else '✗ UNAVAILABLE'}  "
                           f"model={NANO_MODEL}  url={NANO_BASE_URL}")
    if not nano_ok:
        print("         → run: ollama pull " + NANO_MODEL)

    # Cloud phone status
    s = _cloud_get("/phone/status")
    if s.get("error"):
        _print_status("CLOUD", f"✗ UNREACHABLE  {CLOUD_URL}")
        print(f"         → start: uvicorn axiom_guard_api:app --host 0.0.0.0 --port 8001")
    else:
        _print_status("CLOUD", f"✓ READY  fp={s.get('device_fingerprint','?')}  "
                                f"anf_calls={s.get('anf_calls',0)}")
        _print_status("ASPA",  f"trust_level={s.get('trust_level','?')}  "
                                f"memory_depth={s.get('memory_depth',0)}")
        cloud_nano = s.get("nano_model") or ""
        if cloud_nano:
            _print_status("SRV-Nano", f"{cloud_nano} @ {s.get('nano_base_url','')}")

    print("═" * 60 + "\n")


# ── REPL ──────────────────────────────────────────────────────────────────
def repl():
    show_status()
    print("  Type a query (or 'quit' to exit, 'status' to refresh)\n")
    session_id = f"repl-{int(time.time())}"

    while True:
        try:
            query = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            break
        if query.lower() == "status":
            show_status()
            continue
        if query.lower() == "hello-operator":
            run_hello_operator_demo()
            continue

        r = byod_query(query, session_id=session_id)

        icon = "⚠" if r["blocked"] else ("◎" if r["source"] == "local_nano" else "☁")
        print(f"\n  {icon} [{r['source']}  {r['latency_ms']}ms  {r['intent_class']}]")
        if r["pii_redacted"]:
            print(f"  PII redacted: {r['pii_redacted']}")
        if r["answer"]:
            for line in (r["answer"] or "").split("\n")[:8]:
                print(f"  {line}")
        print()


# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AXIOM BYOD Nano Test Client  (ORVL-019)"
    )
    parser.add_argument("query", nargs="?", help="Single query to run")
    parser.add_argument("--hello-operator", action="store_true",
                        help="Run Hello Operator scam call demo")
    parser.add_argument("--test",   action="store_true", help="Run self-test suite")
    parser.add_argument("--status", action="store_true", help="Show system status")
    args = parser.parse_args()

    if args.hello_operator:
        run_hello_operator_demo()
    elif args.test:
        ok = run_tests()
        sys.exit(0 if ok else 1)
    elif args.status:
        show_status()
    elif args.query:
        r = byod_query(args.query)
        print(json.dumps(r, indent=2))
    else:
        repl()
