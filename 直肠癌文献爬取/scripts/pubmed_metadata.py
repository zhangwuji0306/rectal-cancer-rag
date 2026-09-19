# -*- coding: utf-8 -*-
"""A2 PubMed XML parsing and canonical metadata refresh.

The module treats the current PubMed XML response as the canonical metadata
source. It deliberately does not implement a task state machine, retry
orchestration, OA resolution, content acquisition, validation, or indexing.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from a1_schema import DEFAULT_DB_PATH, _connect, migrate_to_v2, utc_now
from fulltext_client import UnifiedHttpClient, UnifiedHttpError, load_project_config
from ingest_pmids import normalize_pmids


PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DEFAULT_BATCH_SIZE = 200

# This is intentionally the complete A2 write allow-list. No task state,
# fetch history, lease, content, retry, or discovery column is in this set.
ALLOWED_METADATA_FIELDS = (
    "doi",
    "pmcid",
    "title",
    "title_norm",
    "year",
    "journal",
    "issn",
    "authors",
    "first_author",
    "first_author_family",
    "pub_type",
    "language",
    "metadata_updated_at",
    "updated_at",
)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_INITIALS_RE = re.compile(r"^[A-Z](?:[A-Z]|\.){0,7}$")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


class PubMedFetchError(RuntimeError):
    """A single official PubMed request failed before any database write."""


class PubMedParseError(ValueError):
    """A PubMed response is not a usable PubmedArticle XML document."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element | None, name: str) -> list[ET.Element]:
    if element is None:
        return []
    return [child for child in list(element) if _local_name(child.tag) == name]


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    children = _children(element, name)
    return children[0] if children else None


def _descendants(element: ET.Element | None, name: str) -> list[ET.Element]:
    if element is None:
        return []
    return [node for node in element.iter() if node is not element and _local_name(node.tag) == name]


def _first_descendant(element: ET.Element | None, name: str) -> ET.Element | None:
    nodes = _descendants(element, name)
    return nodes[0] if nodes else None


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None


