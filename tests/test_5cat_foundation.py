"""Foundation tests for the 5-category benchmark: schema, signing,
reproducibility, stub adapter contract. These must pass before any
category lands."""
from __future__ import annotations

import json

import pytest

from axiom_5cat_benchmark import (
    BenchmarkResults, Completion, PerCategoryReport, TrialResult,
    sign_result, verify_result,
)
from axiom_5cat_benchmark.adapters import (
    ModelAdapter, StubAdapter, build_adapter,
)
from axiom_5cat_benchmark.reproducibility import (
    SCHEMA_TAG, adapter_versions, build_meta, utcnow_iso,
)
from axiom_5cat_benchmark.schema import winner_label
from axiom_5cat_benchmark.signing import (
    master_key_fingerprint, sign_and_attach, verify_attached,
)


# ─── Signing round-trip ────────────────────────────────────────────

def test_sign_then_verify_passes():
    payload = {"foo": 1, "bar": [1, 2, 3], "nested": {"k": "v"}}
    sig = sign_result(payload)
    assert sig.startswith("hmac-sha256:")
    assert verify_result(payload, sig) is True


def test_verify_rejects_tampered_payload():
    payload = {"foo": 1, "bar": [1, 2, 3]}
    sig = sign_result(payload)
    tampered = dict(payload, foo=2)
    assert verify_result(tampered, sig) is False


def test_verify_rejects_garbage_signature():
    payload = {"foo": 1}
    assert verify_result(payload, "not-a-real-signature") is False
    assert verify_result(payload, "hmac-sha256:0000") is False
    assert verify_result(payload, "") is False


def test_canonical_form_is_key_order_independent():
    """Two dicts with the same contents but different insertion
    order must produce the same signature."""
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}
    assert sign_result(a) == sign_result(b)


def test_sign_and_attach_round_trip():
    signed = sign_and_attach({"category": "test", "score": 14})
    assert "signature" in signed
    assert verify_attached(signed) is True
    # Tamper:
    signed["score"] = 99
    assert verify_attached(signed) is False


def test_master_key_fingerprint_is_stable_and_short():
    fpr = master_key_fingerprint()
    assert fpr.startswith("sha256:")
    assert len(fpr) == len("sha256:") + 16
    # Stability under the same master key:
    assert master_key_fingerprint() == fpr


def test_master_key_fingerprint_different_from_signing_key():
    """Publishing the fingerprint must not leak the signing key."""
    # If they were the same derivation, a tampered payload + the
    # fingerprint would let an attacker forge.  We're not testing
    # cryptographic strength here, just that they're distinct.
    fpr = master_key_fingerprint()
    sig = sign_result({"probe": "value"})
    assert fpr not in sig
    assert sig.split(":")[1] not in fpr


# ─── Reproducibility meta block ───────────────────────────────────

def test_build_meta_has_required_fields():
    started = utcnow_iso()
    ended = utcnow_iso()
    meta = build_meta(
        seed=1729, temperature=0.0,
        started_utc=started, ended_utc=ended,
    )
    assert meta["schema"] == SCHEMA_TAG
    assert meta["seed"] == 1729
    assert meta["temperature"] == 0.0
    assert "axiom_commit" in meta
    assert "master_key_fingerprint" in meta
    assert "adapter_versions" in meta


def test_adapter_versions_lists_known_providers():
    versions = adapter_versions()
    # Stub always reports its synthetic version.
    assert versions["stub"] == "stub-v1"
    # Other adapters report either a real version string or "absent".
    for provider in ("anthropic", "openai", "local"):
        assert provider in versions


def test_utcnow_iso_returns_zulu_string():
    s = utcnow_iso()
    assert s.endswith("Z")
    # Round-trip parse must succeed.
    import datetime
    datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ─── Stub adapter contract ─────────────────────────────────────────

def test_stub_satisfies_protocol_at_runtime():
    """Runtime Protocol check — guards against silent contract drift."""
    a = StubAdapter()
    assert isinstance(a, ModelAdapter)


def test_stub_returns_deterministic_completion():
    a = StubAdapter(table={"hello": "world"})
    c1 = a.complete("hello")
    c2 = a.complete("hello")
    assert c1.text == "world"
    assert c1.text == c2.text
    assert c1.raw_response_sha == c2.raw_response_sha


def test_stub_default_response_when_unregistered():
    a = StubAdapter()
    c = a.complete("never-seen-prompt-xyz")
    assert c.text.startswith("stub:")
    assert "never-seen-prompt-xyz" in c.text


def test_stub_counts_calls():
    a = StubAdapter()
    a.complete("first")
    a.complete("second")
    a.complete("third")
    assert a.call_count == 3


def test_stub_respects_max_tokens_cap():
    long_text = "x" * 100_000   # ~25k tokens at 4-chars-per-token
    a = StubAdapter(table={"big": long_text})
    c = a.complete("big", max_tokens=10)
    assert c.output_tokens <= 10


def test_stub_completion_has_provenance_hash():
    """Every Completion must carry a raw_response_sha so tampering
    with a recorded results.json is detectable."""
    c = StubAdapter().complete("anything")
    assert c.raw_response_sha
    assert len(c.raw_response_sha) == 64   # hex sha256


# ─── Adapter factory ──────────────────────────────────────────────

def test_build_adapter_stub_works_without_external_sdks():
    a = build_adapter("stub:fixed-v1")
    assert isinstance(a, StubAdapter)
    assert a.model_id() == "stub:fixed-v1"


