"""HTTP client for the public Skill Pack registry.

When `AXIOM_FIREWALL_REGISTRY_URL` is set, the dashboard's
/dashboard/packs route fetches available packs from the registry
instead of reading them from the local filesystem.

Signatures are verified at this client layer too (defense in depth):
the registry server already refuses to serve unsigned packs, but the
dashboard re-verifies before installing in case a malicious mirror
substitutes a tampered version.

Stdlib-only — uses urllib.request so the SDK / dashboard has no new
runtime dependencies. Timeouts default to 10 seconds.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .skill_pack import SkillPackManifest, verify_first_party

DEFAULT_TIMEOUT_SECONDS = 10.0

log = logging.getLogger("axiom_firewall.registry_client")


class RegistryError(Exception):
    """Raised when the registry cannot be reached or returns malformed data."""


def _fetch_json(url: str, *, timeout: float) -> dict | list:
    """GET `url` and decode as JSON. Raises RegistryError on any failure."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "axiom-firewall/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RegistryError(f"{url} returned {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RegistryError(f"{url} returned 404")
        raise RegistryError(f"{url} HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RegistryError(f"failed to reach {url}: {e.reason}") from e
    except TimeoutError as e:
        raise RegistryError(f"timeout fetching {url}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RegistryError(f"{url} returned non-JSON body: {e}") from e


def list_packs(
    base_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[SkillPackManifest]:
    """Fetch the registry's pack index and return parsed manifests.

    The index endpoint returns metadata-only entries (no policy body)
    so this triggers one follow-up call per pack to load the full
    manifest. Phase 3+: add a /v1/packs?include=policy variant or
    introduce client caching.

    Skips packs whose signature fails to verify — never raises on
    individual pack failures so a single bad upstream pack doesn't
    break the dashboard.
    """
    url = base_url.rstrip("/") + "/v1/packs"
    body = _fetch_json(url, timeout=timeout)
    if not isinstance(body, dict) or "packs" not in body:
        raise RegistryError(f"{url} did not return a packs index")

    out: list[SkillPackManifest] = []
    for entry in body["packs"]:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        try:
            manifest = get_pack(base_url, entry["name"], timeout=timeout)
        except RegistryError as e:
            log.warning("skipping pack %s from registry: %s", entry.get("name"), e)
            continue
        if manifest is not None:
            out.append(manifest)
    return out


def get_pack(
    base_url: str,
    name: str,
    *,
    version: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[SkillPackManifest]:
    """Fetch one pack manifest. Returns None if the pack doesn't exist or
    fails signature verification.

    Raises RegistryError only for transport-level failures (DNS,
    timeout, malformed JSON). A pack that returns 404 is a clean None.
    """
    safe_name = urllib.parse.quote(name, safe="")
    if version is None:
        url = f"{base_url.rstrip('/')}/v1/packs/{safe_name}"
    else:
        safe_version = urllib.parse.quote(version, safe="")
        url = f"{base_url.rstrip('/')}/v1/packs/{safe_name}/{safe_version}"

    try:
        body = _fetch_json(url, timeout=timeout)
    except RegistryError as e:
        if "404" in str(e):
            return None
        raise

    if not isinstance(body, dict):
        raise RegistryError(f"{url} did not return a manifest object")

    try:
        manifest = SkillPackManifest.parse(body)
    except ValueError as e:
        log.warning("pack %s manifest failed to parse: %s", name, e)
        return None

    if not verify_first_party(manifest):
        log.warning(
            "pack %s@%s from %s has invalid signature — refusing",
            manifest.name, manifest.version, base_url,
        )
        return None

    return manifest
