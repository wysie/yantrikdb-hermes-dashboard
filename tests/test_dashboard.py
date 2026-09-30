from pathlib import Path
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as dashboard


def test_index_serves_static_html():
    client = TestClient(dashboard.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "YantrikDB for Hermes" in response.text
    assert "brandHome" in response.text


def test_admin_requires_admin_mode_when_disabled(monkeypatch):
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", False)
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda: {})
    with pytest.raises(dashboard.HTTPException) as exc:
        dashboard.require_admin(None)
    assert exc.value.status_code == 403
    assert "Admin mode is disabled" in exc.value.detail


def test_admin_accepts_env_admin_mode(monkeypatch):
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", True)
    dashboard.require_admin(None)


def test_admin_accepts_stored_admin_mode(monkeypatch):
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", False)
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda: {"admin_mode": True})
    dashboard.require_admin(None)


def test_infer_embedding_dim_from_sqlite_blob(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (embedding BLOB)")
        conn.execute("INSERT INTO memories VALUES (?)", (b"0" * (512 * 4),))

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.delenv("YANTRIKDB_EMBEDDING_DIM", raising=False)
    assert dashboard.infer_embedding_dim() == 512


def test_static_assets_exist():
    assert (Path(dashboard.STATIC_DIR) / "index.html").exists()
    assert (Path(dashboard.STATIC_DIR) / "app.js").exists()
    assert (Path(dashboard.STATIC_DIR) / "styles.css").exists()
    assert (Path(dashboard.STATIC_DIR) / "assets" / "favicon.svg").exists()


def test_three_visualiser_css_has_bounded_viewport():
    css = (Path(dashboard.STATIC_DIR) / "styles.css").read_text()
    assert ".three-viewport" in css
    assert "height:650px" in css
    assert "min-height:650px" in css
    assert ".three-viewport canvas" in css
    assert "position:absolute" in css
    assert "height:100%" in css


def test_memory_city_visualiser_contract():
    html = (Path(dashboard.STATIC_DIR) / "index.html").read_text()
    js = (Path(dashboard.STATIC_DIR) / "app.js").read_text()
    css = (Path(dashboard.STATIC_DIR) / "styles.css").read_text()

    assert 'data-three-mode="city">Memory City</button>' not in html
    assert "Memory City inspector" in js
    assert "buildThreeCityPositions" in js
    assert "addMemoryCity" in js
    assert "District landmark" in js
    assert "Memory building" in js
    assert "layout_district" in js
    assert "healthStateForNode" in js
    assert "threeRunRecall" in html
    assert "visualiser-recall-card" in html
    assert "Highlight recall results" in html
    assert "Search memory, inject hits" not in html
    assert 'class="pill good">Recall replay' not in html
    assert "three-replay-status" not in html
    assert "threeRunRecall')?.addEventListener('click',runVisualiserRecall" in js
    assert "runVisualiserRecall" in js
    assert "addReplayBeacons" in js
    assert "replayStateForNode(a) === 'included'" in js
    assert "threeCameraState" in js
    assert "applyThreeCameraState(viewState)" in js
    assert "renderThreeVisualiser(buildRecallOverlayData(baseData, results), {preserveView:true, viewState})" in js
    assert "X-ray district" in js
    assert "dataset.cityStyle" in js
    assert "bottomPad" in js
    assert "new THREE.MeshStandardMaterial" not in js
    assert "new THREE.PointLight(baseColor" not in js
    assert "new THREE.InstancedMesh(boxGeom" in js
    assert "wireframe:true" in js
    assert "outlineLimit = xray ? 72 : 54" in js
    assert "cityFrameMs = 1000 / (threeVis.drag ? 30 : 20)" in js
    assert "cityCap = fullscreen ? 1.8 : (mobile ? 1.35 : 1.45)" in js
    assert 'data-three-mode=city' in css
    init_py = (Path(__file__).resolve().parent.parent / "__init__.py").read_text()
    assert "def register(ctx)" in init_py
    assert "intentionally no-op" in init_py


def test_password_gate_login_and_cookie_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", False)
    client = TestClient(dashboard.app)

    response = client.post("/api/settings", json={"admin_mode": False, "new_password": "old-pass"})
    assert response.status_code == 200
    assert "yantrikdb_dashboard_session" in response.headers.get("set-cookie", "")
    assert client.get("/api/health").status_code == 401

    assert client.post("/api/auth/login", json={"password": "old-pass"}).status_code == 200
    assert client.get("/api/settings").status_code == 200

    response = client.post("/api/settings", json={"admin_mode": False, "new_password": "new-pass"})
    assert response.status_code == 200
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/auth/login", json={"password": "old-pass"}).status_code == 403
    assert client.post("/api/auth/login", json={"password": "new-pass"}).status_code == 200


def test_disabling_password_clears_cookie_and_opens_api(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    client = TestClient(dashboard.app)
    assert client.post("/api/settings", json={"admin_mode": False, "new_password": "pass"}).status_code == 200
    assert client.post("/api/auth/login", json={"password": "pass"}).status_code == 200
    response = client.post("/api/settings", json={"admin_mode": False, "disable_password": True})
    assert response.status_code == 200
    assert "yantrikdb_dashboard_session" in response.headers.get("set-cookie", "")
    assert client.get("/api/settings").status_code == 200


def test_settings_exposes_and_updates_yantrikdb_runtime_config(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    config_path = tmp_path / "yantrikdb.json"
    config_path.write_text(json.dumps({
        "mode": "embedded",
        "namespace": "hermes",
        "top_k": "10",
        "owner_scoping": True,
        "include_base_namespace_recall": True,
        "include_legacy_actor_namespace_recall": True,
        "identity_map_path": str(tmp_path / "identity-map.json"),
    }))
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", False)
    client = TestClient(dashboard.app)

    response = client.get("/api/settings")
    assert response.status_code == 200
    ycfg = response.json()["yantrikdb"]
    assert ycfg["owner_scoping"] is True
    assert ycfg["default_namespace"] == "hermes:hermes:default"

    response = client.post("/api/settings", json={
        "admin_mode": True,
        "owner_scoping": False,
        "include_base_namespace_recall": False,
        "include_legacy_actor_namespace_recall": True,
        "top_k": 7,
    })
    assert response.status_code == 200
    saved = json.loads(config_path.read_text())
    assert saved["owner_scoping"] is False
    assert saved["include_base_namespace_recall"] is False
    assert saved["include_legacy_actor_namespace_recall"] is True
    assert saved["top_k"] == 7


def test_settings_exposes_and_updates_v06_self_tuning_config(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    config_path = tmp_path / "yantrikdb.json"
    config_path.write_text(json.dumps({
        "self_tuning_recall": True,
        "self_tuning_max_boost": 0.2,
        "surface_hygiene": False,
        "hygiene_max_surfaced": 4,
    }))
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", False)
    client = TestClient(dashboard.app)

    response = client.get("/api/settings")
    assert response.status_code == 200
    ycfg = response.json()["yantrikdb"]
    assert ycfg["self_tuning_recall"] is True
    assert ycfg["self_tuning_max_boost"] == 0.2
    assert ycfg["surface_hygiene"] is False
    assert ycfg["hygiene_max_surfaced"] == 4

    response = client.post("/api/settings", json={
        "admin_mode": True,
        "self_tuning_recall": False,
        "self_tuning_max_boost": 0.12,
        "surface_hygiene": True,
        "hygiene_max_surfaced": 9,
    })
    assert response.status_code == 200
    saved = json.loads(config_path.read_text())
    assert saved["self_tuning_recall"] is False
    assert saved["self_tuning_max_boost"] == 0.12
    assert saved["surface_hygiene"] is True
    assert saved["hygiene_max_surfaced"] == 9


def test_recall_feedback_api_summarizes_v06_ledger(tmp_path, monkeypatch):
    feedback_path = tmp_path / "yantrikdb-recall-feedback.json"
    feedback_path.write_text(json.dumps({
        "rid-useful": {"surfaced": 5, "reinforced": 2, "last_ts": 1000},
        "rid-noisy": {"surfaced": 7, "reinforced": 0, "last_ts": 900},
    }))
    monkeypatch.setattr(dashboard, "RECALL_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(dashboard, "now", lambda: 1200)

    payload = dashboard.recall_feedback(limit=10)

    assert payload["summary"] == {"tracked": 2, "surfaced": 12, "reinforced": 2, "low_usefulness": 1}
    assert payload["items"][0]["rid"] == "rid-noisy"
    assert payload["items"][0]["low_usefulness"] is True
    assert payload["items"][0]["age_seconds"] == 300


def test_hygiene_scan_and_apply_surface_v06_cleanup(monkeypatch):
    class FakeEngine:
        def __init__(self):
            self.calls = []
        def stats(self, namespace=None):
            self.calls.append(("stats", namespace))
            return {"active_memories": 3, "consolidated_memories": 1, "tombstoned_memories": 2}
        def get_conflicts(self, namespace=None, status=None, limit=50):
            self.calls.append(("conflicts", namespace, status, limit))
            return [{"conflict_id": "c1", "priority": "high"}]
        def think(self, config=None):
            self.calls.append(("think", config))
            return {"consolidated": 1}
        def forget(self, rid):
            self.calls.append(("forget", rid))
            return rid == "rid-stale"

    fake = FakeEngine()
    monkeypatch.setattr(dashboard, "engine", lambda: fake)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", True)
    monkeypatch.setattr(dashboard, "recall_feedback_payload", lambda limit=25: {"items": [{"rid": "rid-stale", "low_usefulness": True}]})
    client = TestClient(dashboard.app)

    scan = client.get("/api/hygiene?namespace=ns:test").json()
    assert scan["summary"]["open_conflicts"] == 1
    assert scan["summary"]["low_usefulness"] == 1
    assert scan["engine"]["active_memories"] == 3

    applied = client.post("/api/hygiene", json={"namespace": "ns:test", "consolidate": True, "forget_rids": ["rid-stale", "rid-missing"]}).json()
    assert applied["consolidation"] == {"consolidated": 1}
    assert applied["forgotten"] == [{"rid": "rid-stale", "found": True}, {"rid": "rid-missing", "found": False}]
    assert ("forget", "rid-stale") in fake.calls


def test_hygiene_scan_all_namespaces_uses_sql_status_counts(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY, consolidation_status TEXT, namespace TEXT, domain TEXT, source TEXT, type TEXT, created_at REAL)")
        conn.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?,?)", [
            ("r1", "active", "ns:a", "general", "user", "semantic", 1),
            ("r2", "active", "ns:b", "general", "user", "semantic", 2),
            ("r3", "consolidated", "ns:b", "general", "user", "semantic", 3),
            ("r4", "tombstoned", "ns:c", "general", "user", "semantic", 4),
        ])
        conn.execute("CREATE TABLE conflicts (id TEXT, conflict_id TEXT, status TEXT)")
        conn.execute("INSERT INTO conflicts VALUES ('c1','c1','open')")
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)
    monkeypatch.setattr(dashboard, "recall_feedback_payload", lambda limit=50: {"items": [], "summary": {}})

    scan = dashboard.hygiene_scan(namespace="__all__")

    assert scan["summary"]["active_memories"] == 2
    assert scan["summary"]["consolidated_memories"] == 1
    assert scan["summary"]["tombstoned_memories"] == 1
    assert scan["summary"]["open_conflicts"] == 1
    assert scan["engine"]["scope"] == "all_namespaces_sql"


def test_memories_all_namespaces_sql_filter(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE memories (
                rid TEXT PRIMARY KEY, type TEXT, text TEXT, created_at REAL, updated_at REAL,
                importance REAL, half_life REAL, last_access REAL, access_count INTEGER,
                valence REAL, consolidated_into TEXT, consolidation_status TEXT,
                storage_tier TEXT, metadata TEXT, namespace TEXT, certainty REAL,
                domain TEXT, source TEXT, emotional_state TEXT, session_id TEXT,
                due_at REAL, temporal_kind TEXT, tombstone_reason TEXT,
                embedding_model TEXT, embedding BLOB
            )
        """)
        rows = [
            ("r1", "semantic", "alpha", 2, 2, .8, None, 2, 0, None, None, "active", None, "{}", "ns:a", .8, "general", "user", None, None, None, None, None, None, None),
            ("r2", "semantic", "beta", 1, 1, .5, None, 1, 0, None, None, "active", None, "{}", "ns:b", .5, "general", "user", None, None, None, None, None, None, None),
        ]
        conn.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    result = dashboard.memories(namespace="__all__", status="active", limit=10, offset=0)
    assert result["total"] == 2
    assert {item["namespace"] for item in result["items"]} == {"ns:a", "ns:b"}


def test_constellation_all_namespaces_builds_scope_hubs(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE memories (
                rid TEXT PRIMARY KEY, text TEXT, domain TEXT, source TEXT, type TEXT,
                importance REAL, created_at REAL, updated_at REAL, access_count INTEGER,
                consolidation_status TEXT, namespace TEXT
            )
        """)
        conn.executemany(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("rid-alpha-0001", "Alpha project uses YantrikDB visualiser", "work", "user", "semantic", .9, 3, 3, 0, "active", "ns:a"),
                ("rid-beta-0002", "Beta household memory graph", "home", "assistant", "semantic", .8, 2, 2, 0, "active", "ns:b"),
            ],
        )

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    result = dashboard.constellation(namespace="__all__", limit=40)
    labels = {node["label"] for node in result["nodes"]}
    categories = {node["category"] for node in result["nodes"]}
    assert result["all_namespaces"] is True
    assert "a" in labels
    assert "b" in labels
    assert categories >= {"a", "b"}
    assert any(edge["kind"] == "scope" for edge in result["edges"])
    assert {cluster["label"] for cluster in result["clusters"]} >= {"a", "b"}


