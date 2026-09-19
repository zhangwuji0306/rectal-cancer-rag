# -*- coding: utf-8 -*-
"""A5 unified HTTP retry client for one full-text source request.

The client owns transport retries only. It delegates request classification
and durable attempt/status bookkeeping to the A3 state machine and never
performs content validation.
"""

from __future__ import annotations

import email.utils
import http.client
import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from a1_schema import DEFAULT_DB_PATH, utc_now
from state_machine import classify_exception, classify_request, record_fetch_attempt


SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "apikey", "authorization", "email", "key", "mail", "mailto",
    "ncbi_api_key", "password", "secret", "token", "access_token",
})
SECRET_ENV_NAMES = ("CROSSREF_MAILTO", "UNPAYWALL_EMAIL", "NCBI_API_KEY")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
MAX_REDIRECTS = 5
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

def load_project_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load the repository's canonical acquisition configuration."""
    with Path(config_path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("project config must be a JSON object")
    return config


def redact_text(value: Any) -> str | None:
    """Redact configured secrets and common labelled secret values."""
    if value is None:
        return None
    text = str(value)
    configured = sorted(
        (os.environ.get(name) for name in SECRET_ENV_NAMES),
        key=lambda item: len(item or ""),
        reverse=True,
    )
    for secret in configured:
        if secret:
            text = text.replace(secret, "[REDACTED]")
            encoded = urllib.parse.quote(secret, safe="")
            if encoded != secret:
                text = text.replace(encoded, "[REDACTED]")
    import re
    return re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|token|mailto|email)(\s*[=:]\s*)[^\s,;&]+",
        r"\1\2[REDACTED]",
        text,
    )


def redact_url(url: str) -> str:
    """Return an absolute URL with sensitive query values removed."""
    parsed = urllib.parse.urlsplit(str(url))
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_pairs = [
        (key, "[REDACTED]" if key.casefold() in SENSITIVE_QUERY_KEYS else value)
        for key, value in pairs
    ]
    netloc = parsed.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"[REDACTED]@{host}"
    redacted = urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path,
         urllib.parse.urlencode(redacted_pairs), parsed.fragment)
    )
    return redact_text(redacted) or "[REDACTED]"


@dataclass(frozen=True)
class RetrySettings:
    """One retry policy shared by every A5 source and official endpoint."""
    max_attempts: int = 3
    connect_timeout: float = 30.0
    read_timeout: float = 90.0
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    jitter_seconds: float = 0.5

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if any(value < 0 for value in (
            self.connect_timeout, self.read_timeout, self.backoff_base_seconds,
            self.backoff_max_seconds, self.jitter_seconds,
        )):
            raise ValueError("timeout, backoff and jitter values cannot be negative")


def retry_settings_from_config(config: Mapping[str, Any] | None = None) -> RetrySettings:
    """Load the unified policy and map old split retry counts if encountered."""
    config = config or {}
    raw = config.get("retry") or {}
    legacy = [
        config.get(name)
        for name in ("network_retries", "pdf_retries", "pmc_retries")
        if config.get(name) is not None
    ]
    max_attempts = raw.get("max_attempts")
    if max_attempts is None:
        max_attempts = max([int(value) for value in legacy], default=2) + 1
    return RetrySettings(
        max_attempts=int(max_attempts),
        connect_timeout=float(raw.get("connect_timeout", config.get("page_timeout", 30.0))),
        read_timeout=float(raw.get("read_timeout", config.get("download_timeout", 90.0))),
        backoff_base_seconds=float(raw.get("backoff_base_seconds", 1.0)),
        backoff_max_seconds=float(raw.get("backoff_max_seconds", 60.0)),
        jitter_seconds=float(raw.get("jitter_seconds", 0.5)),
    )


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def header(self, name: str) -> str | None:
        wanted = name.casefold()
        for key, value in self.headers.items():
            if str(key).casefold() == wanted:
                return str(value)
        return None


@dataclass
class FetchResult:
    ok: bool
    response: HttpResponse | None
    attempts: int
    error_class: str | None = None
    error_detail: str | None = None
    retry_after: str | None = None
    artifact: Any = None


class UnifiedHttpError(RuntimeError):
    """Expose a failed shared request without changing A3's taxonomy."""

    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.error_class = result.error_class or "unknown"
        self.code = result.response.status_code if result.response is not None else None
        detail = result.error_detail or self.error_class
        super().__init__(f"unified HTTP request failed ({self.error_class}): {detail}")


Transport = Callable[..., HttpResponse]
SuccessHandler = Callable[[HttpResponse], Any]


def _default_transport(
    url: str,
    *,
    connect_timeout: float,
    read_timeout: float,
    headers: Mapping[str, str],
) -> HttpResponse:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("full-text URL must be an absolute HTTP(S) URL")
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme.casefold() == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, port=parsed.port, timeout=connect_timeout)
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(read_timeout)
        connection.request("GET", path, headers=dict(headers))
        response = connection.getresponse()
        body = response.read()
        return HttpResponse(
            status_code=int(response.status),
            headers={str(key): str(value) for key, value in response.getheaders()},
            body=body,
            url=url,
        )
    finally:
        connection.close()


