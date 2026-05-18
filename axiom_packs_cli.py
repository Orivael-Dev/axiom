"""AXIOM Skill Packs — CLI client for the registry server.

Talks to an axiom_packs.server instance (default: http://localhost:8002)
and:

  list      — fetches /v1/packs and renders a summary table
  show      — fetches /v1/packs/<name>[/<version>] and prints the manifest
  install   — downloads + signature-verifies + writes pack.json to disk
  verify    — local signature check on an already-installed pack
              (no network)
  sources   — prints the registry URL the CLI is configured with

The registry URL resolves in this priority order:

  1. --registry flag
  2. AXIOM_PACKS_REGISTRY env var
  3. http://localhost:8002 (sensible default for local self-hosting)

The CLI uses only stdlib (urllib + json + argparse) — no requests,
no httpx — so it works in minimal Docker images and on the Orin
without extra deps.

## Examples

    axiom-packs list
    axiom-packs list --registry https://packs.orivael.dev
    axiom-packs show fdcpa
    axiom-packs show kid-ages-3-5 --version 0.1.0
    axiom-packs install coppa --dest ./packs
    axiom-packs install fdcpa --version 0.1.0 --dest /etc/axiom/packs
    axiom-packs verify ./packs/coppa/pack.json
    axiom-packs sources
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from axiom_firewall.skill_pack import (  # noqa: E402
    SkillPackManifest, verify_first_party,
)


DEFAULT_REGISTRY = "http://localhost:8002"
DEFAULT_TIMEOUT_S = 30


# ─── HTTP helpers ───────────────────────────────────────────────────────


class RegistryError(Exception):
    """Anything that went wrong talking to the registry."""


def _fetch_json(url: str, *, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    """GET <url> + return decoded JSON. Raises RegistryError on failure."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RegistryError(f"HTTP {resp.status} from {url}")
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise RegistryError(f"HTTP {e.code} from {url}: {body}") from e
    except urllib.error.URLError as e:
        raise RegistryError(f"Could not reach {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RegistryError(f"Non-JSON response from {url}: {e}") from e


# ─── Command implementations ────────────────────────────────────────────


def cmd_list(registry: str) -> int:
    data = _fetch_json(f"{registry}/v1/packs")
    packs = data.get("packs", [])
    if not packs:
        print("(no packs in this registry)")
        return 0
    # Pretty-printed table — no external deps
    name_w  = max(len("NAME"), max(len(p["name"]) for p in packs))
    ver_w   = max(len("VERSION"), max(len(p["version"]) for p in packs))
    title_w = max(len("TITLE"), min(50, max(len(p["title"]) for p in packs)))
    fmt = f"  {{:<{name_w}}}  {{:<{ver_w}}}  {{:<{title_w}}}  {{}}"
    print(fmt.format("NAME", "VERSION", "TITLE", "TAGS"))
    print(fmt.format("-" * name_w, "-" * ver_w, "-" * title_w, "----"))
    for p in sorted(packs, key=lambda x: x["name"]):
        title = (p["title"][:title_w-1] + "…") if len(p["title"]) > title_w else p["title"]
        tags = ",".join(p.get("tags", []))
        print(fmt.format(p["name"], p["version"], title, tags))
    print()
    print(f"  {len(packs)} pack(s) available from {registry}")
    return 0


def cmd_show(registry: str, name: str, version: Optional[str]) -> int:
    url = f"{registry}/v1/packs/{name}"
    if version:
        url = f"{url}/{version}"
    manifest_dict = _fetch_json(url)
    print(json.dumps(manifest_dict, indent=2, sort_keys=True))
    # Verify signature inline so the user knows the manifest is intact
    try:
        manifest = SkillPackManifest.parse(manifest_dict)
        ok = verify_first_party(manifest)
        print(file=sys.stderr)
        print(f"  signature: {'VERIFIED' if ok else 'FAILED'} "
              f"(first-party namespace)", file=sys.stderr)
        return 0 if ok else 2
    except Exception as e:
        print(f"  signature: UNVERIFIABLE ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 2


def cmd_install(
    registry: str, name: str, version: Optional[str],
    dest: Path, *, force: bool = False,
) -> int:
    url = f"{registry}/v1/packs/{name}"
    if version:
        url = f"{url}/{version}"
    manifest_dict = _fetch_json(url)

    # Always verify before writing — never leave an unverified pack on disk
    try:
        manifest = SkillPackManifest.parse(manifest_dict)
    except (KeyError, ValueError) as e:
        print(f"  ERROR: server returned malformed manifest: {e}",
              file=sys.stderr)
        return 2
    if not verify_first_party(manifest):
        print(f"  ERROR: signature did not verify under the first-party key.",
              file=sys.stderr)
        print(f"  REFUSING to install {name!r}.", file=sys.stderr)
        return 2

    pack_dir = dest / name
    pack_path = pack_dir / "pack.json"
    if pack_path.exists() and not force:
        print(f"  {pack_path} already exists. Pass --force to overwrite.",
              file=sys.stderr)
        return 1
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(
        json.dumps(manifest_dict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"  installed:  {pack_path}")
    print(f"  name:       {manifest.name}")
    print(f"  version:    {manifest.version}")
    print(f"  signature:  VERIFIED (first-party)")
    return 0


def cmd_verify(pack_path: Path) -> int:
    if not pack_path.is_file():
        print(f"  ERROR: not a file: {pack_path}", file=sys.stderr)
        return 1
    try:
        manifest_dict = json.loads(pack_path.read_text(encoding="utf-8"))
        manifest = SkillPackManifest.parse(manifest_dict)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"  ERROR: not a valid pack manifest: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    ok = verify_first_party(manifest)
    print(f"  pack:       {pack_path}")
    print(f"  name:       {manifest.name}")
    print(f"  version:    {manifest.version}")
    print(f"  signature:  {'VERIFIED' if ok else 'FAILED'} (first-party)")
    return 0 if ok else 2


def cmd_sources(registry: str) -> int:
    print(f"  active registry: {registry}")
    src = "default"
    if os.environ.get("AXIOM_PACKS_REGISTRY"):
        src = "AXIOM_PACKS_REGISTRY env var"
    print(f"  source:          {src}")
    print()
    print("  Override with --registry <url> or:")
    print("    export AXIOM_PACKS_REGISTRY=https://packs.orivael.dev")
    return 0


# ─── argparse wiring ────────────────────────────────────────────────────


def _resolve_registry(arg_value: Optional[str]) -> str:
    if arg_value:
        return arg_value.rstrip("/")
    env = os.environ.get("AXIOM_PACKS_REGISTRY", "")
    if env:
        return env.rstrip("/")
    return DEFAULT_REGISTRY


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axiom-packs",
        description="Client for the AXIOM Skill Pack registry.",
    )
    p.add_argument("--registry", default=None,
                   help="Registry base URL (default: AXIOM_PACKS_REGISTRY "
                        f"env var, else {DEFAULT_REGISTRY}).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="List all packs in the registry.")

    ps = sub.add_parser("show", help="Show one pack's manifest.")
    ps.add_argument("name")
    ps.add_argument("--version", default=None)

    pi = sub.add_parser("install", help="Download + verify + install a pack.")
    pi.add_argument("name")
    pi.add_argument("--version", default=None)
    pi.add_argument("--dest", type=Path, default=Path("./packs"),
                    help="Directory to install into. Default: ./packs")
    pi.add_argument("--force", action="store_true",
                    help="Overwrite if the pack is already installed.")

    pv = sub.add_parser("verify", help="Locally verify an installed pack.json.")
    pv.add_argument("path", type=Path)

    sub.add_parser("sources", help="Print the active registry URL + source.")

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    registry = _resolve_registry(args.registry)

    try:
        if args.cmd == "list":
            return cmd_list(registry)
        if args.cmd == "show":
            return cmd_show(registry, args.name, args.version)
        if args.cmd == "install":
            return cmd_install(registry, args.name, args.version,
                               args.dest, force=args.force)
        if args.cmd == "verify":
            return cmd_verify(args.path)
        if args.cmd == "sources":
            return cmd_sources(registry)
    except RegistryError as e:
        print(f"  registry error: {e}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