def test_constellation_all_namespaces_does_not_merge_same_label_across_scopes(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE memories (
                rid TEXT PRIMARY KEY, text TEXT, domain TEXT, source TEXT, type TEXT,
                importance REAL, created_at REAL, updated_at REAL, access_count INTEGER,
                consolidation_status TEXT, namespace TEXT
            )
        """)
        conn.executemany(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("rid-alpha-0001", "Shared Topic appears here", "shared", "user", "semantic", .9, 3, 3, 0, "active", "ns:a"),
                ("rid-beta-0002", "Shared Topic appears there", "shared", "user", "semantic", .8, 2, 2, 0, "active", "ns:b"),
            ],
        )

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    result = dashboard.constellation(namespace="__all__", limit=80)
    shared_nodes = [node for node in result["nodes"] if node["label"] == "shared"]
    assert len(shared_nodes) == 2
    assert {node["namespace"] for node in shared_nodes} == {"ns:a", "ns:b"}


def test_constellation_all_namespaces_balances_scope_sampling(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE memories (
                rid TEXT PRIMARY KEY, text TEXT, domain TEXT, source TEXT, type TEXT,
                importance REAL, created_at REAL, updated_at REAL, access_count INTEGER,
                consolidation_status TEXT, namespace TEXT
            )
        """)
        rows = []
        for i in range(70):
            rows.append((f"big-{i:03d}", f"Big namespace memory {i}", "shared", "user", "semantic", 1.0, 1000 - i, 1000 - i, 0, "active", "ns:big"))
        for i in range(5):
            rows.append((f"small-{i:03d}", f"Small namespace memory {i}", "shared", "user", "semantic", .2, 10 - i, 10 - i, 0, "active", "ns:small"))
        conn.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    result = dashboard.constellation(namespace="__all__", limit=40)
    namespaces = {node["namespace"] for node in result["nodes"] if node.get("namespace")}
    assert {"ns:big", "ns:small"} <= namespaces
    assert any(edge.get("item", {}).get("namespace") == "ns:small" for edge in result["edges"])


