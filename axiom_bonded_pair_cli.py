"""AXIOM Bonded Paired-Token CLI.

Operator-facing tool for the bonded-pair primitive
(see ``axiom_event_token/bonded_pair.py``). Mints pairs, drives
state transitions through the atomic ledger, inspects history,
and verifies the chain.

The CLI uses only stdlib + axiom_signing — no extra deps so it
works in minimal containers.

Subcommands:

  mint        Mint a bonded pair and initialise it to ACTIVE_VALIDATED.
              Prints the two signed tokens + the pair_id (the pair_id
              is what downstream gates use to consult the ledger).
  transition  Move a pair to a new state (ACTIVE_VALIDATED,
              ACTIVE_PENDING, SUSPENDED, REVOKED, EXPIRED).
  revoke      Shortcut: transition to REVOKED (terminal).
  state       Print the current state of a pair, exit 0 iff
              ACTIVE_VALIDATED. Designed to be used in shell pipelines:
                  if axiom-bonded-pair state $PID -q; then ...
  history     Print the full transition record for a pair.
  verify      Replay the ledger and verify the hash chain end-to-end.
              Exit 0 on clean chain, 2 on detected tampering.

The ledger path resolves in this order (per ``BondedPairLedger``):

  1. --ledger flag
  2. AXIOM_BONDED_PAIR_LEDGER env var
  3. ~/.axiom/bonded_pair_ledger.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _add_ledger_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Path to the bonded-pair ledger file. "
             "Default: $AXIOM_BONDED_PAIR_LEDGER, or "
             "~/.axiom/bonded_pair_ledger.jsonl.",
    )


def _ledger(args) -> "BondedPairLedger":
    from axiom_event_token.bonded_pair import BondedPairLedger
    return BondedPairLedger(args.ledger) if args.ledger else BondedPairLedger()


def _parse_payload(raw: str) -> dict:
    """Parse a payload string. Accepts inline JSON or @path/to/file.json."""
    if raw.startswith("@"):
        path = Path(raw[1:])
        if not path.is_file():
            raise SystemExit(f"payload file not found: {path}")
        raw = path.read_text(encoding="utf-8")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"payload is not valid JSON: {e}")
    if not isinstance(d, dict):
        raise SystemExit("payload must be a JSON object (dict)")
    return d


# ── mint ────────────────────────────────────────────────────────────────


def cmd_mint(args) -> int:
    from axiom_event_token.bonded_pair import mint_pair
    primary_payload = _parse_payload(args.primary)
    mirror_payload  = _parse_payload(args.mirror)
    primary, mirror = mint_pair(primary_payload, mirror_payload)
    led = _ledger(args)
    if not args.no_init:
        led.init_pair(primary.pair_id, actor=args.actor or "cli")

    if args.json:
        json.dump({
            "pair_id":   primary.pair_id,
            "primary":   primary.to_dict(),
            "mirror":    mirror.to_dict(),
            "ledger":    str(led.path),
            "initialised": not args.no_init,
        }, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    print(f"pair_id:  {primary.pair_id}")
    print(f"ledger:   {led.path}")
    print(f"primary:  {primary.token_id}")
    print(f"  payload:   {json.dumps(primary.payload, sort_keys=True)}")
    print(f"  signature: {primary.signature[:16]}…{primary.signature[-16:]}")
    print(f"mirror:   {mirror.token_id}")
    print(f"  payload:   {json.dumps(mirror.payload, sort_keys=True)}")
    print(f"  signature: {mirror.signature[:16]}…{mirror.signature[-16:]}")
    print(f"state:    {led.current_state(primary.pair_id) or '(not initialised)'}")
    return 0


# ── transition / revoke ─────────────────────────────────────────────────


def cmd_transition(args) -> int:
    from axiom_event_token.bonded_pair import BondedPairLedgerError
    led = _ledger(args)
    try:
        t = led.transition(args.pair_id, args.to_state,
                           actor=args.actor or "cli")
    except BondedPairLedgerError as e:
        print(f"transition failed: {e}", file=sys.stderr)
        return 2
    print(f"  {t.from_state or '(init)':>16}  →  {t.to_state}")
    print(f"  actor: {t.actor}")
    print(f"  ts:    {t.timestamp_ns}")
    print(f"  sig:   {t.signature[:16]}…")
    return 0


def cmd_revoke(args) -> int:
    from axiom_event_token.bonded_pair import BondedPairLedgerError
    led = _ledger(args)
    try:
        t = led.revoke(args.pair_id, actor=args.actor or "cli")
    except BondedPairLedgerError as e:
        print(f"revoke failed: {e}", file=sys.stderr)
        return 2
    print(f"  REVOKED  ←  {t.from_state}")
    print(f"  actor: {t.actor}")
    print(f"  sig:   {t.signature[:16]}…")
    return 0


# ── state / history / verify ────────────────────────────────────────────


def cmd_state(args) -> int:
    led = _ledger(args)
    s = led.current_state(args.pair_id)
    if s is None:
        if not args.quiet:
            print(f"{args.pair_id}: (not initialised)", file=sys.stderr)
        return 1
    if not args.quiet:
        print(s)
    # Pipeline-friendly: exit 0 iff ACTIVE_VALIDATED.
    return 0 if s == "ACTIVE_VALIDATED" else 1


def cmd_history(args) -> int:
    led = _ledger(args)
    history = led.history(args.pair_id)
    if not history:
        print(f"no transitions for pair {args.pair_id}", file=sys.stderr)
        return 1
    if args.json:
        json.dump([t.to_dict() for t in history],
                  sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    print(f"# {args.pair_id} — {len(history)} transition(s)")
    print(f"{'from':>17}  →  {'to':<17}  actor             ts (ns)")
    print("-" * 78)
    for t in history:
        print(f"{(t.from_state or '(init)'):>17}  →  {t.to_state:<17}  "
              f"{t.actor:<16}  {t.timestamp_ns}")
    return 0


def cmd_verify(args) -> int:
    led = _ledger(args)
    ok = led.verify_chain()
    if args.quiet:
        return 0 if ok else 2
    if ok:
        print(f"  ledger:  {led.path}")
        print(f"  RESULT:  PASS — hash chain intact.")
        return 0
    print(f"  ledger:  {led.path}")
    print(f"  RESULT:  FAIL — chain broken (tampered, deleted, or reordered).",
          file=sys.stderr)
    return 2


# ── argparse harness ────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axiom-bonded-pair",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint", help="Mint a bonded pair + init the ledger.")
    m.add_argument("--primary", required=True,
                   help="Primary payload as JSON (or @path/to/file.json).")
    m.add_argument("--mirror", required=True,
                   help="Mirror payload as JSON (or @path/to/file.json).")
    m.add_argument("--actor", default=None,
                   help="Actor recorded on the init transition. Default: 'cli'.")
    m.add_argument("--no-init", action="store_true",
                   help="Mint without writing the init transition.")
    m.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of human text.")
    _add_ledger_arg(m)
    m.set_defaults(func=cmd_mint)

    t = sub.add_parser("transition", help="Move a pair to a new state.")
    t.add_argument("pair_id")
    t.add_argument("to_state", help="ACTIVE_VALIDATED | ACTIVE_PENDING | "
                                    "SUSPENDED | REVOKED | EXPIRED")
    t.add_argument("--actor", default=None)
    _add_ledger_arg(t)
    t.set_defaults(func=cmd_transition)

    r = sub.add_parser("revoke", help="Shortcut: transition to REVOKED.")
    r.add_argument("pair_id")
    r.add_argument("--actor", default=None)
    _add_ledger_arg(r)
    r.set_defaults(func=cmd_revoke)

    s = sub.add_parser(
        "state",
        help="Print current state. Exit 0 iff ACTIVE_VALIDATED.",
    )
    s.add_argument("pair_id")
    s.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress output; only the exit code is used.")
    _add_ledger_arg(s)
    s.set_defaults(func=cmd_state)

    h = sub.add_parser("history", help="Print full transition history for a pair.")
    h.add_argument("pair_id")
    h.add_argument("--json", action="store_true")
    _add_ledger_arg(h)
    h.set_defaults(func=cmd_history)

    v = sub.add_parser("verify", help="Verify the hash chain of the ledger.")
    v.add_argument("-q", "--quiet", action="store_true")
    _add_ledger_arg(v)
    v.set_defaults(func=cmd_verify)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
