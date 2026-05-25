"""Bonded paired-token primitive.

Two tokens minted together, sharing a `pair_id`, each carrying a
deterministic reference to the other. State is NOT in the signed
token bytes — it lives in an append-only ledger (`BondedPairLedger`)
that a single writer keeps consistent. Either half can be presented
to a gate; the gate consults the ledger for the current pair state.

Use cases this primitive serves:

  1. Action + monitor — primary carries the command to execute;
     mirror is the live security monitor. Flipping the pair to
     REVOKED short-circuits the primary's authority.
  2. Two-party commit — both halves must transition together;
     `transition()` is the only mutation surface.
  3. Long-lived authorization with live revocation — primary is
     the grant; holding the mirror is sufficient to revoke without
     rotating the primary or its signing key.

NOT entanglement. This is a co-signed token pair with an atomic
state register — useful, well-defined, and defensible to an
auditor. The quantum framing belongs on a whiteboard, not in the
signed bytes.

Signing layout:
  axiom-bonded-pair-token-v1   — outer HMAC over each token's bundle
  axiom-bonded-pair-ledger-v1  — outer HMAC over each ledger entry

The ledger is hash-chained: every entry carries the previous
entry's signature, so a single byte flip anywhere in the log breaks
`verify_chain()` from that point forward.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Tuple


TOKEN_KEY_NS  = b"axiom-bonded-pair-token-v1"
LEDGER_KEY_NS = b"axiom-bonded-pair-ledger-v1"

# Where the ledger lives by default. Override per-instance via
# `BondedPairLedger(path)` or globally via the
# `AXIOM_BONDED_PAIR_LEDGER` environment variable. Matches the
# convention used by axiom_exoskeleton_ledger.default_ledger_path.
DEFAULT_LEDGER_FILENAME = "bonded_pair_ledger.jsonl"


def default_ledger_path() -> Path:
    """Default location for the bonded-pair ledger.

    Resolution order:
      1. ``$AXIOM_BONDED_PAIR_LEDGER`` env var (absolute path)
      2. ``~/.axiom/bonded_pair_ledger.jsonl``
    """
    override = os.environ.get("AXIOM_BONDED_PAIR_LEDGER")
    if override:
        return Path(override)
    return Path.home() / ".axiom" / DEFAULT_LEDGER_FILENAME

# Allowed states. "ACTIVE_VALIDATED" is the default at init; "REVOKED"
# and "EXPIRED" are terminal. Operators can add more states by
# extending this set, but a fixed set keeps the gate code small.
DEFAULT_STATES = frozenset({
    "ACTIVE_VALIDATED", "ACTIVE_PENDING", "SUSPENDED",
    "REVOKED", "EXPIRED",
})
INITIAL_STATE = "ACTIVE_VALIDATED"
TERMINAL_STATES = frozenset({"REVOKED", "EXPIRED"})


# ── Token ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BondedToken:
    """One half of a bonded pair. Immutable, signed once at mint."""
    token_id:         str
    pair_id:          str
    role:             str          # "primary" | "mirror"
    partner_token_id: str
    timestamp_ns:     int
    payload:          Mapping      # frozen application data
    signature:        str = ""

    def _canonical(self) -> dict:
        return {
            "token_id":         self.token_id,
            "pair_id":          self.pair_id,
            "role":             self.role,
            "partner_token_id": self.partner_token_id,
            "timestamp_ns":     int(self.timestamp_ns),
            "payload":          dict(self.payload),
        }

    def to_dict(self) -> dict:
        d = self._canonical()
        d["signature"] = self.signature
        return d

    def verify(self) -> bool:
        """Outer signature verifies under TOKEN_KEY_NS."""
        if not self.signature:
            return False
        expected = _sign_token(self._canonical())
        return hmac.compare_digest(self.signature, expected)


# ── Ledger entry ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StateTransition:
    """One row in the bonded-pair ledger. Hash-chained via prev_signature."""
    pair_id:        str
    from_state:     str        # "" for the init record
    to_state:       str
    timestamp_ns:   int
    actor:          str        # who initiated; opaque application string
    prev_signature: str        # signature of the previous record (or "" for init)
    signature:      str = ""

    def _canonical(self) -> dict:
        return {
            "pair_id":        self.pair_id,
            "from_state":     self.from_state,
            "to_state":       self.to_state,
            "timestamp_ns":   int(self.timestamp_ns),
            "actor":          self.actor,
            "prev_signature": self.prev_signature,
        }

    def to_dict(self) -> dict:
        d = self._canonical()
        d["signature"] = self.signature
        return d

    def verify(self) -> bool:
        if not self.signature:
            return False
        expected = _sign_ledger(self._canonical())
        return hmac.compare_digest(self.signature, expected)


# ── Mint + verify ───────────────────────────────────────────────────────


def _new_pair_id() -> str:
    """A pair_id is content-independent so both halves can reference
    each other before being signed — resolves the chicken-and-egg
    where each token's signature would otherwise depend on the
    other's."""
    return f"bp-{uuid.uuid4().hex[:16]}"


