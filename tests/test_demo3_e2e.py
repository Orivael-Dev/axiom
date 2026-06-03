# -*- coding: utf-8 -*-
"""
Demo 3/4 end-to-end — signed agent install lifecycle vs a real server.
======================================================================
Auto-skips unless an Axiom server is reachable. The test signs the demo
manifest with the publisher key (axiom_firewall.skill_pack) — standing in
for a registry; product code never does this.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _axiom_repo():
    repo = os.environ.get("AXIOM_REPO")
    if repo and (Path(repo) / "axiom_mcp_server.py").exists():
        return repo
    return None


pytestmark = pytest.mark.skipif(
    _axiom_repo() is None and importlib.util.find_spec("axiom_mcp_server") is None,
    reason="Axiom unavailable (set AXIOM_REPO or pip install axiom)")


def _signed_manifest():
    sys.path.insert(0, _axiom_repo() or "")
    from axiom_firewall.skill_pack import sign_first_party   # publisher-side
    body = {
        "format_version": "1.0", "name": "tone-beatz-agent", "title": "Tone Beatz",
        "description": "mixing helper", "version": "0.1.0", "author": "Orivael Dev",
        "license": "MIT", "tags": ["music"], "tested_against": ["axiom-firewall>=0.1.0"],
        "policy": {"version": 1,
                   "additional_block_patterns": [{"class": "HARM", "regex": "leak\\s+keys"}],
                   "disabled_default_classes": [], "allow_only_classes": None},
    }
    body["signature"] = sign_first_party(body)
    return body


def test_install_approve_act_revoke_block(tmp_path, capsys):
    os.environ.setdefault("AXIOM_MASTER_KEY", "test_key_for_demo3")
    from aui.demo3 import run
    rc = run(_signed_manifest(), approve=True, then_revoke=True,
             axiom_repo=_axiom_repo(),
             audit_ledger=str(tmp_path / "audit.jsonl"),
             mkt_ledger=str(tmp_path / "mkt.jsonl"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "signature: VALID" in out
    assert "after approve" in out and "after revoke" in out
    # the signed audit ledger captured the agent lifecycle
    rows = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    kinds = {r["event_type"] for r in rows}
    assert {"agent_sandboxed", "agent_approved", "agent_revoked"} <= kinds


def test_tampered_manifest_refused(tmp_path, capsys):
    os.environ.setdefault("AXIOM_MASTER_KEY", "test_key_for_demo3")
    from aui.demo3 import run
    man = _signed_manifest()
    man["author"] = "Attacker"     # invalidates signature
    rc = run(man, approve=False, then_revoke=False, axiom_repo=_axiom_repo(),
             audit_ledger=str(tmp_path / "audit.jsonl"),
             mkt_ledger=str(tmp_path / "mkt.jsonl"))
    assert rc == 2
    assert "rejected" in capsys.readouterr().out
