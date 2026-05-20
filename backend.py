"""HTTP backend for cluster-mode deployments.

The dashboard's default mode reads the embedded-mode SQLite store at
``YANTRIKDB_DB_PATH`` directly. That works for single-instance Hermes
plugin deployments but locks out anyone running yantrikdb-server on an
HA cluster — there's no local SQLite file to read.

Setting ``YANTRIKDB_SERVER_URL`` switches the dashboard to HTTP mode:
every supported route proxies to the matching ``/v1/*`` endpoint on the
cluster. Auth is the same Bearer-token scheme used by the plugin's
``client.py``.

Scope (matches yantrikdb-server v0.8.17 Phase 1 — issue #39):

    /v1/health, /v1/stats, /v1/memories, /v1/memory/{rid}

Routes that depend on engine endpoints not yet exposed over HTTP
(entities, graph, lifecycle queries, export, recall, conflict ops,
think, identity-scope writes) raise :class:`NotImplementedHTTPBackend`,
which :mod:`app` turns into HTTP 501 with a "phase 2 pending" note.
Those land as endpoints are added in subsequent server releases.

The fallback path stays clean: when ``YANTRIKDB_SERVER_URL`` is unset
the dashboard runs unchanged, reading SQLite directly.
"""
from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import HTTPException

DEFAULT_TIMEOUT = (3.0, 15.0)  # (connect, read)


class NotImplementedHTTPBackend(Exception):
    """Raised when a route has no v0.8.17 HTTP equivalent yet.

    ``app`` catches this and surfaces it as a 501 with a clear message
    pointing the user at the server-side issue tracking the gap.
    """


class HTTPBackend:
    """Thin proxy to a yantrikdb-server v0.8.17+ cluster.

    No retries, no circuit breaker, no response caching — dashboard
    callers are interactive and see errors immediately. Keep this
    simple; the plugin's ``client.py`` is the place for those features
    when an agent loop needs them.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        # Multiple URLs comma-separated → use the first (HA failover
        # belongs in a load balancer in front of the cluster, not here).
        self.base_url = base_url.split(",")[0].strip().rstrip("/")
        self.token = token.strip()
        self._session = requests.Session()
        if self.token:
            self._session.headers["Authorization"] = f"Bearer {self.token}"

    # ----- HTTP plumbing -----

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.request(
                method,
                url,
                params=params,
                json=json,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise HTTPException(502, f"yantrikdb-server unreachable: {e}") from e
        if resp.status_code == 404:
            # Server route absent — most likely server is on a release
            # older than v0.8.17. Surface that as a clear hint.
            raise HTTPException(
                501,
                f"server route {path} not found "
                "(needs yantrikdb-server v0.8.17+ for Phase 1 dashboard endpoints; "
                "see https://github.com/yantrikos/yantrikdb-server/issues/39)",
            )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error", {}).get("message") or body.get("detail") or resp.text
            except ValueError:
                msg = resp.text
            raise HTTPException(resp.status_code, f"yantrikdb-server: {msg}")
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as e:
            raise HTTPException(502, f"non-JSON response from {path}: {e}") from e

    # ----- Supported routes (v0.8.17 Phase 1) -----

    def health(self) -> dict[str, Any]:
        """Proxy /v1/health and adapt to the dashboard's /api/health shape.

        The server returns cluster/raft info; the dashboard expects a
        flatter shape with db_path/namespaces/version fields. We fill
        the dashboard-shaped fields from what the server exposes and
        leave SQLite-only fields (db_path, db_size_bytes, settings_path)
        as HTTP-mode markers so the UI knows it's in cluster mode.
        """
        body = self._request("GET", "/v1/health")
        cluster = body.get("cluster") or {}
        return {
            "ok": body.get("status") == "ok" or cluster.get("healthy") is True,
            "mode": "http",
            "server_url": self.base_url,
            "db_path": None,
            "db_exists": True,
            "db_size_bytes": 0,
            "yantrikdb_version": body.get("version") or "0.8.17+",
            "cluster": cluster,
            "engines_loaded": body.get("engines_loaded"),
            "namespaces": [],  # populated lazily by /api/stats per namespace
        }

    def stats(self, namespace: str) -> dict[str, Any]:
        """Proxy /v1/stats. Engine block is canonical; aggregations the
        SQLite path computes locally (by_domain, by_source, by_type,
        recent_by_day) come back empty in HTTP mode until the server
        exposes those breakdowns. See issue #39 phase 2.
        """
        body = self._request("GET", "/v1/stats", params={"namespace": namespace})
        return {
            "namespace": namespace,
            "memory_status": [],
            "by_domain": [],
            "by_source": [],
            "by_type": [],
            "recent_by_day": [],
            "open_conflicts": body.get("open_conflicts") or 0,
            "entities": body.get("entities") or 0,
            "edges": body.get("edges") or 0,
            "engine": body,
        }

    def list_memories(
        self,
        *,
        namespace: str,
        status: str = "active",
        domain: str = "",
        source: str = "",
        memory_type: str = "",
        q: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "created_at",
    ) -> dict[str, Any]:
        """Proxy /v1/memories. The v0.8.17 endpoint rejects q/source/
        certain sort variants with structured 400s — we forward those
        unchanged so the UI can surface them.
        """
        params: dict[str, Any] = {
            "namespace": namespace,
            "status": status,
            "limit": limit,
            "offset": offset,
            "sort": sort,
        }
        if domain:
            params["domain"] = domain
        if source:
            params["source"] = source
        if memory_type:
            params["type"] = memory_type
        if q:
            params["q"] = q
        body = self._request("GET", "/v1/memories", params=params)
        return body  # server shape matches dashboard expectations

    def get_memory(self, rid: str) -> dict[str, Any]:
        """Proxy /v1/memory/{rid}. Phase 1 server may return null for
        updated_at / tombstone_reason / embedding_model / embedding_bytes
        and empty arrays for consolidation_sources / entities / claims —
        the UI is expected to handle those nulls.
        """
        return self._request("GET", f"/v1/memory/{rid}")

    # ----- Routes pending server endpoints (issue #39 Phase 2/3) -----

    def recall(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("recall")

    def conflicts(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("conflicts")

    def identity_scope(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("identity-scope")

    def entities(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("entities")

    def graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("graph")

    def sessions(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("sessions")

    def stale(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("stale")

    def upcoming(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("upcoming")

    def patterns(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("patterns")

    def triggers(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("triggers")

    def export_memories(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("export")

    def constellation(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("constellation")

    def think(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("think")

    def forget(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedHTTPBackend("forget")


def make_backend() -> HTTPBackend | None:
    """Return an HTTPBackend if YANTRIKDB_SERVER_URL is set, else None.

    None means "stay in SQLite mode" — the dashboard's existing route
    handlers fall through to the original SQLite codepath.
    """
    url = os.environ.get("YANTRIKDB_SERVER_URL", "").strip()
    if not url:
        return None
    token = os.environ.get("YANTRIKDB_TOKEN", "")
    return HTTPBackend(url, token)


def not_implemented_response(feature: str) -> HTTPException:
    """Build the 501 envelope the dashboard returns for HTTP-mode routes
    that need server endpoints from issue #39 Phase 2/3.
    """
    return HTTPException(
        501,
        f"'{feature}' not available in HTTP backend mode yet — "
        "needs yantrikdb-server endpoint coverage from "
        "https://github.com/yantrikos/yantrikdb-server/issues/39 "
        "(Phase 2: entities + graph; Phase 3: lifecycle + analytics + export)",
    )
