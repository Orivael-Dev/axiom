"""
AXIOM Action Manifest (Layer 4 — Governance Guard)

Signed scope declaration for autonomous agent sessions. An agent declares
its intended action set upfront; the pre-tool-call hook validates every
action against the manifest before execution — one approval gates the
whole session instead of one approval per action.

Env vars:
    AXIOM_MANIFEST_PATH  JSONL store (default: ~/.axiom/manifests.jsonl)
    AXIOM_SESSION_ID     Active session ID (read by the hook)
    AXIOM_MASTER_KEY     Used for HMAC signing
"""
from __future__ import annotations

import enum
import fnmatch
import hashlib
import hmac as hmac_lib
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

# ── Signing ───────────────────────────────────────────────────────────────────
_NAMESPACE: bytes = b"axiom-manifest-v1"
_MANIFEST_VERSION: str = "1.0"  # CANNOT_MUTATE

try:
    from axiom_signing import derive_key as _ax_derive_key
    def _derive_key() -> bytes:
        return _ax_derive_key(_NAMESPACE)
except (ImportError, RuntimeError):
    def _derive_key() -> bytes:
        master = os.environ.get("AXIOM_MASTER_KEY", "")
        return hashlib.pbkdf2_hmac("sha256", master.encode(), _NAMESPACE, iterations=1)


