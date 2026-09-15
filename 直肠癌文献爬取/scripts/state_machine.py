# -*- coding: utf-8 -*-
"""A3 canonical acquisition states, error taxonomy and attempt recording.

This module owns document-level state derivation and source-level request
classification. It deliberately does not discover OA sources, perform HTTP
requests, download content, validate content, or implement retry orchestration.
Full-text readiness consumes explicit validation evidence from the caller; it
never treats a source-level HTTP success as content acceptance.
"""

from __future__ import annotations

import socket
import sqlite3
import uuid
from typing import NamedTuple
from pathlib import Path
from typing import Any, Iterable, Mapping

from a1_schema import DEFAULT_DB_PATH, _connect, migrate_to_v2, utc_now


CANONICAL_STATES = (
    "pending",
    "metadata_ready",
    "oa_resolved",
    "fetching",
    "fulltext_ready",
    "metadata_only",
    "retryable_error",
    "excluded",
    "archived",
)

ERROR_CLASSES = (
    "timeout",
    "connection_error",
    "rate_limit",
    "server_error",
    "source_not_found",
    "no_oa_fulltext",
    "license_restricted",
    "invalid_pdf",
    "invalid_xml",
    "content_too_small",
    "metadata_mismatch",
    "identifier_mismatch",
    "parser_error",
    "storage_error",
    "unknown",
)

RETRYABLE_ERROR_CLASSES = frozenset(
    {"timeout", "connection_error", "rate_limit", "server_error"}
)


class RetryPolicy(NamedTuple):
    """A3 retry decision metadata; it does not perform a retry."""

    name: str
    retryable: bool
    honors_retry_after: bool
    backoff: str


RETRY_POLICIES = {
    "none": RetryPolicy("none", False, False, "none"),
    "timeout": RetryPolicy("timeout", True, False, "exponential_backoff"),
    "connection_error": RetryPolicy(
        "connection_error", True, False, "exponential_backoff"
    ),
    "rate_limit": RetryPolicy(
        "rate_limit", True, True, "retry_after_then_exponential"
    ),
    "server_error": RetryPolicy("server_error", True, False, "exponential_backoff"),
}


class RequestClassification(NamedTuple):
    """Canonical result of classifying one source request."""

    outcome: str
    error_class: str | None
    retryable: bool
    source_level: bool
    retry_policy: str

    def as_dict(self) -> dict[str, Any]:
        return dict(self._asdict())


class StateTransitionError(ValueError):
    """Raised when a requested state is not in the canonical state machine."""


class ErrorTaxonomyError(ValueError):
    """Raised when a caller supplies a non-canonical error class."""


# The transition table permits direct observations from a source request while
# retaining terminal-state protection. A later source success can therefore
# promote a metadata-only task, while archived/excluded tasks cannot silently
# re-enter acquisition.
STATE_TRANSITIONS = {
    "pending": frozenset(CANONICAL_STATES),
    "metadata_ready": frozenset(CANONICAL_STATES),
    "oa_resolved": frozenset(CANONICAL_STATES),
    "fetching": frozenset(CANONICAL_STATES),
    "fulltext_ready": frozenset({"fulltext_ready", "archived"}),
    "metadata_only": frozenset(CANONICAL_STATES),
    "retryable_error": frozenset(CANONICAL_STATES),
    "excluded": frozenset({"excluded", "archived"}),
    "archived": frozenset({"archived"}),
}


# These aliases are read-only compatibility for pre-A3 rows. They are never
# emitted as canonical states and do not change the source_not_found rule.
_LEGACY_STATUS_ALIASES = {
    "downloading": "fetching",
    # Legacy ``done`` has no A6 validation evidence. Keep it conservative
    # rather than treating an unverified historical success as ready content.
    "done": "metadata_only",
    "failed": "retryable_error",
    "not_found": "metadata_only",
}


HTTP_ERROR_MAPPING = {
    403: "license_restricted",
    404: "source_not_found",
    408: "timeout",
    429: "rate_limit",
    500: "server_error",
    502: "server_error",
    503: "server_error",
    504: "server_error",
}