def _attr(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    for key, value in element.attrib.items():
        if _local_name(key).casefold() == name.casefold():
            return value.strip() or None
    return None


def _clean_identifier(value: str | None, *, prefix: str | None = None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if prefix == "doi":
        cleaned = _DOI_PREFIX_RE.sub("", cleaned).strip()
    if prefix == "pmcid" and cleaned.casefold().startswith("pmc"):
        cleaned = "PMC" + cleaned[3:]
    return cleaned or None


def _title_norm(title: str | None) -> str | None:
    if not title:
        return None
    normalized = unicodedata.normalize("NFKC", title).casefold()
    # Match the established project title_norm representation: punctuation is
    # a separator, while Unicode letters and digits remain searchable.
    normalized = "".join(char if char.isalnum() else " " for char in normalized)
    return " ".join(normalized.split()) or None


def _family_value(value: str | None) -> str | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(char if char.isalnum() or char in " -'" else " " for char in normalized)
    return " ".join(normalized.split()).strip(" -'") or None


def _looks_like_initials(value: str) -> bool:
    compact = value.replace(" ", "")
    return bool(compact) and bool(_INITIALS_RE.fullmatch(compact.upper())) and compact.upper() == compact


def normalize_author_family(
    value: str | None,
    fore_name: str | None = None,
    initials: str | None = None,
) -> str | None:
    """Return a normalized family name from structured or common text forms.

    When ``fore_name`` or ``initials`` is supplied, ``value`` is treated as
    the structured LastName. Without those arguments the fallback accepts
    ``Smith AB``, ``Smith, Andrew B`` and ``Andrew B Smith``.
    """

    if not value or not str(value).strip():
        return None
    text = " ".join(str(value).strip().split())
    if fore_name is not None or initials is not None:
        return _family_value(text)
    if "," in text:
        return _family_value(text.split(",", 1)[0])
    parts = text.split()
    if len(parts) == 1:
        return _family_value(parts[0])
    if _looks_like_initials(parts[-1]):
        return _family_value(parts[0])
    return _family_value(parts[-1])


def _format_author(
    last_name: str | None,
    fore_name: str | None,
    initials: str | None,
    collective_name: str | None,
    fallback: str | None,
) -> tuple[str | None, str | None]:
    if collective_name:
        return collective_name, normalize_author_family(collective_name)
    if last_name:
        given = fore_name or initials
        display = f"{last_name}, {given}" if given else last_name
        return display, normalize_author_family(last_name, fore_name, initials)
    if fallback:
        return fallback, normalize_author_family(fallback)
    return None, None


def _parse_authors(article: ET.Element | None) -> tuple[str | None, str | None, str | None]:
    author_list = _first_descendant(article, "AuthorList")
    formatted: list[str] = []
    first_author: str | None = None
    first_family: str | None = None
    for author in _children(author_list, "Author"):
        last_name = _text(_child(author, "LastName"))
        fore_name = _text(_child(author, "ForeName"))
        initials = _text(_child(author, "Initials"))
        collective_name = _text(_child(author, "CollectiveName"))
        fallback = _text(author) if not (last_name or fore_name or initials or collective_name) else None
        display, family = _format_author(last_name, fore_name, initials, collective_name, fallback)
        if display:
            formatted.append(display)
            if first_author is None:
                first_author = display
                first_family = family
    return "; ".join(formatted) or None, first_author, first_family


def _month_number(value: str | None) -> int | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit() and 1 <= int(stripped) <= 12:
        return int(stripped)
    return _MONTHS.get(stripped.casefold())


def _date_from_container(container: ET.Element | None) -> str | None:
    if container is None:
        return None
    year = _text(_child(container, "Year"))
    month = _text(_child(container, "Month"))
    day = _text(_child(container, "Day"))
    if year and year.isdigit() and len(year) == 4:
        month_number = _month_number(month)
        if month_number is not None:
            if day and day.isdigit() and 1 <= int(day) <= 31:
                return f"{int(year):04d}-{month_number:02d}-{int(day):02d}"
            return f"{int(year):04d}-{month_number:02d}"
        return year
    medline = _text(_child(container, "MedlineDate"))
    if medline:
        return medline
    return None


def _publication_date(article: ET.Element | None, pubmed_data: ET.Element | None) -> str | None:
    article_date = _first_descendant(article, "ArticleDate")
    date = _date_from_container(article_date)
    if date:
        return date
    journal = _first_descendant(article, "Journal")
    issue = _child(journal, "JournalIssue")
    date = _date_from_container(_child(issue, "PubDate"))
    if date:
        return date
    history = _child(pubmed_data, "History")
    history_dates = _children(history, "PubMedPubDate")
    for item in history_dates:
        if (_attr(item, "PubStatus") or "").casefold() in {"pubmed", "epublish", "ppublish"}:
            date = _date_from_container(item)
            if date:
                return date
    return _date_from_container(history_dates[0]) if history_dates else None


def _year_from_date(date: str | None) -> int | None:
    if not date:
        return None
    match = _YEAR_RE.search(date)
    return int(match.group(1)) if match else None


def _parse_article_ids(pubmed_data: ET.Element | None, article: ET.Element | None) -> dict[str, str]:
    result: dict[str, str] = {}
    article_id_list = _first_descendant(pubmed_data, "ArticleIdList")
    for node in _children(article_id_list, "ArticleId"):
        id_type = (_attr(node, "IdType") or "").casefold()
        value = _text(node)
        if id_type and value and id_type not in result:
            result[id_type] = value
    for node in _descendants(article, "ELocationID"):
        id_type = (_attr(node, "EIdType") or "").casefold()
        value = _text(node)
        if id_type and value and id_type not in result:
            result[id_type] = value
    return result


def _parse_issn(journal: ET.Element | None) -> str | None:
    nodes = _children(journal, "ISSN")
    if not nodes:
        return None
    # Electronic is the most specific current identifier when both forms are
    # present; otherwise preserve PubMed XML order.
    nodes.sort(key=lambda node: 0 if (_attr(node, "IssnType") or "").casefold() == "electronic" else 1)
    return _text(nodes[0])


def _parse_one_article(pubmed_article: ET.Element) -> dict[str, Any] | None:
    citation = _child(pubmed_article, "MedlineCitation")
    pubmed_data = _child(pubmed_article, "PubmedData")
    pmid_text = _text(_child(citation, "PMID"))
    if not pmid_text or not pmid_text.isdigit() or int(pmid_text) <= 0:
        return None
    article = _child(citation, "Article")
    journal = _first_descendant(article, "Journal")
    pub_date = _publication_date(article, pubmed_data)
    article_ids = _parse_article_ids(pubmed_data, article)
    doi = _clean_identifier(article_ids.get("doi"), prefix="doi")
    # NCBI currently emits ``pmc`` here; accept ``pmcid`` as well because
    # both labels occur in equivalent PubMed XML fixtures and the canonical
    # task column is named ``pmcid``.
    pmcid = _clean_identifier(
        article_ids.get("pmc") or article_ids.get("pmcid"), prefix="pmcid"
    )
    if doi is None:
        for node in _descendants(article, "ELocationID"):
            if (_attr(node, "EIdType") or "").casefold() == "doi":
                doi = _clean_identifier(_text(node), prefix="doi")
                break
    authors, first_author, first_family = _parse_authors(article)
    title = _text(_first_descendant(article, "ArticleTitle"))
    publication_types = [
        value for node in _children(_first_descendant(article, "PublicationTypeList"), "PublicationType")
        if (value := _text(node))
    ]
    languages = [
        value for node in _descendants(article, "Language")
        if (value := _text(node))
    ]
    return {
        "pmid": int(pmid_text),
        "doi": doi,
        "pmcid": pmcid,
        "title": title,
        "title_norm": _title_norm(title),
        "year": _year_from_date(pub_date),
        "journal": _text(_child(journal, "Title")) or _text(_child(journal, "ISOAbbreviation")),
        "issn": _parse_issn(journal),
        "authors": authors,
        "first_author": first_author,
        "first_author_family": first_family,
        "pub_type": "; ".join(publication_types) or None,
        "language": "; ".join(languages) or None,
        "publication_date": pub_date,
        "article_ids": article_ids,
    }


def parse_pubmed_xml(xml: str | bytes) -> dict[int, dict[str, Any]]:
    """Parse a PubMed XML payload into PMID-keyed canonical metadata."""

    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError) as exc:
        raise PubMedParseError(f"invalid PubMed XML: {exc}") from exc
    articles = [node for node in root.iter() if _local_name(node.tag) == "PubmedArticle"]
    parsed: dict[int, dict[str, Any]] = {}
    for article in articles:
        metadata = _parse_one_article(article)
        if metadata is not None:
            parsed[int(metadata["pmid"])] = metadata
    if not parsed:
        raise PubMedParseError("PubMed XML contains no usable PubmedArticle")
    return parsed


class PubMedClient:
    """One-shot official NCBI EFetch client; retry policy belongs to A3/A5."""

    def __init__(
        self,
        *,
        endpoint: str = PUBMED_EFETCH_URL,
        timeout: float | None = None,
        email: str | None = None,
        api_key: str | None = None,
        http_client: UnifiedHttpClient | None = None,
        config: Mapping[str, Any] | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.email = email
        self.api_key = api_key or os.environ.get("NCBI_API_KEY")
        self.http_client = http_client or UnifiedHttpClient.from_config(
            config if config is not None else load_project_config(), db_path=db_path
        )

    def fetch(self, pmids: Sequence[int]) -> bytes:
        query: dict[str, str] = {
            "db": "pubmed",
            "id": ",".join(str(pmid) for pmid in pmids),
            "retmode": "xml",
        }
        if self.email:
            query["email"] = self.email
        if self.api_key:
            query["api_key"] = self.api_key
        if not pmids:
            raise ValueError("at least one PMID is required")
        result = self.http_client.get(
            pmid=int(pmids[0]),
            source="NCBI",
            url=f"{self.endpoint}?{urllib.parse.urlencode(query)}",
            route="metadata:pubmed:efetch",
            identifier=",".join(str(pmid) for pmid in pmids),
            headers={"User-Agent": "writing-rag-pubmed-metadata/1.0"},
            success_handler=lambda response: response.body,
            record_attempts=False,
        )
        if not result.ok:
            error = UnifiedHttpError(result)
            raise PubMedFetchError(str(error)) from error
        return bytes(result.artifact)


def _call_fetcher(fetcher: Any, batch: list[int]) -> str | bytes:
    method = getattr(fetcher, "fetch", None)
    result = method(batch) if callable(method) else fetcher(batch)
    if not isinstance(result, (str, bytes)):
        raise TypeError("metadata fetcher must return PubMed XML as str or bytes")
    return result


def _chunks(values: Sequence[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def _metadata_value_is_non_empty(field: str, value: Any) -> bool:
    if value is None:
        return False
    if field == "year":
        return isinstance(value, int) and value > 0
    return isinstance(value, str) and bool(value.strip())


def _upsert_metadata(conn, metadata: Mapping[str, Any], refreshed_at: str) -> str:
    pmid = int(metadata["pmid"])
    if conn.execute("SELECT 1 FROM tasks WHERE pmid=?", (pmid,)).fetchone() is None:
        return "missing_task"
    assignments = {
        field: metadata.get(field)
        for field in ALLOWED_METADATA_FIELDS
        if field not in {"metadata_updated_at", "updated_at"}
        and _metadata_value_is_non_empty(field, metadata.get(field))
    }
    assignments["metadata_updated_at"] = refreshed_at
    assignments["updated_at"] = refreshed_at
    set_clause = ", ".join(f'"{field}"=?' for field in assignments)
    values = [assignments[field] for field in assignments]
    values.append(pmid)
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE pmid=?", values)
    return "refreshed"


def refresh_pubmed_metadata(
    pmids: Iterable[str | int] | str | int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    fetcher: Any | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: float | None = None,
    email: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Refresh selected task metadata from PubMed XML.

    ``fetcher`` is an offline-test seam: it receives a list of PMIDs and must
    return one XML payload. A failed batch is reported without a database
    write; no A3 error-state transition or retry scheduling is performed.
    """

    normalized = normalize_pmids(pmids)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    migrate_to_v2(db_path)
    client = fetcher or PubMedClient(timeout=timeout, email=email, api_key=api_key, db_path=db_path)
    result: dict[str, Any] = {
        "requested": len(normalized),
        "batches": 0,
        "refreshed": 0,
        "updated": 0,
        "missing_tasks": [],
        "missing_response": [],
        "failures": [],
    }
    for batch in _chunks(normalized, batch_size):
        result["batches"] += 1
        try:
            payload = _call_fetcher(client, batch)
            records = parse_pubmed_xml(payload)
        except Exception as exc:
            result["failures"].extend(
                {"pmid": pmid, "error_class": "temporary_fetch_or_parse_failure", "detail": str(exc)}
                for pmid in batch
            )
            continue

        result["missing_response"].extend(pmid for pmid in batch if pmid not in records)
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            refreshed_at = utc_now()
            for pmid in batch:
                record = records.get(pmid)
                if record is None:
                    continue
                outcome = _upsert_metadata(conn, record, refreshed_at)
                if outcome == "missing_task":
                    result["missing_tasks"].append(pmid)
                else:
                    result["refreshed"] += 1
                    result["updated"] += 1
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    result["failed"] = len(result["failures"])
    result["missing"] = len(result["missing_response"])
    return result


def _pmids_for_mode(
    *,
    db_path: str | Path,
    pmids: Sequence[str | int] | None = None,
    input_path: str | Path | None = None,
    batch_id: str | None = None,
    all_pmids: bool = False,
) -> list[int]:
    modes = int(pmids is not None) + int(input_path is not None) + int(batch_id is not None) + int(all_pmids)
    if modes != 1:
        raise ValueError("choose exactly one of pmid, input, batch-id, or all")
    if pmids is not None:
        return normalize_pmids(pmids)
    if input_path is not None:
        path = Path(input_path)
        if not path.is_file():
            raise ValueError(f"input file does not exist: {path}")
        values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return normalize_pmids(values)
    conn = _connect(db_path)
    try:
        if batch_id is not None:
            rows = conn.execute(
                "SELECT DISTINCT pmid FROM task_discoveries WHERE batch_id=? ORDER BY pmid",
                (batch_id,),
            ).fetchall()
            if not rows:
                exists = conn.execute("SELECT 1 FROM discovery_batches WHERE batch_id=?", (batch_id,)).fetchone()
                if exists is None:
                    raise ValueError(f"discovery batch not found: {batch_id}")
            return normalize_pmids([int(row[0]) for row in rows]) if rows else []
        rows = conn.execute("SELECT pmid FROM tasks ORDER BY pmid").fetchall()
        return normalize_pmids([int(row[0]) for row in rows]) if rows else []
    finally:
        conn.close()


def run_cli(argv: Sequence[str] | None = None, *, fetcher: Any | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Refresh canonical PubMed metadata into tasks.sqlite")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--pmid", action="append", dest="pmids", help="one PMID; may be repeated")
    modes.add_argument("--input", type=Path, help="UTF-8 file containing one PMID per line")
    modes.add_argument("--batch-id", help="refresh the unique PMIDs discovered in one A1 batch")
    modes.add_argument("--all", action="store_true", help="explicitly refresh every PMID in tasks")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--email")
    parser.add_argument("--api-key")
    args = parser.parse_args(argv)
    selected = _pmids_for_mode(
        db_path=args.db,
        pmids=args.pmids,
        input_path=args.input,
        batch_id=args.batch_id,
        all_pmids=args.all,
    )
    result = refresh_pubmed_metadata(
        selected,
        db_path=args.db,
        fetcher=fetcher,
        batch_size=args.batch_size,
        timeout=args.timeout,
        email=args.email,
        api_key=args.api_key,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
