# -*- coding: utf-8 -*-
"""
Live observability server — turns the offline Layer-6 console into an auto-refreshing
dashboard fed by POST /ingest as InferenceOS traffic flows. The dispatcher is pure
(no socket I/O), so these tests exercise it directly.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_obs_server"

from axiom_observability_server import ConsoleServer
from axiom_observability_console import ObservabilityConsole, _demo_rows


def _srv():
    return ConsoleServer(ObservabilityConsole().ingest(_demo_rows()))


class TestDispatch:

    def test_root_serves_live_html(self):
        status, ctype, body = _srv().handle("GET", "/")
        assert status == 200 and "text/html" in ctype
        assert b"Observability Console" in body

    def test_html_injects_auto_refresh(self):
        status, ctype, body = ConsoleServer(refresh=7).handle("GET", "/")
        assert b"http-equiv=refresh content=7" in body

    def test_refresh_zero_omits_meta(self):
        _, _, body = ConsoleServer(refresh=0).handle("GET", "/")
        assert b"http-equiv=refresh" not in body

    def test_report_json_is_signed(self):
        srv = _srv()
        status, ctype, body = srv.handle("GET", "/report.json")
        assert status == 200 and "application/json" in ctype
        rep = json.loads(body)
        assert srv.console.verify(rep) is True
        assert rep["overall"]["requests"] == 7

    def test_healthz(self):
        status, _, body = _srv().handle("GET", "/healthz")
        assert status == 200 and body == b"ok"

    def test_unknown_route_404(self):
        status, _, _ = _srv().handle("GET", "/nope")
        assert status == 404


class TestIngest:

    def test_ingest_single_dict(self):
        srv = ConsoleServer()
        row = _demo_rows()[0]
        status, ctype, body = srv.handle("POST", "/ingest", json.dumps(row).encode())
        assert status == 200
        assert json.loads(body) == {"ingested": 1, "total": 1}
        assert srv.console.report()["overall"]["requests"] == 1

    def test_ingest_list(self):
        srv = ConsoleServer()
        status, _, body = srv.handle("POST", "/ingest", json.dumps(_demo_rows()).encode())
        assert json.loads(body)["ingested"] == 7
        assert srv.console.report()["overall"]["requests"] == 7

    def test_ingest_surfaces_new_signals(self):
        # a live economy request shows up in the served report's tier distribution
        srv = ConsoleServer()
        srv.handle("POST", "/ingest", json.dumps(
            {"route": "local", "total_latency_ms": 100, "route_tier": "economy",
             "cognition": {"action": "REASON_CHEAPLY"}}).encode())
        rep = json.loads(srv.handle("GET", "/report.json")[2])
        assert rep["tier_distribution"]["economy"] == 1
        assert rep["overall"]["economy_rate"] == 1.0
        assert rep["cognition_action_distribution"]["REASON_CHEAPLY"] == 1

    def test_ingest_bad_json_400(self):
        status, _, body = ConsoleServer().handle("POST", "/ingest", b"{not json")
        assert status == 400
        assert b"invalid json" in body

    def test_ingest_ignores_non_dict_items(self):
        srv = ConsoleServer()
        srv.handle("POST", "/ingest", json.dumps([{"route": "local"}, 42, "x"]).encode())
        assert srv.console.report()["overall"]["requests"] == 1


class TestRealOSToLiveServer:

    def test_real_os_result_flows_to_report(self):
        from axiom_inference_os import InferenceOS, InferenceRequest
        ios = InferenceOS(retriever=None, backend=None, audit_ledger=None)
        srv = ConsoleServer()
        r = ios.run(InferenceRequest(query="explain transformers", session_id="s",
                                     tenant_id="t", domain="general"))
        srv.handle("POST", "/ingest", json.dumps(r.to_dict()).encode())
        rep = json.loads(srv.handle("GET", "/report.json")[2])
        assert rep["overall"]["requests"] == 1
        assert "tier_distribution" in rep and "cognition_action_distribution" in rep


def test_cli_prints_routes_without_serving(capsys):
    from axiom_observability_server import _main
    assert _main([]) == 0
    out = capsys.readouterr().out
    assert "/ingest" in out and "/report.json" in out


def test_make_http_server_binds_and_closes():
    # bind on port 0 (ephemeral) to prove the adapter wires up, then close immediately
    from axiom_observability_server import make_http_server
    httpd = make_http_server(ConsoleServer(), "127.0.0.1", 0)
    try:
        assert httpd.server_address[1] > 0
    finally:
        httpd.server_close()
