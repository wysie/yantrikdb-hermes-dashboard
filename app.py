#!/usr/bin/env python3
"""Local-first YantrikDB dashboard.

Read-only by default. Admin mutations are controlled by
YANTRIKDB_DASHBOARD_ADMIN_MODE or the local Settings page.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    import importlib.metadata as importlib_metadata
except Exception:  # pragma: no cover
    import importlib_metadata  # type: ignore

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_DB = Path.home() / ".hermes" / "yantrikdb-memory.db"
DB_PATH = Path(os.environ.get("YANTRIKDB_DB_PATH") or DEFAULT_DB).expanduser()
BASE_NAMESPACE = os.environ.get("YANTRIKDB_NAMESPACE", "hermes")
DEFAULT_NAMESPACE = os.environ.get("YANTRIKDB_DASHBOARD_NAMESPACE", f"{BASE_NAMESPACE}:hermes:default")
DEFAULT_SETTINGS_PATH = Path.home() / ".hermes" / "plugin-data" / "yantrikdb-hermes-dashboard" / "settings.json"
LEGACY_SETTINGS_PATH = Path.home() / ".hermes" / "plugin-data" / "yantrikdb-dashboard" / "settings.json"
SETTINGS_PATH = Path(os.environ.get("YANTRIKDB_DASHBOARD_SETTINGS_PATH") or (LEGACY_SETTINGS_PATH if LEGACY_SETTINGS_PATH.exists() and not DEFAULT_SETTINGS_PATH.exists() else DEFAULT_SETTINGS_PATH)).expanduser()
YANTRIKDB_CONFIG_PATH = Path(os.environ.get("YANTRIKDB_CONFIG_PATH") or (Path.home() / ".hermes" / "yantrikdb.json")).expanduser()
WHATSAPP_SESSION_DIR = Path(os.environ.get("HERMES_WHATSAPP_SESSION_DIR") or (Path.home() / ".hermes" / "whatsapp" / "session")).expanduser()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


ADMIN_MODE_ENV = env_bool("YANTRIKDB_DASHBOARD_ADMIN_MODE", False)

# HTTP backend mode (optional). When YANTRIKDB_SERVER_URL is set the
# dashboard proxies supported routes to that yantrikdb-server v0.8.17+
# cluster instead of reading the embedded SQLite store. When unset,
# behaviour is unchanged: routes read SQLite directly as before.
from backend import HTTPBackend, NotImplementedHTTPBackend, make_backend, not_implemented_response  # noqa: E402

HTTP_BACKEND: HTTPBackend | None = make_backend()

app = FastAPI(title="YantrikDB for Hermes", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1", "http://localhost"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_db_handle = None
_db_dim: int | None = None


def now() -> float:
    return time.time()


def connect() -> sqlite3.Connection:
    # In HTTP-backend mode there is no local SQLite file by design.
    # Any route that still falls through to a SQL helper (rows/one/etc.)
    # is one we haven't wrapped yet — surface that as a 501 with a
    # clear pointer to issue #39 rather than a confusing 500.
    if HTTP_BACKEND is not None:
        raise not_implemented_response("SQL-backed route")
    if not DB_PATH.exists():
        raise HTTPException(500, f"YantrikDB database not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        cur = conn.execute(sql, params)
        r = cur.fetchone()
        return dict(r) if r else None


def table_exists(name: str) -> bool:
    return bool(one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def is_all_namespaces(namespace: str | None) -> bool:
    return str(namespace or "").strip().lower() in {"", "__all__", "all", "*"}


def namespace_clause(column: str, namespace: str | None) -> tuple[list[str], list[Any]]:
    if is_all_namespaces(namespace):
        return [], []
    return [f"{column}=?"], [namespace or DEFAULT_NAMESPACE]


def parse_json(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def clean_row(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    if "metadata" in out:
        out["metadata_json"] = parse_json(out.get("metadata"), {})
    if "embedding" in out:
        blob = out.pop("embedding")
        out["embedding_bytes"] = len(blob) if blob else 0
    for key in ("created_at", "updated_at", "last_access", "due_at"):
        if out.get(key) is not None:
            try:
                out[key + "_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(out[key])))
            except Exception:
                pass
    return out


def load_dashboard_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_dashboard_settings(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def admin_mode_enabled() -> bool:
    return bool(ADMIN_MODE_ENV or load_dashboard_settings().get("admin_mode"))


def require_admin(request: Request) -> None:
    if not admin_mode_enabled():
        raise HTTPException(403, "Admin mode is disabled. Enable it in Settings or set YANTRIKDB_DASHBOARD_ADMIN_MODE=true.")


SESSION_COOKIE = "yantrikdb_dashboard_session"


def password_enabled(settings: dict[str, Any] | None = None) -> bool:
    settings = settings if settings is not None else load_dashboard_settings()
    return bool(settings.get("password_hash") and settings.get("password_salt") and settings.get("session_secret"))


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return salt.hex(), digest.hex()


def verify_password(password: str, settings: dict[str, Any]) -> bool:
    if not password_enabled(settings):
        return False
    _salt, digest = hash_password(password, str(settings["password_salt"]))
    return hmac.compare_digest(digest, str(settings["password_hash"]))


def session_value(settings: dict[str, Any]) -> str:
    msg = str(settings.get("password_hash", "")).encode("utf-8")
    secret = str(settings.get("session_secret", "")).encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def is_authenticated(request: Request, settings: dict[str, Any] | None = None) -> bool:
    settings = settings if settings is not None else load_dashboard_settings()
    if not password_enabled(settings):
        return True
    return hmac.compare_digest(request.cookies.get(SESSION_COOKIE, ""), session_value(settings))


def infer_embedding_dim() -> int:
    """Infer the DB vector dimension.

    YantrikDB.with_default() may open an engine with a default dimension that
    does not match the live database. The dashboard must open the engine with the DB's
    actual dimension.
    """
    env_dim = os.environ.get("YANTRIKDB_EMBEDDING_DIM")
    if env_dim:
        try:
            return int(env_dim)
        except ValueError:
            pass
    try:
        r = one("SELECT length(embedding) bytes FROM memories WHERE embedding IS NOT NULL AND length(embedding) > 0 LIMIT 1")
        b = int((r or {}).get("bytes") or 0)
        if b and b % 4 == 0:
            return b // 4
    except Exception:
        pass
    return 384


def preferred_embedder_for_dim(dim: int) -> str | None:
    explicit = (os.environ.get("YANTRIKDB_EMBEDDER") or "").strip()
    if explicit:
        return explicit
    return {512: "potion-base-32M", 256: "potion-base-8M"}.get(dim)


def engine():
    global _db_handle, _db_dim
    if _db_handle is None:
        import yantrikdb
        _db_dim = infer_embedding_dim()
        _db_handle = yantrikdb.YantrikDB(str(DB_PATH), embedding_dim=_db_dim)
        embedder = preferred_embedder_for_dim(_db_dim)
        if embedder:
            _db_handle.set_embedder_named(embedder)
    return _db_handle


class RecallRequest(BaseModel):
    query: str
    top_k: int = 10
    namespace: Optional[str] = None
    domain: Optional[str] = None
    source: Optional[str] = None
    include_consolidated: bool = False
    expand_entities: bool = True


class ResolveRequest(BaseModel):
    strategy: str
    winner_rid: Optional[str] = None
    new_text: Optional[str] = None
    resolution_note: Optional[str] = None


class ThinkRequest(BaseModel):
    run_consolidation: bool = True
    run_conflict_scan: bool = True
    run_pattern_mining: bool = False
    run_personality: bool = False
    consolidation_limit: Optional[int] = None


class LoginRequest(BaseModel):
    password: str


class SettingsRequest(BaseModel):
    admin_mode: bool
    new_password: Optional[str] = None
    disable_password: bool = False
    owner_scoping: Optional[bool] = None
    include_base_namespace_recall: Optional[bool] = None
    include_legacy_actor_namespace_recall: Optional[bool] = None
    top_k: Optional[int] = None


class IdentityScopeRequest(BaseModel):
    identity_scope: dict[str, Any]


@app.middleware("http")
async def password_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        settings = load_dashboard_settings()
        if password_enabled(settings) and not is_authenticated(request, settings):
            return JSONResponse(status_code=401, content={"detail": "Dashboard password required"})
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    if HTTP_BACKEND is not None:
        return HTTP_BACKEND.health()
    try:
        core_version = importlib_metadata.version("yantrikdb")
    except Exception:
        core_version = "unknown"
    plugin_dir = Path.home() / ".hermes" / "plugins" / "yantrikdb"
    plugin = {
        "path": str(plugin_dir),
        "exists": plugin_dir.exists(),
    }
    if (plugin_dir / ".git").exists():
        try:
            import subprocess
            plugin["branch"] = subprocess.check_output(["git", "branch", "--show-current"], cwd=plugin_dir, text=True).strip()
            plugin["commit"] = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=plugin_dir, text=True).strip()
        except Exception:
            pass
    namespaces = rows("SELECT namespace, COUNT(*) count FROM memories GROUP BY namespace ORDER BY count DESC") if table_exists("memories") else []
    return {
        "ok": True,
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "base_namespace": BASE_NAMESPACE,
        "default_namespace": DEFAULT_NAMESPACE,
        "admin_enabled": admin_mode_enabled(),
        "admin_mode_env": ADMIN_MODE_ENV,
        "password_enabled": password_enabled(),
        "settings_path": str(SETTINGS_PATH),
        "yantrikdb_version": core_version,
        "embedder": preferred_embedder_for_dim(infer_embedding_dim()) or os.environ.get("YANTRIKDB_EMBEDDER") or "default",
        "embedding_dim": infer_embedding_dim(),
        "plugin": plugin,
        "namespaces": namespaces,
    }



def default_identity_scope_config() -> dict[str, list[dict[str, Any]]]:
    return {"identities": [], "actors": [], "spaces": [], "conversations": []}


def normalise_identity_scope_config(value: Any) -> dict[str, list[dict[str, Any]]]:
    base = default_identity_scope_config()
    if not isinstance(value, dict):
        return base
    for key in base:
        rows_value = value.get(key, [])
        if isinstance(rows_value, list):
            base[key] = [dict(item) for item in rows_value if isinstance(item, dict)]
    return base


def split_actor(value: str) -> tuple[str, str]:
    raw = str(value or "")
    if ":" in raw:
        platform, actor = raw.split(":", 1)
        return platform or "unknown", actor
    return "unknown", raw


def safe_namespace_part(value: str) -> str:
    text = str(value or "default")
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()[:32]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{slug or 'owner'}-{digest}"


def owner_namespace_suffix(owner_id: str) -> str:
    return f"owner:{safe_namespace_part(owner_id)}"


def actor_from_owner_namespace(namespace: str) -> str:
    tail = str(namespace or "").split(":owner:", 1)[-1]
    if tail.startswith("whatsapp-default-"):
        return ""
    match = re.match(r"whatsapp-(\d+)-lid-[0-9a-f]{12}$", tail)
    if match:
        return f"whatsapp:{match.group(1)}@lid"
    match = re.match(r"whatsapp-(\d+)-[0-9a-f]{12}$", tail)
    if match:
        return f"whatsapp:{match.group(1)}"
    match = re.match(r"telegram-(.+)-[0-9a-f]{12}$", tail)
    if match:
        return f"telegram:{match.group(1)}"
    return ""


def whatsapp_alias_for_lid(actor_id: str) -> str:
    if not actor_id.endswith("@lid"):
        return ""
    lid = actor_id[:-4]
    path = WHATSAPP_SESSION_DIR / f"lid-mapping-{lid}_reverse.json"
    try:
        alias = json.loads(path.read_text())
        return str(alias) if alias else ""
    except Exception:
        return ""


def load_yantrikdb_identity_scope_config() -> dict[str, list[dict[str, Any]]]:
    if not YANTRIKDB_CONFIG_PATH.exists():
        return default_identity_scope_config()
    try:
        ycfg = json.loads(YANTRIKDB_CONFIG_PATH.read_text())
        identity_map_path = Path(str(ycfg.get("identity_map_path") or "")).expanduser()
        if not identity_map_path.exists():
            return default_identity_scope_config()
        identity_map = json.loads(identity_map_path.read_text())
    except Exception:
        return default_identity_scope_config()
    out = default_identity_scope_config()
    owners = identity_map.get("owners", {}) if isinstance(identity_map, dict) else {}
    if not isinstance(owners, dict):
        return out
    for owner_key, details in sorted(owners.items()):
        if not isinstance(details, dict):
            details = {}
        owner = str(owner_key)
        ident_id = owner.replace("owner:", "", 1) if owner.startswith("owner:") else owner
        out["identities"].append({
            "id": ident_id,
            "label": ident_id.replace("-", " ").replace("_", " ").title(),
            "private_scope": owner,
            "resolved_scope": owner_namespace_suffix(owner),
            "source": "yantrikdb_identity_map",
        })
        for actor_value in details.get("actors", []) if isinstance(details.get("actors", []), list) else []:
            platform, actor_id = split_actor(str(actor_value))
            out["actors"].append({
                "platform": platform,
                "actor_id": actor_id,
                "identity": ident_id,
                "legacy_scope": owner_namespace_suffix(str(actor_value)),
                "source": "yantrikdb_identity_map",
            })
    return out


def merge_identity_scope_config(primary: dict[str, list[dict[str, Any]]], imported: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Merge auto-imported defaults with dashboard edits.

    Imported provider rows are discovery defaults. Dashboard-local rows must win
    so editing a detected person/actor in the UI persists instead of being
    overwritten on the next reload.
    """
    merged = default_identity_scope_config()
    keys = {
        "identities": lambda item: str(item.get("id") or item.get("private_scope") or item),
        "actors": lambda item: f"{item.get('platform','')}:{item.get('actor_id','')}",
        "spaces": lambda item: str(item.get("id") or item.get("scope") or item),
        "conversations": lambda item: f"{item.get('platform','')}:{item.get('conversation_id','')}",
    }
    for key, key_fn in keys.items():
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for item in imported.get(key, []):
            marker = key_fn(item)
            if marker not in by_key:
                order.append(marker)
            by_key[marker] = dict(item)
        for item in primary.get(key, []):
            marker = key_fn(item)
            if marker not in by_key:
                order.append(marker)
            combined = {**by_key.get(marker, {}), **dict(item)}
            combined["source"] = "dashboard" if item.get("source") != "namespace_inventory" else "namespace_inventory"
            by_key[marker] = combined
        merged[key] = [by_key[marker] for marker in order if marker in by_key]
    return merged


