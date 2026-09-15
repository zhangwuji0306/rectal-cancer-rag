# -*- coding: utf-8 -*-
"""A1: tasks.sqlite Schema v2 migration and verification.

The migration is intentionally additive. Existing task columns used by the
legacy downloader (``pmc``, ``attempts``, ``route``, ``last_error`` and
``pdf_path``) remain in place while v2 columns receive canonical names.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 2
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = SCRIPT_DIR.parent / "tasks.sqlite"


class SchemaError(RuntimeError):
    """Raised when a database cannot be safely upgraded or verified."""


class RollbackError(RuntimeError):
    """Raised when rollback would discard provenance or cannot be structural."""


V2_COLUMNS = {
    "pmcid": "TEXT",
    "journal": "TEXT",
    "issn": "TEXT",
    "authors": "TEXT",
    "first_author": "TEXT",
    "first_author_family": "TEXT",
    "pub_type": "TEXT",
    "language": "TEXT",
    "oa_status": "TEXT",
    "reuse_allowed": "INTEGER",
    "content_status": "TEXT",
    "content_source": "TEXT",
    "content_format": "TEXT",
    "content_path": "TEXT",
    "source_url": "TEXT",
    "content_sha256": "TEXT",
    "metadata_updated_at": "TEXT",
    "source_updated_at": "TEXT",
    "retrieved_at": "TEXT",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "last_error_class": "TEXT",
    "last_error_detail": "TEXT",
    "next_retry_at": "TEXT",
    "worker_id": "TEXT",
    "lease_until": "TEXT",
    "heartbeat_at": "TEXT",
    "retracted": "INTEGER NOT NULL DEFAULT 0",
    "excluded_reason": "TEXT",
    "created_at": "TEXT",
}

LEGACY_COLUMNS = {
    "doi": "TEXT",
    "pmc": "TEXT",
    "year": "INTEGER",
    "title": "TEXT",
    "title_norm": "TEXT",
    "status": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "route": "TEXT",
    "last_error": "TEXT",
    "pdf_path": "TEXT",
    "license": "TEXT NOT NULL DEFAULT 'unverified'",
    "updated_at": "TEXT",
}

REQUIRED_V2_COLUMNS = {
    "pmid", "doi", "pmcid", "title", "title_norm", "year", "journal", "issn",
    "authors", "first_author", "first_author_family", "pub_type", "language",
    "oa_status", "license", "reuse_allowed", "content_status", "content_source",
    "content_format", "content_path", "source_url", "content_sha256",
    "metadata_updated_at", "source_updated_at", "retrieved_at", "attempt_count",
    "status", "last_error_class", "last_error_detail", "next_retry_at", "worker_id",
    "lease_until", "heartbeat_at", "retracted", "excluded_reason", "created_at",
    "updated_at",
}

FETCH_ATTEMPT_COLUMNS = {
    "attempt_id", "pmid", "source", "route", "url", "identifier", "started_at",
    "finished_at", "http_status", "outcome", "error_class", "error_detail",
    "retryable", "retry_after", "content_type", "content_length", "worker_id",
}
SOURCE_CANDIDATE_COLUMNS = {
    "candidate_id", "pmid", "source", "url", "format", "version", "license",
    "is_oa", "reuse_allowed", "priority", "resolved_at",
}
DISCOVERY_BATCH_COLUMNS = {
    "batch_id", "source", "query_text", "query_hash", "searched_at", "imported_at",
    "input_sha256", "note",
}
TASK_DISCOVERY_COLUMNS = {"batch_id", "pmid", "discovered_at"}


def utc_now() -> str:
    """Return a stable, timezone-aware timestamp for new provenance rows."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: os.PathLike[str] | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _create_empty_tasks(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE tasks (
            pmid INTEGER PRIMARY KEY,
            doi TEXT, pmcid TEXT, title TEXT, title_norm TEXT, year INTEGER,
            journal TEXT, issn TEXT, authors TEXT, first_author TEXT,
            first_author_family TEXT, pub_type TEXT, language TEXT, oa_status TEXT,
            license TEXT NOT NULL DEFAULT 'unverified', reuse_allowed INTEGER,
            content_status TEXT, content_source TEXT, content_format TEXT,
            content_path TEXT, source_url TEXT, content_sha256 TEXT,
            metadata_updated_at TEXT, source_updated_at TEXT, retrieved_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            last_error_class TEXT, last_error_detail TEXT, next_retry_at TEXT,
            worker_id TEXT, lease_until TEXT, heartbeat_at TEXT,
            retracted INTEGER NOT NULL DEFAULT 0, excluded_reason TEXT,
            created_at TEXT, updated_at TEXT,
            pmc TEXT, attempts INTEGER NOT NULL DEFAULT 0, route TEXT,
            last_error TEXT, pdf_path TEXT
        )"""
    )


def _create_provenance_tables(conn: sqlite3.Connection) -> list[str]:
    created: list[str] = []
    if not _table_exists(conn, "fetch_attempts"):
        conn.execute(
            """CREATE TABLE fetch_attempts (
                attempt_id TEXT PRIMARY KEY,
                pmid INTEGER NOT NULL REFERENCES tasks(pmid),
                source TEXT NOT NULL, route TEXT, url TEXT, identifier TEXT,
                started_at TEXT NOT NULL, finished_at TEXT, http_status INTEGER,
                outcome TEXT, error_class TEXT, error_detail TEXT, retryable INTEGER,
                retry_after TEXT, content_type TEXT, content_length INTEGER, worker_id TEXT
            )"""
        )
        created.append("fetch_attempts")
    if not _table_exists(conn, "source_candidates"):
        conn.execute(
            """CREATE TABLE source_candidates (
                candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                pmid INTEGER NOT NULL REFERENCES tasks(pmid),
                source TEXT NOT NULL, url TEXT, format TEXT, version TEXT, license TEXT,
                is_oa INTEGER, reuse_allowed INTEGER, priority INTEGER, resolved_at TEXT
            )"""
        )
        created.append("source_candidates")
    if not _table_exists(conn, "discovery_batches"):
        conn.execute(
            """CREATE TABLE discovery_batches (
                batch_id TEXT PRIMARY KEY, source TEXT NOT NULL, query_text TEXT,
                query_hash TEXT, searched_at TEXT NOT NULL, imported_at TEXT NOT NULL,
                input_sha256 TEXT NOT NULL, note TEXT
            )"""
        )
        created.append("discovery_batches")
    if not _table_exists(conn, "task_discoveries"):
        conn.execute(
            """CREATE TABLE task_discoveries (
                batch_id TEXT NOT NULL REFERENCES discovery_batches(batch_id),
                pmid INTEGER NOT NULL REFERENCES tasks(pmid),
                discovered_at TEXT NOT NULL, PRIMARY KEY (batch_id, pmid)
            )"""
        )
        created.append("task_discoveries")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fetch_attempts_pmid ON fetch_attempts(pmid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_candidates_pmid ON source_candidates(pmid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_discoveries_pmid ON task_discoveries(pmid)")
    return created


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "schema_version"):
        conn.execute(
            """CREATE TABLE schema_version (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL, applied_at TEXT NOT NULL,
                previous_version INTEGER NOT NULL, task_count_before INTEGER NOT NULL,
                task_columns_added TEXT NOT NULL, provenance_tables_created TEXT NOT NULL
            )"""
        )
        return
    columns = _columns(conn, "schema_version")
    if "version" not in columns:
        raise SchemaError("schema_version exists but has no version column")
    additions = {
        "singleton": "INTEGER", "applied_at": "TEXT", "previous_version": "INTEGER",
        "task_count_before": "INTEGER", "task_columns_added": "TEXT",
        "provenance_tables_created": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE schema_version ADD COLUMN "{name}" {definition}')
    row = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    if row["n"]:
        conn.execute(
            "UPDATE schema_version SET singleton=COALESCE(singleton,1), "
            "applied_at=COALESCE(applied_at,?), previous_version=COALESCE(previous_version,0), "
            "task_count_before=COALESCE(task_count_before,0), "
            "task_columns_added=COALESCE(task_columns_added,'[]'), "
            "provenance_tables_created=COALESCE(provenance_tables_created,'[]')",
            (utc_now(),),
        )


def get_schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_version"):
        return 0
    row = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
    return int(row["version"] or 0)


def _assert_primary_pmid(conn: sqlite3.Connection) -> None:
    pmid = next((row for row in conn.execute("PRAGMA table_info(tasks)") if row[1] == "pmid"), None)
    if pmid is None or pmid[5] != 1:
        raise SchemaError("tasks.pmid must remain the INTEGER PRIMARY KEY")


def verify_schema_v2(conn: sqlite3.Connection) -> dict[str, int | bool]:
    """Verify the A1 contract against a live connection without changing it."""

    if not _table_exists(conn, "tasks"):
        raise SchemaError("tasks table is missing")
    _assert_primary_pmid(conn)
    missing = REQUIRED_V2_COLUMNS - _columns(conn, "tasks")
    if missing:
        raise SchemaError("tasks missing v2 columns: " + ", ".join(sorted(missing)))
    for table, expected in (
        ("fetch_attempts", FETCH_ATTEMPT_COLUMNS),
        ("source_candidates", SOURCE_CANDIDATE_COLUMNS),
        ("discovery_batches", DISCOVERY_BATCH_COLUMNS),
        ("task_discoveries", TASK_DISCOVERY_COLUMNS),
    ):
        if not _table_exists(conn, table):
            raise SchemaError(f"{table} table is missing")
        missing = expected - _columns(conn, table)
        if missing:
            raise SchemaError(f"{table} missing columns: " + ", ".join(sorted(missing)))
    version = get_schema_version(conn)
    if version != SCHEMA_VERSION:
        raise SchemaError(f"schema_version is {version}, expected {SCHEMA_VERSION}")
    if int(conn.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
        raise SchemaError("PRAGMA user_version is not Schema v2")
    count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    distinct = conn.execute("SELECT COUNT(DISTINCT pmid) FROM tasks").fetchone()[0]
    if count != distinct:
        raise SchemaError("tasks.pmid is not unique")
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise SchemaError(f"foreign_key_check failed: {foreign_key_errors[:3]}")
    return {"tasks": count, "distinct_pmids": distinct, "verified": True}


def migrate_to_v2(db_path: os.PathLike[str] | str = DEFAULT_DB_PATH) -> dict[str, int | bool]:
    """Apply the additive A1 migration in one explicit transaction."""

    conn = _connect(db_path)
    before = 0
    added_columns: list[str] = []
    created_tables: list[str] = []
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        current = get_schema_version(conn)
        if current > SCHEMA_VERSION:
            raise SchemaError(f"database schema version {current} is newer than A1")
        if current == SCHEMA_VERSION:
            result = verify_schema_v2(conn)
            conn.execute("COMMIT")
            return result
        if not _table_exists(conn, "tasks"):
            _create_empty_tasks(conn)
        else:
            _assert_primary_pmid(conn)
            existing = _columns(conn, "tasks")
            additions = dict(LEGACY_COLUMNS)
            additions.update(V2_COLUMNS)
            for name, definition in additions.items():
                if name not in existing:
                    conn.execute(f'ALTER TABLE tasks ADD COLUMN "{name}" {definition}')
                    added_columns.append(name)

        before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        columns = _columns(conn, "tasks")
        if "pmcid" in columns and "pmc" in columns:
            conn.execute("UPDATE tasks SET pmcid=pmc WHERE pmcid IS NULL AND pmc IS NOT NULL")
        if "attempt_count" in columns and "attempts" in columns:
            conn.execute(
                "UPDATE tasks SET attempt_count=attempts "
                "WHERE COALESCE(attempt_count,0)=0 AND COALESCE(attempts,0)<>0"
            )
        if "content_source" in columns and "route" in columns:
            conn.execute("UPDATE tasks SET content_source=route WHERE content_source IS NULL AND route IS NOT NULL")
        if "last_error_detail" in columns and "last_error" in columns:
            conn.execute("UPDATE tasks SET last_error_detail=last_error WHERE last_error_detail IS NULL AND last_error IS NOT NULL")
        if "content_path" in columns and "pdf_path" in columns:
            conn.execute("UPDATE tasks SET content_path=pdf_path WHERE content_path IS NULL AND pdf_path IS NOT NULL")
        if "created_at" in columns and "updated_at" in columns:
            conn.execute("UPDATE tasks SET created_at=updated_at WHERE created_at IS NULL AND updated_at IS NOT NULL")

        created_tables = _create_provenance_tables(conn)
        _ensure_schema_version_table(conn)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            """INSERT INTO schema_version
                (singleton, version, applied_at, previous_version, task_count_before,
                 task_columns_added, provenance_tables_created)
                VALUES (1, ?, ?, ?, ?, ?, ?)""",
            (SCHEMA_VERSION, utc_now(), current, before,
             json.dumps(added_columns, ensure_ascii=False),
             json.dumps(created_tables, ensure_ascii=False)),
        )
        result = verify_schema_v2(conn)
        after = int(result["tasks"])
        if after != before:
            raise SchemaError(f"task row count changed during migration: {before} -> {after}")
        conn.execute("COMMIT")
        result["before"] = before
        result["after"] = after
        return result
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def rollback_schema(db_path: os.PathLike[str] | str = DEFAULT_DB_PATH) -> dict[str, int | bool]:
    """Rollback A1 additions without rebuilding ``tasks`` or losing provenance."""

    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        if get_schema_version(conn) != SCHEMA_VERSION:
            raise RollbackError("database is not at Schema v2")
        row = conn.execute(
            "SELECT task_count_before, task_columns_added, provenance_tables_created "
            "FROM schema_version WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RollbackError("schema_version migration record is incomplete")
        provenance = {
            "fetch_attempts": "SELECT COUNT(*) FROM fetch_attempts",
            "source_candidates": "SELECT COUNT(*) FROM source_candidates",
            "task_discoveries": "SELECT COUNT(*) FROM task_discoveries",
            "discovery_batches": "SELECT COUNT(*) FROM discovery_batches",
        }
        non_empty = {}
        for name, sql in provenance.items():
            if _table_exists(conn, name):
                count = int(conn.execute(sql).fetchone()[0])
                if count:
                    non_empty[name] = count
        if non_empty:
            raise RollbackError(
                "refusing rollback with provenance rows; restore the verified backup: "
                + repr(non_empty)
            )
        before_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        expected_count = int(row["task_count_before"])
        if before_count != expected_count:
            raise RollbackError(
                f"refusing rollback after task ingestion: {expected_count} -> {before_count}"
            )
        current_columns = _columns(conn, "tasks")
        for name in reversed(json.loads(row["task_columns_added"] or "[]")):
            if name in current_columns:
                conn.execute(f'ALTER TABLE tasks DROP COLUMN "{name}"')
        created_tables = json.loads(row["provenance_tables_created"] or "[]")
        for table in ("task_discoveries", "discovery_batches", "source_candidates", "fetch_attempts"):
            if table in created_tables and _table_exists(conn, table):
                conn.execute(f'DROP TABLE "{table}"')
        if _table_exists(conn, "schema_version"):
            conn.execute("DROP TABLE schema_version")
        conn.execute("PRAGMA user_version=0")
        conn.execute("COMMIT")
        return {"before": before_count, "after": before_count, "rolled_back": True}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


rollback_to_v1 = rollback_schema


def _cli() -> int:
    parser = argparse.ArgumentParser(description="A1 tasks.sqlite Schema v2 migration")
    parser.add_argument("command", choices=("migrate", "verify", "rollback"), nargs="?", default="migrate")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    args = parser.parse_args()
    if args.command == "migrate":
        result = migrate_to_v2(args.db)
    elif args.command == "verify":
        conn = _connect(args.db)
        try:
            result = verify_schema_v2(conn)
        finally:
            conn.close()
    else:
        result = rollback_schema(args.db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