# Public aliases make the canonical contract straightforward to inspect from
# tests and downstream callers without duplicating the taxonomy definition.
CANONICAL_ERROR_CLASSES = ERROR_CLASSES
HTTP_MAPPING = HTTP_ERROR_MAPPING


def is_state(value: str) -> bool:
    return value in CANONICAL_STATES


def is_error_class(value: str) -> bool:
    return value in ERROR_CLASSES


def validate_state(value: str) -> str:
    if value not in CANONICAL_STATES:
        raise StateTransitionError(f"non-canonical state: {value!r}")
    return value


def normalize_error_class(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    value = str(value).strip().lower()
    if value not in ERROR_CLASSES:
        return "unknown"
    return value


def retry_policy_for(error_class: str | None) -> RetryPolicy:
    """Return policy metadata for an error; no network or sleep is performed."""

    normalized = normalize_error_class(error_class)
    if normalized is None:
        return RETRY_POLICIES["none"]
    return RETRY_POLICIES.get(normalized, RETRY_POLICIES["none"])


def _classification_for_error(
    error_class: str,
    *,
    outcome: str | None = None,
) -> RequestClassification:
    normalized = normalize_error_class(error_class) or "unknown"
    policy = retry_policy_for(normalized)
    source_level = normalized == "source_not_found"
    if outcome is None:
        outcome = "not_found" if source_level else "failure"
    return RequestClassification(
        outcome=outcome,
        error_class=normalized,
        retryable=policy.retryable,
        source_level=source_level,
        retry_policy=policy.name,
    )


def classify_http_status(http_status: int) -> RequestClassification:
    """Map one HTTP result to the canonical A3 taxonomy."""

    if isinstance(http_status, bool) or not isinstance(http_status, int):
        raise ValueError("http_status must be an integer")
    if 200 <= http_status < 300:
        return RequestClassification("success", None, False, False, "none")
    error_class = HTTP_ERROR_MAPPING.get(http_status)
    if error_class is None and 500 <= http_status < 600:
        error_class = "server_error"
    if error_class is None:
        return _classification_for_error("unknown")
    return _classification_for_error(
        error_class,
        outcome="not_found" if error_class == "source_not_found" else "failure",
    )


def classify_exception(exception: BaseException) -> RequestClassification:
    """Classify timeout/connection exceptions without importing a HTTP client."""

    if isinstance(exception, (TimeoutError, socket.timeout)) or "timeout" in type(
        exception
    ).__name__.lower():
        return _classification_for_error("timeout")
    if isinstance(exception, ConnectionError) or "connectionerror" in type(
        exception
    ).__name__.lower():
        return _classification_for_error("connection_error")
    return _classification_for_error("unknown")


def classify_request(
    *,
    http_status: int | None = None,
    exception: BaseException | None = None,
    error_class: str | None = None,
    outcome: str | None = None,
) -> RequestClassification:
    """Classify a request result with HTTP and exception precedence.

    A caller can provide a pre-classified error for non-HTTP failures such as
    parsing or storage. HTTP 404 remains source-level source_not_found; it is
    never converted into a document-level not-found state.
    """

    if error_class is not None and (
        http_status is None or (
            isinstance(http_status, int) and not isinstance(http_status, bool)
            and 200 <= http_status < 300
        )
    ):
        return _classification_for_error(error_class, outcome=outcome)
    if http_status is not None:
        return classify_http_status(http_status)
    if exception is not None:
        return classify_exception(exception)
    if outcome is not None:
        normalized_outcome = str(outcome).strip().lower()
        if normalized_outcome in {"success", "succeeded", "fulltext_ready"}:
            return RequestClassification("success", None, False, False, "none")
        if normalized_outcome in {"not_found", "source_not_found"}:
            return _classification_for_error("source_not_found", outcome="not_found")
    return _classification_for_error("unknown", outcome="failure")


def can_transition(
    current: str,
    new: str,
    *,
    file_valid: Any = None,
    bibliographic_match: Any = None,
) -> bool:
    validate_state(current)
    validate_state(new)
    if (
        new == "fulltext_ready"
        and current != "fulltext_ready"
        and not _has_fulltext_validation(file_valid, bibliographic_match)
    ):
        return False
    return new in STATE_TRANSITIONS[current]


def transition_status(
    current: str,
    new: str,
    *,
    file_valid: Any = None,
    bibliographic_match: Any = None,
) -> str:
    """Validate and return a canonical state transition."""

    validate_state(current)
    validate_state(new)
    if not can_transition(
        current,
        new,
        file_valid=file_valid,
        bibliographic_match=bibliographic_match,
    ):
        if new == "fulltext_ready" and current != "fulltext_ready":
            raise StateTransitionError(
                "fulltext_ready requires file_valid=True and "
                "bibliographic_match=True"
            )
        raise StateTransitionError(f"invalid state transition: {current} -> {new}")
    return new


def _canonical_current_status(status: str | None) -> str:
    if status is None or not str(status).strip():
        return "pending"
    status = str(status).strip()
    return _LEGACY_STATUS_ALIASES.get(status, status)


def _attempt_value(attempt: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return attempt[key]
    except (KeyError, IndexError, TypeError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _has_fulltext_validation(
    file_valid: Any,
    bibliographic_match: Any,
) -> bool:
    """Return whether the caller supplied both required validation results."""

    return _truthy(file_valid) and _truthy(bibliographic_match)


def _attempt_has_fulltext_validation(attempt: Mapping[str, Any]) -> bool:
    return _has_fulltext_validation(
        _attempt_value(attempt, "file_valid"),
        _attempt_value(attempt, "bibliographic_match"),
    )


def _attempt_is_success(attempt: Mapping[str, Any]) -> bool:
    # A source-level 2xx response is only a fetched response. A document-level
    # fulltext_ready state requires both validation results, which are supplied
    # by the A6 caller and intentionally not computed here.
    if not _attempt_has_fulltext_validation(attempt):
        return False
    outcome = str(_attempt_value(attempt, "outcome", "") or "").strip().lower()
    if outcome in {"success", "succeeded", "fulltext_ready"}:
        return True
    status = _attempt_value(attempt, "http_status")
    return isinstance(status, int) and 200 <= status < 300 and not _attempt_value(
        attempt, "error_class"
    )


def _attempt_error_class(attempt: Mapping[str, Any]) -> str | None:
    error_class = normalize_error_class(_attempt_value(attempt, "error_class"))
    if error_class is not None:
        return error_class
    outcome = str(_attempt_value(attempt, "outcome", "") or "").strip().lower()
    if outcome in {"not_found", "source_not_found"}:
        return "source_not_found"
    return None


def derive_document_status(
    attempts: Iterable[Mapping[str, Any]],
    *,
    current_status: str | None = None,
    metadata_available: bool | None = None,
    has_metadata: bool | None = None,
) -> str:
    """Derive document status from all source attempts, order-independently.

    Success wins over source failures. With no success, a retryable source
    failure wins over permanent/source absence. A lone 404 therefore yields
    metadata_only when metadata exists (or leaves an unmetadataed task
    pending), never a document-level not_found state.
    """

    current = _canonical_current_status(current_status)
    validate_state(current)
    if has_metadata is not None:
        metadata_available = has_metadata
    if metadata_available is None:
        metadata_available = current in {
            "metadata_ready",
            "oa_resolved",
            "fetching",
            "fulltext_ready",
            "metadata_only",
        }

    rows = list(attempts)
    if current == "archived":
        return "archived"
    if current == "excluded":
        return "excluded"
    if current == "fulltext_ready":
        return "fulltext_ready"
    if not rows:
        return current
    if any(_attempt_is_success(attempt) for attempt in rows):
        return "fulltext_ready"
    if any(
        str(_attempt_value(attempt, "outcome", "") or "").strip().lower()
        == "excluded"
        for attempt in rows
    ):
        return "excluded"

    retryable = False
    for attempt in rows:
        error_class = _attempt_error_class(attempt)
        if _truthy(_attempt_value(attempt, "retryable")) or (
            error_class in RETRYABLE_ERROR_CLASSES
        ):
            retryable = True
            break
    if retryable:
        return "retryable_error"

    # A source-level 404, license restriction, no-OA result, or permanent
    # source failure means that source is exhausted; it does not erase the
    # bibliographic task. Metadata-only is the document-level representation.
    return "metadata_only" if metadata_available else "pending"


def _task_has_metadata(row: sqlite3.Row) -> bool:
    return any(
        row[field]
        for field in ("title", "doi", "pmcid", "journal", "metadata_updated_at")
        if field in row.keys()
    )


def _require_pmid(pmid: int) -> int:
    if isinstance(pmid, bool) or not isinstance(pmid, int) or pmid <= 0:
        raise ValueError("pmid must be a positive integer")
    return pmid


def _require_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty string")
    return source.strip()


def _transition_in_connection(
    conn: sqlite3.Connection,
    pmid: int,
    new_status: str,
    *,
    file_valid: Any = None,
    bibliographic_match: Any = None,
) -> tuple[str, str]:
    row = conn.execute("SELECT status FROM tasks WHERE pmid=?", (pmid,)).fetchone()
    if row is None:
        raise ValueError(f"task PMID {pmid} does not exist")
    current = _canonical_current_status(row["status"])
    transition_status(
        current,
        new_status,
        file_valid=file_valid,
        bibliographic_match=bibliographic_match,
    )
    return current, new_status


def transition_task_status(
    pmid: int,
    new_status: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    updated_at: str | None = None,
    file_valid: Any = None,
    bibliographic_match: Any = None,
) -> dict[str, Any]:
    """Apply one explicit state transition with a readiness precondition."""

    _require_pmid(pmid)
    validate_state(new_status)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current, target = _transition_in_connection(
            conn,
            pmid,
            new_status,
            file_valid=file_valid,
            bibliographic_match=bibliographic_match,
        )
        timestamp = updated_at or utc_now()
        conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE pmid=?",
            (target, timestamp, pmid),
        )
        conn.execute("COMMIT")
        return {
            "pmid": pmid,
            "from": current,
            "to": target,
            "changed": current != target,
        }
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def derive_task_status(
    pmid: int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> str:
    """Read a task and derive its status from every recorded attempt."""

    _require_pmid(pmid)
    conn = _connect(db_path)
    try:
        task = conn.execute("SELECT * FROM tasks WHERE pmid=?", (pmid,)).fetchone()
        if task is None:
            raise ValueError(f"task PMID {pmid} does not exist")
        attempts = conn.execute(
            "SELECT * FROM fetch_attempts WHERE pmid=? ORDER BY started_at, attempt_id",
            (pmid,),
        ).fetchall()
        return derive_document_status(
            attempts,
            current_status=task["status"],
            metadata_available=_task_has_metadata(task),
        )
    finally:
        conn.close()


def record_fetch_attempt(
    pmid: int,
    source: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    route: str | None = None,
    url: str | None = None,
    identifier: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    http_status: int | None = None,
    outcome: str | None = None,
    error_class: str | None = None,
    error_detail: str | None = None,
    exception: BaseException | None = None,
    retry_after: str | None = None,
    next_retry_at: str | None = None,
    content_type: str | None = None,
    content_length: int | None = None,
    worker_id: str | None = None,
    attempt_id: str | None = None,
    file_valid: Any = None,
    bibliographic_match: Any = None,
) -> dict[str, Any]:
    """Record one source request and atomically update document status.

    Both successful and failed requests insert a row. The request's error
    class and source-level nature remain in fetch_attempts; the task's
    canonical status is separately derived from the complete attempt history.
    ``file_valid`` and ``bibliographic_match`` are caller-provided A6 evidence;
    omitted or false evidence can never promote the task to fulltext_ready.
    """

    _require_pmid(pmid)
    source = _require_source(source)
    classification = classify_request(
        http_status=http_status,
        exception=exception,
        error_class=error_class,
        outcome=outcome,
    )
    stored_outcome = classification.outcome
    stored_error_class = classification.error_class
    if error_detail is None and exception is not None:
        error_detail = str(exception)
    started_at = started_at or utc_now()
    finished_at = finished_at or utc_now()
    attempt_id = attempt_id or uuid.uuid4().hex
    if content_length is not None and (
        isinstance(content_length, bool) or not isinstance(content_length, int)
    ):
        raise ValueError("content_length must be an integer or None")

    # A1 is already the authority; this idempotent call only makes the helper
    # usable against a freshly prepared test database and never creates a
    # parallel workflow database.
    migrate_to_v2(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE pmid=?", (pmid,)).fetchone()
        if task is None:
            raise ValueError(f"task PMID {pmid} does not exist")
        conn.execute(
            """INSERT INTO fetch_attempts
                (attempt_id, pmid, source, route, url, identifier, started_at,
                 finished_at, http_status, outcome, error_class, error_detail,
                 retryable, retry_after, content_type, content_length, worker_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt_id,
                pmid,
                source,
                route,
                url,
                identifier,
                started_at,
                finished_at,
                http_status,
                stored_outcome,
                stored_error_class,
                error_detail,
                int(classification.retryable),
                retry_after,
                content_type,
                content_length,
                worker_id,
            ),
        )
        attempt_rows = conn.execute(
            "SELECT * FROM fetch_attempts WHERE pmid=? ORDER BY started_at, attempt_id",
            (pmid,),
        ).fetchall()
        # Schema v2 intentionally remains A1-owned and has no validation
        # columns. Keep the current caller-supplied evidence attached in
        # memory for this atomic derivation; an unannotated stored success is
        # therefore never treated as validated on a later derivation.
        attempts = [dict(row) for row in attempt_rows]
        for attempt in attempts:
            if attempt.get("attempt_id") == attempt_id:
                attempt["file_valid"] = file_valid
                attempt["bibliographic_match"] = bibliographic_match
        new_status = derive_document_status(
            attempts,
            current_status=task["status"],
            metadata_available=_task_has_metadata(task),
        )
        current_status = _canonical_current_status(task["status"])
        validated_attempt = next(
            (attempt for attempt in attempts if _attempt_is_success(attempt)),
            None,
        )
        transition_status(
            current_status,
            new_status,
            file_valid=_attempt_value(validated_attempt, "file_valid"),
            bibliographic_match=_attempt_value(
                validated_attempt, "bibliographic_match"
            ),
        )
        timestamp = finished_at
        conn.execute(
            """UPDATE tasks SET status=?, attempt_count=COALESCE(attempt_count, 0)+1,
                last_error_class=?, last_error_detail=?, next_retry_at=?, updated_at=?
                WHERE pmid=?""",
            (
                new_status,
                stored_error_class,
                error_detail,
                next_retry_at if classification.retryable else None,
                timestamp,
                pmid,
            ),
        )
        conn.execute("COMMIT")
        return {
            "attempt_id": attempt_id,
            "pmid": pmid,
            "source": source,
            "status": new_status,
            "outcome": stored_outcome,
            "error_class": stored_error_class,
            "retryable": classification.retryable,
            "source_level": classification.source_level,
            "retry_policy": classification.retry_policy,
            "file_valid": _truthy(file_valid),
            "bibliographic_match": _truthy(bibliographic_match),
        }
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# Descriptive aliases for callers that think in terms of source requests.
record_source_request = record_fetch_attempt
record_attempt = record_fetch_attempt


def taxonomy_snapshot() -> dict[str, Any]:
    """Return a JSON-friendly snapshot for contract tests and diagnostics."""

    return {
        "states": list(CANONICAL_STATES),
        "error_classes": list(ERROR_CLASSES),
        "retryable_error_classes": sorted(RETRYABLE_ERROR_CLASSES),
        "http_mapping": {
            str(code): {
                "error_class": error_class,
                "retryable": retry_policy_for(error_class).retryable,
                "source_level": error_class == "source_not_found",
            }
            for code, error_class in sorted(HTTP_ERROR_MAPPING.items())
        },
    }
