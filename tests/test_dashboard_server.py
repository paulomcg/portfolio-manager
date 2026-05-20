"""End-to-end-ish tests for the dashboard HTTP server.

Starts make_server() in a daemon thread on a random port + drives requests
via urllib so we exercise the full route table + content types.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

from scripts.dashboard import server as dashboard_server

FIXTURES = Path(__file__).parent / "fixtures"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(url: str, timeout: float = 3.0) -> tuple[int, str, str]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PM_STATE_DIR", str(state))
    monkeypatch.setenv("PM_AUDIT_PATH", str(state / "audit.jsonl"))
    monkeypatch.setenv("PM_SQLITE_PATH", str(state / "positions.sqlite"))
    monkeypatch.setenv("PM_ALERTS_LOG_PATH", str(state / "alerts.jsonl"))
    (state / "audit.jsonl").write_text((FIXTURES / "sample_audit.jsonl").read_text())

    port = _free_port()
    srv = dashboard_server.make_server(host="127.0.0.1", port=port, sse_interval=0.1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)  # let the socket settle
    yield f"http://127.0.0.1:{port}", state
    srv.shutdown()
    srv.server_close()


class TestStaticRoutes:
    def test_index_html_served(self, dashboard):
        base, _ = dashboard
        status, ct, body = _request(f"{base}/")
        assert status == 200
        assert ct.startswith("text/html")
        assert "<title>portfolio-manager" in body

    def test_vite_assets_served(self, dashboard):
        """The Vite-built UI ships hashed bundles under /assets/. We
        scan the served index.html for the asset references and verify
        each comes back 200 with the right content-type. Replaces the
        old test_style_css_served / test_app_js_served pair from the
        pre-React static layout."""
        import re
        base, _ = dashboard
        _, _, idx_body = _request(f"{base}/")
        urls = re.findall(r'/assets/[\w./-]+', idx_body)
        assert urls, "index.html should reference at least one hashed asset bundle"
        for url in set(urls):
            status, ct, _ = _request(f"{base}{url}")
            assert status == 200, f"{url} returned {status}"
            if url.endswith(".css"):
                assert ct == "text/css"
            elif url.endswith(".js"):
                assert ct in ("text/javascript", "application/javascript")

    def test_unknown_path_404(self, dashboard):
        base, _ = dashboard
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"{base}/nope")
        assert ei.value.code == 404


class TestJsonRoutes:
    def test_state(self, dashboard):
        base, _ = dashboard
        status, ct, body = _request(f"{base}/api/state")
        assert status == 200
        assert ct == "application/json"
        payload = json.loads(body)
        assert payload["ok"] is True

    def test_audit(self, dashboard):
        base, _ = dashboard
        status, _, body = _request(f"{base}/api/audit?limit=5")
        payload = json.loads(body)
        assert payload["ok"] is True
        assert len(payload["rows"]) <= 5

    def test_snapshot(self, dashboard):
        base, _ = dashboard
        status, _, body = _request(f"{base}/api/snapshot")
        payload = json.loads(body)
        assert payload["ok"] is True
        assert "state" in payload and "audit" in payload
        assert "alerts_pending" in payload and "metrics" in payload

    def test_equity(self, dashboard):
        base, _ = dashboard
        status, _, body = _request(f"{base}/api/equity")
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["count"] >= 1

    def test_metrics(self, dashboard):
        base, _ = dashboard
        status, _, body = _request(f"{base}/api/metrics")
        payload = json.loads(body)
        assert payload["ok"] is True
        assert "metrics" in payload