def mint_pair(
    payload_primary: Mapping,
    payload_mirror:  Mapping,
    *,
    pair_id:      Optional[str] = None,
    timestamp_ns: Optional[int] = None,
) -> Tuple[BondedToken, BondedToken]:
    """Mint a bonded pair atomically. Pure — no IO, no ledger write.

    Caller is responsible for initialising the pair's state in a
    `BondedPairLedger` afterwards (typically `ledger.init_pair(...)`).
    """
    if not isinstance(payload_primary, Mapping) or not isinstance(payload_mirror, Mapping):
        raise TypeError("payloads must be mappings (dict-like)")

    pid = pair_id or _new_pair_id()
    ts  = int(timestamp_ns if timestamp_ns is not None else time.time_ns())
    primary_id = f"AXIOM-BP-{pid}-A"
    mirror_id  = f"AXIOM-BP-{pid}-B"

    primary_unsigned = BondedToken(
        token_id=primary_id, pair_id=pid, role="primary",
        partner_token_id=mirror_id, timestamp_ns=ts,
        payload=dict(payload_primary),
    )
    mirror_unsigned = BondedToken(
        token_id=mirror_id, pair_id=pid, role="mirror",
        partner_token_id=primary_id, timestamp_ns=ts,
        payload=dict(payload_mirror),
    )
    primary = _resign(primary_unsigned)
    mirror  = _resign(mirror_unsigned)
    return primary, mirror


def verify_pair(primary: BondedToken, mirror: BondedToken) -> bool:
    """A bonded pair verifies iff:
      (a) both outer signatures verify under TOKEN_KEY_NS,
      (b) they share the same pair_id,
      (c) each token's partner_token_id matches the other's token_id,
      (d) one is role='primary' and the other role='mirror',
      (e) they share the same timestamp_ns (minted together).
    """
    if not primary.verify() or not mirror.verify():
        return False
    if primary.pair_id != mirror.pair_id:
        return False
    if primary.partner_token_id != mirror.token_id:
        return False
    if mirror.partner_token_id != primary.token_id:
        return False
    if {primary.role, mirror.role} != {"primary", "mirror"}:
        return False
    if primary.timestamp_ns != mirror.timestamp_ns:
        return False
    return True


# ── Ledger (atomic state register) ──────────────────────────────────────


class BondedPairLedgerError(RuntimeError):
    """Ledger invariant violated — illegal transition, unknown pair,
    or hash chain broken."""