def test_constellation_payload_includes_layout_and_health_hints(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE memories (
                rid TEXT PRIMARY KEY, text TEXT, domain TEXT, source TEXT, type TEXT,
                importance REAL, created_at REAL, updated_at REAL, access_count INTEGER,
                consolidation_status TEXT, namespace TEXT
            )
        """)
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("rid-hot-0001", "Important YantrikDB dashboard memory", "work", "user", "semantic", .95, 10, 10, 9, "active", "ns:a"),
        )

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    result = dashboard.constellation(namespace="ns:a", limit=40)
    memory_nodes = [node for node in result["nodes"] if node["kind"] == "memory"]
    assert memory_nodes
    node = memory_nodes[0]
    assert node["layout_region"] == node["semantic_category"]
    assert node["layout_district"] == node["semantic_category"]
    assert node["health"]["state"] in {"busy", "important"}
    assert "high-recall" in node["health"]["tags"]


def test_index_has_memory_namespace_filter_and_maintenance_label():
    html = (Path(dashboard.STATIC_DIR) / "index.html").read_text()
    assert "memoryNamespaceFilter" in html
    assert "Maintenance" in html
    assert "think()</button>" not in html


def test_index_and_javascript_expose_v06_hygiene_controls():
    html = (Path(dashboard.STATIC_DIR) / "index.html").read_text()
    js = (Path(dashboard.STATIC_DIR) / "app.js").read_text()
    assert "selfTuningRecallToggle" in html
    assert "surfaceHygieneToggle" in html
    assert "recallFeedbackCards" in html
    assert "/api/recall-feedback" in js
    assert "/api/hygiene" in js
    assert "runHygieneScan" in js


def test_identity_scope_api_returns_config_and_unmapped_namespaces(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY, namespace TEXT)")
        conn.executemany("INSERT INTO memories VALUES (?,?)", [("r1", "owner:person-alpha"), ("r2", "space:team-alpha")])
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", tmp_path / "missing-yantrikdb.json")
    dashboard.save_dashboard_settings({
        "identity_scope": {
            "identities": [{"id": "person-alpha", "label": "Person Alpha", "private_scope": "owner:person-alpha"}],
            "actors": [{"platform": "chat", "actor_id": "actor-alpha", "identity": "person-alpha"}],
            "spaces": [{"id": "team-alpha", "label": "Team Alpha", "scope": "space:team-alpha", "members": ["person-alpha"]}],
            "conversations": [{"platform": "chat", "conversation_id": "room-alpha", "scope": "space:team-alpha"}],
        }
    })

    client = TestClient(dashboard.app)
    response = client.get("/api/identity-scope")

    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == {"identities": 1, "actors": 1, "spaces": 1, "conversations": 1, "unmapped_namespaces": 0}
    assert data["identity_scope"]["spaces"][0]["scope"] == "space:team-alpha"
    assert data["namespace_inventory"][0] | {"mapped_to": data["namespace_inventory"][0]["mapped_to"]} == data["namespace_inventory"][0]
    assert data["namespace_inventory"][0]["namespace"] == "owner:person-alpha"
    assert data["namespace_inventory"][0]["mapped"] is True
    assert data["namespace_inventory"][0]["mapped_to"] == "Person Alpha"
    assert data["namespace_inventory"][0]["mapping_type"] == "identity"
    assert data["namespace_inventory"][1]["namespace"] == "space:team-alpha"
    assert data["namespace_inventory"][1]["mapped"] is True
    assert data["namespace_inventory"][1]["mapped_to"] == "Team Alpha"
    assert data["namespace_inventory"][1]["mapping_type"] == "shared_scope"


def test_identity_scope_marks_config_covered_namespaces(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (namespace TEXT)")
        conn.executemany("INSERT INTO memories(namespace) VALUES (?)", [
            ("hermes:hermes:default",),
            ("hermes:hermes:default:owner:whatsapp-6590264641-d4754bd8c823",),
        ])

    identity_map_path = tmp_path / "identity-map.json"
    identity_map_path.write_text(json.dumps({
        "owners": {
            "owner:yc": {"actors": ["whatsapp:6590264641"]}
        }
    }))
    config_path = tmp_path / "yantrikdb.json"
    config_path.write_text(json.dumps({
        "mode": "embedded",
        "namespace": "hermes",
        "top_k": "10",
        "owner_scoping": True,
        "include_base_namespace_recall": True,
        "include_legacy_actor_namespace_recall": True,
        "identity_map_path": str(identity_map_path),
    }))
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")

    payload = dashboard.identity_scope_payload()
    by_ns = {item["namespace"]: item for item in payload["namespace_inventory"]}
    assert by_ns["hermes:hermes:default"]["mapped_to"] == "Shared by all profiles"
    assert by_ns["hermes:hermes:default"]["mapping_type"] == "shared_fallback"
    assert by_ns["hermes:hermes:default"]["derived_by_config"] is True
    legacy = by_ns["hermes:hermes:default:owner:whatsapp-6590264641-d4754bd8c823"]
    assert legacy["mapped_to"] == "Yc via old account bucket"
    assert legacy["mapping_type"] == "legacy_actor_fallback"
    assert payload["runtime_scope"]["owner_scoping"] is True


def test_identity_scope_api_persists_config_when_admin_enabled(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (namespace TEXT)")
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", tmp_path / "missing-yantrikdb.json")
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", True)
    client = TestClient(dashboard.app)
    payload = {
        "identity_scope": {
            "identities": [{"id": "person-beta", "label": "Person Beta", "private_scope": "owner:person-beta"}],
            "actors": [],
            "spaces": [],
            "conversations": [],
        }
    }

    response = client.post("/api/identity-scope", json=payload)

    assert response.status_code == 200
    assert dashboard.load_dashboard_settings()["identity_scope"]["identities"][0]["id"] == "person-beta"


def test_index_has_identity_scope_page_contract():
    html = (Path(dashboard.STATIC_DIR) / "index.html").read_text()
    js = (Path(dashboard.STATIC_DIR) / "app.js").read_text()
    assert 'data-view="identity-scope">Identity &amp; Scope</button>' in html
    assert 'data-view="ops">Maintenance</button>\n      <button class="nav-item" data-view="identity-scope"' in html
    assert 'id="view-identity-scope"' in html
    assert 'id="identityScopeSummary"' in html
    assert 'id="identityScopingStatus"' in html
    assert 'id="ownerScopingToggle"' in html
    assert 'id="includeBaseRecallToggle"' in html
    assert 'id="includeActorRecallToggle"' in html
    assert 'id="saveMemoryScoping"' in html
    assert "top_k" in html
    assert "include_legacy_actor_namespace_recall" in html
    assert "include_base_namespace_recall" in html
    assert "owner_scoping" in html
    assert 'id="identityScopeJson"' in html
    assert 'id="identityForm"' in html
    assert 'id="actorForm"' in html
    assert "Add actor mapping manually" in html
    assert 'id="actorIdentityFilter"' in html
    assert 'id="spaceForm"' in html
    assert 'id="spaceMembersChecklist"' in html
    assert 'id="conversationForm"' in html
    assert '<select id="conversationPlatform"' in html
    assert 'id="conversationIdOptions"' in html
    assert '<h2>Actors</h2><span class="muted">platform accounts</span>' in html
    assert "Create identities to group platform accounts" in html
    assert "Shared spaces" in html
    assert "Chat routing" in html
    assert "who each memory bucket belongs to" in html
    assert "Edit person" in js
    assert "Technical details" in js
    assert "Storage namespace" in js
    assert "actorIdentityFilterOptions" in js
    assert "filteredActors" in js
    assert "inline-identity-select" in js
    assert "data-save-actor-identity" in js
    assert "Identity" in js
    assert "Unassigned" in js
    assert "memory bucket discovery" in js
    assert "checkbox-pill-row" in js
    assert "renderSpaceMemberChecklist" in js
    assert "selectedSpaceMembers" in js
    assert "availablePlatformOptions" in js
    assert "Chat route saved" in js
    assert "Shared space added" in js
    assert "identity-scope" in js
    assert "/api/identity-scope" in js
    assert "addIdentityFromForm" in js
    assert "addActorFromForm" in js
    assert "saveInlineActorIdentity" in js
    assert "addSpaceFromForm" in js
    assert "addConversationFromForm" in js


def test_identity_scope_api_imports_yantrikdb_identity_map(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY, namespace TEXT)")
        conn.executemany("INSERT INTO memories VALUES (?,?)", [("r1", "owner:person-alpha"), ("r2", "hermes:hermes:default:owner:person-alpha")])
    identity_map = tmp_path / "identity-map.json"
    identity_map.write_text('{"owners":{"owner:person-alpha":{"actors":["chat:actor-alpha","telegram:actor-alpha"]}}}')
    config = tmp_path / "yantrikdb.json"
    config.write_text('{"identity_map_path":"' + str(identity_map) + '"}')
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", config)

    data = TestClient(dashboard.app).get("/api/identity-scope").json()

    assert data["summary"]["identities"] == 1
    assert data["summary"]["actors"] == 2
    assert data["identity_scope"]["identities"][0]["id"] == "person-alpha"
    assert {a["platform"] for a in data["identity_scope"]["actors"]} == {"chat", "telegram"}
    assert all(item["mapped"] for item in data["namespace_inventory"])


def test_identity_scope_api_detects_unassigned_actor_from_namespace(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "yantrikdb.db"
    namespace = "hermes:hermes:default:owner:whatsapp-123456789-lid-abcdef123456"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY, namespace TEXT)")
        conn.execute("INSERT INTO memories VALUES (?,?)", ("r1", namespace))
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", tmp_path / "missing-yantrikdb.json")
    monkeypatch.setattr(dashboard, "WHATSAPP_SESSION_DIR", tmp_path / "wa-session")

    data = TestClient(dashboard.app).get("/api/identity-scope").json()

    actor = data["identity_scope"]["actors"][0]
    assert actor["platform"] == "whatsapp"
    assert actor["actor_id"] == "123456789@lid"
    assert actor["identity"] == ""
    assert actor["source"] == "namespace_inventory"
    assert data["namespace_inventory"][0]["mapped"] is False


def test_identity_scope_dashboard_edits_override_imported_identity_map(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY, namespace TEXT)")
    identity_map = tmp_path / "identity-map.json"
    identity_map.write_text('{"owners":{"owner:person-alpha":{"actors":["chat:actor-alpha"]}}}')
    config = tmp_path / "yantrikdb.json"
    config.write_text('{"identity_map_path":"' + str(identity_map) + '"}')
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(dashboard, "YANTRIKDB_CONFIG_PATH", config)
    dashboard.save_dashboard_settings({"identity_scope": {"identities": [
        {"id": "person-alpha", "label": "Person Alpha Edited", "private_scope": "owner:person-alpha"}
    ], "actors": [], "spaces": [], "conversations": []}})

    data = TestClient(dashboard.app).get("/api/identity-scope").json()

    identity = data["identity_scope"]["identities"][0]
    assert identity["label"] == "Person Alpha Edited"
    assert identity["resolved_scope"].startswith("owner:owner-person-alpha-")
    assert identity["source"] == "dashboard"


def test_visualiser_legend_colours_match_runtime_themes():
    css = Path("static/styles.css").read_text()
    assert "--legend-entity:#ffd6dd" in css
    assert "--legend-memory:orange" in css
    assert "--legend-link:#c6e0ff" in css
    assert "--legend-entity:#66e8c6" in css
    assert "--legend-memory:#ff9b6a" in css
    assert "--legend-link:#52d6b5" in css
    assert "var(--legend-entity)" in css
    assert "var(--legend-memory)" in css
    assert "var(--legend-link)" in css


def test_memory_scoping_controls_auto_save_on_change():
    js = Path("static/app.js").read_text()
    assert "saveMemoryScoping" in js
    assert "saveMemoryScopingSettings({silent:true})" in js
    assert "#ownerScopingToggle" in js
    assert "#includeBaseRecallToggle" in js
    assert "#includeActorRecallToggle" in js
    assert "#topKSetting" in js


def test_sidebar_credit_links_are_present():
    html = Path("static/index.html").read_text()
    assert "Built by" in html
    assert "YantrikDB" in html
    assert "https://github.com/wysie" in html
    assert "https://github.com/spranab" in html
    assert "https://github.com/yantrikos/y" in html
    assert html.count('rel="noopener noreferrer"') >= 3


def test_sidebar_admin_status_is_compact_and_hidden_in_mobile_header():
    html = Path("static/index.html").read_text()
    css_source = Path("src/styles.css").read_text()
    js = Path("static/app.js").read_text()
    assert 'class="admin-status" aria-label="Admin mode status"' in html
    assert "Admin mode disabled" in html
    assert "Toggle writes from Settings." not in html
    assert "<div class=\"label\">Mode</div>" not in html
    assert ".admin-status .pill" in css_source
    assert ".side-card, .admin-status, .sidebar-credit { display: none; }" in css_source
    assert "Admin mode enabled" in js
    assert "Admin mode disabled" in js
    assert "on?'Admin mode enabled':'Admin mode disabled'" in js


def test_identity_scope_status_chips_have_layout_styles():
    css_source = Path("src/styles.css").read_text()
    assert ".scope-status-row" in css_source
    assert ".scope-status" in css_source
    assert "flex-col" in css_source
    assert "text-ellipsis" in css_source
    js = Path("static/app.js").read_text()
    assert "scope-status" in js
    assert "title=\"${esc(key)}\"" in js


def test_memory_status_active_uses_good_not_hot_pill():
    js = Path("static/app.js").read_text()
    assert "function statusPillClass" in js
    assert "v==='active' ? 'good'" in js
    assert "<span class=\"pill ${statusPillClass(m.consolidation_status)}\">" in js
    assert "<span class=\"pill hot\">${esc(statusLabel(m.consolidation_status))}" not in js


def test_memory_detail_actions_use_neutral_copy_and_destructive_forget():
    js = Path("static/app.js").read_text()
    css_source = Path("src/styles.css").read_text()
    assert '<button class="drawer-action" onclick="showSelectableCopy(\'RID\',\'${rid}\')">Copy RID</button>' in js
    assert '<button class="drawer-action danger" onclick="forgetSelected(\'${rid}\')">Forget memory</button>' in js
    assert ".drawer-action.danger" in css_source
    assert "border-[rgba(239,68,68,.45)]" in css_source
    assert "drawer-action primary" not in js
    assert "drawer-action warn" not in js


def test_copyable_diag_values_are_right_aligned():
    css_source = Path("src/styles.css").read_text()
    assert ".diag-row strong { @apply break-words text-right text-zinc-100; }" in css_source
    assert ".diag-link { @apply inline-block max-w-full break-words text-right text-yan-pink" in css_source


def test_identity_scope_forms_have_accessible_field_names():
    html = Path("static/index.html").read_text()
    js = Path("static/app.js").read_text()
    for label in [
        "Person short ID",
        "Person display name",
        "Advanced owner ID",
        "Actor platform",
        "Actor ID",
        "Shared space ID",
        "Shared space label",
        "Advanced shared-space storage ID",
        "Chat ID",
        "Recall result count",
    ]:
        assert f'aria-label="{label}"' in html
    assert 'aria-label="Shared space member ${esc(label)}"' in js


def test_live_audit_contrast_and_glow_are_quieter():
    css = Path("src/styles.css").read_text()
    tailwind = Path("tailwind.config.js").read_text()
    assert "text-zinc-500" not in css
    assert "text-zinc-600" not in css
    assert "glow: '0 12px 34px rgba(0, 0, 0, .22)'" in tailwind
    assert "0 0 34px rgba(233, 69, 96" not in tailwind
    assert "shadow-[0_0_24px_rgba(52,211,153" not in css
    assert "box-shadow: none;" in css


def test_readme_documents_hermes_plugin_install_and_no_admin_token():
    readme = Path("README.md").read_text()
    assert "# YantrikDB for Hermes Dashboard" in readme
    assert "hermes plugins install wysie/yantrikdb-hermes-dashboard --enable" in readme
    assert "hermes plugins update yantrikdb-hermes-dashboard" in readme
    assert "There is no admin token to configure" in readme
    assert "Admin Mode" in readme
    assert "Dashboard password" in readme or "dashboard password" in readme


def test_product_metadata_uses_hermes_positioning():
    assert "YantrikDB for Hermes" in Path("static/index.html").read_text()
    assert "YantrikDB for Hermes" in Path("app.py").read_text()
    plugin = Path("plugin.yaml").read_text()
    assert "Hermes Agent memory operations" in plugin
    assert "author: wysie" in plugin


def test_renamed_repo_metadata_uses_new_slug():
    assert "name: yantrikdb-hermes-dashboard" in Path("plugin.yaml").read_text()
    assert 'name = "yantrikdb-hermes-dashboard"' in Path("pyproject.toml").read_text()
    readme = Path("README.md").read_text()
    assert "wysie/yantrikdb-hermes-dashboard" in readme
    assert "~/.hermes/plugins/yantrikdb-hermes-dashboard" in readme
    assert "~/.hermes/plugin-data/yantrikdb-hermes-dashboard/settings.json" in readme
    assert "LEGACY_SETTINGS_PATH" in Path("app.py").read_text()


def test_three_visualiser_inspector_actions_use_button_styles():
    js = Path("static/app.js").read_text()
    assert "const memoryId = overlay ? 'threeOverlayMemory' : 'threeMemory';" in js
    assert "class=\"btn primary tiny\"" in js
    assert "class=\"btn secondary tiny\"" in js
    assert 'id="threeSearch" class="tiny"' not in js


def test_three_visualiser_fullscreen_overlay_is_inside_viewport():
    html = Path("static/index.html").read_text()
    css = Path("src/styles.css").read_text()
    js = Path("static/app.js").read_text()
    assert 'id="threeFullscreenInspector" class="three-fullscreen-inspector"' in html
    assert html.index('id="threeFullscreenInspector"') < html.index('id="threeInspector"')
    assert ".three-viewport:fullscreen .three-fullscreen-inspector.active" in css
    assert "threeOverlayMemory" in js
    assert "threeOverlaySearch" in js
    assert "threeOverlayClose" in js
    assert "three-fullscreen-inspector,.fullscreen-exit,.viewport-fullscreen,.constellation-legend" in js


def test_mobile_app_background_is_fixed_across_tabs():
    css = Path("src/styles.css").read_text()
    assert "@apply m-0 overflow-hidden bg-yan-bg" in css
    assert "background-attachment: fixed;" in css
    assert "background-position: center top;" in css
    assert "background-size: 100vw 100vh;" in css


def test_impeccable_surface_polish_removes_raw_black_and_quiets_mobile_config_keys():
    css = Path("src/styles.css").read_text()
    assert "--surface-35: rgba(13, 13, 22, .35);" in css
    assert "bg-black" not in css
    assert "border-black" not in css
    assert "ring-offset-black" not in css
    assert "input[type=\"checkbox\"] { @apply relative h-5 w-5 min-w-5" in css
    assert "input[type=\"checkbox\"]:checked::after" in css
    assert "@apply absolute left-1/2 top-1/2 h-4 w-4" in css
    assert "clip-path: polygon" in css
    assert ".config-name { @apply mt-1.5 text-[10px]; }" in css
    assert ".scope-status code { @apply mt-1; }" in css
    assert ".config-name { display: none; }" not in css
    assert ".scope-status code { display: none; }" not in css


def test_mobile_scope_and_visualiser_toolbar_css_is_compact():
    css = Path("src/styles.css").read_text()
    assert ".scope-bar select" in css
    assert "appearance: none" in css
    assert "background-position: calc(100% - 15px) 50%" in css
    assert ".visualiser-actions { @apply grid w-full grid-cols-4 gap-2; }" in css
    assert ".visualiser-toolbar .visualiser-actions .btn" in css
    assert "whitespace-normal text-xs leading-5" in css


def test_visualiser_fullscreen_control_lives_in_viewport():
    html = Path("static/index.html").read_text()
    css = Path("src/styles.css").read_text()
    js = Path("static/app.js").read_text()
    assert 'id="threeFullscreen" class="viewport-fullscreen" aria-label="Open visualiser fullscreen"' in html
    assert "function threeRenderPixelRatio(viewport)" in js
    assert "qualityBoost = fullscreen ? 1.45 : 1.35" in js
    assert "renderer.setPixelRatio(threeRenderPixelRatio(viewport));" in js
    assert "canvas.width = 256; canvas.height = 256;" in js
    assert "canvas.width = 1024; canvas.height = 640;" in js
    assert "precision:'highp'" in js
    assert "text-rendering: geometricPrecision" in css
    assert "Drag to rotate · Right-click/Shift+drag to pan · Wheel/pinch to zoom." in html
    assert "Drag to rotate · Right-click/Shift+drag to pan · Wheel/pinch to zoom." in js
    assert "e.button===2 || threeVis.panMode || e.shiftKey" in js
    assert "if(d.mode === 'pan')" in js
    assert "canvas.addEventListener('contextmenu',e=>e.preventDefault())" in js
    assert "rightPan=e.button===2" in js
    assert "Admin mode enabled" not in html
    assert html.index('id="threeFullscreen"') > html.index('id="threeViewport"')
    assert html.index('id="threeFullscreen"') < html.index('id="threeLabels"')
    assert 'id="threeFullscreen" class="btn secondary"' not in html
    assert ".viewport-fullscreen" in css
    assert ".three-viewport:fullscreen .viewport-fullscreen { display: none; }" in css
    assert ".viewport-fullscreen" in js
    assert "Drag to rotate · Right-click/Shift+drag to pan · Wheel/pinch to zoom." in html


def test_identity_namespace_coverage_uses_mobile_cards():
    js = Path("static/app.js").read_text()
    assert "namespace-coverage-list" in js
    assert "namespace-coverage-card" in js
    assert "coverage-status" in js
    assert "<table><thead><tr><th>Namespace</th><th>Rows</th><th>Belongs to</th><th>Status</th>" not in js
    css = Path("src/styles.css").read_text()
    assert ".namespace-coverage-head { @apply grid" in css
    assert ".coverage-status" in css
    assert "whitespace-normal" in css


def test_identity_namespace_coverage_does_not_use_table_wrapper_border():
    html = Path("static/index.html").read_text()
    assert 'id="identityNamespaceTable" class="coverage-wrap"' in html
    assert 'id="identityNamespaceTable" class="table-wrap"' not in html
    css = Path("src/styles.css").read_text()
    assert ".coverage-wrap { @apply max-w-full overflow-visible rounded-none border-0 bg-transparent; }" in css


def test_mobile_memory_browser_filters_are_compact():
    html = Path("static/index.html").read_text()
    css = Path("src/styles.css").read_text()
    js = Path("static/app.js").read_text()
    assert 'class="toolbar memory-toolbar"' in html
    assert 'id="memoryAdvancedFilters" class="memory-advanced"' in html
    assert "More filters" in html
    assert '<button id="memoryApply" class="btn secondary">Apply</button>' in html
    assert '<button id="memoryReset" class="btn ghost">Reset</button>' in html
    assert ".memory-toolbar #memoryNamespaceFilter { display: none; }" in css
    assert ".memory-toolbar #memorySearch { @apply col-span-2 min-h-10; }" in css
    assert ".memory-filter-actions { @apply grid grid-cols-2 gap-2; }" in css
    assert "function updateMemoryAdvancedFilters()" in js
    assert "details.open = hasAdvanced" in js


def test_readme_documents_mock_screenshot_gallery():
    readme = Path("README.md").read_text()
    assert "## Screenshots" in readme
    assert "synthetic mock YantrikDB database" in readme
    assert "docs/screenshots/desktop-overview.png" in readme
    assert "docs/screenshots/mobile-identity-scope.png" in readme
    assert "docs/screenshots/mobile-settings.png" in readme
    assert "python3 scripts/generate_mock_screenshots.py" in readme


def test_mock_screenshot_generator_avoids_private_db_paths():
    script = Path("scripts/generate_mock_screenshots.py").read_text()
    assert "never reads the user's real YantrikDB memory store" in script
    assert "/Users/wysie/.hermes/yantrikdb-memory.db" not in script
    assert "mock-yantrikdb.db" in script



def test_health_reports_import_source_and_plugin_version_warnings(monkeypatch, tmp_path):
    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (namespace TEXT)")
    plugin_dir = tmp_path / "yantrikdb-plugin"
    (plugin_dir / "yantrikdb").mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("name: yantrikdb\nversion: 0.4.12\n")
    (plugin_dir / "yantrikdb" / "plugin.yaml").write_text("name: yantrikdb\nversion: 0.4.17\n")
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "YANTRIKDB_PLUGIN_DIR", plugin_dir)
    monkeypatch.setattr(dashboard.importlib_metadata, "version", lambda name: "0.2.4")

    payload = dashboard.health()

    assert payload["yantrikdb_version"] == "0.2.4"
    assert payload["plugin"]["versions"]["root_manifest"] == "0.4.12"
    assert payload["plugin"]["versions"]["bundled_manifest"] == "0.4.17"
    assert any("Plugin manifests disagree" in w for w in payload["warnings"])


def test_dashboard_engine_avoids_plugin_checkout_shadowing(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "yantrikdb-plugin"
    package_dir = plugin_dir / "yantrikdb"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("# provider plugin, not core\n")
    core_dir = tmp_path / "core"
    core_pkg = core_dir / "yantrikdb"
    core_pkg.mkdir(parents=True)
    (core_pkg / "__init__.py").write_text(
        "class YantrikDB:\n"
        "    def __init__(self, path, embedding_dim=None):\n"
        "        self.path = path; self.embedding_dim = embedding_dim; self.named = None\n"
        "    def set_embedder_named(self, name):\n"
        "        self.named = name\n"
    )
    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (embedding BLOB)")
    monkeypatch.syspath_prepend(str(core_dir))
    monkeypatch.syspath_prepend(str(plugin_dir))
    monkeypatch.setattr(dashboard, "YANTRIKDB_PLUGIN_DIR", plugin_dir)
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "_db_handle", None)
    monkeypatch.setattr(dashboard, "_db_dim", None)
    __import__('sys').modules.pop('yantrikdb', None)

    handle = dashboard.engine()

    assert handle.path == str(db_path)
    assert "core" in __import__('yantrikdb').__file__


def test_triggers_route_uses_core_get_pending_triggers(monkeypatch):
    class FakeCoreEngine:
        def get_pending_triggers(self, limit=10):
            return [{"trigger_id": "t-core", "hlc": b"abc", "status": "pending"}]
    monkeypatch.setattr(dashboard, "engine", lambda: FakeCoreEngine())
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)

    payload = dashboard.triggers(limit=5, status="pending")

    assert payload["source"] == "engine"
    assert payload["items"][0]["trigger_id"] == "t-core"
    assert payload["items"][0]["hlc"] == {"hex": "616263", "bytes": 3}


def test_trigger_action_endpoints_call_engine_and_require_admin(monkeypatch):
    class FakeEngine:
        def __init__(self):
            self.calls = []
        def acknowledge_trigger(self, trigger_id):
            self.calls.append(("ack", trigger_id)); return {"trigger_id": trigger_id, "acknowledged": True}
        def dismiss_trigger(self, trigger_id):
            self.calls.append(("dismiss", trigger_id)); return {"trigger_id": trigger_id, "dismissed": True}
        def act_on_trigger(self, trigger_id):
            self.calls.append(("act", trigger_id)); return {"trigger_id": trigger_id, "acted": True}
    fake = FakeEngine()
    monkeypatch.setattr(dashboard, "engine", lambda: fake)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)
    monkeypatch.setattr(dashboard, "ADMIN_MODE_ENV", True)
    client = TestClient(dashboard.app)

    assert client.post("/api/triggers/t1/acknowledge").json()["acknowledged"] is True
    assert client.post("/api/triggers/t2/dismiss").json()["dismissed"] is True
    assert client.post("/api/triggers/t3/act").json()["acted"] is True
    assert fake.calls == [("ack", "t1"), ("dismiss", "t2"), ("act", "t3")]


def test_recent_skills_api_reads_capped_recent_skill_file(tmp_path, monkeypatch):
    recent = tmp_path / "recent.json"
    recent.write_text(json.dumps([
        {"skill_id": "git.commit_clean", "skill_type": "procedure", "applies_to": ["git", "workflow"], "ts": 1000, "session_id": "old"},
        {"skill_id": "stale.skill", "skill_type": "lesson", "applies_to": [], "ts": 1000 - (8 * 86400), "session_id": "old"},
    ]))
    monkeypatch.setattr(dashboard, "RECENT_SKILLS_PATH", recent)
    monkeypatch.setattr(dashboard, "now", lambda: 1000 + 3600)

    payload = dashboard.recent_skills(limit=5)

    assert payload["items"][0]["skill_id"] == "git.commit_clean"
    assert payload["items"][0]["age_seconds"] == 3600
    assert len(payload["items"]) == 1


def test_load_hermes_env_defaults_reads_missing_yantrikdb_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("YANTRIKDB_SKILLS_ENABLED=true\nYANTRIKDB_TOP_K=13\nOTHER_SECRET=nope\n")
    monkeypatch.setenv("HERMES_ENV_PATH", str(env_path))
    monkeypatch.delenv("YANTRIKDB_SKILLS_ENABLED", raising=False)
    monkeypatch.setenv("YANTRIKDB_TOP_K", "99")

    loaded = dashboard.load_hermes_env_defaults()

    assert loaded == {"YANTRIKDB_SKILLS_ENABLED": "true"}
    assert __import__('os').environ["YANTRIKDB_SKILLS_ENABLED"] == "true"
    assert __import__('os').environ["YANTRIKDB_TOP_K"] == "99"
    assert "OTHER_SECRET" not in __import__('os').environ


def test_skill_recall_status_reports_provider_config(monkeypatch):
    class FakeConfig:
        skills_enabled = True
        auto_skill_attach = True
        auto_skill_min_score = 0.61
        auto_skill_max_bodies = 3
        surface_recent_skills = False
        top_k = 7
        mode = "embedded"
        db_path = "/tmp/yan.db"
        namespace = "hermes"

    monkeypatch.setattr(dashboard, "load_yantrikdb_plugin_config", lambda: FakeConfig())
    monkeypatch.setattr(dashboard, "yantrikdb_skill_tool_names", lambda: ["yantrikdb_skill_search", "yantrikdb_skill_define", "yantrikdb_skill_outcome"])
    monkeypatch.setattr(dashboard, "RECENT_SKILLS_PATH", Path("/tmp/no-recent-skills.json"))

    payload = dashboard.skill_recall_status()

    assert payload["installed"] is True
    assert payload["skills_enabled"] is True
    assert payload["skill_tools_exposed"] is True
    assert payload["tool_names"] == ["yantrikdb_skill_search", "yantrikdb_skill_define", "yantrikdb_skill_outcome"]
    assert payload["auto_skill_min_score"] == 0.61
    assert payload["warning"] is None


def test_skill_recall_search_normalizes_embedded_hits(monkeypatch):
    class FakeBackend:
        def __init__(self):
            self.calls = []
        def skill_search(self, query, *, top_k=None, applies_to=None):
            self.calls.append((query, top_k, applies_to))
            return {"skills": [{"text": "Use pytest first", "score": 0.77, "metadata": {"skill_id": "tdd.pytest", "skill_type": "procedure", "applies_to": ["tests", "python"]}}]}

    fake = FakeBackend()
    monkeypatch.setattr(dashboard, "skill_backend", lambda: fake)

    payload = dashboard.skill_recall_search(dashboard.SkillSearchRequest(query="pytest", top_k=5, applies_to="python"))

    assert fake.calls == [("pytest", 5, "python")]
    assert payload["items"][0]["skill_id"] == "tdd.pytest"
    assert payload["items"][0]["body"] == "Use pytest first"
    assert payload["items"][0]["score"] == 0.77


def test_skill_recall_outcomes_reads_outcome_substrate(tmp_path, monkeypatch):
    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT, text TEXT, namespace TEXT, domain TEXT, metadata TEXT, created_at REAL)")
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?)",
            ("r1", "outcome: skill=tdd.pytest succeeded=True", "outcome_substrate", "skill_outcome", json.dumps({"skill_id": "tdd.pytest", "succeeded": True, "note": "worked"}), 100.0),
        )
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?)",
            ("r2", "outcome: skill=other succeeded=False", "outcome_substrate", "skill_outcome", json.dumps({"skill_id": "other", "succeeded": False}), 101.0),
        )
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)

    payload = dashboard.skill_recall_outcomes("tdd.pytest", limit=10)

    assert payload["total"] == 1
    assert payload["items"][0]["rid"] == "r1"
    assert payload["items"][0]["metadata_json"]["note"] == "worked"


def test_dashboard_contract_for_0417_features():
    html = Path("static/index.html").read_text()
    js = Path("static/app.js").read_text()
    css = Path("src/styles.css").read_text()
    assert 'data-view="skills">Learned Skills</button>' in html
    assert 'id="view-skills"' in html
    assert 'id="skillRecallCards"' in html
    assert 'id="skillSearchResults"' in html
    assert 'id="skillOutcomeDetail"' in html
    assert 'id="recentSkillsList"' in html
    assert "renderScoreBreakdown" in js
    assert "score-contrib-bar" in js
    assert "acknowledgeTrigger" in js
    assert "dismissTrigger" in js
    assert "actOnTrigger" in js
    assert "/api/recent-skills" in js
    assert "/api/skill-recall/status" in js
    assert "/api/skill-recall/search" in js
    assert "showSkillOutcome" in js
    assert "`/api/triggers/${encodeURIComponent(id)}/${action}`" in js
    assert "acknowledge','Trigger acknowledged" in js
    assert ".score-breakdown" in css
    assert ".trigger-actions" in css
    assert ".runtime-warning" in css


def test_raw_sql_reads_the_engine_store_read_only(tmp_path, monkeypatch):
    """The raw-SQL helpers must never be able to commit to the engine's store.

    The dashboard's stdlib ``sqlite3`` connection is a second SQLite library
    on a file the engine owns. A commit from it bumps ``PRAGMA data_version``
    under the engine's writer connection, which makes the engine queue a
    ``quick_check`` and, on a non-"ok" result, latch ``foreign_sqlite_tainted``
    and refuse every write until it is reopened
    (yantrikos/yantrikdb#225, yantrikos/yantrikdb#247).
    """
    db_path = tmp_path / "yantrikdb.db"
    with sqlite3.connect(db_path) as seed:
        seed.execute("CREATE TABLE memories (rid TEXT PRIMARY KEY)")
        seed.execute("INSERT INTO memories (rid) VALUES ('rid-1')")
        seed.commit()

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)

    # Reads still work, including the read-only PRAGMA the helpers use.
    assert dashboard.rows("SELECT rid FROM memories") == [{"rid": "rid-1"}]
    assert dashboard.rows("PRAGMA table_info(memories)")

    # Writes are refused by SQLite itself, not merely by convention.
    with pytest.raises(sqlite3.OperationalError, match="readonly database"):
        dashboard.rows("INSERT INTO memories (rid) VALUES ('rid-2')")

    # The store is unchanged.
    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