def _sign_payload(key: bytes, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac_lib.new(key, canonical, hashlib.sha256).hexdigest()


def _verify_payload(key: bytes, payload: dict, signature: str) -> bool:
    expected = _sign_payload(key, payload)
    return hmac_lib.compare_digest(signature, expected)


# ── Default blocked paths ─────────────────────────────────────────────────────
DEFAULT_BLOCKED_PATHS: Tuple[str, ...] = (
    ".env", "*.env", ".env.*",
    "*.key", "*.pem", "*.p12", "*.pfx",
    ".git/config",
    "*credentials*", "*secrets*",
    "*id_rsa*", "*id_ed25519*",
)

_FILE_TOOLS = frozenset({"Edit", "Write", "Read", "Glob", "Grep", "MultiEdit"})


# ── Verdict ───────────────────────────────────────────────────────────────────
class ManifestVerdict(enum.Enum):
    ALLOW  = "allow"   # in-scope + low-risk → auto-proceed
    REVIEW = "review"  # borderline → surface to user without hard-blocking
    BLOCK  = "block"   # out-of-scope or blocked path → refuse


# ── Manifest dataclass ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ActionManifest:
    session_id:       str
    scope:            str                  # human description of the task
    allowed_paths:    Tuple[str, ...]      # fnmatch patterns for file ops
    blocked_paths:    Tuple[str, ...]      # checked before allowed_paths
    allowed_tools:    Tuple[str, ...]      # ("*",) = all tools
    allowed_commands: Tuple[str, ...]      # fnmatch for Bash; ("*",) = all
    created_at:       str = ""             # ISO 8601 UTC
    expires_at:       str = ""             # ISO 8601 UTC, "" = no expiry
    version:          str = _MANIFEST_VERSION
    hmac_signature:   str = ""             # over all other fields


# ── Validator ─────────────────────────────────────────────────────────────────
class ManifestValidator:
    """Create, sign, verify, and check actions against an ActionManifest."""

    def create(
        self,
        scope: str,
        allowed_paths: Tuple[str, ...] = ("**",),
        blocked_paths: Tuple[str, ...] = DEFAULT_BLOCKED_PATHS,
        allowed_tools: Tuple[str, ...] = ("*",),
        allowed_commands: Tuple[str, ...] = ("*",),
        session_id: Optional[str] = None,
        expires_at: str = "",
    ) -> ActionManifest:
        return ActionManifest(
            session_id=session_id or str(uuid.uuid4()),
            scope=scope,
            allowed_paths=tuple(allowed_paths),
            blocked_paths=tuple(blocked_paths),
            allowed_tools=tuple(allowed_tools),
            allowed_commands=tuple(allowed_commands),
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at,
        )

    def sign(self, manifest: ActionManifest, key: bytes) -> ActionManifest:
        payload = {k: v for k, v in asdict(manifest).items() if k != "hmac_signature"}
        sig = _sign_payload(key, payload)
        return ActionManifest(**{**asdict(manifest), "hmac_signature": sig})

    def verify(self, manifest: ActionManifest, key: bytes) -> bool:
        if not manifest.hmac_signature:
            return False
        payload = {k: v for k, v in asdict(manifest).items() if k != "hmac_signature"}
        return _verify_payload(key, payload, manifest.hmac_signature)

    def is_expired(self, manifest: ActionManifest) -> bool:
        if not manifest.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(manifest.expires_at)
            return datetime.now(timezone.utc) > exp
        except ValueError:
            return False

    def check_action(
        self,
        manifest: ActionManifest,
        tool_name: str,
        path: Optional[str] = None,
        command: Optional[str] = None,
    ) -> Tuple[ManifestVerdict, str]:
        """Validate a proposed tool call against the manifest. Pure function — no I/O."""

        if self.is_expired(manifest):
            return ManifestVerdict.BLOCK, "manifest has expired"

        # 1. Blocked paths are checked first regardless of everything else
        if path:
            basename = os.path.basename(path)
            for pat in manifest.blocked_paths:
                if fnmatch.fnmatch(basename, pat) or fnmatch.fnmatch(path, pat):
                    return ManifestVerdict.BLOCK, f"path {path!r} is explicitly blocked"

        # 2. Tool allowlist
        if "*" not in manifest.allowed_tools and tool_name not in manifest.allowed_tools:
            return ManifestVerdict.BLOCK, f"tool {tool_name!r} not in allowed_tools"

        # 3. Path scope check for file-touching tools
        if (
            path
            and tool_name in _FILE_TOOLS
            and "**" not in manifest.allowed_paths
            and "*" not in manifest.allowed_paths
        ):
            basename = os.path.basename(path)
            matched = any(
                fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(basename, pat)
                for pat in manifest.allowed_paths
            )
            if not matched:
                return ManifestVerdict.BLOCK, f"path {path!r} is outside declared scope"

        # 4. Command scope check for Bash (REVIEW not BLOCK — commands are hard to predict exactly)
        if command and tool_name == "Bash" and "*" not in manifest.allowed_commands:
            matched = any(fnmatch.fnmatch(command, pat) for pat in manifest.allowed_commands)
            if not matched:
                return ManifestVerdict.REVIEW, "command is outside declared scope — review before proceeding"

        return ManifestVerdict.ALLOW, "within manifest scope"


# ── Store ─────────────────────────────────────────────────────────────────────
class ManifestStore:
    """Per-session JSONL store. One line per session_id (last-write-wins)."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or Path(
            os.environ.get("AXIOM_MANIFEST_PATH",
                           str(Path.home() / ".axiom" / "manifests.jsonl"))
        )

    def _read_all(self) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        if not self._path.exists():
            return entries
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    sid = data.get("session_id")
                    if sid:
                        entries[sid] = data
                except json.JSONDecodeError:
                    continue
        return entries

    def _write_all(self, entries: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            for data in entries.values():
                f.write(json.dumps(data) + "\n")

    def load(self, session_id: str) -> Optional[ActionManifest]:
        data = self._read_all().get(session_id)
        if data is None:
            return None
        return ActionManifest(
            session_id=data["session_id"],
            scope=data.get("scope", ""),
            allowed_paths=tuple(data.get("allowed_paths", [])),
            blocked_paths=tuple(data.get("blocked_paths", [])),
            allowed_tools=tuple(data.get("allowed_tools", [])),
            allowed_commands=tuple(data.get("allowed_commands", [])),
            created_at=data.get("created_at", ""),
            expires_at=data.get("expires_at", ""),
            version=data.get("version", _MANIFEST_VERSION),
            hmac_signature=data.get("hmac_signature", ""),
        )

    def save(self, manifest: ActionManifest) -> None:
        entries = self._read_all()
        entries[manifest.session_id] = {
            k: list(v) if isinstance(v, tuple) else v
            for k, v in asdict(manifest).items()
        }
        self._write_all(entries)

    def forget(self, session_id: str) -> None:
        entries = self._read_all()
        entries.pop(session_id, None)
        self._write_all(entries)

    def purge_expired(self) -> int:
        validator = ManifestValidator()
        entries = self._read_all()
        before = len(entries)
        entries = {
            sid: data for sid, data in entries.items()
            if not validator.is_expired(self.load(sid))  # type: ignore[arg-type]
        }
        self._write_all(entries)
        return before - len(entries)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Declare and inspect AXIOM session manifests"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Declare a new manifest for this session")
    c.add_argument("--scope",         required=True, help="Task description")
    c.add_argument("--paths",         default="**",  help="Comma-separated allowed path patterns")
    c.add_argument("--blocked-paths",                help="Comma-sep extra blocked paths (appended to defaults)")
    c.add_argument("--tools",         default="*",   help="Comma-separated allowed tool names")
    c.add_argument("--commands",      default="*",   help="Comma-separated allowed Bash command patterns")
    c.add_argument("--session-id",                   help="Session ID (auto-generated if omitted)")
    c.add_argument("--no-sign",       action="store_true")

    s = sub.add_parser("show",   help="Print manifest for a session")
    s.add_argument("session_id")

    d = sub.add_parser("forget", help="Remove manifest for a session")
    d.add_argument("session_id")

    ch = sub.add_parser("check", help="Check a proposed action against the active manifest")
    ch.add_argument("--session-id")
    ch.add_argument("--tool",    required=True)
    ch.add_argument("--path")
    ch.add_argument("--command")

    args = parser.parse_args()
    validator = ManifestValidator()
    store     = ManifestStore()

    if args.cmd == "create":
        paths    = tuple(p.strip() for p in args.paths.split(","))
        blocked  = DEFAULT_BLOCKED_PATHS
        if args.blocked_paths:
            blocked = blocked + tuple(p.strip() for p in args.blocked_paths.split(","))
        tools    = tuple(t.strip() for t in args.tools.split(","))
        commands = tuple(c.strip() for c in args.commands.split(","))

        manifest = validator.create(
            scope=args.scope,
            allowed_paths=paths,
            blocked_paths=blocked,
            allowed_tools=tools,
            allowed_commands=commands,
            session_id=args.session_id,
        )
        if not args.no_sign:
            manifest = validator.sign(manifest, _derive_key())

        store.save(manifest)
        print(f"manifest created: {manifest.session_id}")
        print(f"  scope:    {manifest.scope}")
        print(f"  paths:    {manifest.allowed_paths}")
        print(f"  tools:    {manifest.allowed_tools}")
        print(f"  commands: {manifest.allowed_commands}")
        print(f"\nexport AXIOM_SESSION_ID={manifest.session_id}")

    elif args.cmd == "show":
        m = store.load(args.session_id)
        if not m:
            print(f"no manifest for {args.session_id!r}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(
            {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(m).items()},
            indent=2
        ))

    elif args.cmd == "forget":
        store.forget(args.session_id)
        print(f"manifest forgotten: {args.session_id}")

    elif args.cmd == "check":
        sid = args.session_id or os.environ.get("AXIOM_SESSION_ID", "")
        if not sid:
            print("no session_id — set --session-id or AXIOM_SESSION_ID", file=sys.stderr)
            sys.exit(1)
        m = store.load(sid)
        if not m:
            print("no manifest found — ALLOW (no scope declared)")
            sys.exit(0)
        verdict, reason = validator.check_action(m, args.tool, args.path, args.command)
        print(f"{verdict.value}: {reason}")
        sys.exit(1 if verdict == ManifestVerdict.BLOCK else 0)
