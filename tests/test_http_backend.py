"""Tests for the HTTP backend mode added in v0.2.0.

Two layers:

- ``HTTPBackend`` unit tests: mock the underlying ``requests.Session.request``
  so we verify the wire shape (URL, params, headers, error mapping) without
  touching a live server.
- ``app`` route guard tests: install a stub backend with
  ``monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)`` and hit the FastAPI
  app via ``TestClient`` to confirm the early-return wiring + the SQL-fallthrough
  guard in ``connect()`` both behave correctly.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app as dashboard
from backend import HTTPBackend, NotImplementedHTTPBackend, make_backend


# ----- HTTPBackend unit tests -----


def _fake_response(status_code: int = 200, body: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"x" if status_code != 204 else b""
    if body is not None:
        resp.json.return_value = body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text or (str(body) if body else "")
    return resp


def test_make_backend_returns_none_when_env_unset(monkeypatch):
    monkeypatch.delenv("YANTRIKDB_SERVER_URL", raising=False)
    assert make_backend() is None


def test_make_backend_returns_instance_when_env_set(monkeypatch):
    monkeypatch.setenv("YANTRIKDB_SERVER_URL", "http://cluster:7438")
    monkeypatch.setenv("YANTRIKDB_TOKEN", "ydb_test")
    backend = make_backend()
    assert isinstance(backend, HTTPBackend)
    assert backend.base_url == "http://cluster:7438"
    assert backend._session.headers.get("Authorization") == "Bearer ydb_test"


def test_backend_strips_trailing_slash_and_picks_first_url():
    backend = HTTPBackend("http://a:7438/, http://b:7438/")
    assert backend.base_url == "http://a:7438"


def test_request_404_maps_to_501_with_issue_link(monkeypatch):
    backend = HTTPBackend("http://cluster:7438")
    monkeypatch.setattr(backend._session, "request", lambda *a, **kw: _fake_response(404))
    with pytest.raises(Exception) as exc:
        backend.list_memories(namespace="default")
    assert exc.value.status_code == 501
    assert "issues/39" in exc.value.detail
    assert "/v1/memories" in exc.value.detail


def test_request_other_4xx_surfaces_server_error_message(monkeypatch):
    backend = HTTPBackend("http://cluster:7438")
    body = {"error": {"message": "bad namespace"}}
    monkeypatch.setattr(backend._session, "request", lambda *a, **kw: _fake_response(400, body))
    with pytest.raises(Exception) as exc:
        backend.stats(namespace="default")
    assert exc.value.status_code == 400
    assert "bad namespace" in exc.value.detail


def test_request_connection_error_maps_to_502(monkeypatch):
    import requests as _r
    backend = HTTPBackend("http://cluster:7438")
    def raise_conn(*a, **kw):
        raise _r.ConnectionError("refused")
    monkeypatch.setattr(backend._session, "request", raise_conn)
    with pytest.raises(Exception) as exc:
        backend.health()
    assert exc.value.status_code == 502
    assert "unreachable" in exc.value.detail


def test_health_adapts_cluster_response(monkeypatch):
    backend = HTTPBackend("http://cluster:7438")
    server_body = {
        "status": "ok",
        "cluster": {"healthy": True, "leader": 4, "node_id": 4, "role": "Leader", "term": 27077, "raft_mode": "openraft"},
        "engines_loaded": 7,
    }
    monkeypatch.setattr(backend._session, "request", lambda *a, **kw: _fake_response(200, server_body))
    body = backend.health()
    assert body["ok"] is True
    assert body["mode"] == "http"
    assert body["server_url"] == "http://cluster:7438"
    assert body["cluster"]["role"] == "Leader"
    assert body["engines_loaded"] == 7


def test_list_memories_forwards_filter_params(monkeypatch):
    backend = HTTPBackend("http://cluster:7438")
    captured: dict[str, Any] = {}
    def capture(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _fake_response(200, {"total": 0, "items": []})
    monkeypatch.setattr(backend._session, "request", capture)
    backend.list_memories(
        namespace="default", status="active", domain="work", source="user",
        memory_type="semantic", q="alpha", limit=25, offset=10, sort="importance",
    )
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/v1/memories")
    p = captured["params"]
    assert p["namespace"] == "default"
    assert p["domain"] == "work"
    assert p["source"] == "user"
    assert p["type"] == "semantic"   # memory_type renamed to 'type' on the wire
    assert p["q"] == "alpha"
    assert p["limit"] == 25
    assert p["offset"] == 10
    assert p["sort"] == "importance"


def test_list_memories_omits_empty_filters(monkeypatch):
    backend = HTTPBackend("http://cluster:7438")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(backend._session, "request", lambda m, u, **kw: (captured.update(kw), _fake_response(200, {}))[1])
    backend.list_memories(namespace="default")
    p = captured["params"]
    assert "domain" not in p
    assert "source" not in p
    assert "type" not in p
    assert "q" not in p
    assert p["namespace"] == "default"


def test_phase_2_3_methods_raise_not_implemented():
    backend = HTTPBackend("http://cluster:7438")
    for method in ("recall", "conflicts", "identity_scope", "entities", "graph",
                   "sessions", "stale", "upcoming", "patterns", "triggers",
                   "export_memories", "constellation", "think", "forget"):
        with pytest.raises(NotImplementedHTTPBackend):
            getattr(backend, method)()


# ----- app.py route-guard tests -----


def test_health_route_uses_http_backend_when_set(monkeypatch):
    stub = MagicMock(spec=HTTPBackend)
    stub.health.return_value = {"ok": True, "mode": "http", "server_url": "http://x:7438"}
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)
    client = TestClient(dashboard.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "http"
    assert body["server_url"] == "http://x:7438"
    stub.health.assert_called_once()


def test_stats_route_uses_http_backend_when_set(monkeypatch):
    stub = MagicMock(spec=HTTPBackend)
    stub.stats.return_value = {"namespace": "default", "open_conflicts": 5, "engine": {}}
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)
    client = TestClient(dashboard.app)
    response = client.get("/api/stats?namespace=default")
    assert response.status_code == 200
    assert response.json()["open_conflicts"] == 5
    stub.stats.assert_called_once_with("default")


def test_memories_route_uses_http_backend_when_set(monkeypatch):
    stub = MagicMock(spec=HTTPBackend)
    stub.list_memories.return_value = {"total": 1, "items": [{"rid": "r1"}]}
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)
    client = TestClient(dashboard.app)
    response = client.get("/api/memories?namespace=default&domain=work&limit=5")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    call_kwargs = stub.list_memories.call_args.kwargs
    assert call_kwargs["namespace"] == "default"
    assert call_kwargs["domain"] == "work"
    assert call_kwargs["limit"] == 5


def test_memory_detail_route_uses_http_backend_when_set(monkeypatch):
    stub = MagicMock(spec=HTTPBackend)
    stub.get_memory.return_value = {"rid": "r1", "text": "hello"}
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)
    client = TestClient(dashboard.app)
    response = client.get("/api/memory/r1")
    assert response.status_code == 200
    assert response.json()["rid"] == "r1"
    stub.get_memory.assert_called_once_with("r1")


def test_connect_raises_501_in_http_mode(monkeypatch):
    stub = MagicMock(spec=HTTPBackend)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)
    with pytest.raises(Exception) as exc:
        dashboard.connect()
    assert exc.value.status_code == 501
    assert "issues/39" in exc.value.detail


def test_unwrapped_route_returns_501_via_connect_guard(monkeypatch):
    """Routes we haven't wrapped (e.g. /api/entities) still call connect()/
    rows() internally. The guard in connect() turns SQL fallthrough into a
    clean 501 instead of a confusing 500 in HTTP mode."""
    stub = MagicMock(spec=HTTPBackend)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", stub)
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", True)  # bypass password gate if any
    client = TestClient(dashboard.app)
    for path in ("/api/entities", "/api/patterns", "/api/triggers", "/api/sessions"):
        response = client.get(path)
        assert response.status_code == 501, f"{path} should 501 in HTTP mode"
        assert "issues/39" in response.json()["detail"]


def test_sqlite_mode_still_works_when_http_backend_unset(monkeypatch, tmp_path):
    """Sanity check: with HTTP_BACKEND=None the existing SQLite path runs
    unchanged. This is the no-regression case for default deployments."""
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY, namespace TEXT)")
        conn.execute("INSERT INTO memories VALUES (?, ?)", ("r1", "default"))
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)
    client = TestClient(dashboard.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["db_exists"] is True
    assert body.get("mode") != "http"  # sqlite path doesn't set 'mode'
