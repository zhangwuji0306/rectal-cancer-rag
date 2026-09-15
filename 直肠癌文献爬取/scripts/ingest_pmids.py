# -*- coding: utf-8 -*-
"""A1 formal, idempotent PMID discovery ingestion API and CLI.

This module records only PMID discovery. It deliberately does not call
PubMed, refresh metadata, resolve OA, fetch content, or change task state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Iterable

from a1_schema import DEFAULT_DB_PATH, _connect, migrate_to_v2, utc_now


PMID_RE = re.compile(r"^[0-9]+$")


def normalize_pmids(pmids: Iterable[str | int] | str | int) -> list[int]:
    """Strip, validate and de-duplicate PMIDs while preserving input order."""

    if isinstance(pmids, (str, int)) and not isinstance(pmids, bool):
        values = [pmids]
    else:
        if isinstance(pmids, (bytes, bytearray)) or isinstance(pmids, bool):
            raise ValueError("PMIDs must be positive integers or decimal strings")
        try:
            values = list(pmids)
        except TypeError as exc:
            raise ValueError("PMIDs must be an iterable of positive integers") from exc
    if not values:
        raise ValueError("at least one PMID is required")

    result: list[int] = []
    seen: set[int] = set()
    for index, value in enumerate(values):
        if isinstance(value, bool):
            raise ValueError(f"invalid PMID at position {index}: {value!r}")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not PMID_RE.fullmatch(stripped):
                raise ValueError(f"invalid PMID at position {index}: {value!r}")
            number = int(stripped)
        else:
            raise ValueError(f"invalid PMID at position {index}: {value!r}")
        if number <= 0:
            raise ValueError(f"PMID must be positive at position {index}: {value!r}")
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _input_hash(pmids: list[int]) -> str:
    return _sha256_text("\n".join(str(pmid) for pmid in pmids))


def _validate_existing_batch(row, *, source: str, query_text: str | None,
                             query_hash: str | None, input_sha256: str) -> None:
    checks = (
        ("source", row["source"], source),
        ("query_text", row["query_text"], query_text),
        ("query_hash", row["query_hash"], query_hash),
        ("input_sha256", row["input_sha256"], input_sha256),
    )
    mismatches = [name for name, stored, supplied in checks if stored != supplied]
    if mismatches:
        raise ValueError(
            "batch_id already exists with different immutable provenance: "
            + ", ".join(mismatches)
        )


def ingest_pmids(
    pmids: Iterable[str | int] | str | int,
    *,
    source: str,
    batch_id: str | None = None,
    query_text: str | None = None,
    searched_at: str | None = None,
    note: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, object]:
    """Ingest one discovery batch into the canonical SQLite authority.

    ``db_path`` is explicit for tests and controlled migrations; production
    callers use the default ``直肠癌文献爬取/tasks.sqlite``.
    """

    normalized = normalize_pmids(pmids)
    source = _required_text(source, "source")
    if batch_id is not None:
        batch_id = _required_text(batch_id, "batch_id")
    if query_text is not None and not isinstance(query_text, str):
        raise ValueError("query_text must be a string or None")
    query_hash = _sha256_text(query_text) if query_text is not None else None
    input_sha256 = _input_hash(normalized)
    batch_id = batch_id or f"pmid-{uuid.uuid4().hex}"
    searched_at = searched_at or utc_now()
    imported_at = utc_now()

    # Ensure callers cannot create a v1 task row and then partially ingest
    # provenance. Migration itself is additive and independently atomic.
    migrate_to_v2(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute(
            "SELECT batch_id, source, query_text, query_hash, input_sha256 "
            "FROM discovery_batches WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        if batch is None:
            conn.execute(
                """INSERT INTO discovery_batches
                    (batch_id, source, query_text, query_hash, searched_at,
                     imported_at, input_sha256, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, source, query_text, query_hash, searched_at,
                 imported_at, input_sha256, note),
            )
        else:
            _validate_existing_batch(
                batch, source=source, query_text=query_text,
                query_hash=query_hash, input_sha256=input_sha256,
            )

        new_tasks = 0
        new_discoveries = 0
        existing_pmids: list[int] = []
        discovered_at = utc_now()
        for pmid in normalized:
            exists = conn.execute("SELECT 1 FROM tasks WHERE pmid=?", (pmid,)).fetchone()
            if exists is None:
                conn.execute(
                    """INSERT INTO tasks
                        (pmid, status, attempt_count, attempts, retracted,
                         created_at, updated_at)
                        VALUES (?, 'pending', 0, 0, 0, ?, ?)""",
                    (pmid, discovered_at, discovered_at),
                )
                new_tasks += 1
            else:
                existing_pmids.append(pmid)
            cursor = conn.execute(
                """INSERT OR IGNORE INTO task_discoveries
                    (batch_id, pmid, discovered_at) VALUES (?, ?, ?)""",
                (batch_id, pmid, discovered_at),
            )
            new_discoveries += cursor.rowcount
        conn.execute("COMMIT")
        return {
            "batch_id": batch_id,
            "input_count": len(normalized),
            "new_tasks": new_tasks,
            "new_discoveries": new_discoveries,
            "existing_pmids": existing_pmids,
        }
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Idempotent PMID discovery ingestion")
    parser.add_argument("pmids", nargs="*", help="PMIDs as positional decimal values")
    parser.add_argument("--pmid", action="append", dest="pmid_flags", default=[],
                        help="one PMID; may be repeated")
    parser.add_argument("--input-file", type=Path,
                        help="UTF-8 file with one PMID per line")
    parser.add_argument("--source", required=True)
    parser.add_argument("--batch-id")
    parser.add_argument("--query-text")
    parser.add_argument("--searched-at")
    parser.add_argument("--note")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    values: list[str | int] = [*args.pmid_flags, *args.pmids]
    if args.input_file is not None:
        values.extend(args.input_file.read_text(encoding="utf-8").splitlines())
    result = ingest_pmids(
        values, source=args.source, batch_id=args.batch_id,
        query_text=args.query_text, searched_at=args.searched_at,
        note=args.note, db_path=args.db,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