def test_build_adapter_rejects_malformed_spec():
    with pytest.raises(ValueError):
        build_adapter("no-colon-here")
    with pytest.raises(ValueError):
        build_adapter("unknown-provider:foo")


# ─── Schema ────────────────────────────────────────────────────────

def test_winner_label_three_outcomes():
    assert winner_label(5, 9) == "AXIOM"
    assert winner_label(9, 5) == "RAW"
    assert winner_label(7, 7) == "TIE"


def test_trial_result_truncates_outputs_to_300_chars():
    """Matches benchmark_v1_0.py:90-91 convention so review_scores.py
    sees the same on-disk shape it always has."""
    long = "A" * 500
    t = TrialResult(
        id="X", category="Demo", name="n", task="t",
        raw_total=0, axiom_total=0,
        raw_scores={}, axiom_scores={},
        winner="TIE",
        raw_output=long, axiom_output=long,
    )
    d = t.to_dict()
    assert len(d["raw_output"]) == 300
    assert len(d["axiom_output"]) == 300


def test_per_category_report_serialises_extras_flat():
    """Extras like 'ece' or 'median_perf_per_watt' should appear
    inline alongside avg/n_trials/gate, not nested."""
    r = PerCategoryReport(
        avg=14.1, n_trials=45, gate="PASS",
        extras={"ece": 0.07, "brier": 0.18},
    )
    d = r.to_dict()
    assert d["avg"] == 14.1
    assert d["gate"] == "PASS"
    assert d["ece"] == 0.07
    assert d["brier"] == 0.18


def test_benchmark_results_top_level_shape_compat_with_v1_0():
    """The five legacy keys must serialise at the top level for
    review_scores.py compatibility."""
    r = BenchmarkResults(
        meta={"schema": SCHEMA_TAG, "signature": "hmac-sha256:abc"},
        raw_avg=8.2, axiom_avg=13.7, improvement_pct=67.0,
        axiom_wins=23, total_tests=30, criteria_met=True,
        per_category={}, tests=[],
    )
    d = r.to_dict()
    for required in ("raw_avg", "axiom_avg", "improvement_pct",
                     "axiom_wins", "total_tests", "criteria_met",
                     "tests"):
        assert required in d, f"missing {required} (review_scores.py needs it)"
    # And the additive keys:
    assert "meta" in d
    assert "per_category" in d


# ─── Runner with stub-only category ────────────────────────────────

def _install_minimal_stub_category():
    """Register a tiny fake category so we can run the runner end-to-
    end without depending on any real cat1/2/3/4/5 subpackage."""
    from axiom_5cat_benchmark.categories import _FACTORIES
    from axiom_5cat_benchmark.categories.base import Category

    class _MinCat:
        id = 99
        name = "MinimalStubForTest"
        max_score_per_trial = 16

        def run(self, adapter, *, n_trials, seed, temperature):
            out = []
            for i in range(n_trials):
                c = adapter.complete(f"trial-{i}")
                out.append(TrialResult(
                    id=f"T{i}", category=self.name, name="stub",
                    task="probe", raw_total=4, axiom_total=12,
                    raw_scores={"Honesty": 0, "Calibration": 0},
                    axiom_scores={"Honesty": 2, "Calibration": 2},
                    winner=winner_label(4, 12),
                    model_id=adapter.model_id(),
                    input_tokens=c.input_tokens,
                    output_tokens=c.output_tokens,
                    latency_ms=c.latency_ms,
                ))
            return out

        def aggregate(self, trials):
            return PerCategoryReport(
                avg=sum(t.axiom_total for t in trials) / max(1, len(trials)),
                n_trials=len(trials),
                gate="PASS",
                extras={"stub_marker": True},
            )

    _FACTORIES[99] = lambda: _MinCat()
    assert isinstance(_MinCat(), Category)


def test_runner_end_to_end_with_stub_adapter(tmp_path):
    _install_minimal_stub_category()
    from axiom_5cat_benchmark.runner import run_benchmark

    log = tmp_path / "trials.jsonl"
    results = run_benchmark(
        adapters=[StubAdapter()],
        category_ids=[99],
        n_trials=3,
        seed=1729,
        temperature=0.0,
        crash_log=log,
    )

    # End-to-end shape:
    assert results.total_tests == 3
    assert results.axiom_wins == 3   # stub category always wins for axiom
    assert results.improvement_pct > 0
    assert "99" in results.per_category

    # Meta is signed and verifies:
    assert verify_attached(results.meta) is True

    # Each trial carries a signature that verifies:
    for t in results.tests:
        d = t.to_dict()
        sig = d.pop("trial_signature")
        assert verify_result(d, sig) is True, f"trial {t.id} signature invalid"

    # Crash log captured every trial:
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    # Top-level JSON round-trips cleanly.
    serialised = json.dumps(results.to_dict())
    reloaded = json.loads(serialised)
    assert reloaded["total_tests"] == 3


def test_runner_refuses_with_no_adapters():
    from axiom_5cat_benchmark.runner import run_benchmark
    with pytest.raises(ValueError):
        run_benchmark(
            adapters=[], category_ids=[1],
            n_trials=1, seed=1, temperature=0.0,
        )


def test_runner_refuses_with_no_categories():
    from axiom_5cat_benchmark.runner import run_benchmark
    with pytest.raises(ValueError):
        run_benchmark(
            adapters=[StubAdapter()], category_ids=[],
            n_trials=1, seed=1, temperature=0.0,
        )
