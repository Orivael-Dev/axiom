# -*- coding: utf-8 -*-
"""
AXIOM eXchange Model (.AXM) — ORVL-023 unit tests
==================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

Exercises the hybrid trust model — open container, per-delegate HMAC
signatures, ANF coprocessor driven on verify, MKB BlockRegistry receives
lazy-loaded skills.

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_axm"

from axiom_axm import (
    AXMContainer, AXMHeader, AXMSignatureMismatch, AXMNotVerified, AXMError,
)
from examples.axm_pack_starter import STARTER_SPEC


@pytest.fixture()
def container(tmp_path):
    cpath = tmp_path / "starter.axm"
    return AXMContainer.pack(STARTER_SPEC, str(cpath))


# ===========================================================================
# SECTION 1 — BLOCKED (signature + verification refusals)
# ===========================================================================

class TestAXMBlocked:

    def test_blocked_tampered_header_fails_load(self, tmp_path):
        cpath = tmp_path / "tampered.axm"
        AXMContainer.pack(STARTER_SPEC, str(cpath))
        # Flip one field in header.json so the signature no longer verifies.
        header_path = cpath / "header.json"
        data = json.loads(header_path.read_text(encoding="utf-8"))
        data["core_logic"] = "tampered_core"
        header_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(AXMSignatureMismatch):
            AXMContainer.from_path(str(cpath))

    def test_blocked_unsigned_delegate_fails_verify(self, tmp_path):
        cpath = tmp_path / "with_unsigned.axm"
        AXMContainer.pack(STARTER_SPEC, str(cpath))
        # Corrupt one delegate signature.
        skill_path = cpath / "delegates" / "pii_redactor" / "skill.json"
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        data["signature"] = "0" * 64
        skill_path.write_text(json.dumps(data), encoding="utf-8")
        c = AXMContainer.from_path(str(cpath))
        assert c.verify_proofs() is False

    def test_blocked_route_before_verify_refused(self, container):
        # verify_proofs() has NOT been called yet.
        with pytest.raises(AXMNotVerified):
            container.route("any task")


# ===========================================================================
# SECTION 2 — PASSED (end-to-end exercise of MKB + ANF wiring)
# ===========================================================================

class TestAXMPassed:

    def test_passed_pack_then_load_round_trip(self, tmp_path):
        cpath = tmp_path / "rt.axm"
        original = AXMContainer.pack(STARTER_SPEC, str(cpath))
        reloaded = AXMContainer.from_path(str(cpath))
        assert original.header.signature == reloaded.header.signature
        assert len(original.delegates) == len(reloaded.delegates)
        assert len(original.proofs) == len(reloaded.proofs)

    def test_passed_verify_proofs_invokes_anf(self, container):
        """The ANF coprocessor must be driven once per proof entry."""
        from unittest.mock import MagicMock
        fake_anf = MagicMock()
        fake_anf.process.return_value = {
            "gate_fired": False, "intent_class": "INFORM",
            "cores_active": 20, "energy_ratio": 1.0,
            "distance": 0.0, "latency_ns": 1, "fused_rom_rules": 0,
            "hmac": "0" * 64,
        }
        ok = container.verify_proofs(anf_emulator=fake_anf)
        assert ok is True
        assert fake_anf.process.call_count == len(container.proofs)

    def test_passed_route_lazy_loads_matched_delegates_only(self, container):
        container.verify_proofs()
        r = container.route("Explain the transformer architecture briefly")
        # INFORM intent → pii_redactor + anf_governance, NOT vector_recall.
        loaded = set(r.loaded_skills)
        assert "pii_redactor" in loaded
        assert "anf_governance" in loaded
        assert "vector_recall" not in loaded
        assert "vector_recall" in r.skipped_skills

    def test_passed_loaded_skill_lands_in_mkb_block_registry(self, container):
        container.verify_proofs()
        container.route("Explain transformers")
        # MKB registry is built lazily on first route. Reuse the
        # container's accessor so this matches production semantics.
        registry = container._mkb_registry
        assert registry is not None
        kb = registry.find("pii_redactor")
        assert kb is not None
        assert kb.block_type == "AXM_SKILL"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestAXMArchive:
    """Binary `.axm` zip-container round-trip + tamper detection."""

    def test_passed_pack_archive_creates_zip(self, tmp_path):
        import zipfile
        out = tmp_path / "shipping.axm"
        c = AXMContainer.pack(STARTER_SPEC, str(out), archive=True)
        assert out.is_file()
        assert zipfile.is_zipfile(out)
        assert c.is_archive is True
        # Every sub-module of the directory layout must appear in the zip.
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "header.json" in names
        assert any(n.startswith("delegates/") and n.endswith("skill.json")
                    for n in names)
        assert "proofs/ledger.jsonl" in names

    def test_passed_from_path_auto_detects_zip(self, tmp_path):
        out = tmp_path / "shipping.axm"
        AXMContainer.pack(STARTER_SPEC, str(out), archive=True)
        reloaded = AXMContainer.from_path(str(out))
        assert reloaded.is_archive is True
        assert len(reloaded.delegates) == 3
        assert reloaded.verify_proofs() is True

    def test_passed_dir_vs_archive_same_fingerprint(self, tmp_path):
        """Packing the same spec as dir vs archive must produce
        identical header signatures (the fingerprint is HMAC-derived
        from the header signature only)."""
        c_dir = AXMContainer.pack(STARTER_SPEC, str(tmp_path / "dir.axm"))
        c_zip = AXMContainer.pack(STARTER_SPEC, str(tmp_path / "shipping.axm"),
                                    archive=True)
        assert c_dir.fingerprint() == c_zip.fingerprint()

    def test_blocked_tampered_zip_header_fails_load(self, tmp_path):
        """Mutating header.json inside the zip must raise
        AXMSignatureMismatch on load — same contract as the dir form."""
        import json
        import zipfile
        out = tmp_path / "shipping.axm"
        AXMContainer.pack(STARTER_SPEC, str(out), archive=True)
        # Rebuild the zip with a mangled header.
        tampered = tmp_path / "tampered.axm"
        with zipfile.ZipFile(out, "r") as src, \
             zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as dst:
            for n in src.namelist():
                if n == "header.json":
                    hdr = json.loads(src.read(n).decode("utf-8"))
                    hdr["core_logic"] = "tampered"
                    dst.writestr(n, json.dumps(hdr))
                else:
                    dst.writestr(n, src.read(n))
        with pytest.raises(AXMSignatureMismatch):
            AXMContainer.from_path(str(tampered))

    def test_blocked_zip_path_traversal_rejected(self, tmp_path):
        """A zip member with a `../` traversal path must be refused —
        otherwise an attacker could plant files outside the extract
        tempdir."""
        import zipfile
        from axiom_axm import AXMError
        evil = tmp_path / "evil.axm"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("header.json", "{}")            # need at least one valid entry
            zf.writestr("../escape.txt", "owned")        # path-traversal probe
        with pytest.raises(AXMError):
            AXMContainer.from_path(str(evil))

    def test_passed_to_archive_from_directory_container(self, tmp_path):
        """A container loaded from an exploded directory can be
        re-packaged into a zip via to_archive() — same fingerprint."""
        c = AXMContainer.pack(STARTER_SPEC, str(tmp_path / "dir.axm"))
        out = tmp_path / "shipped.axm"
        result = c.to_archive(str(out))
        assert result == out
        assert out.is_file()
        re = AXMContainer.from_path(str(out))
        assert re.fingerprint() == c.fingerprint()


class TestAXMInvariants:

    def test_invariant_container_header_is_frozen(self, container):
        # Frozen dataclass — assignment must raise.
        with pytest.raises((AttributeError, Exception)):
            container.header.core_logic = "rewrite"  # type: ignore

    def test_invariant_signing_key_never_exposed(self, container):
        """repr/str must NOT contain the raw HMAC key bytes in any form.
        Only the public fingerprint is allowed."""
        from axiom_signing import derive_key
        raw_container_key = derive_key(b"axiom-axm-container-v1").hex()
        raw_delegate_key  = derive_key(b"axiom-axm-delegate-v1").hex()
        text = repr(container) + str(container)
        assert raw_container_key not in text
        assert raw_delegate_key  not in text
        # Sanity: fingerprint IS exposed.
        assert container.fingerprint() in text


# ===========================================================================
# SECTION 4 — quant_map widening (str | dict) for SRD integration
# ===========================================================================

class TestAXMQuantMapWidening:
    """Phase D of the SRD plan: AXMHeader.quant_map now accepts a
    structured dict in addition to the legacy string tag."""

    def _make_header(self, qm):
        return AXMHeader(
            format_version="1.0",
            core_logic="test_core",
            quant_map=qm,
            delegates=(),
            safety_proofs=False,
            hardware_map="cpu",
        )

    def test_passed_string_form_still_works(self):
        """Backwards compatibility: every existing axm file uses the
        string form — it must still sign cleanly."""
        h = self._make_header("elastic_per_layer")
        payload = h._payload()
        assert payload["quant_map"] == "elastic_per_layer"

    def test_passed_dict_form_round_trips(self):
        """SRD's per-row shape: {scheme, group_size, alpha, bpw}.
        The payload preserves the dict for downstream consumers."""
        srd_spec = {
            "scheme": "srd",
            "group_size": 64,
            "alpha": 1.0,
            "bpw": 13.0,
        }
        h = self._make_header(srd_spec)
        payload = h._payload()
        assert payload["quant_map"] == srd_spec
        # Defensive copy — mutating the returned dict must not poison
        # the header.
        payload["quant_map"]["alpha"] = 0.0
        assert h.quant_map["alpha"] == 1.0

    def test_passed_dict_payload_is_json_canonicalizable(self):
        """The signing path uses json.dumps(sort_keys=True) — the
        dict form must survive that round-trip."""
        h = self._make_header({
            "scheme": "srd",
            "group_size": 64,
            "alpha": 1.0,
            "bpw": 13.0,
        })
        canonical = json.dumps(h._payload(), sort_keys=True)
        round_tripped = json.loads(canonical)
        assert round_tripped["quant_map"]["scheme"] == "srd"
        assert round_tripped["quant_map"]["bpw"] == 13.0

    def test_blocked_wrong_type_rejected(self):
        """quant_map can't be None / list / int — only str | dict."""
        from axiom_axm import _canonicalize_quant_map
        with pytest.raises(TypeError, match="quant_map"):
            _canonicalize_quant_map(None)
        with pytest.raises(TypeError, match="quant_map"):
            _canonicalize_quant_map([("scheme", "srd")])
        with pytest.raises(TypeError, match="quant_map"):
            _canonicalize_quant_map(42)