def namespace_matches_scope(namespace: str, scope: str) -> bool:
    if namespace == scope:
        return True
    return bool(scope and namespace.endswith(f":{scope}"))


def identity_scope_payload() -> dict[str, Any]:
    settings = load_dashboard_settings()
    stored = normalise_identity_scope_config(settings.get("identity_scope"))
    imported = load_yantrikdb_identity_scope_config()
    config = merge_identity_scope_config(stored, imported)
    runtime_scope = yantrikdb_settings_payload()
    namespace_rows = rows("SELECT namespace, COUNT(*) count FROM memories GROUP BY namespace ORDER BY namespace") if table_exists("memories") else []
    existing_actor_keys = {f"{a.get('platform')}:{a.get('actor_id')}" for a in config["actors"]}
    for row in namespace_rows:
        namespace = str(row["namespace"])
        raw_actor = actor_from_owner_namespace(namespace)
        if not raw_actor or raw_actor in existing_actor_keys:
            continue
        platform, actor_id = split_actor(raw_actor)
        legacy_tail = namespace.split(":owner:", 1)[-1] if ":owner:" in namespace else ""
        item = {
            "platform": platform,
            "actor_id": actor_id,
            "identity": "",
            "legacy_scope": f"owner:{legacy_tail}" if legacy_tail else "",
            "source": "namespace_inventory",
        }
        if platform == "whatsapp":
            alias = whatsapp_alias_for_lid(actor_id)
            if alias:
                item["alias"] = alias
        config["actors"].append(item)
        existing_actor_keys.add(raw_actor)
    scope_details: dict[str, dict[str, str]] = {}

    def remember_scope(scope: str, label: str, kind: str, source: str = "configured") -> None:
        if not scope:
            return
        scope_details.setdefault(str(scope), {"label": label, "kind": kind, "source": source})

    identity_labels = {str(i.get("id")): str(i.get("label") or i.get("id")) for i in config["identities"] if i.get("id")}
    actor_identity_lookup: dict[str, str] = {}
    for actor in config["actors"]:
        raw_actor = f"{actor.get('platform')}:{actor.get('actor_id')}" if actor.get('platform') and actor.get('actor_id') else ""
        identity = str(actor.get("identity") or "")
        if raw_actor and identity:
            actor_identity_lookup[raw_actor] = identity
    for ident in config["identities"]:
        label = str(ident.get("label") or ident.get("id") or "Identity")
        if ident.get("private_scope"):
            owner = str(ident["private_scope"])
            remember_scope(owner, label, "identity", str(ident.get("source") or "configured"))
            remember_scope(owner_namespace_suffix(owner), label, "identity", str(ident.get("source") or "configured"))
        if ident.get("resolved_scope"):
            remember_scope(str(ident["resolved_scope"]), label, "identity", str(ident.get("source") or "configured"))
    for actor in config["actors"]:
        raw_actor = f"{actor.get('platform')}:{actor.get('actor_id')}" if actor.get('platform') and actor.get('actor_id') else ""
        identity = str(actor.get("identity") or "")
        label = identity_labels.get(identity, identity)
        actor_label = f"{label} via {raw_actor}" if label and raw_actor else (raw_actor or "Unassigned actor")
        if identity and raw_actor:
            remember_scope(owner_namespace_suffix(raw_actor), actor_label, "actor", str(actor.get("source") or "configured"))
        if identity and actor.get("legacy_scope"):
            remember_scope(str(actor["legacy_scope"]), actor_label, "actor", str(actor.get("source") or "configured"))
    for space in config["spaces"]:
        if space.get("scope"):
            remember_scope(str(space["scope"]), str(space.get("label") or space.get("id") or space["scope"]), "shared_scope", str(space.get("source") or "configured"))
    for convo in config["conversations"]:
        if convo.get("scope"):
            remember_scope(str(convo["scope"]), str(convo.get("label") or convo.get("conversation_id") or convo["scope"]), "conversation_route", str(convo.get("source") or "configured"))
    inventory = []
    base_namespace = str(runtime_scope.get("default_namespace") or DEFAULT_NAMESPACE)
    for row in namespace_rows:
        namespace = str(row["namespace"])
        matched_scope = next((scope for scope in scope_details if namespace_matches_scope(namespace, scope)), "")
        detail = scope_details.get(matched_scope, {}) if matched_scope else {}
        derived_by_config = False
        raw_legacy_actor = actor_from_owner_namespace(namespace)
        if (
            matched_scope
            and runtime_scope.get("owner_scoping")
            and runtime_scope.get("include_legacy_actor_namespace_recall")
            and raw_legacy_actor
            and actor_identity_lookup.get(raw_legacy_actor)
            and detail.get("kind") == "actor"
        ):
            identity = actor_identity_lookup[raw_legacy_actor]
            ident_label = identity_labels.get(identity, identity)
            detail = {
                "label": f"{ident_label} via old account bucket",
                "kind": "legacy_actor_fallback",
                "source": "include_legacy_actor_namespace_recall",
            }
            derived_by_config = True
        if not matched_scope and runtime_scope.get("owner_scoping") and runtime_scope.get("include_base_namespace_recall") and namespace == base_namespace:
            matched_scope = namespace
            detail = {
                "label": "Shared by all profiles",
                "kind": "shared_fallback",
                "source": "include_base_namespace_recall",
            }
            derived_by_config = True
        if not matched_scope and runtime_scope.get("owner_scoping") and runtime_scope.get("include_legacy_actor_namespace_recall"):
            raw_actor = raw_legacy_actor
            identity = actor_identity_lookup.get(raw_actor, "")
            if raw_actor and identity:
                ident_label = identity_labels.get(identity, identity)
                matched_scope = namespace
                detail = {
                    "label": f"{ident_label} via old account bucket",
                    "kind": "legacy_actor_fallback",
                    "source": "include_legacy_actor_namespace_recall",
                }
                derived_by_config = True
        inventory.append({
            "namespace": namespace,
            "count": int(row["count"] or 0),
            "mapped": bool(matched_scope),
            "mapped_scope": matched_scope,
            "mapped_to": detail.get("label", ""),
            "mapping_type": detail.get("kind", ""),
            "mapping_source": detail.get("source", ""),
            "derived_by_config": derived_by_config,
        })
    summary = {
        "identities": len(config["identities"]),
        "actors": len(config["actors"]),
        "spaces": len(config["spaces"]),
        "conversations": len(config["conversations"]),
        "unmapped_namespaces": sum(1 for item in inventory if not item["mapped"]),
    }
    return {"identity_scope": config, "namespace_inventory": inventory, "summary": summary, "imported_identity_scope": imported, "runtime_scope": runtime_scope}