def _unwrap_exception(exception: BaseException) -> BaseException:
    if isinstance(exception, urllib.error.URLError) and exception.reason is not None:
        return exception.reason
    return exception


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds)


def _future_timestamp(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(
        microsecond=0
    ).isoformat()


def _coerce_response(response: Any, url: str) -> HttpResponse:
    if isinstance(response, HttpResponse):
        return response
    status = getattr(response, "status_code", getattr(response, "status", None))
    if status is None:
        raise TypeError("transport must return HttpResponse-like object")
    body = getattr(response, "body", None)
    if body is None and hasattr(response, "content"):
        body = response.content
    if body is None and callable(getattr(response, "read", None)):
        body = response.read()
    if isinstance(body, str):
        body = body.encode("utf-8")
    if body is None:
        body = b""
    return HttpResponse(
        status_code=int(status),
        headers=dict(getattr(response, "headers", {}) or {}),
        body=bytes(body),
        url=str(getattr(response, "url", url) or url),
    )


class UnifiedHttpClient:
    """GET client with one retry policy and one attempt record per request."""

    def __init__(
        self,
        *,
        settings: RetrySettings | None = None,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] | None = None,
        random_value: Callable[[], float] | None = None,
        logger: logging.Logger | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
        user_agent: str = "RectalCancerRAG/A5",
    ) -> None:
        self.settings = settings or RetrySettings()
        self.transport = transport or _default_transport
        self.sleeper = sleeper or time.sleep
        self.random_value = random_value or random.random
        self.logger = logger or logging.getLogger("rectal_rag.fulltext")
        self.db_path = db_path
        self.user_agent = user_agent

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None = None, **kwargs: Any) -> "UnifiedHttpClient":
        config = config or {}
        return cls(
            settings=retry_settings_from_config(config),
            user_agent=str(config.get("user_agent", "RectalCancerRAG/A5")),
            **kwargs,
        )

    @classmethod
    def from_project_config(
        cls, *, config_path: str | Path = DEFAULT_CONFIG_PATH, **kwargs: Any
    ) -> "UnifiedHttpClient":
        return cls.from_config(load_project_config(config_path), **kwargs)

    def _log(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **fields}
        if "url" in payload:
            payload["url"] = redact_url(str(payload["url"]))
        for key, value in list(payload.items()):
            if key in {"error_detail", "identifier"}:
                payload[key] = redact_text(value)
        self.logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _record(
        self,
        *,
        persist: bool = True,
        pmid: int,
        source: str,
        route: str,
        identifier: str | None,
        url: str,
        started_at: str,
        finished_at: str,
        response: HttpResponse | None,
        outcome: str,
        error_class: str | None,
        error_detail: str | None,
        exception: BaseException | None,
        retry_after: str | None,
        next_retry_at: str | None,
    ) -> None:
        if not persist:
            return
        record_fetch_attempt(
            pmid, source, db_path=self.db_path, route=route, url=redact_url(url),
            identifier=redact_text(identifier), started_at=started_at, finished_at=finished_at,
            http_status=response.status_code if response else None, outcome=outcome,
            error_class=error_class, error_detail=redact_text(error_detail),
            exception=exception, retry_after=retry_after, next_retry_at=next_retry_at,
            content_type=response.header("content-type") if response else None,
            content_length=len(response.body) if response else None,
        )

    def get(
        self,
        *,
        pmid: int,
        source: str,
        url: str,
        route: str | None = None,
        identifier: str | None = None,
        headers: Mapping[str, str] | None = None,
        success_handler: SuccessHandler | None = None,
        record_attempts: bool = True,
        success_handler_error_class: str = "storage_error",
    ) -> FetchResult:
        """Fetch one candidate and persist evidence for every transport call."""
        route = route or "fulltext"
        safe_url = redact_url(url)
        request_headers = {
            "Accept": "application/xml, text/xml, text/plain, text/html, application/pdf;q=0.9, */*;q=0.1",
            "User-Agent": self.user_agent,
        }
        if headers:
            request_headers.update({str(key): str(value) for key, value in headers.items()})
        last_response: HttpResponse | None = None
        last_error_class: str | None = None
        last_error_detail: str | None = None
        last_retry_after: str | None = None

        for attempt_number in range(1, self.settings.max_attempts + 1):
            started_at = utc_now()
            response: HttpResponse | None = None
            exception: BaseException | None = None
            retry_after: str | None = None
            classification = None
            request_url = url
            redirect_count = 0
            artifact = None
            attempt_error_detail: str | None = None
            try:
                while True:
                    response = _coerce_response(
                        self.transport(
                            request_url,
                            connect_timeout=self.settings.connect_timeout,
                            read_timeout=self.settings.read_timeout,
                            headers=request_headers,
                        ), request_url,
                    )
                    if response.status_code not in REDIRECT_STATUS_CODES:
                        break
                    location = response.header("location")
                    if not location:
                        break
                    if redirect_count >= MAX_REDIRECTS:
                        raise ValueError("maximum HTTP redirects exceeded")
                    request_url = urllib.parse.urljoin(request_url, location)
                    parsed_redirect = urllib.parse.urlsplit(request_url)
                    if parsed_redirect.scheme.casefold() not in {"http", "https"} or not parsed_redirect.hostname:
                        raise ValueError("HTTP redirect target must be an absolute HTTP(S) URL")
                    redirect_count += 1
                if response.url != request_url:
                    response = HttpResponse(
                        status_code=response.status_code,
                        headers=response.headers,
                        body=response.body,
                        url=request_url,
                    )
                last_response = response
                retry_after = response.header("retry-after")
                classification = classify_request(http_status=response.status_code)
                if 200 <= response.status_code < 300:
                    if success_handler is not None:
                        try:
                            artifact = success_handler(response)
                        except Exception as write_error:
                            attempt_error_detail = redact_text(str(write_error))
                            self._record(
                                persist=record_attempts,
                                pmid=pmid, source=source, route=route, identifier=identifier,
                                url=request_url, started_at=started_at, finished_at=utc_now(),
                                response=response, outcome="failure", error_class=success_handler_error_class,
                                error_detail=attempt_error_detail, exception=None,
                                retry_after=retry_after, next_retry_at=None,
                            )
                            self._log(
                                "request_finished", pmid=pmid, source=source, route=route,
                                url=safe_url, attempt=attempt_number,
                                http_status=response.status_code, outcome="failure",
                                error_class=success_handler_error_class,
                            )
                            return FetchResult(
                                ok=False, response=response, attempts=attempt_number,
                                error_class=success_handler_error_class, error_detail=attempt_error_detail,
                                retry_after=retry_after,
                            )
                    self._record(
                        persist=record_attempts,
                        pmid=pmid, source=source, route=route, identifier=identifier,
                        url=request_url, started_at=started_at, finished_at=utc_now(),
                        response=response, outcome="success", error_class=None,
                        error_detail=None, exception=None, retry_after=retry_after,
                        next_retry_at=None,
                    )
                    self._log(
                        "request_finished", pmid=pmid, source=source, route=route,
                        url=safe_url, attempt=attempt_number,
                        http_status=response.status_code, outcome="success",
                    )
                    return FetchResult(
                        ok=True, response=response, attempts=attempt_number, artifact=artifact,
                    )
                attempt_error_detail = f"HTTP {response.status_code}"
            except Exception as caught:
                last_response = None
                exception = _unwrap_exception(caught)
                classification = classify_exception(exception)
                attempt_error_detail = redact_text(str(exception))

            assert classification is not None
            last_error_class = classification.error_class
            last_error_detail = attempt_error_detail
            last_retry_after = retry_after
            can_retry = classification.retryable and attempt_number < self.settings.max_attempts
            delay = 0.0
            next_retry_at = None
            if can_retry:
                retry_after_seconds = (
                    _retry_after_seconds(retry_after)
                    if classification.retryable else None
                )
                if retry_after_seconds is not None:
                    delay = retry_after_seconds
                else:
                    delay = min(
                        self.settings.backoff_max_seconds,
                        self.settings.backoff_base_seconds * (2 ** (attempt_number - 1)),
                    ) + self.settings.jitter_seconds * max(
                        0.0, min(1.0, float(self.random_value()))
                    )
                next_retry_at = _future_timestamp(delay)

            self._record(
                persist=record_attempts,
                pmid=pmid, source=source, route=route, identifier=identifier, url=request_url,
                started_at=started_at, finished_at=utc_now(), response=response,
                outcome="failure", error_class=classification.error_class,
                error_detail=attempt_error_detail, exception=exception,
                retry_after=retry_after, next_retry_at=next_retry_at,
            )
            self._log(
                "request_finished", pmid=pmid, source=source, route=route, url=safe_url,
                attempt=attempt_number, http_status=response.status_code if response else None,
                outcome="failure", error_class=classification.error_class,
                retry_after=retry_after,
            )
            if not can_retry:
                return FetchResult(
                    ok=False, response=last_response, attempts=attempt_number,
                    error_class=last_error_class, error_detail=last_error_detail,
                    retry_after=last_retry_after,
                )
            self._log(
                "retry_scheduled",
                pmid=pmid, source=source, route=route, url=safe_url,
                attempt=attempt_number, next_attempt=attempt_number + 1,
                delay_seconds=delay, retry_after=retry_after,
            )
            self.sleeper(delay)

        return FetchResult(
            ok=False, response=last_response, attempts=self.settings.max_attempts,
            error_class=last_error_class, error_detail=last_error_detail,
            retry_after=last_retry_after,
        )
