"""Prueba la API HTTP local end-to-end (levanta el servidor real en un
thread, hace requests reales), para confirmar que n8n (via HTTP Request
node) puede consumirla sin problemas."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from cxc_bot.api_server import _Handler
from http.server import ThreadingHTTPServer


@pytest.fixture
def running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_health_endpoint(running_server):
    with urllib.request.urlopen(f"{running_server}/health", timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body == {"status": "ok"}


def test_unknown_path_returns_404(running_server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{running_server}/no-existe", timeout=5)
    assert exc_info.value.code == 404


def test_process_due_routes_returns_same_shape_as_cli(running_server, monkeypatch, tmp_path):
    """No reimplementa nada: usa el mismo process_due_routes() que el CLI.
    Aqui solo se confirma que el servidor expone eso por HTTP correctamente,
    con un stub minimo para no depender de rutas reales del proyecto."""
    from cxc_bot import api_server
    from cxc_bot.runner import ProcessRunResult

    def fake_run(overrides):
        return ProcessRunResult(run_id="test-run-123").to_dict()

    monkeypatch.setattr(api_server, "_run_process_due_routes", fake_run)

    req = urllib.request.Request(
        f"{running_server}/process-due-routes",
        data=json.dumps({"today": "2026-10-12"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read())
    assert body["run_id"] == "test-run-123"
    assert set(body.keys()) == {"run_id", "status", "processed", "succeeded", "skipped", "failed", "routes"}


def test_process_due_routes_invalid_json_body_returns_400(running_server):
    req = urllib.request.Request(
        f"{running_server}/process-due-routes",
        data=b"esto no es json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400


def test_process_due_routes_internal_error_returns_500_not_a_crash(running_server, monkeypatch):
    from cxc_bot import api_server

    def failing_run(overrides):
        raise RuntimeError("algo se rompio")

    monkeypatch.setattr(api_server, "_run_process_due_routes", failing_run)

    req = urllib.request.Request(f"{running_server}/process-due-routes", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 500

    # el servidor sigue vivo despues de un error interno
    with urllib.request.urlopen(f"{running_server}/health", timeout=5) as resp:
        assert resp.status == 200