@app.get("/api/identity-scope")
def get_identity_scope() -> dict[str, Any]:
    return identity_scope_payload()


@app.post("/api/identity-scope")
def update_identity_scope(req: IdentityScopeRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    data = load_dashboard_settings()
    data["identity_scope"] = normalise_identity_scope_config(req.identity_scope)
    save_dashboard_settings(data)
    return identity_scope_payload()

def load_yantrikdb_runtime_config() -> dict[str, Any]:
    if not YANTRIKDB_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(YANTRIKDB_CONFIG_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_yantrikdb_runtime_config(data: dict[str, Any]) -> None:
    YANTRIKDB_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    YANTRIKDB_CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")


def bool_config(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def yantrikdb_settings_payload() -> dict[str, Any]:
    ycfg = load_yantrikdb_runtime_config()
    namespace = str(ycfg.get("namespace") or BASE_NAMESPACE)
    default_namespace = f"{namespace}:hermes:default"
    return {
        "config_path": str(YANTRIKDB_CONFIG_PATH),
        "mode": ycfg.get("mode") or "embedded",
        "namespace": namespace,
        "default_namespace": default_namespace,
        "top_k": int(ycfg.get("top_k") or 10),
        "owner_scoping": bool_config(ycfg.get("owner_scoping"), False),
        "include_base_namespace_recall": bool_config(ycfg.get("include_base_namespace_recall"), True),
        "include_legacy_actor_namespace_recall": bool_config(ycfg.get("include_legacy_actor_namespace_recall"), True),
        "identity_map_path": str(Path(str(ycfg.get("identity_map_path") or "")).expanduser()) if ycfg.get("identity_map_path") else "",
    }


@app.get("/api/settings")
def get_settings(request: Request) -> dict[str, Any]:
    stored = load_dashboard_settings()
    return {
        "admin_mode": admin_mode_enabled(),
        "admin_mode_env": ADMIN_MODE_ENV,
        "admin_mode_stored": bool(stored.get("admin_mode")),
        "password_enabled": password_enabled(stored),
        "authenticated": is_authenticated(request, stored),
        "settings_path": str(SETTINGS_PATH),
        "db_path": str(DB_PATH),
        "default_namespace": DEFAULT_NAMESPACE,
        "embedder": preferred_embedder_for_dim(infer_embedding_dim()) or os.environ.get("YANTRIKDB_EMBEDDER") or "default",
        "embedding_dim": infer_embedding_dim(),
        "yantrikdb": yantrikdb_settings_payload(),
    }


@app.post("/api/settings")
def update_settings(req: SettingsRequest, request: Request) -> Response:
    data = load_dashboard_settings()
    data["admin_mode"] = bool(req.admin_mode)
    clear_cookie = False
    if req.disable_password:
        data.pop("password_hash", None)
        data.pop("password_salt", None)
        data.pop("session_secret", None)
        clear_cookie = True
    elif req.new_password is not None and req.new_password.strip():
        salt, digest = hash_password(req.new_password.strip())
        data["password_salt"] = salt
        data["password_hash"] = digest
        data["session_secret"] = secrets.token_hex(32)
        clear_cookie = True
    if any(v is not None for v in [req.owner_scoping, req.include_base_namespace_recall, req.include_legacy_actor_namespace_recall, req.top_k]):
        if not req.admin_mode:
            require_admin(request)
        ycfg = load_yantrikdb_runtime_config()
        if req.owner_scoping is not None:
            ycfg["owner_scoping"] = bool(req.owner_scoping)
        if req.include_base_namespace_recall is not None:
            ycfg["include_base_namespace_recall"] = bool(req.include_base_namespace_recall)
        if req.include_legacy_actor_namespace_recall is not None:
            ycfg["include_legacy_actor_namespace_recall"] = bool(req.include_legacy_actor_namespace_recall)
        if req.top_k is not None:
            ycfg["top_k"] = max(1, min(50, int(req.top_k)))
        save_yantrikdb_runtime_config(ycfg)
    data["updated_at"] = now()
    save_dashboard_settings(data)
    response = JSONResponse(get_settings(request))
    if clear_cookie:
        response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    settings = load_dashboard_settings()
    return {"password_required": password_enabled(settings), "authenticated": is_authenticated(request, settings)}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest) -> Response:
    settings = load_dashboard_settings()
    if not password_enabled(settings):
        return JSONResponse({"ok": True, "password_required": False})
    if not verify_password(req.password, settings):
        raise HTTPException(403, "Invalid dashboard password")
    response = JSONResponse({"ok": True, "password_required": True, "authenticated": True})
    response.set_cookie(SESSION_COOKIE, session_value(settings), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/stats")
def stats(namespace: str = Query(DEFAULT_NAMESPACE)) -> dict[str, Any]:
    if HTTP_BACKEND is not None:
        return HTTP_BACKEND.stats(namespace)
    mem_ns_clauses, mem_ns_params = namespace_clause("namespace", namespace)
    mem_where = " AND ".join(mem_ns_clauses) if mem_ns_clauses else "1=1"
    mem_counts = rows(
        f"""
        SELECT consolidation_status status, COUNT(*) count
        FROM memories WHERE {mem_where} GROUP BY consolidation_status
        """,
        tuple(mem_ns_params),
    ) if table_exists("memories") else []
    by_domain = rows(
        f"SELECT domain, COUNT(*) count FROM memories WHERE {mem_where} GROUP BY domain ORDER BY count DESC LIMIT 20",
        tuple(mem_ns_params),
    ) if table_exists("memories") else []
    by_source = rows(
        f"SELECT source, COUNT(*) count FROM memories WHERE {mem_where} GROUP BY source ORDER BY count DESC LIMIT 20",
        tuple(mem_ns_params),
    ) if table_exists("memories") else []
    by_type = rows(
        f"SELECT type, COUNT(*) count FROM memories WHERE {mem_where} GROUP BY type ORDER BY count DESC",
        tuple(mem_ns_params),
    ) if table_exists("memories") else []
    recent = rows(
        f"SELECT date(created_at, 'unixepoch', 'localtime') day, COUNT(*) count FROM memories WHERE {mem_where} GROUP BY day ORDER BY day DESC LIMIT 30",
        tuple(mem_ns_params),
    ) if table_exists("memories") else []
    conflicts = one("SELECT COUNT(*) count FROM conflicts WHERE COALESCE(status,'open') IN ('open','active','')") if table_exists("conflicts") else {"count": 0}
    entities = one("SELECT COUNT(*) count FROM entities") if table_exists("entities") else {"count": 0}
    edges = one("SELECT COUNT(*) count FROM edges") if table_exists("edges") else {"count": 0}
    if is_all_namespaces(namespace):
        by_status = {str(x.get("status") or "active"): int(x.get("count") or 0) for x in mem_counts}
        engine_stats = {
            "active_memories": by_status.get("active", 0),
            "consolidated_memories": by_status.get("consolidated", 0),
            "tombstoned_memories": by_status.get("tombstoned", 0),
            "scope": "all_namespaces_sql",
        }
    else:
        try:
            engine_stats = engine().stats(namespace=namespace)
        except Exception as e:
            engine_stats = {"error": str(e)}
    return {
        "namespace": namespace,
        "memory_status": mem_counts,
        "by_domain": by_domain,
        "by_source": by_source,
        "by_type": by_type,
        "recent_by_day": list(reversed(recent)),
        "open_conflicts": conflicts.get("count", 0) if conflicts else 0,
        "entities": entities.get("count", 0) if entities else 0,
        "edges": edges.get("count", 0) if edges else 0,
        "engine": engine_stats,
    }


@app.get("/api/memories")
def memories(
    namespace: str = Query(DEFAULT_NAMESPACE),
    status: str = "active",
    domain: str = "",
    source: str = "",
    memory_type: str = "",
    q: str = "",
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    sort: str = "created_at",
) -> dict[str, Any]:
    if HTTP_BACKEND is not None:
        return HTTP_BACKEND.list_memories(
            namespace=namespace, status=status, domain=domain, source=source,
            memory_type=memory_type, q=q, limit=limit, offset=offset, sort=sort,
        )
    clauses, params = namespace_clause("namespace", namespace)
    if status and status != "all":
        clauses.append("consolidation_status=?")
        params.append(status)
    if domain:
        clauses.append("domain=?")
        params.append(domain)
    if source:
        clauses.append("source=?")
        params.append(source)
    if memory_type:
        clauses.append("type=?")
        params.append(memory_type)
    if q:
        clauses.append("(text LIKE ? OR rid LIKE ? OR domain LIKE ? OR source LIKE ? OR type LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    where = " AND ".join(clauses) if clauses else "1=1"
    sort_col = sort if sort in {"created_at", "updated_at", "last_access", "importance", "access_count", "certainty"} else "created_at"
    total = one(f"SELECT COUNT(*) count FROM memories WHERE {where}", tuple(params)) or {"count": 0}
    data = rows(
        f"""
        SELECT rid,type,text,created_at,updated_at,importance,half_life,last_access,access_count,valence,
               consolidated_into,consolidation_status,storage_tier,metadata,namespace,certainty,domain,source,
               emotional_state,session_id,due_at,temporal_kind,tombstone_reason,embedding_model,
               length(embedding) embedding_bytes
        FROM memories WHERE {where}
        ORDER BY {sort_col} DESC LIMIT ? OFFSET ?
        """,
        tuple(params + [limit, offset]),
    )
    return {"total": total.get("count", 0), "limit": limit, "offset": offset, "items": [clean_row(x) for x in data]}


@app.get("/api/memory/{rid}")
def memory_detail(rid: str) -> dict[str, Any]:
    if HTTP_BACKEND is not None:
        return HTTP_BACKEND.get_memory(rid)
    m = one("SELECT *, length(embedding) embedding_bytes FROM memories WHERE rid=?", (rid,))
    if not m:
        raise HTTPException(404, "memory not found")
    detail = clean_row(m)
    if table_exists("consolidation_members"):
        # Current YantrikDB schema uses consolidation_rid/source_rid.
        detail["consolidation_sources"] = rows("SELECT * FROM consolidation_members WHERE consolidation_rid=? OR source_rid=?", (rid, rid))
    if table_exists("memory_entities"):
        detail["entities"] = rows("SELECT * FROM memory_entities WHERE memory_rid=?", (rid,))
    if table_exists("claims"):
        # Claims point back to memories via source_memory_rid in recent schemas.
        detail["claims"] = rows("SELECT * FROM claims WHERE source_memory_rid=? LIMIT 50", (rid,))
    return detail


def sql_recall_fallback(query: str, namespace: str, limit: int, domain: str | None = None, source: str | None = None) -> list[dict[str, Any]]:
    """FTS/LIKE fallback so the debugger is useful even if vector recall has no hits."""
    clauses, params = namespace_clause("m.namespace", namespace)
    clauses.append("m.consolidation_status='active'")
    if domain:
        clauses.append("m.domain=?")
        params.append(domain)
    if source:
        clauses.append("m.source=?")
        params.append(source)
    where = " AND ".join(clauses)
    terms = [t for t in query.replace('"', ' ').split() if len(t) > 1]
    match = " OR ".join(terms) if terms else query
    try:
        data = rows(
            f"""
            SELECT m.rid,m.type,m.text,m.created_at,m.updated_at,m.importance,m.access_count,
                   m.consolidation_status,m.namespace,m.certainty,m.domain,m.source,
                   bm25(memories_fts) AS rank
            FROM memories_fts
            JOIN memories m ON m.rowid = memories_fts.rowid
            WHERE memories_fts MATCH ? AND {where}
            ORDER BY rank LIMIT ?
            """,
            tuple([match] + params + [limit]),
        )
    except Exception:
        like_clauses = " OR ".join(["m.text LIKE ?" for _ in terms]) or "m.text LIKE ?"
        like_params = [f"%{t}%" for t in (terms or [query])]
        data = rows(
            f"""
            SELECT m.rid,m.type,m.text,m.created_at,m.updated_at,m.importance,m.access_count,
                   m.consolidation_status,m.namespace,m.certainty,m.domain,m.source, 0 AS rank
            FROM memories m
            WHERE ({like_clauses}) AND {where}
            ORDER BY m.importance DESC, m.created_at DESC LIMIT ?
            """,
            tuple(like_params + params + [limit]),
        )
    out = []
    for i, item in enumerate(data):
        clean = clean_row(item)
        clean["score"] = max(0.01, 1.0 - (i * 0.03))
        scope_reason = "all namespaces" if is_all_namespaces(namespace) else "same namespace"
        clean["why_retrieved"] = ["fts5/keyword fallback", scope_reason, f"domain:{clean.get('domain')}"]
        out.append(clean)
    return out


@app.post("/api/recall")
def recall(req: RecallRequest) -> dict[str, Any]:
    if not req.query.strip():
        raise HTTPException(400, "query required")
    namespace = req.namespace or DEFAULT_NAMESPACE
    top_k = max(1, min(req.top_k, 50))
    if is_all_namespaces(namespace):
        fallback = sql_recall_fallback(req.query, namespace, top_k, req.domain, req.source)
        return {
            "results": fallback,
            "fallback": "fts5_keyword",
            "namespace": "__all__",
            "certainty_reasons": ["All-namespaces recall uses SQL/FTS fallback because YantrikDB semantic recall is namespace-scoped."],
        }
    try:
        res = engine().recall_with_response(
            query=req.query,
            top_k=top_k,
            namespace=namespace,
            domain=req.domain or None,
            source=req.source or None,
            include_consolidated=req.include_consolidated,
            expand_entities=req.expand_entities,
            skip_reinforce=True,
        )
        data = res if isinstance(res, dict) else json.loads(json.dumps(res, default=lambda o: getattr(o, "__dict__", str(o))))
        if data.get("results"):
            return data
        fallback = sql_recall_fallback(req.query, namespace, top_k, req.domain, req.source)
        if fallback:
            data["results"] = fallback
            data["fallback"] = "fts5_keyword"
            data.setdefault("certainty_reasons", []).append("Semantic recall returned no hits; showing FTS/keyword matches")
        return data
    except Exception as e:
        try:
            items = engine().recall_text(req.query, top_k=top_k, namespace=namespace, domain=req.domain or None, source=req.source or None)
            results = list(items) if items else []
            if not results:
                results = sql_recall_fallback(req.query, namespace, top_k, req.domain, req.source)
            return {"results": results, "error": str(e), "fallback": True}
        except Exception as e2:
            fallback = sql_recall_fallback(req.query, namespace, top_k, req.domain, req.source)
            if fallback:
                return {"results": fallback, "error": f"semantic recall failed: {e}; recall_text failed: {e2}", "fallback": "fts5_keyword"}
            raise HTTPException(500, f"recall failed: {e}; fallback failed: {e2}")


@app.get("/api/conflicts")
def conflicts(namespace: str = Query(DEFAULT_NAMESPACE), status: str = "", limit: int = Query(50, le=200)) -> dict[str, Any]:
    try:
        items = engine().get_conflicts(namespace=namespace, status=status or None, limit=limit)
        return {"items": list(items) if items else []}
    except Exception:
        if not table_exists("conflicts"):
            return {"items": []}
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        return {"items": rows(f"SELECT * FROM conflicts {where} ORDER BY priority DESC, created_at DESC LIMIT ?", tuple(params + [limit]))}


@app.get("/api/conflicts/{conflict_id}")
def conflict_detail(conflict_id: str) -> dict[str, Any]:
    try:
        c = engine().get_conflict(conflict_id)
        return c if isinstance(c, dict) else {"conflict": c}
    except Exception:
        c = one("SELECT * FROM conflicts WHERE conflict_id=? OR id=?", (conflict_id, conflict_id))
        if not c:
            raise HTTPException(404, "conflict not found")
        return c


@app.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: str, req: ResolveRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        out = engine().resolve_conflict(conflict_id, req.strategy, winner_rid=req.winner_rid, new_text=req.new_text, resolution_note=req.resolution_note)
        return out if isinstance(out, dict) else {"ok": True, "result": out}
    except Exception as e:
        raise HTTPException(500, f"resolve failed: {e}")


@app.post("/api/think")
def run_think(req: ThinkRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    cfg = req.model_dump(exclude_none=True)
    try:
        out = engine().think(cfg)
        return out if isinstance(out, dict) else {"ok": True, "result": out}
    except Exception as e:
        raise HTTPException(500, f"think failed: {e}")


@app.post("/api/memory/{rid}/forget")
def forget_memory(rid: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        found = bool(engine().forget(rid))
        return {"rid": rid, "found": found}
    except Exception as e:
        raise HTTPException(500, f"forget failed: {e}")


def _category_for_text(text: str) -> str:
    lower = (text or "").lower()
    buckets = [
        ("People", ["person", "people", "user", "team", "customer", "client", "family", "friend"]),
        ("Memory", ["yantrik", "mnemosyne", "memory", "recall", "dashboard", "graph", "entity"]),
        ("Hermes", ["hermes", "plugin", "gateway", "whatsapp", "telegram", "cron", "skill"]),
        ("Home", ["home", "house", "kitchen", "room", "car", "whoop", "health", "assistant"]),
        ("Work", ["promptlybuilt", "business", "marketing", "github", "repo", "code", "project"]),
    ]
    for label, terms in buckets:
        if any(t in lower for t in terms):
            return label
    return "Other"


_STOP_TERMS = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "about", "memory", "memories",
    "dashboard", "yantrikdb", "hermes", "agent", "assistant", "should", "would", "could", "there", "their",
    "what", "when", "where", "which", "into", "using", "local", "default", "active", "current",
    "general", "user", "assistant", "semantic", "involving", "april", "january", "february", "march", "may", "june", "july", "august", "september", "october", "november", "december",
}


def _entity_terms(text: str, limit: int = 4) -> list[str]:
    text = text or ""
    terms: list[str] = []
    # Prefer proper nouns / product-ish tokens first.
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9_./-]{2,}\b|\b[A-Za-z]+(?:DB|AI|LLM|API|CLI|TTS|GPU)\b", text):
        term = match.group(0).strip(".,:;()[]{}'\"")
        if term and term.lower() not in _STOP_TERMS and term not in terms:
            terms.append(term[:54])
        if len(terms) >= limit:
            return terms
    # Then salient lowercase tokens.
    counts = Counter(t.lower() for t in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{4,}\b", text))
    for term, _count in counts.most_common(limit * 3):
        if term not in _STOP_TERMS and term not in terms:
            terms.append(term[:54])
        if len(terms) >= limit:
            break
    return terms


@app.get("/api/constellation")
def constellation(namespace: str = Query(DEFAULT_NAMESPACE), limit: int = Query(240, ge=40, le=600)) -> dict[str, Any]:
    """Mnemosyne-style visualiser payload derived from YantrikDB memories.

    YantrikDB's explicit entity/edge tables may be empty, so this builds a read-only
    visual map from high-importance/recent memories plus lightweight local term
    extraction. No external calls, no DB mutation.
    """
    clauses, params = namespace_clause("namespace", namespace)
    clauses.append("consolidation_status IN ('active','consolidated')")
    where_sql = " AND ".join(clauses)
    memory_limit = min(limit, 320 if is_all_namespaces(namespace) else 180)
    if is_all_namespaces(namespace):
        scope_rows = rows(
            f"""
            SELECT namespace, COUNT(*) AS count
            FROM memories
            WHERE {where_sql}
            GROUP BY namespace
            ORDER BY count DESC
            LIMIT 12
            """,
            tuple(params),
        )
        scope_values = [str(r.get("namespace") or "") for r in scope_rows if r.get("namespace")]
        per_scope = max(24, min(90, memory_limit // max(1, len(scope_values))))
        raw_memories: list[dict[str, Any]] = []
        for scope in scope_values:
            raw_memories.extend(rows(
                """
                SELECT rid,text,domain,source,type,importance,created_at,updated_at,access_count,consolidation_status,namespace
                FROM memories
                WHERE namespace=? AND consolidation_status IN ('active','consolidated')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (scope, per_scope),
            ))
        memories = [clean_row(m) for m in raw_memories[:memory_limit]]
    else:
        memories = [clean_row(m) for m in rows(
            f"""
            SELECT rid,text,domain,source,type,importance,created_at,updated_at,access_count,consolidation_status,namespace
            FROM memories
            WHERE {where_sql}
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
            """,
            (*params, memory_limit),
        )]
    all_scope = is_all_namespaces(namespace)
    nodes_by_key: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def scope_label(value: str) -> str:
        value = str(value or "unknown scope")
        if ":owner:" in value:
            return "owner:" + value.rsplit(":owner:", 1)[-1]
        return value.rsplit(":", 1)[-1] if ":" in value else value

    def touch(
        label: str,
        kind: str = "entity",
        weight: float = 1.0,
        category: str = "Other",
        rid: str = "",
        preview: str = "",
        scope: str = "",
        semantic_category: str = "",
    ) -> dict[str, Any]:
        label = str(label or "unknown").strip()[:80] or "unknown"
        scope = str(scope or "")
        # In All namespaces mode, namespace is part of node identity. Otherwise
        # shared labels like "user" or "general" collapse every namespace into
        # one graph, which hides the separate memory scopes.
        key = f"{scope}::{kind}::{label}" if all_scope and kind != "namespace" else f"{kind}::{label}"
        node = nodes_by_key.setdefault(key, {
            "id": f"n{len(nodes_by_key)+1}", "label": label, "kind": kind, "category": category,
            "namespace": scope or None, "scope_label": scope_label(scope) if scope else None,
            "semantic_category": semantic_category or category,
            "weight": 0.0, "count": 0, "memory_id": rid, "preview": preview,
        })
        node["weight"] = round(float(node.get("weight") or 0) + max(0.1, float(weight or 0.1)), 3)
        node["count"] = int(node.get("count") or 0) + 1
        if kind == "memory":
            node["memory_id"] = rid
            node["preview"] = preview[:220]
        if node.get("category") == "Other" and category != "Other":
            node["category"] = category
        return node

    for m in memories:
        text = str(m.get("text") or "")
        rid = str(m.get("rid") or "")
        scope = str(m.get("namespace") or namespace or DEFAULT_NAMESPACE)
        semantic_category = _category_for_text(" ".join([text, str(m.get("domain") or ""), str(m.get("source") or "")]))
        graph_category = scope_label(scope) if all_scope else semantic_category
        importance = float(m.get("importance") or 0.35)
        m_node = touch(f"memory:{rid[:8]}…{rid[-6:]}", kind="memory", weight=importance * 1.8, category=graph_category, rid=rid, preview=text, scope=scope, semantic_category=semantic_category)
        seeds = [m.get("domain"), m.get("source"), *_entity_terms(text, limit=4)]
        if all_scope:
            scope_node = touch(scope_label(scope), kind="namespace", weight=max(0.35, importance * 1.2), category=graph_category, scope=scope, semantic_category="Namespace")
            edges.append({"id": f"e{len(edges)+1}", "source": scope_node["id"], "target": m_node["id"], "label": "contains", "kind": "scope", "item": {"rid": rid, "namespace": scope}})
        seen: set[str] = set()
        for raw in seeds:
            entity = str(raw or "").strip()[:54]
            if not entity or entity.lower() in _STOP_TERMS or entity.lower() in seen:
                continue
            seen.add(entity.lower())
            e_node = touch(entity, kind="entity", weight=max(0.25, importance), category=graph_category, scope=scope, semantic_category=semantic_category)
            edges.append({"id": f"e{len(edges)+1}", "source": m_node["id"], "target": e_node["id"], "label": "mentions", "kind": "memory", "item": {"rid": rid, "entity": entity, "namespace": scope}})

    ranked_nodes = sorted(nodes_by_key.values(), key=lambda n: (float(n.get("weight") or 0), int(n.get("count") or 0)), reverse=True)
    if all_scope:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in ranked_nodes:
            grouped[str(node.get("category") or "Other")].append(node)
        per_group = max(10, limit // max(1, len(grouped)))
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        for group_nodes in grouped.values():
            # Keep each namespace hub, then enough local nodes to make the subgraph visible.
            group_pick = sorted(group_nodes, key=lambda n: (n.get("kind") == "namespace", float(n.get("weight") or 0), int(n.get("count") or 0)), reverse=True)[:per_group]
            for node in group_pick:
                if node["id"] not in selected_ids:
                    selected.append(node); selected_ids.add(node["id"])
        for node in ranked_nodes:
            if len(selected) >= limit:
                break
            if node["id"] not in selected_ids:
                selected.append(node); selected_ids.add(node["id"])
        nodes = selected[:limit]
    else:
        nodes = ranked_nodes[:limit]
    kept = {n["id"] for n in nodes}
    kept_edges = [e for e in edges if e["source"] in kept and e["target"] in kept]
    if all_scope:
        grouped_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in kept_edges:
            grouped_edges[str((edge.get("item") or {}).get("namespace") or "Other")].append(edge)
        edge_limit = limit * 2
        per_edge_group = max(8, edge_limit // max(1, len(grouped_edges)))
        selected_edges: list[dict[str, Any]] = []
        seen_edges: set[str] = set()
        for group_edges in grouped_edges.values():
            for edge in group_edges[:per_edge_group]:
                if edge["id"] not in seen_edges:
                    selected_edges.append(edge); seen_edges.add(edge["id"])
        for edge in kept_edges:
            if len(selected_edges) >= edge_limit:
                break
            if edge["id"] not in seen_edges:
                selected_edges.append(edge); seen_edges.add(edge["id"])
        edges = selected_edges[:edge_limit]
    else:
        edges = kept_edges[:limit * 2]
    clusters = Counter(str(n.get("category") or "Other") for n in nodes)
    return {"read_only": True, "all_namespaces": all_scope, "nodes": nodes, "edges": edges, "clusters": [{"label": k, "count": v} for k, v in clusters.most_common()]}


@app.get("/api/entities")
def entities(q: str = "", limit: int = Query(50, le=200)) -> dict[str, Any]:
    if not table_exists("entities"):
        return {"items": []}
    if q:
        data = rows("SELECT * FROM entities WHERE name LIKE ? ORDER BY mention_count DESC LIMIT ?", (f"%{q}%", limit))
    else:
        data = rows("SELECT * FROM entities ORDER BY mention_count DESC LIMIT ?", (limit,))
    return {"items": data}


@app.get("/api/graph/{entity}")
def graph(entity: str, namespace: str = Query(DEFAULT_NAMESPACE)) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    try:
        rels = engine().get_edges(entity)
        for r in list(rels) if rels else []:
            d = dict(r) if isinstance(r, dict) else {"raw": str(r)}
            src = d.get("src") or d.get("source") or entity
            dst = d.get("dst") or d.get("target") or d.get("entity") or "unknown"
            nodes[src] = {"id": src, "label": src}
            nodes[dst] = {"id": dst, "label": dst}
            edges.append({"source": src, "target": dst, "type": d.get("rel_type") or d.get("relationship") or d.get("type", "related"), "weight": d.get("weight", 1)})
    except Exception:
        if table_exists("edges"):
            for d in rows("SELECT * FROM edges WHERE src=? OR dst=? OR source=? OR target=? LIMIT 100", (entity, entity, entity, entity)):
                src = d.get("src") or d.get("source") or entity
                dst = d.get("dst") or d.get("target") or entity
                nodes[src] = {"id": src, "label": src}
                nodes[dst] = {"id": dst, "label": dst}
                edges.append({"source": src, "target": dst, "type": d.get("rel_type") or d.get("relationship") or "related", "weight": d.get("weight", 1)})
    related_memories = rows(
        """
        SELECT m.rid,m.text,m.domain,m.importance,m.created_at,m.consolidation_status
        FROM memories m JOIN memory_entities me ON m.rid=me.memory_rid
        WHERE me.entity_name=? AND m.namespace=? ORDER BY m.created_at DESC LIMIT 20
        """,
        (entity, namespace),
    ) if table_exists("memory_entities") else []
    return {"entity": entity, "nodes": list(nodes.values()), "edges": edges, "memories": [clean_row(x) for x in related_memories]}


@app.get("/api/patterns")
def patterns(limit: int = Query(50, le=200)) -> dict[str, Any]:
    if not table_exists("patterns"):
        return {"items": []}
    return {"items": rows("SELECT * FROM patterns ORDER BY confidence DESC, created_at DESC LIMIT ?", (limit,))}


@app.get("/api/triggers")
def triggers(limit: int = Query(50, le=200)) -> dict[str, Any]:
    if not table_exists("trigger_log"):
        return {"items": []}
    return {"items": rows("SELECT * FROM trigger_log ORDER BY created_at DESC LIMIT ?", (limit,))}


@app.get("/api/sessions")
def sessions(namespace: str = Query(DEFAULT_NAMESPACE), limit: int = Query(50, le=200)) -> dict[str, Any]:
    if not table_exists("sessions"):
        return {"items": []}
    cols = [r["name"] for r in rows("PRAGMA table_info(sessions)")]
    if "namespace" in cols:
        return {"items": rows("SELECT * FROM sessions WHERE namespace=? ORDER BY started_at DESC LIMIT ?", (namespace, limit))}
    return {"items": rows("SELECT * FROM sessions ORDER BY rowid DESC LIMIT ?", (limit,))}


@app.get("/api/stale")
def stale(namespace: str = Query(DEFAULT_NAMESPACE), days: float = 30.0, limit: int = Query(50, le=200)) -> dict[str, Any]:
    try:
        items = engine().stale(days=days, limit=limit, namespace=namespace)
        return {"items": list(items) if items else []}
    except Exception as e:
        cutoff = now() - days * 86400
        return {"items": [clean_row(x) for x in rows("SELECT rid,text,domain,importance,last_access,created_at FROM memories WHERE namespace=? AND consolidation_status='active' AND last_access < ? ORDER BY last_access ASC LIMIT ?", (namespace, cutoff, limit))], "fallback": True, "error": str(e)}


@app.get("/api/upcoming")
def upcoming(namespace: str = Query(DEFAULT_NAMESPACE), days: float = 7.0, limit: int = Query(50, le=200)) -> dict[str, Any]:
    try:
        items = engine().upcoming(days=days, limit=limit, namespace=namespace)
        return {"items": list(items) if items else []}
    except Exception as e:
        end = now() + days * 86400
        return {"items": [clean_row(x) for x in rows("SELECT rid,text,domain,importance,due_at,temporal_kind FROM memories WHERE namespace=? AND due_at IS NOT NULL AND due_at <= ? ORDER BY due_at ASC LIMIT ?", (namespace, end, limit))], "fallback": True, "error": str(e)}


@app.get("/api/export/memories.jsonl")
def export_memories(namespace: str = Query(DEFAULT_NAMESPACE), status: str = "active") -> StreamingResponse:
    def generate():
        offset = 0
        while True:
            batch = memories(namespace=namespace, status=status, limit=200, offset=offset)["items"]
            if not batch:
                break
            for item in batch:
                yield json.dumps(item, ensure_ascii=False) + "\n"
            offset += 200
    return StreamingResponse(generate(), media_type="application/x-ndjson", headers={"Content-Disposition": "attachment; filename=yantrikdb-memories.jsonl"})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": str(exc), "path": str(request.url)})


def main() -> None:
    import uvicorn
    host = os.environ.get("YANTRIKDB_DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("YANTRIKDB_DASHBOARD_PORT", "8767"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
