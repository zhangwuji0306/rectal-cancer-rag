# -*- coding: utf-8 -*-
"""A4: resolve trusted, legal full-text source candidates."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from a1_schema import DEFAULT_DB_PATH, _connect, migrate_to_v2, utc_now
from ingest_pmids import normalize_pmids
from fulltext_client import UnifiedHttpClient, UnifiedHttpError, load_project_config
from state_machine import classify_exception, classify_request, transition_task_status


PMC_AWS = "PMC_AWS"
EUROPE_PMC = "EuropePMC"
UNPAYWALL = "Unpaywall"
PUBLISHER = "Publisher"
INSTITUTIONAL_REPOSITORY = "InstitutionalRepository"
METADATA_ONLY = "metadata_only"

SOURCE_ORDER = (
    PMC_AWS,
    EUROPE_PMC,
    UNPAYWALL,
    PUBLISHER,
    INSTITUTIONAL_REPOSITORY,
)
SOURCE_PRIORITY = {
    PMC_AWS: 1,
    EUROPE_PMC: 2,
    UNPAYWALL: 3,
    PUBLISHER: 4,
    INSTITUTIONAL_REPOSITORY: 4,
    METADATA_ONLY: 5,
}
FORMAT_ORDER = {"xml": 0, "txt": 1, "html": 2, "pdf": 3, "landing": 4}

PMC_AWS_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL_API_URL = "https://api.unpaywall.org/v2"

# A1 owns the original table. These additive columns are the A4 provenance
# contract and deliberately do not change A1's schema version.
A4_SOURCE_CANDIDATE_COLUMNS = {
    "pmcid": "TEXT",
    "article_version": "TEXT",
    "xml_url": "TEXT",
    "txt_url": "TEXT",
    "pdf_url": "TEXT",
    "url_for_pdf": "TEXT",
    "oa_status": "TEXT",
    "best_oa_location": "TEXT",
    "host_type": "TEXT",
    "updated_at": "TEXT",
    "metadata_json": "TEXT",
}

SOURCE_CANDIDATE_FIELDS = (
    "pmid", "pmcid", "source", "url", "format", "version", "article_version",
    "license", "is_oa", "reuse_allowed", "priority", "xml_url", "txt_url",
    "pdf_url", "url_for_pdf", "oa_status", "best_oa_location", "host_type",
    "updated_at", "resolved_at", "metadata_json",
)


class OAResolverError(RuntimeError):
    """Raised when OA resolver input or storage cannot be handled safely."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _pmcid(task: Mapping[str, Any]) -> str | None:
    value = _text(task.get("pmcid") or task.get("pmc"))
    if value and not value.upper().startswith("PMC"):
        value = "PMC" + value
    return value.upper() if value else None


def _safe_url(value: Any) -> str | None:
    url = _text(value)
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"candidate URL must be an absolute HTTP(S) URL: {url!r}")
    return url


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _as_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "y", "1"}:
            return 1
        if lowered in {"false", "no", "n", "0"}:
            return 0
    return None


