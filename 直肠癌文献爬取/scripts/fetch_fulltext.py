# -*- coding: utf-8 -*-
"""A5 single-PMID full-text acquisition into the raw corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a1_schema import DEFAULT_DB_PATH, _connect, migrate_to_v2, utc_now
from fulltext_client import (
    FetchResult,
    HttpResponse,
    UnifiedHttpClient,
    redact_text,
    redact_url,
)
from ingest_pmids import normalize_pmids
from oa_resolver import (
    INSTITUTIONAL_REPOSITORY,
    PUBLISHER,
    SOURCE_PRIORITY,
    UNPAYWALL,
    _candidate_sort_key,
)
from state_machine import transition_task_status


RAW_ROOT = Path(__file__).resolve().parents[2] / "corpus_raw"
FETCHABLE_FORMATS = frozenset({"xml", "txt", "html", "pdf"})
FORMAT_FILENAMES = {
    "xml": "article.xml",
    "txt": "article.txt",
    "html": "article.html",
    "pdf": "article.pdf",
}
LEGAL_OA_SOURCES = frozenset({UNPAYWALL, PUBLISHER, INSTITUTIONAL_REPOSITORY})


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _pmcid(value: Any) -> str | None:
    value = _text(value)
    if value and not value.upper().startswith("PMC"):
        value = "PMC" + value
    return value.upper() if value else None


def _format(candidate: Mapping[str, Any]) -> str | None:
    value = _text(candidate.get("format"))
    if value:
        value = value.casefold()
        if value in {"text", "plain", "plaintext"}:
            value = "txt"
        elif value in {"xhtml", "htm"}:
            value = "html"
        return value if value in FETCHABLE_FORMATS else None
    url = _text(candidate.get("url")) or ""
    suffix = Path(url.split("?", 1)[0].split("#", 1)[0]).suffix.casefold().lstrip(".")
    return suffix if suffix in FETCHABLE_FORMATS else "html"


def _fetch_rank(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """Apply A5's format/source sequence with A4 as the tie-break owner."""
    source = candidate.get("source")
    fmt = _format(candidate)
    if source == "PMC_AWS" and fmt == "xml":
        rank = 0
    elif source == "EuropePMC" and fmt == "xml":
        rank = 1
    elif source == "PMC_AWS" and fmt == "txt":
        rank = 2
    elif fmt in {"xml", "html"} and source in LEGAL_OA_SOURCES:
        rank = 3
    elif fmt == "pdf":
        rank = 4
    else:
        rank = 5
    return (rank, *_candidate_sort_key(candidate))


def order_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return only fetchable A4 candidates in deterministic A5 order."""
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        row["format"] = _format(row)
        if row.get("source") not in SOURCE_PRIORITY:
            continue
        if not row.get("url") or row["format"] not in FETCHABLE_FORMATS:
            continue
        if row["source"] in LEGAL_OA_SOURCES and row["format"] not in {"xml", "html", "pdf"}:
            continue
        rows.append(row)
    return sorted(rows, key=_fetch_rank)


@dataclass(frozen=True)
class RawArtifact:
    path: Path
    source_json: Path
    sha256: str
    format: str
    retrieved_at: str


class RawCorpusWriter:
    """Write article bytes and source.json atomically under corpus_raw."""

    def __init__(self, raw_root: str | Path = RAW_ROOT) -> None:
        self.raw_root = Path(raw_root)

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _atomic_write_text(path: Path, value: str) -> None:
        RawCorpusWriter._atomic_write_bytes(path, value.encode("utf-8"))

    def write(
        self,
        *,
        pmid: int,
        task: Mapping[str, Any],
        candidate: Mapping[str, Any],
        response: HttpResponse,
    ) -> RawArtifact:
        fmt = _format(candidate)
        if fmt not in FORMAT_FILENAMES:
            raise ValueError(f"unsupported raw format: {fmt!r}")
        retrieved_at = utc_now()
        content = bytes(response.body)
        digest = hashlib.sha256(content).hexdigest()
        directory = self.raw_root / f"PMID_{int(pmid)}"
        article_path = directory / FORMAT_FILENAMES[fmt]
        source_json_path = directory / "source.json"
        record = {
            "PMID": int(pmid),
            "DOI": _text(task.get("doi")),
            "PMCID": _pmcid(candidate.get("pmcid") or task.get("pmcid") or task.get("pmc")),
            "source": _text(candidate.get("source")),
            "URL": redact_url(str(candidate.get("url"))),
            "license": _text(candidate.get("license") or task.get("license") or "unverified"),
            "retrieved_at": retrieved_at,
            "sha256": digest,
            "format": fmt,
            "version": _text(candidate.get("version") or candidate.get("article_version")) or "unknown",
        }
        record = {
            key: redact_text(value) if isinstance(value, str) else value
            for key, value in record.items()
        }
        self._atomic_write_bytes(article_path, content)
        self._atomic_write_text(
            source_json_path,
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return RawArtifact(
            path=article_path,
            source_json=source_json_path,
            sha256=digest,
            format=fmt,
            retrieved_at=retrieved_at,
        )


def _task_and_candidates(
    pmid: int,
    db_path: str | Path,
    candidates: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    migrate_to_v2(db_path)
    conn = _connect(db_path)
    try:
        task_row = conn.execute("SELECT * FROM tasks WHERE pmid=?", (pmid,)).fetchone()
        if task_row is None:
            raise ValueError(f"task PMID {pmid} does not exist")
        if candidates is None:
            candidate_rows = conn.execute(
                "SELECT * FROM source_candidates WHERE pmid=?", (pmid,)
            ).fetchall()
            candidates = [dict(row) for row in candidate_rows]
        return dict(task_row), order_candidates(candidates)
    finally:
        conn.close()


def _update_task_content(
    pmid: int,
    candidate: Mapping[str, Any],
    artifact: RawArtifact,
    *,
    db_path: str | Path,
) -> None:
    timestamp = artifact.retrieved_at
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE tasks SET content_status=?, content_source=?, content_format=?,
                content_path=?, source_url=?, content_sha256=?, source_updated_at=?,
                retrieved_at=?, updated_at=? WHERE pmid=?""",
            (
                "fetched",
                candidate.get("source"),
                artifact.format,
                str(artifact.path),
                redact_url(str(candidate.get("url"))),
                artifact.sha256,
                timestamp,
                timestamp,
                timestamp,
                pmid,
            ),
        )
        if conn.total_changes != 1:
            raise ValueError(f"task PMID {pmid} disappeared during content update")
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _current_status(pmid: int, db_path: str | Path) -> str:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT status FROM tasks WHERE pmid=?", (pmid,)).fetchone()
        if row is None:
            raise ValueError(f"task PMID {pmid} does not exist")
        return str(row["status"])
    finally:
        conn.close()


