"""
AXIOM Inference OS — live Observability server
===============================================
The Layer-6 console (axiom_observability_console.py) renders a signed operating
picture, but only offline — you point it at a JSONL file and get a snapshot. This
makes it *live*: a tiny stdlib HTTP server that ingests InferenceOSResult dicts as
requests flow and serves an auto-refreshing console, so the routing / cognition /
economy decisions this session wired in are visible to someone watching in real time.

No web framework — Python's http.server only, so it runs anywhere the repo runs.

Routes:
  GET  /             → the live HTML console (meta-refreshes every ``refresh`` seconds)
  GET  /report.json  → the signed report (same bytes render_html signs over)
  POST /ingest       → body = one InferenceOSResult dict, or a JSON list of them
  GET  /healthz      → "ok"

The request path never calls this — it's a sink. Feed it from the OS with a one-liner:

    con = ObservabilityConsole()
    srv = ConsoleServer(con)
    # in your request loop:
    requests.post("http://host:8799/ingest", json=result.to_dict())

Or run standalone with the demo stream:

    python axiom_observability_server.py --serve --demo --port 8799
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axiom_observability_console import ObservabilityConsole, _demo_rows

_JSON = "application/json; charset=utf-8"
_HTML = "text/html; charset=utf-8"
_TEXT = "text/plain; charset=utf-8"


class ConsoleServer:
    """Wraps an ObservabilityConsole with a pure request dispatcher.

    ``handle(method, path, body)`` returns ``(status, content_type, body_bytes)`` and
    does no socket I/O — so it is unit-testable without binding a port. The HTTP handler
    below is a thin adapter over it.
    """

    def __init__(self, console: Optional[ObservabilityConsole] = None,
                 *, refresh: int = 5) -> None:
        self.console = console or ObservabilityConsole()
        self.refresh = max(0, int(refresh))
        self.ingested = 0

    # ── pure dispatch (unit-tested directly) ────────────────────────────────────
    def handle(self, method: str, path: str, body: bytes = b"") -> Tuple[int, str, bytes]:
        route = path.split("?", 1)[0].rstrip("/") or "/"
        if method == "GET" and route == "/":
            return 200, _HTML, self._live_html().encode("utf-8")
        if method == "GET" and route == "/report.json":
            return 200, _JSON, json.dumps(self.console.report()).encode("utf-8")
        if method == "GET" and route == "/healthz":
            return 200, _TEXT, b"ok"
        if method == "POST" and route == "/ingest":
            return self._ingest(body)
        return 404, _TEXT, b"not found"

    def _ingest(self, body: bytes) -> Tuple[int, str, bytes]:
        try:
            data = json.loads(body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            return 400, _JSON, b'{"error":"invalid json"}'
        rows = data if isinstance(data, list) else [data]
        added = 0
        for r in rows:
            if isinstance(r, dict):
                self.console.record(r)
                added += 1
        self.ingested += added
        return 200, _JSON, json.dumps({"ingested": added, "total": self.ingested}).encode("utf-8")

    def _live_html(self) -> str:
        html = self.console.render_html()
        if self.refresh:
            # inject an auto-refresh so a watcher sees new traffic without reloading
            html = html.replace(
                "<meta charset=utf-8>",
                f"<meta charset=utf-8><meta http-equiv=refresh content={self.refresh}>", 1)
        return html


# ── HTTP adapter ──────────────────────────────────────────────────────────────
def _make_handler(server: ConsoleServer):
    class _Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            status, ctype, out = server.handle(method, self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):  # noqa: N802
            self._respond("GET")

        def do_POST(self):  # noqa: N802
            self._respond("POST")

        def log_message(self, *a):  # keep the console quiet
            pass

    return _Handler


def make_http_server(console_server: ConsoleServer, host: str = "127.0.0.1",
                     port: int = 8799) -> HTTPServer:
    return HTTPServer((host, port), _make_handler(console_server))


# ── CLI ───────────────────────────────────────────────────────────────────────
def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inference OS live observability server")
    p.add_argument("--serve", action="store_true", help="bind and serve (else print URL map)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--refresh", type=int, default=5, help="HTML auto-refresh seconds (0=off)")
    p.add_argument("--results", help="seed from a JSONL of InferenceOSResult dicts")
    p.add_argument("--demo", action="store_true", help="seed the deterministic demo stream")
    args = p.parse_args(argv)

    console = (ObservabilityConsole.from_jsonl(args.results) if args.results
               else ObservabilityConsole())
    if args.demo:
        console.ingest(_demo_rows())
    cs = ConsoleServer(console, refresh=args.refresh)

    if not args.serve:
        print("Inference OS observability server — routes:")
        print("  GET  /            live HTML console")
        print("  GET  /report.json signed report")
        print("  POST /ingest      InferenceOSResult dict (or list)")
        print("  GET  /healthz     ok")
        print(f"\nrun: python {Path(__file__).name} --serve --demo --port {args.port}")
        return 0

    httpd = make_http_server(cs, args.host, args.port)
    print(f"serving live console on http://{args.host}:{args.port}  (ctrl-c to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
