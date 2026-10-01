"""Disposable-store coverage for the raw SQL process boundary."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

import app as dashboard
import sqlite_reader


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "memories.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE memories (rid TEXT, embedding BLOB)")
        conn.execute("INSERT INTO memories VALUES (?, ?)", ("rid-1", b"\x00\xff" * 8))
        conn.commit()
    monkeypatch.setattr(dashboard, "DB_PATH", path)
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)
    return path


def test_reads_never_open_sqlite_in_dashboard_process(store, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("stdlib sqlite3 opened a store in the dashboard process")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(dashboard, "_db_handle", object())
    monkeypatch.delenv("YANTRIKDB_EMBEDDING_DIM", raising=False)
    assert dashboard.rows("SELECT rid FROM memories") == [{"rid": "rid-1"}]
    assert dashboard.one("SELECT rid FROM memories") == {"rid": "rid-1"}
    assert dashboard.rows("PRAGMA table_info(memories)")
    assert dashboard.infer_embedding_dim() == 4


@pytest.mark.parametrize("helper", [dashboard.rows, dashboard.one])
def test_writes_are_refused_and_subsequent_reads_succeed(store, helper):
    with pytest.raises(sqlite3.OperationalError, match="readonly database") as exc:
        helper("INSERT INTO memories VALUES ('rid-2', NULL)")
    assert exc.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
    assert exc.value.sqlite_errorname == "SQLITE_READONLY"
    assert dashboard.one("SELECT count(*) AS n FROM memories") == {"n": 1}


@pytest.mark.parametrize("filename", ["space name.db", "hash#name.db", "percent%23.db", "日本語.db"] + ([] if os.name == "nt" else ["question?mode=rw.db"]))
def test_uri_special_characters_and_relative_paths(tmp_path, monkeypatch, filename):
    path = tmp_path / filename
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE example (value TEXT)")
        conn.execute("INSERT INTO example VALUES ('right file')")
        conn.commit()
    monkeypatch.chdir(tmp_path)
    assert sqlite_reader.query(Path(filename), "SELECT value FROM example") == [{"value": "right file"}]
    with pytest.raises(sqlite3.OperationalError, match="readonly database"):
        sqlite_reader.query(Path(filename), "DELETE FROM example")


def test_committed_wal_rows_are_visible_while_writer_is_open(store):
    with closing(sqlite3.connect(store)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        for index in range(3):
            writer.execute("INSERT INTO memories VALUES (?, NULL)", (f"wal-{index}",))
            writer.commit()
            assert dashboard.one("SELECT count(*) AS n FROM memories") == {"n": index + 2}
        assert Path(str(store) + "-wal").stat().st_size > 0


@pytest.mark.parametrize("blob", [b"", b"\x00\xff", bytearray(b"\x00\xff"), memoryview(b"\x00\xff")])
def test_sqlite_values_and_blob_params_round_trip(store, blob):
    params = (None, -9223372036854775808, 1.25, "quoted \"雪\" \n text", blob, '{"blob":"not binary"}')
    result = sqlite_reader.query(store, "SELECT ? AS nil, ? AS integer, ? AS real, ? AS text, ? AS blob, ? AS json_text", params)
    assert result == [dict(zip(("nil", "integer", "real", "text", "blob", "json_text"), (*params[:4], bytes(blob), params[5])))]
    assert sqlite_reader.query(store, "SELECT ? AS blob", (blob,)) == [{"blob": bytes(blob)}]


def test_one_empty_and_first_row_contract(store):
    assert dashboard.one("SELECT rid FROM memories WHERE 0") is None
    assert dashboard.rows("SELECT rid FROM memories WHERE 0") == []
    assert dashboard.one("SELECT 1 AS value UNION ALL SELECT 2") == {"value": 1}
    assert dashboard.one("SELECT 1 AS duplicate, 2 AS duplicate") == {"duplicate": 1}


@pytest.mark.parametrize("sql, params, error", [
    ("SELECT * FROM absent", (), sqlite3.OperationalError),
    ("SELECT ?", (), sqlite3.ProgrammingError),
])
def test_query_errors_propagate_and_do_not_break_later_reads(store, sql, params, error):
    with pytest.raises(error):
        dashboard.rows(sql, params)
    assert dashboard.one("SELECT rid FROM memories") == {"rid": "rid-1"}


@pytest.mark.parametrize("fails", [False, True])
def test_worker_connection_is_closed_even_on_error(monkeypatch, fails):
    class Connection:
        closed = False
        def execute(self, sql, *args):
            if sql == "PRAGMA query_only=ON":
                return None
            if fails:
                raise sqlite3.OperationalError("bad query")
            return self
        def fetchall(self):
            return []
        def close(self):
            self.closed = True
    conn = Connection()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: conn)
    request = {"uri": "file:unused?mode=ro", "sql": "SELECT 1", "params": [], "first": False}
    if fails:
        with pytest.raises(sqlite3.OperationalError):
            sqlite_reader._read(request)
    else:
        assert sqlite_reader._read(request) == []
    assert conn.closed


def test_worker_fetches_only_one_when_requested(monkeypatch):
    class Connection:
        def execute(self, *args):
            return self
        def fetchone(self):
            return {"n": 1}
        def fetchall(self):
            pytest.fail("one() fetched the entire result set")
        def close(self):
            pass
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: Connection())
    assert sqlite_reader._read({"uri": "unused", "sql": "SELECT 1", "params": [], "first": True}) == [{"n": 1}]


def test_http_guard_and_missing_store_never_launch_a_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "read_sql", lambda *a, **kw: pytest.fail("reader started"))
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", object())
    for helper in (dashboard.rows, dashboard.one):
        with pytest.raises(dashboard.HTTPException) as exc:
            helper("SELECT 1")
        assert exc.value.status_code == 501
    monkeypatch.setattr(dashboard, "HTTP_BACKEND", None)
    path = tmp_path / "missing.db"
    monkeypatch.setattr(dashboard, "DB_PATH", path)
    with pytest.raises(dashboard.HTTPException) as exc:
        dashboard.rows("SELECT 1")
    assert exc.value.status_code == 500
    assert not path.exists()
    with pytest.raises(sqlite3.OperationalError):
        sqlite_reader.query(path, "SELECT 1")
    assert not path.exists()


@pytest.mark.parametrize("failure, message", [
    (subprocess.TimeoutExpired(["python"], 30), "timed out"),
    (subprocess.CalledProcessError(1, ["python"]), "subprocess failed"),
    (FileNotFoundError("missing worker"), "subprocess failed"),
])
def test_subprocess_failures_are_actionable(store, monkeypatch, failure, message):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(sqlite3.OperationalError, match=message):
        sqlite_reader.query(store, "SELECT 1")


@pytest.mark.parametrize("output", ["not json", "null", "{}", '{"rows":[null]}', '{"error":{}}'])
def test_malformed_response_is_rejected(store, monkeypatch, output):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=output))
    with pytest.raises(sqlite3.OperationalError, match="Invalid SQLite reader response"):
        sqlite_reader.query(store, "SELECT 1")


def test_worker_runs_isolated_without_sql_in_command_line(store, monkeypatch):
    real_run = subprocess.run
    def inspect_run(command, **kwargs):
        assert command[1:3] == ["-I", "-S"]
        assert Path(command[3]).is_absolute()
        assert Path(command[3]).name == "sqlite_reader.py"
        assert len(command) == 4
        assert kwargs["timeout"] == sqlite_reader.READER_TIMEOUT
        assert json.loads(kwargs["input"])["uri"].endswith("?mode=ro")
        return real_run(command, **kwargs)
    monkeypatch.setattr(subprocess, "run", inspect_run)
    assert dashboard.one("SELECT rid FROM memories") == {"rid": "rid-1"}


def test_parallel_reads_have_independent_workers(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: dashboard.one("SELECT count(*) AS n FROM memories"), range(12)))
    assert results == [{"n": 1}] * 12


def test_real_timeout_is_reaped_and_next_query_works(store, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(sqlite_reader, "READER_TIMEOUT", 0.1)
        with pytest.raises(sqlite3.OperationalError, match="timed out"):
            dashboard.rows("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n")
    assert dashboard.one("SELECT rid FROM memories") == {"rid": "rid-1"}


def test_live_yantrikdb_engine_remains_writable(tmp_path):
    """Optional wheel-backed check; subprocess keeps test import stubs out.

    The parent owns the temp directory, so even engines with background
    references finish at child exit before cleanup (including on Windows).
    """
    import importlib.metadata
    import sys
    try:
        importlib.metadata.version("yantrikdb")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("install yantrikdb==0.23.1 to run the live-engine check")
    script = r'''
import sqlite3
import sys
import time
from pathlib import Path
import yantrikdb
import app as dashboard
path = Path(sys.argv[1])
db = yantrikdb.YantrikDB(str(path), embedding_dim=4)
dashboard.DB_PATH = path
dashboard.HTTP_BACKEND = None
dashboard._db_handle = db

def forbidden(*args, **kwargs):
    raise AssertionError("stdlib SQLite opened in engine process")
sqlite3.connect = forbidden
try:
    for index in range(5):
        rid = dashboard.engine().record(
            f"synthetic safety check {index}",
            embedding=[1.0, 0.0, 0.0, 0.0], namespace="test",
        )
        deadline = time.monotonic() + 5
        while not dashboard.one("SELECT rid FROM memories WHERE rid=?", (rid,)):
            assert time.monotonic() < deadline, "engine row did not materialize"
            time.sleep(0.01)
        assert dashboard.rows("PRAGMA table_info(memories)")
        assert db.get(rid) is not None
    stats = db.stats(namespace="test")
    assert stats["active_memories"] == 5
    assert stats["foreign_sqlite_mode"] == "refuse"
    assert not stats["foreign_sqlite_active"]
    assert not stats["foreign_sqlite_tainted"]
    assert stats["foreign_sqlite_detected_since_boot"] == 0
    assert stats["foreign_sqlite_refused_since_boot"] == 0
    assert stats["foreign_commits_detected_since_boot"] == 0
    assert db.integrity_check() == "ok"
finally:
    db.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "engine # % 雪.db")],
        cwd=Path(dashboard.__file__).parent, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
