#!/usr/bin/env python3
"""Sign all first-party packs in packs/.

Run after editing a pack manifest. Re-running is safe: idempotent for
unchanged packs. Uses AXIOM_MASTER_KEY (the production master key) to
derive the first-party signing namespace.

Usage:
    AXIOM_MASTER_KEY=<hex> python scripts/sign_packs.py [packs/<name>]

If a specific pack path is given, only that one is signed; otherwise
all directories under packs/ that contain a pack.json are signed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axiom_firewall.skill_pack import sign_first_party  # noqa: E402

PACKS_DIR = REPO_ROOT / "packs"


def sign_one(manifest_path: Path) -> tuple[bool, str]:
    """Sign a single pack.json. Returns (changed, message)."""
    body = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_sig = sign_first_party(body)
    current_sig = body.get("signature", "")
    if current_sig == expected_sig:
        return (False, f"  {manifest_path.parent.name}: signature already current")
    body["signature"] = expected_sig
    manifest_path.write_text(
        json.dumps(body, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return (True, f"  {manifest_path.parent.name}: signed (sig={expected_sig[:16]}...)")


def main(args: list[str]) -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set. The signature is derived from this "
            "key, so first-party packs must be signed with the production "
            "master key to install on first-party deployments."
        )

    if len(args) > 1:
        targets = [Path(args[1])]
    else:
        targets = [
            entry / "pack.json"
            for entry in sorted(PACKS_DIR.iterdir())
            if entry.is_dir() and (entry / "pack.json").is_file()
        ]

    if not targets:
        sys.exit("No packs found under packs/")

    changed = 0
    for target in targets:
        path = target if target.name == "pack.json" else target / "pack.json"
        if not path.is_file():
            print(f"  {path}: not a pack.json file — skipping")
            continue
        was_changed, msg = sign_one(path)
        print(msg)
        if was_changed:
            changed += 1

    print()
    print(f"{changed} pack(s) updated, {len(targets) - changed} already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
