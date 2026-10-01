"""Read the engine store using a fresh, read-only SQLite subprocess.

Never open this store with stdlib sqlite3 in the dashboard process: it can
already hold YantrikDB's bundled SQLite library. Even read-only connections
violate upstream CONCURRENCY.md rule 9 on Linux/macOS.
"""
from __future__ import annotations

import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any


READER_TIMEOUT = 30.0


def _encode_value(value: Any) -> Any:
    # SQLite values are scalar, so a tagged object cannot collide with a
    # stored value (even JSON text or a column named "blob").
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"blob": base64.b64encode(value).decode("ascii")}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        return base64.b64decode(value["blob"], validate=True)
    return value


def query(path: Path, sql: str, params: tuple[Any, ...] = (), *, first: bool = False) -> list[dict[str, Any]]:
    """Execute one read outside this process; return detached Python values.

    A new interpreter (not multiprocessing/fork) cannot inherit the engine's
    live connections. -I -S also prevents PYTHONPATH/site startup hooks from
    loading the engine. SQL, bindings, and database paths travel over stdin,
    not the process command line. run() kills and reaps a timed-out child.
    """
    request = {"uri": path.resolve().as_uri() + "?mode=ro", "sql": sql,
               "params": [_encode_value(value) for value in params], "first": first}
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(Path(__file__).resolve())],
            input=json.dumps(request), capture_output=True, text=True,
            encoding="utf-8", timeout=READER_TIMEOUT, check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise sqlite3.OperationalError("SQLite reader timed out") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise sqlite3.OperationalError("SQLite reader subprocess failed") from exc
    try:
        response = json.loads(result.stdout)
        if not isinstance(response, dict):
            raise ValueError("expected an object")
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict) or not all(isinstance(error.get(key), str) for key in ("type", "message")):
                raise ValueError("invalid error")
        elif not isinstance(response.get("rows"), list) or not all(isinstance(row, dict) for row in response["rows"]):
            raise ValueError("invalid rows")
    except ValueError as exc:
        raise sqlite3.OperationalError("Invalid SQLite reader response") from exc
    if "error" in response:
        error = response["error"]
        error_type = getattr(sqlite3, error["type"], sqlite3.DatabaseError)
        if not isinstance(error_type, type) or not issubclass(error_type, sqlite3.Error):
            error_type = sqlite3.DatabaseError
        exc = error_type(error["message"])
        for attr in ("sqlite_errorcode", "sqlite_errorname"):
            if attr in error:
                setattr(exc, attr, error[attr])
        raise exc
    return [{key: _decode_value(value) for key, value in row.items()} for row in response["rows"]]


def _read(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Worker-only SQL execution. The connection closes even on query errors."""
    with closing(sqlite3.connect(request["uri"], uri=True, timeout=5.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        with closing(conn.execute(request["sql"], tuple(_decode_value(v) for v in request["params"]))) as cursor:
            if request["first"]:
                row = cursor.fetchone()
                records = [] if row is None else [row]
            else:
                records = cursor.fetchall()
            return [{key: _encode_value(value) for key, value in dict(row).items()} for row in records]


def _main() -> None:
    # This interpreter is deliberately stdlib-only. Do not import app,
    # yantrikdb, plugins, or site packages here.
    request = json.load(sys.stdin)
    try:
        response = {"rows": _read(request)}
    except sqlite3.Error as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        for attr in ("sqlite_errorcode", "sqlite_errorname"):
            if hasattr(exc, attr):
                error[attr] = getattr(exc, attr)
        response = {"error": error}
    json.dump(response, sys.stdout)


if __name__ == "__main__":
    _main()