def ensure_source_candidates_schema(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Add A4 provenance columns without changing existing task rows."""

    migrate_to_v2(db_path)
    conn = _connect(db_path)
    added: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(source_candidates)")}
        for name, definition in A4_SOURCE_CANDIDATE_COLUMNS.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE source_candidates ADD COLUMN "{name}" {definition}')
                added.append(name)
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return {"added_columns": added, "columns": sorted(existing | set(added))}


def _provider_call(
    provider: Any, task: Mapping[str, Any], *, source: str | None = None
) -> Any:
    if provider is None:
        return []
    if isinstance(provider, Mapping):
        if source == UNPAYWALL and any(
            key in provider for key in ("is_oa", "best_oa_location", "oa_locations")
        ):
            # Unpaywall's response is itself a mapping. Keep it intact so the
            # candidate normalizer can expand best_oa_location/oa_locations.
            return provider
        pmid = int(task["pmid"])
        pmcid = _pmcid(task)
        return provider.get(pmid, provider.get(str(pmid), provider.get(pmcid, []))) or []
    method = getattr(provider, "resolve", None)
    if callable(method):
        return method(task)
    if callable(provider):
        return provider(task)
    raise TypeError("source provider must be callable, mapping, or expose resolve(task)")


def _source_alias(value: str) -> str:
    aliases = {
        "pmc_aws": PMC_AWS, "pmc aws": PMC_AWS, "pmcaws": PMC_AWS,
        "europe_pmc": EUROPE_PMC, "europe pmc": EUROPE_PMC,
        "europepmc": EUROPE_PMC, "unpaywall": UNPAYWALL,
        "publisher": PUBLISHER, "institutional_repository": INSTITUTIONAL_REPOSITORY,
        "institutional repository": INSTITUTIONAL_REPOSITORY,
    }
    return aliases.get(value.strip().casefold(), value.strip())


def _format_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urllib.parse.urlparse(url).path.casefold()
    if path.endswith(".xml") or "fulltextxml" in path:
        return "xml"
    if path.endswith(".txt"):
        return "txt"
    if path.endswith(".pdf") or "pdf" in path:
        return "pdf"
    return "html"


def _candidate_urls(raw: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for field, fmt in (
        ("xml_url", "xml"),
        ("text_url", "txt"),
        ("txt_url", "txt"),
        ("pdf_url", "pdf"),
        ("url_for_pdf", "pdf"),
    ):
        if raw.get(field):
            found.append((field, fmt))
    if raw.get("url"):
        found.append((
            "url",
            str(raw.get("format") or _format_from_url(raw.get("url")) or "html"),
        ))
    if not found and raw.get("best_oa_location"):
        best = raw["best_oa_location"]
        best_url = best.get("url") if isinstance(best, Mapping) else best
        if best_url:
            found.append((
                "best_oa_location",
                str(raw.get("format") or _format_from_url(best_url) or "html"),
            ))
    return found


def _normalize_candidates(
    task: Mapping[str, Any], source: str, values: Any
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, Mapping):
        values = [values]
    elif isinstance(values, (str, bytes, bytearray)):
        values = [{"url": values}]
    else:
        values = list(values)

    normalized: list[dict[str, Any]] = []
    for raw_value in values:
        raw = {"url": raw_value} if isinstance(raw_value, str) else dict(raw_value)
        source_name = _source_alias(str(raw.get("source") or source))
        if source_name not in SOURCE_PRIORITY:
            raise ValueError(f"unsupported OA source: {source_name!r}")
        best_location = raw.get("best_oa_location")
        if isinstance(best_location, Mapping):
            best_location_url = (
                best_location.get("url") or best_location.get("url_for_landing_page")
            )
        else:
            best_location_url = best_location
        article_version = _text(raw.get("article_version") or raw.get("version"))
        license_value = raw.get("license")
        if license_value is not None:
            license_value = str(license_value)
        common = {
            "pmcid": _pmcid(task) or _text(raw.get("pmcid")),
            "source": source_name,
            "version": article_version,
            "article_version": article_version,
            "license": license_value,
            "is_oa": _as_bool_int(raw.get("is_oa")),
            "reuse_allowed": _as_bool_int(raw.get("reuse_allowed")),
            "priority": SOURCE_PRIORITY[source_name],
            "oa_status": _text(raw.get("oa_status")),
            "best_oa_location": _text(best_location_url),
            "host_type": _text(raw.get("host_type")),
            "updated_at": _text(raw.get("updated_at") or raw.get("last_modified")),
            "url_for_pdf": (
                _safe_url(raw.get("url_for_pdf")) if raw.get("url_for_pdf") else None
            ),
            "metadata_json": _json_text(raw),
        }
        if common["source"] == UNPAYWALL and common["license"] is None:
            common["license"] = _text(raw.get("oa_license"))
        urls = _candidate_urls(raw)
        for url_field, fmt in urls:
            url_value = raw.get(url_field)
            if url_field == "best_oa_location" and isinstance(best_location, Mapping):
                url_value = (
                    best_location.get("url")
                    or best_location.get("url_for_landing_page")
                )
            url = _safe_url(url_value)
            if not url:
                continue
            row = dict(common)
            row["url"] = url
            row["format"] = fmt
            row["xml_url"] = (
                _safe_url(raw.get("xml_url"))
                if raw.get("xml_url")
                else (url if fmt == "xml" else None)
            )
            row["txt_url"] = (
                _safe_url(raw.get("txt_url") or raw.get("text_url"))
                if raw.get("txt_url") or raw.get("text_url")
                else (url if fmt == "txt" else None)
            )
            row["pdf_url"] = (
                _safe_url(raw.get("pdf_url") or raw.get("url_for_pdf"))
                if raw.get("pdf_url") or raw.get("url_for_pdf")
                else (url if fmt == "pdf" else None)
            )
            normalized.append(row)
    return normalized


def _candidate_identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(row["pmid"]),
        row["source"],
        row.get("url"),
        row.get("format"),
        row.get("article_version") or row.get("version"),
    )


def _article_prefix(pmcid: str, version: Any) -> str:
    value = _text(version) or "1"
    if value.upper().startswith(pmcid.upper() + "."):
        return value
    if value.isdigit():
        return f"{pmcid}.{value}"
    return value


def _inventory_record(row: Mapping[str, Any]) -> dict[str, Any] | None:
    key = _text(row.get("Key") or row.get("key") or row.get("name"))
    if not key:
        return None
    match = re.search(r"(PMC[0-9A-Za-z]+)\.(\d+)\.json$", key, re.I)
    if not match:
        match = re.search(r"(PMC[0-9A-Za-z]+)\.(\d+)", key, re.I)
    if not match:
        return None
    pmcid = match.group(1).upper()
    version = f"{pmcid}.{match.group(2)}"
    updated = (
        row.get("Last modified date")
        or row.get("LastModifiedDate")
        or row.get("last_modified")
        or row.get("updated_at")
    )
    prefix = _article_prefix(pmcid, version)
    record = dict(row)
    record.update({
        "pmcid": pmcid,
        "article_version": version,
        "version": version,
        "xml_url": f"{PMC_AWS_BUCKET}/{prefix}/{prefix}.xml",
        "text_url": f"{PMC_AWS_BUCKET}/{prefix}/{prefix}.txt",
        "updated_at": _text(updated),
    })
    record.setdefault("json_url", f"{PMC_AWS_BUCKET}/metadata/{prefix}.json")
    return record


def load_pmc_aws_inventory(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load a local PMC AWS metadata/inventory snapshot without downloading it."""

    source = Path(path)
    if not source.is_file():
        raise ValueError(f"PMC AWS inventory does not exist: {source}")
    text = source.read_text(encoding="utf-8-sig")
    records: list[Any]
    try:
        payload = json.loads(text)
        if isinstance(payload, Mapping):
            records = []
            for key, value in payload.items():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    item = dict(item)
                    item.setdefault("pmcid", key)
                    records.append(item)
        elif isinstance(payload, list):
            records = payload
        else:
            raise ValueError("PMC AWS inventory JSON must be an object or array")
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records = []
                break
        if not records:
            reader = csv.reader(text.splitlines())
            for fields in reader:
                if len(fields) >= 2:
                    records.append({
                        "Bucket": fields[0],
                        "Key": fields[1],
                        "Last modified date": fields[2] if len(fields) > 2 else None,
                    })
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        record = _inventory_record(raw)
        if record is None:
            record = dict(raw)
            pmcid = _text(record.get("pmcid") or record.get("pmc"))
            if pmcid:
                pmcid = pmcid.upper()
                record["pmcid"] = pmcid
        pmcid = _text(record.get("pmcid"))
        if pmcid:
            result.setdefault(pmcid.upper(), []).append(record)
    return result


class PmcAwsInventoryProvider:
    """Resolve PMC AWS candidates from a pre-existing local snapshot."""

    def __init__(
        self,
        inventory: Mapping[str, Any] | None = None,
        *,
        inventory_path: str | Path | None = None,
    ) -> None:
        if inventory_path is not None:
            inventory = load_pmc_aws_inventory(inventory_path)
        self.inventory = dict(inventory or {})

    def resolve(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        pmcid = _pmcid(task)
        if not pmcid:
            return []
        values = self.inventory.get(pmcid, self.inventory.get(pmcid.casefold(), []))
        if isinstance(values, Mapping):
            values = [values]
        result: list[dict[str, Any]] = []
        for raw in values or []:
            record = dict(raw)
            record.setdefault("pmcid", pmcid)
            if record.get("license_code") and not record.get("license"):
                record["license"] = record["license_code"]
            if record.get("text_url") and not record.get("txt_url"):
                record["txt_url"] = record["text_url"]
            result.append(record)
        return result


def _metadata_json(
    url: str,
    *,
    http_client: UnifiedHttpClient,
    pmid: int,
    source: str,
    route: str,
    identifier: str | None,
) -> Any:
    result = http_client.get(
        pmid=pmid,
        source=source,
        url=url,
        route=route,
        identifier=identifier,
        headers={"Accept": "application/json"},
        success_handler=lambda response: json.loads(response.body.decode("utf-8")),
    )
    if not result.ok:
        raise UnifiedHttpError(result)
    return result.artifact


def _result_payload(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    result_list = payload.get("resultList")
    if isinstance(result_list, Mapping):
        results = result_list.get("result")
        if isinstance(results, list):
            return results[0] if results else {}
        if isinstance(results, Mapping):
            return results
    return payload


def _fulltext_xml_entries(payload: Any) -> list[Mapping[str, Any]]:
    result = _result_payload(payload)
    if isinstance(result, Mapping):
        urls = result.get("fullTextUrlList")
        if isinstance(urls, Mapping):
            urls = urls.get("fullTextUrl")
        if isinstance(urls, Mapping):
            urls = [urls]
        if isinstance(urls, list):
            return [item for item in urls if isinstance(item, Mapping)]
        direct = result.get("fullTextXML") or result.get("fulltextXML")
        if direct:
            return [{"url": direct, "documentStyle": "fullTextXML"}]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, Mapping)]
    return []


class EuropePmcClient:
    """Query Europe PMC metadata; it does not request full-text bytes."""

    def __init__(
        self,
        *,
        fetcher: Callable[[Mapping[str, Any]], Any] | None = None,
        endpoint: str = EUROPE_PMC_SEARCH_URL,
        http_client: UnifiedHttpClient | None = None,
        config: Mapping[str, Any] | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self.fetcher = fetcher
        self.endpoint = endpoint
        self.http_client = http_client or UnifiedHttpClient.from_config(
            config if config is not None else load_project_config(), db_path=db_path
        )

    def resolve(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        pmcid = _pmcid(task)
        if not pmcid:
            return []
        if self.fetcher is not None:
            payload = self.fetcher(task)
        else:
            query = urllib.parse.urlencode({
                "query": f"PMCID:{pmcid}",
                "format": "json",
                "resultType": "core",
                "pageSize": "1",
            })
            payload = _metadata_json(
                f"{self.endpoint}?{query}",
                http_client=self.http_client,
                pmid=int(task["pmid"]),
                source=EUROPE_PMC,
                route="oa:europepmc:search",
                identifier=pmcid,
            )
        result: list[dict[str, Any]] = []
        for entry in _fulltext_xml_entries(payload):
            url = entry.get("url") or entry.get("fullTextXML")
            style = str(entry.get("documentStyle") or entry.get("type") or "").casefold()
            if not url or ("xml" not in style and "xml" not in str(url).casefold()):
                continue
            result.append({
                "pmcid": pmcid,
                "url": url,
                "format": "xml",
                "xml_url": url,
                "license": entry.get("license"),
                "updated_at": entry.get("updated_at") or entry.get("lastModifiedDate"),
                "metadata_json": entry,
            })
        return result


def _unpaywall_location(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


class UnpaywallClient:
    """Resolve DOI OA locations from Unpaywall's metadata API."""

    def __init__(
        self,
        *,
        email: str | None = None,
        fetcher: Callable[[Mapping[str, Any]], Any] | None = None,
        endpoint: str = UNPAYWALL_API_URL,
        http_client: UnifiedHttpClient | None = None,
        config: Mapping[str, Any] | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self.email = email or os.environ.get("UNPAYWALL_EMAIL")
        self.fetcher = fetcher
        self.endpoint = endpoint
        self.http_client = http_client or UnifiedHttpClient.from_config(
            config if config is not None else load_project_config(), db_path=db_path
        )

    def resolve(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        doi = _text(task.get("doi"))
        if not doi:
            return []
        if self.fetcher is not None:
            payload = self.fetcher(task)
        else:
            # Unpaywall requires a real contact email. Missing configuration
            # is a clean metadata-only result, not a fake OA candidate.
            if not self.email:
                return []
            encoded_doi = urllib.parse.quote(doi, safe="")
            query = urllib.parse.urlencode({"email": self.email})
            payload = _metadata_json(
                f"{self.endpoint}/{encoded_doi}?{query}",
                http_client=self.http_client,
                pmid=int(task["pmid"]),
                source=UNPAYWALL,
                route="oa:unpaywall:metadata",
                identifier=doi,
            )
        if not isinstance(payload, Mapping):
            raise ValueError("Unpaywall response must be a JSON object")

        best = _unpaywall_location(payload.get("best_oa_location"))
        best_url = best.get("url") or best.get("url_for_landing_page")
        locations = payload.get("oa_locations") or []
        if isinstance(locations, Mapping):
            locations = [locations]
        locations = [_unpaywall_location(item) for item in locations]
        if best and not any(item == best for item in locations):
            locations.insert(0, best)
        result: list[dict[str, Any]] = []
        for location in locations:
            url = location.get("url") or location.get("url_for_landing_page")
            pdf_url = location.get("url_for_pdf")
            if not url:
                url = pdf_url
            if not url:
                continue
            result.append({
                "url": url,
                "format": "pdf" if not location.get("url") and pdf_url else (
                    _format_from_url(url) or "html"
                ),
                "pdf_url": pdf_url,
                "url_for_pdf": pdf_url,
                "is_oa": payload.get("is_oa"),
                "oa_status": payload.get("oa_status"),
                "best_oa_location": best_url,
                "host_type": location.get("host_type"),
                "version": location.get("version"),
                "license": location.get("license") or payload.get("license"),
                "updated_at": payload.get("updated_at"),
                "metadata_json": payload,
                "_is_best": bool(best_url and url == best_url),
            })
        return result


class StaticSourceProvider:
    """Small mapping provider useful for offline fixtures and dry-runs."""

    def __init__(self, values: Mapping[Any, Any]) -> None:
        self.values = dict(values)

    def resolve(self, task: Mapping[str, Any]) -> Any:
        pmid = int(task["pmid"])
        pmcid = _pmcid(task)
        return self.values.get(pmid, self.values.get(str(pmid), self.values.get(pmcid, [])))


def _provider_failure(source: str, exc: BaseException) -> dict[str, Any]:
    error_class = getattr(exc, "error_class", None)
    status = getattr(exc, "code", None)
    if error_class is not None:
        classification = classify_request(error_class=error_class)
    elif isinstance(status, int):
        classification = classify_request(http_status=status)
    else:
        classification = classify_exception(exc)
    return {
        "source": source,
        "error_class": classification.error_class,
        "retryable": classification.retryable,
        "source_level": classification.source_level,
        "retry_policy": classification.retry_policy,
        "detail": str(exc),
    }


def _normalize_provider_values(
    task: Mapping[str, Any], source: str, values: Any
) -> list[dict[str, Any]]:
    # A provider may return a single Unpaywall response object rather than a
    # list of already-shaped candidates.
    if source == UNPAYWALL and isinstance(values, Mapping):
        if values.get("best_oa_location") is not None or values.get("oa_locations") is not None:
            values = UnpaywallClient(fetcher=lambda _task: values).resolve(task)
    normalized = _normalize_candidates(task, source, values)
    for row in normalized:
        row["pmid"] = int(task["pmid"])
    return normalized


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    best = row.get("best_oa_location") == row.get("url")
    return (
        int(row.get("priority") or SOURCE_PRIORITY[row["source"]]),
        0 if best else 1,
        FORMAT_ORDER.get(str(row.get("format") or "").casefold(), 99),
        str(row.get("source") or ""),
        str(row.get("url") or ""),
    )


def _write_candidates_in_connection(
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    resolved_at: str,
) -> int:
    written = 0
    for source_row in rows:
        row = dict(source_row)
        row["resolved_at"] = resolved_at
        identity = _candidate_identity(row)
        existing = conn.execute(
            """SELECT * FROM source_candidates
               WHERE pmid=? AND source=? AND url IS ?
                 AND format IS ? AND COALESCE(article_version, version) IS ?""",
            identity,
        ).fetchone()
        values = {
            field: row.get(field)
            for field in SOURCE_CANDIDATE_FIELDS
            if field in row and field != "pmid"
        }
        if existing is None:
            fields = [field for field in SOURCE_CANDIDATE_FIELDS if field in row]
            placeholders = ", ".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO source_candidates ({', '.join(fields)}) VALUES ({placeholders})",
                [row[field] for field in fields],
            )
            written += 1
            continue
        updates = {}
        for field, value in values.items():
            if field == "resolved_at" or value is not None:
                updates[field] = value
        if updates:
            clause = ", ".join(f'"{field}"=?' for field in updates)
            conn.execute(
                f"UPDATE source_candidates SET {clause} WHERE candidate_id=?",
                [*updates.values(), existing["candidate_id"]],
            )
    return written


def write_source_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    resolved_at: str | None = None,
) -> dict[str, int]:
    """Persist candidate provenance idempotently and preserve non-empty licenses."""

    ensure_source_candidates_schema(db_path)
    timestamp = resolved_at or utc_now()
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        written = _write_candidates_in_connection(conn, candidates, timestamp)
        conn.execute("COMMIT")
        return {"received": len(candidates), "written": written}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _task_update_values(
    task: Mapping[str, Any],
    selected: Mapping[str, Any] | None,
    resolved_at: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {"source_updated_at": resolved_at, "updated_at": resolved_at}
    if selected is None:
        return values
    values["source_url"] = selected.get("url")
    for field in ("oa_status", "reuse_allowed"):
        if selected.get(field) is not None:
            values[field] = selected[field]
    candidate_license = selected.get("license")
    old_license = _text(task.get("license"))
    if candidate_license and old_license in {None, "", "unverified"}:
        # Keep the source-provided license verbatim; only the A1 placeholder
        # may be replaced by an A4 source observation.
        values["license"] = candidate_license
    return values


def _update_task_metadata(
    conn: sqlite3.Connection,
    task: Mapping[str, Any],
    selected: Mapping[str, Any] | None,
    resolved_at: str,
) -> None:
    values = _task_update_values(task, selected, resolved_at)
    assignments = ", ".join(f'"{field}"=?' for field in values)
    conn.execute(
        f"UPDATE tasks SET {assignments} WHERE pmid=?",
        [*values.values(), int(task["pmid"])],
    )


def _canonical_status(value: Any) -> str:
    aliases = {
        "downloading": "fetching",
        "done": "metadata_only",
        "failed": "retryable_error",
        "not_found": "metadata_only",
    }
    return aliases.get(str(value or "pending").strip(), str(value or "pending").strip())


def _promote_task_status(
    pmid: int,
    *,
    db_path: str | Path,
    target: str,
    resolved_at: str,
) -> str:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT status FROM tasks WHERE pmid=?", (pmid,)).fetchone()
        if row is None:
            raise ValueError(f"task PMID {pmid} does not exist")
        current = _canonical_status(row["status"])
    finally:
        conn.close()
    if current in {"archived", "excluded", "fulltext_ready"}:
        return current
    transition_task_status(
        pmid,
        target,
        db_path=db_path,
        updated_at=resolved_at,
    )
    return target


class OAResolver:
    """Resolve candidates in fixed priority order with metadata-only providers."""

    def __init__(
        self,
        *,
        pmc_aws: Any | None = None,
        europe_pmc: Any | None = None,
        unpaywall: Any | None = None,
        publisher: Any | None = None,
        institutional_repository: Any | None = None,
        http_client: UnifiedHttpClient | None = None,
        config: Mapping[str, Any] | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        shared_client = http_client
        if shared_client is None and (europe_pmc is None or unpaywall is None):
            shared_client = UnifiedHttpClient.from_config(
                config if config is not None else load_project_config(), db_path=db_path
            )
        self.providers = {
            PMC_AWS: pmc_aws if pmc_aws is not None else PmcAwsInventoryProvider(),
            EUROPE_PMC: europe_pmc if europe_pmc is not None else EuropePmcClient(http_client=shared_client, db_path=db_path),
            UNPAYWALL: unpaywall if unpaywall is not None else UnpaywallClient(http_client=shared_client, db_path=db_path),
            PUBLISHER: publisher,
            INSTITUTIONAL_REPOSITORY: institutional_repository,
        }

    def resolve_one(
        self,
        task: Mapping[str, Any],
        *,
        db_path: str | Path = DEFAULT_DB_PATH,
        dry_run: bool = True,
        content_fetcher: Any | None = None,
        resolved_at: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one task; the dry-run path never calls content_fetcher."""

        del content_fetcher  # A4 intentionally has no content-fetch path.
        pmid = int(task["pmid"])
        timestamp = resolved_at or utc_now()
        candidates: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for source in SOURCE_ORDER:
            if source == EUROPE_PMC and not _pmcid(task):
                continue
            if source == UNPAYWALL and not _text(task.get("doi")):
                continue
            provider = self.providers[source]
            if provider is None:
                continue
            try:
                values = _provider_call(provider, task, source=source)
                normalized = _normalize_provider_values(task, source, values)
                for row in normalized:
                    identity = _candidate_identity(row)
                    if identity not in seen:
                        seen.add(identity)
                        candidates.append(row)
            except Exception as exc:
                failures.append(_provider_failure(source, exc))

        candidates.sort(key=_candidate_sort_key)
        selected = candidates[0] if candidates else None
        ensure_source_candidates_schema(db_path)
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            task_row = conn.execute("SELECT * FROM tasks WHERE pmid=?", (pmid,)).fetchone()
            if task_row is None:
                raise ValueError(f"task PMID {pmid} does not exist")
            _write_candidates_in_connection(conn, candidates, timestamp)
            _update_task_metadata(conn, dict(task_row), selected, timestamp)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        target = "oa_resolved" if selected is not None else (
            "retryable_error"
            if any(failure["retryable"] for failure in failures)
            else METADATA_ONLY
        )
        status = _promote_task_status(
            pmid, db_path=db_path, target=target, resolved_at=timestamp
        )
        return {
            "pmid": pmid,
            "pmcid": _pmcid(task),
            "dry_run": dry_run,
            "candidate_count": len(candidates),
            "candidates_written": len(candidates),
            "selected_source": selected.get("source") if selected else METADATA_ONLY,
            "selected_url": selected.get("url") if selected else None,
            "status": status,
            "failures": failures,
            "candidates": candidates,
        }

    def resolve_pmids(
        self,
        pmids: Iterable[str | int] | str | int,
        *,
        db_path: str | Path = DEFAULT_DB_PATH,
        dry_run: bool = True,
        content_fetcher: Any | None = None,
        resolved_at: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_pmids(pmids)
        ensure_source_candidates_schema(db_path)
        timestamp = resolved_at or utc_now()
        result: dict[str, Any] = {
            "requested": len(normalized),
            "dry_run": dry_run,
            "pmcid_count": 0,
            "pmc_aws_available": 0,
            "europe_pmc_available": 0,
            "unpaywall_available": 0,
            "publisher_available": 0,
            "institutional_repository_available": 0,
            "resolved": 0,
            "metadata_only": 0,
            "no_candidates": 0,
            "candidate_rows": 0,
            "failures": [],
            "items": [],
        }
        conn = _connect(db_path)
        try:
            tasks = {
                int(row["pmid"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM tasks WHERE pmid IN ({}) ORDER BY pmid".format(
                        ",".join("?" for _ in normalized)
                    ),
                    normalized,
                ).fetchall()
            }
        finally:
            conn.close()
        missing = [pmid for pmid in normalized if pmid not in tasks]
        for pmid in missing:
            result["failures"].append({
                "pmid": pmid,
                "source": "task",
                "error_class": "source_not_found",
                "retryable": False,
                "source_level": True,
                "retry_policy": "none",
                "detail": "task does not exist",
            })
        metric_keys = {
            PMC_AWS: "pmc_aws_available",
            EUROPE_PMC: "europe_pmc_available",
            UNPAYWALL: "unpaywall_available",
            PUBLISHER: "publisher_available",
            INSTITUTIONAL_REPOSITORY: "institutional_repository_available",
        }
        for pmid in normalized:
            task = tasks.get(pmid)
            if task is None:
                continue
            item = self.resolve_one(
                task,
                db_path=db_path,
                dry_run=dry_run,
                content_fetcher=content_fetcher,
                resolved_at=timestamp,
            )
            result["items"].append(item)
            result["candidate_rows"] += item["candidate_count"]
            if _pmcid(task):
                result["pmcid_count"] += 1
            if item["candidate_count"]:
                result["resolved"] += 1
            elif item["status"] == METADATA_ONLY:
                result["metadata_only"] += 1
            if not item["candidate_count"]:
                result["no_candidates"] += 1
            for candidate in item["candidates"]:
                result[metric_keys[candidate["source"]]] += 1
            result["failures"].extend(
                {"pmid": pmid, **failure} for failure in item["failures"]
            )
        return result


def resolve_oa(
    pmids: Iterable[str | int] | str | int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    dry_run: bool = True,
    pmc_aws: Any | None = None,
    europe_pmc: Any | None = None,
    unpaywall: Any | None = None,
    publisher: Any | None = None,
    institutional_repository: Any | None = None,
    content_fetcher: Any | None = None,
    http_client: UnifiedHttpClient | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Public A4 entry point. All content acquisition remains out of scope."""

    resolver = OAResolver(
        pmc_aws=pmc_aws,
        europe_pmc=europe_pmc,
        unpaywall=unpaywall,
        publisher=publisher,
        institutional_repository=institutional_repository,
        http_client=http_client,
        config=config,
        db_path=db_path,
    )
    return resolver.resolve_pmids(
        pmids,
        db_path=db_path,
        dry_run=dry_run,
        content_fetcher=content_fetcher,
    )


resolve_sources = resolve_oa


def _pmids_for_mode(
    *,
    db_path: str | Path,
    pmids: Sequence[str | int] | None = None,
    input_path: str | Path | None = None,
    batch_id: str | None = None,
    all_pmids: bool = False,
) -> list[int]:
    modes = int(pmids is not None) + int(input_path is not None)
    modes += int(batch_id is not None) + int(all_pmids)
    if modes != 1:
        raise ValueError("choose exactly one of pmid, input, batch-id, or all")
    if pmids is not None:
        return normalize_pmids(pmids)
    if input_path is not None:
        path = Path(input_path)
        if not path.is_file():
            raise ValueError(f"input file does not exist: {path}")
        values = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return normalize_pmids(values)
    conn = _connect(db_path)
    try:
        if batch_id is not None:
            rows = conn.execute(
                "SELECT DISTINCT pmid FROM task_discoveries WHERE batch_id=? ORDER BY pmid",
                (batch_id,),
            ).fetchall()
            if not rows:
                exists = conn.execute(
                    "SELECT 1 FROM discovery_batches WHERE batch_id=?", (batch_id,)
                ).fetchone()
                if exists is None:
                    raise ValueError(f"discovery batch not found: {batch_id}")
            return [int(row[0]) for row in rows]
        rows = conn.execute("SELECT pmid FROM tasks ORDER BY pmid").fetchall()
        return [int(row[0]) for row in rows]
    finally:
        conn.close()


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve trusted OA source candidates without downloading content"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--pmid", action="append", dest="pmids")
    modes.add_argument("--input", type=Path)
    modes.add_argument("--batch-id")
    modes.add_argument("--all", action="store_true")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--pmc-aws-inventory", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="metadata-only resolution; this stage never downloads content",
    )
    args = parser.parse_args(argv)
    selected = _pmids_for_mode(
        db_path=args.db,
        pmids=args.pmids,
        input_path=args.input,
        batch_id=args.batch_id,
        all_pmids=args.all,
    )
    pmc_provider = (
        PmcAwsInventoryProvider(inventory_path=args.pmc_aws_inventory)
        if args.pmc_aws_inventory else PmcAwsInventoryProvider()
    )
    http_client = UnifiedHttpClient.from_config(
        load_project_config(), db_path=args.db
    )
    resolver = OAResolver(
        pmc_aws=pmc_provider,
        http_client=http_client,
    )
    result = resolver.resolve_pmids(
        selected,
        db_path=args.db,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
