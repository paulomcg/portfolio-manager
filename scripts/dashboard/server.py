"""Local HTTP server for the PM dashboard.

Stdlib-only. ThreadingHTTPServer + a route table mapping URL paths to
JSON-returning handlers in api.py. Static assets (index.html, app.js,
style.css) live in ./static/. SSE endpoint /events streams updates when
audit.jsonl or alerts.jsonl is appended to.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .. import config
from . import api

STATIC_DIR = Path(__file__).resolve().parent / "static"
JSON_TYPE = "application/json"
SSE_TYPE = "text/event-stream"


# ---------------------------------------------------------------------------
# Route registry
# ---------------------------------------------------------------------------


def _route_state(query: dict[str, str]) -> dict[str, Any]:
    return api.get_state(wallet=query.get("wallet"))


def _route_audit(query: dict[str, str]) -> dict[str, Any]:
    limit = int(query.get("limit", 50))
    return api.get_audit(
        limit=limit,
        since=query.get("since"),
        event=query.get("event"),
        wallet=query.get("wallet"),
    )


def _route_alerts(query: dict[str, str]) -> dict[str, Any]:
    return api.get_alerts_pending(
        wallet=query.get("wallet"),
        severity=query.get("severity"),
        limit=int(query.get("limit", 100)),
    )


def _route_equity(query: dict[str, str]) -> dict[str, Any]:
    return api.get_equity(
        wallet=query.get("wallet"),
        since=query.get("since"),
        until=query.get("until"),
    )


def _route_metrics(query: dict[str, str]) -> dict[str, Any]:
    return api.get_metrics(
        wallet=query.get("wallet"),
        since=query.get("since"),
        until=query.get("until"),
    )


def _route_snapshot(query: dict[str, str]) -> dict[str, Any]:
    return api.get_snapshot(
        wallet=query.get("wallet"),
        audit_limit=int(query.get("audit_limit", 30)),
    )


def _route_safety(query: dict[str, str]) -> dict[str, Any]:
    return api.get_safety(wallet=query.get("wallet"))


JSON_ROUTES: dict[str, Callable[[dict[str, str]], dict[str, Any]]] = {
    "/api/state": _route_state,
    "/api/audit": _route_audit,
    "/api/alerts/pending": _route_alerts,
    "/api/equity": _route_equity,
    "/api/metrics": _route_metrics,
    "/api/snapshot": _route_snapshot,
    "/api/safety": _route_safety,
}


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "pm-dashboard/0.2.0"

    # Quiet stdout — log only errors.
    def log_message(self, format, *args):  # noqa: A002
        if int(args[1] if len(args) > 1 else 0) >= 400:
            super().log_message(format, *args)

    # ----- routing -----------------------------------------------------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: (v[0] if v else "") for k, v in parse_qs(parsed.query).items()}

        # JSON routes
        if path in JSON_ROUTES:
            self._serve_json(JSON_ROUTES[path](query))
            return

        # SSE
        if path == "/events":
            self._serve_sse()
            return

        # Static / index
        if path == "/" or path == "":
            self._serve_static(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        # Strip leading slash
        rel = path.lstrip("/")
        candidate = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() in candidate.parents and candidate.is_file():
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self._serve_static(candidate, mime)
            return

        self._serve_error(404, "not found")

    # ----- helpers -----------------------------------------------------

    def _serve_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", JSON_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: Path, mime: str) -> None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self._serve_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_error(self, code: int, message: str) -> None:
        body = json.dumps({"ok": False, "error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", JSON_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", SSE_TYPE)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        audit_path = config.audit_path()
        alerts_path = config.alerts_log_path()
        last_audit_mtime = _safe_mtime(audit_path)
        last_alerts_mtime = _safe_mtime(alerts_path)

        # Initial hello.
        self._sse_write({"event": "hello", "ts": api._now_iso()})
        try:
            while True:
                time.sleep(getattr(self.server, "sse_interval", 1.0))
                m1 = _safe_mtime(audit_path)
                m2 = _safe_mtime(alerts_path)
                if m1 != last_audit_mtime:
                    last_audit_mtime = m1
                    self._sse_write({"event": "cycle", "ts": api._now_iso()})
                if m2 != last_alerts_mtime:
                    last_alerts_mtime = m2
                    self._sse_write({"event": "alert", "ts": api._now_iso()})
                # Heartbeat every ~15s so clients know we're alive.
                self._sse_write({"event": "ping", "ts": api._now_iso()})
        except (BrokenPipeError, ConnectionResetError):
            return

    def _sse_write(self, payload: dict[str, Any]) -> None:
        try:
            data = f"event: {payload.get('event','message')}\n"
            data += f"data: {json.dumps(payload)}\n\n"
            self.wfile.write(data.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            raise


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def make_server(
    host: str = "127.0.0.1",
    port: int = 7777,
    sse_interval: float = 1.0,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.sse_interval = sse_interval  # type: ignore[attr-defined]
    return server


def serve_forever(host: str = "127.0.0.1", port: int = 7777) -> None:
    server = make_server(host, port)
    try:
        print(f"# pm dashboard: http://{host}:{port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("# pm dashboard: shutting down", flush=True)
    finally:
        server.server_close()