class BondedPairLedger:
    """Append-only, hash-chained, file-backed state register.

    One process at a time should write. The ledger uses an advisory
    fcntl lock on the file to serialise concurrent writers in the
    same OS; cross-host coordination is outside scope (use one
    writer or put a proper consensus layer in front).

    Reads are lock-free and replay the log; the in-memory cache is
    refreshed on every read so observers see committed state without
    blocking on the writer.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── writes ──

    def init_pair(self, pair_id: str, actor: str = "") -> StateTransition:
        """Write the genesis transition for a pair_id. Raises if the
        pair already has a state."""
        if self.current_state(pair_id) is not None:
            raise BondedPairLedgerError(
                f"pair {pair_id!r} already initialised"
            )
        return self._append(
            pair_id=pair_id, from_state="", to_state=INITIAL_STATE,
            actor=actor or "system",
        )

    def transition(
        self, pair_id: str, to_state: str, actor: str,
        *, allowed_states: Optional[Iterable[str]] = None,
    ) -> StateTransition:
        """Move the pair to a new state. Validates:
          - the pair has been initialised (init_pair was called)
          - current state is not terminal (REVOKED / EXPIRED)
          - to_state is in allowed_states (default: DEFAULT_STATES)
        """
        states = frozenset(allowed_states) if allowed_states else DEFAULT_STATES
        if to_state not in states:
            raise BondedPairLedgerError(
                f"to_state {to_state!r} not in allowed states {sorted(states)}"
            )
        current = self.current_state(pair_id)
        if current is None:
            raise BondedPairLedgerError(
                f"pair {pair_id!r} not initialised — call init_pair() first"
            )
        if current in TERMINAL_STATES:
            raise BondedPairLedgerError(
                f"pair {pair_id!r} is in terminal state {current!r}; "
                f"cannot transition to {to_state!r}"
            )
        return self._append(
            pair_id=pair_id, from_state=current, to_state=to_state,
            actor=actor,
        )

    def revoke(self, pair_id: str, actor: str) -> StateTransition:
        """Shortcut: transition to REVOKED. Convenience for the
        common 'live revocation' use case."""
        return self.transition(pair_id, "REVOKED", actor)

    def _append(
        self, *, pair_id: str, from_state: str, to_state: str, actor: str,
    ) -> StateTransition:
        # Lock the file before reading the tail so we don't race with
        # another writer for the prev_signature.
        try:
            import fcntl
            _have_fcntl = True
        except ImportError:
            _have_fcntl = False

        # Open in append+read mode so we can both read the tail signature
        # and append the new entry atomically.
        self.path.touch(exist_ok=True)
        with self.path.open("r+", encoding="utf-8") as fh:
            if _have_fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                prev_sig = _tail_signature(fh)
                ts = int(time.time_ns())
                unsigned = StateTransition(
                    pair_id=pair_id, from_state=from_state,
                    to_state=to_state, timestamp_ns=ts,
                    actor=actor, prev_signature=prev_sig,
                )
                sig = _sign_ledger(unsigned._canonical())
                signed = StateTransition(**{**unsigned.to_dict(),
                                             "signature": sig})
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(signed.to_dict(),
                                    ensure_ascii=True,
                                    sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return signed
            finally:
                if _have_fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ── reads ──

    def current_state(self, pair_id: str) -> Optional[str]:
        """Latest state for pair_id, or None if uninitialised.

        Replays the log — O(n) in the number of entries. For
        latency-sensitive paths, wrap with an in-memory cache that
        invalidates on file mtime change."""
        last: Optional[str] = None
        for entry in self._iter_entries():
            if entry.pair_id == pair_id:
                last = entry.to_state
        return last

    def history(self, pair_id: str) -> List[StateTransition]:
        return [e for e in self._iter_entries() if e.pair_id == pair_id]

    def verify_chain(self) -> bool:
        """Replay every entry and check (a) each signature verifies,
        (b) every entry's prev_signature equals the previous entry's
        signature. Returns False on the first inconsistency."""
        prev_sig = ""
        for entry in self._iter_entries():
            if entry.prev_signature != prev_sig:
                return False
            if not entry.verify():
                return False
            prev_sig = entry.signature
        return True

    def _iter_entries(self) -> Iterable[StateTransition]:
        if not self.path.exists():
            return
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            d = json.loads(line)
            yield StateTransition(
                pair_id=d["pair_id"],
                from_state=d["from_state"],
                to_state=d["to_state"],
                timestamp_ns=int(d["timestamp_ns"]),
                actor=d["actor"],
                prev_signature=d["prev_signature"],
                signature=d.get("signature", ""),
            )


# ── Gate helper ─────────────────────────────────────────────────────────


def is_authorized(ledger: BondedPairLedger, pair_id: str) -> bool:
    """Convenience gate: True iff the pair's current state is
    ACTIVE_VALIDATED. The whole point of the primitive — a primary
    token's authority can be live-revoked by transitioning the pair
    to REVOKED, and any gate that calls this function will see the
    revocation on the next call without restarting or re-issuing the
    primary token."""
    return ledger.current_state(pair_id) == "ACTIVE_VALIDATED"


# ── Internal: signing + tail lookup ─────────────────────────────────────


def _canonical_bytes(d: Mapping) -> bytes:
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _sign_token(canonical: Mapping) -> str:
    from axiom_signing import derive_key
    key = derive_key(TOKEN_KEY_NS)
    return hmac.new(key, _canonical_bytes(canonical), hashlib.sha256).hexdigest()


def _sign_ledger(canonical: Mapping) -> str:
    from axiom_signing import derive_key
    key = derive_key(LEDGER_KEY_NS)
    return hmac.new(key, _canonical_bytes(canonical), hashlib.sha256).hexdigest()


def _resign(t: BondedToken) -> BondedToken:
    """Return a new BondedToken with `signature` set to the canonical HMAC."""
    sig = _sign_token(t._canonical())
    return BondedToken(**{**t.to_dict(), "signature": sig})


def _tail_signature(fh) -> str:
    """Read the signature of the last entry in an open file handle.
    Returns '' if the file is empty."""
    fh.seek(0)
    last_sig = ""
    for raw in fh:
        line = raw.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            last_sig = d.get("signature", "")
        except json.JSONDecodeError:
            # Don't silently chain past a malformed line — that hides
            # corruption. verify_chain() will catch it on the next read.
            return last_sig
    return last_sig


__all__ = [
    "BondedToken", "StateTransition",
    "BondedPairLedger", "BondedPairLedgerError",
    "mint_pair", "verify_pair", "is_authorized",
    "default_ledger_path",
    "TOKEN_KEY_NS", "LEDGER_KEY_NS",
    "DEFAULT_STATES", "INITIAL_STATE", "TERMINAL_STATES",
    "DEFAULT_LEDGER_FILENAME",
]
