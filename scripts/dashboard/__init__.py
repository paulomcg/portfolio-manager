"""Local web dashboard for the portfolio-manager.

`pm dashboard [--port 7777] [--host 127.0.0.1]` starts an HTTP server
that serves a single-page web UI for observability — positions, recent
audit rows, pending alerts, equity curve. Read-only by design.

Stdlib-only: uses http.server.ThreadingHTTPServer for the transport. The
heavy lifting (computing snapshots, reading audit) lives in
scripts.dashboard.api; the server is a thin dispatcher.
"""