def acquire_one(
    pmid: int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    raw_root: str | Path = RAW_ROOT,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    client: UnifiedHttpClient | None = None,
    writer: RawCorpusWriter | None = None,
) -> dict[str, Any]:
    """Acquire one PMID from A4 candidates; no bulk mode is provided."""
    pmid = normalize_pmids([pmid])[0]
    task, candidate_rows = _task_and_candidates(pmid, db_path, candidates)
    if not candidate_rows:
        return {
            "pmid": pmid,
            "status": _current_status(pmid, db_path),
            "outcome": "no_fetchable_candidate",
            "attempts": 0,
            "failures": [],
        }
    existing_status = _current_status(pmid, db_path)
    if existing_status in {"archived", "excluded", "fulltext_ready"}:
        return {
            "pmid": pmid,
            "status": existing_status,
            "outcome": "skipped_terminal_status",
            "attempts": 0,
            "failures": [],
        }
    transition_task_status(pmid, "fetching", db_path=db_path)
    client = client or UnifiedHttpClient(db_path=db_path)
    writer = writer or RawCorpusWriter(raw_root)
    failures: list[dict[str, Any]] = []
    attempts = 0
    for candidate in candidate_rows:
        result: FetchResult = client.get(
            pmid=pmid,
            source=str(candidate["source"]),
            url=str(candidate["url"]),
            route=f"fulltext:{candidate['format']}",
            identifier=_pmcid(candidate.get("pmcid") or task.get("pmcid")) or _text(task.get("doi")) or str(pmid),
            success_handler=lambda response, c=candidate: writer.write(
                pmid=pmid, task=task, candidate=c, response=response
            ),
        )
        attempts += result.attempts
        if result.ok:
            _update_task_content(pmid, candidate, result.artifact, db_path=db_path)
            return {
                "pmid": pmid,
                "status": _current_status(pmid, db_path),
                "outcome": "raw_fetched",
                "source": candidate["source"],
                "format": candidate["format"],
                "path": str(result.artifact.path),
                "source_json": str(result.artifact.source_json),
                "attempts": attempts,
                "failures": failures,
            }
        failures.append(
            {
                "source": candidate["source"],
                "url": redact_url(str(candidate["url"])),
                "format": candidate["format"],
                "error_class": result.error_class,
                "error_detail": result.error_detail,
                "attempts": result.attempts,
            }
        )
    return {
        "pmid": pmid,
        "status": _current_status(pmid, db_path),
        "outcome": "failed",
        "attempts": attempts,
        "failures": failures,
    }


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A5 fetch one PMID into corpus_raw")
    parser.add_argument("pmid", type=int)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--raw-root", default=str(RAW_ROOT))
    args = parser.parse_args(argv)
    result = acquire_one(args.pmid, db_path=args.db, raw_root=args.raw_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["outcome"] in {"raw_fetched", "no_fetchable_candidate", "skipped_terminal_status"} else 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
