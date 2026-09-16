"""Offline regression tests for the A5 full-text acquisition layer."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "直肠癌文献爬取" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import a1_schema as schema  # noqa: E402
import fetch_fulltext as fetch  # noqa: E402
import fulltext_client as client_module  # noqa: E402
import ingest_pmids as ingest  # noqa: E402
import oa_resolver as oa  # noqa: E402
import pubmed_metadata as pubmed  # noqa: E402


class A5FullTextTests(unittest.TestCase):
    pmid = 5001

    def make_db(self, path: Path, *, status: str = "oa_resolved") -> None:
        schema.migrate_to_v2(path)
        ingest.ingest_pmids(
            [self.pmid], source="a5-test", batch_id="a5-test-batch", db_path=path
        )
        conn = sqlite3.connect(path)
        conn.execute(
            "UPDATE tasks SET pmcid=?, doi=?, title=?, status=? WHERE pmid=?",
            (
                "PMC5001",
                "10.1000/a5",
                "A5 full-text test article",
                status,
                self.pmid,
            ),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def task(path: Path) -> dict:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute(
                "SELECT * FROM tasks WHERE pmid=?", (A5FullTextTests.pmid,)
            ).fetchone())
        finally:
            conn.close()

    @staticmethod
    def attempts(path: Path) -> list[dict]:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM fetch_attempts ORDER BY started_at, attempt_id"
            )]
        finally:
            conn.close()

    @staticmethod
    def response(status: int, body: bytes = b"body", headers=None) -> client_module.HttpResponse:
        return client_module.HttpResponse(
            status_code=status,
            headers=headers or {"Content-Type": "application/xml"},
            body=body,
            url="https://mock.example/response",
        )

    @staticmethod
    def sequence_transport(items):
        pending = list(items)
        calls = []

        def transport(url, **kwargs):
            calls.append((url, kwargs))
            item = pending.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        return transport, calls

    def make_client(self, db: Path, transport, *, max_attempts=3, sleeper=None,
                    random_value=lambda: 0.0, logger=None, **settings):
        retry = client_module.RetrySettings(
            max_attempts=max_attempts,
            backoff_base_seconds=settings.pop("backoff_base_seconds", 1.0),
            backoff_max_seconds=settings.pop("backoff_max_seconds", 60.0),
            jitter_seconds=settings.pop("jitter_seconds", 0.0),
            connect_timeout=settings.pop("connect_timeout", 3.0),
            read_timeout=settings.pop("read_timeout", 4.0),
        )
        self.assertFalse(settings)
        return client_module.UnifiedHttpClient(
            settings=retry,
            transport=transport,
            sleeper=sleeper or (lambda _seconds: None),
            random_value=random_value,
            logger=logger,
            db_path=db,
        )

    def test_mock_success_writes_attempt_evidence_without_a6_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            transport, calls = self.sequence_transport([self.response(200, b"<article/>")])
            result = self.make_client(db, transport, max_attempts=1).get(
                pmid=self.pmid,
                source=oa.PMC_AWS,
                url="https://aws.example/PMC5001.xml",
                route="fulltext:xml",
                identifier="PMC5001",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.attempts, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["connect_timeout"], 3.0)
            self.assertEqual(calls[0][1]["read_timeout"], 4.0)
            attempt = self.attempts(db)[0]
            self.assertEqual(attempt["source"], oa.PMC_AWS)
            self.assertEqual(attempt["url"], "https://aws.example/PMC5001.xml")
            self.assertEqual(attempt["http_status"], 200)
            self.assertEqual(attempt["outcome"], "success")
            self.assertEqual(attempt["error_class"], None)
            self.assertNotEqual(self.task(db)["status"], "fulltext_ready")

    def test_429_honors_retry_after_and_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            delays = []
            transport, _ = self.sequence_transport([
                self.response(429, headers={"Retry-After": "7"}),
                self.response(200),
            ])
            result = self.make_client(
                db, transport, sleeper=delays.append, max_attempts=3
            ).get(
                pmid=self.pmid, source=oa.EUROPE_PMC,
                url="https://europe.example/PMC5001.xml",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.attempts, 2)
            self.assertEqual(delays, [7.0])
            attempts = self.attempts(db)
            self.assertEqual(sorted(row["http_status"] for row in attempts), [200, 429])
            limited = next(row for row in attempts if row["http_status"] == 429)
            successful = next(row for row in attempts if row["http_status"] == 200)
            self.assertEqual(limited["error_class"], "rate_limit")
            self.assertEqual(limited["retry_after"], "7")
            self.assertEqual(successful["outcome"], "success")

    def test_5xx_uses_exponential_backoff_and_jitter(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            delays = []
            transport, _ = self.sequence_transport([
                self.response(503), self.response(200)
            ])
            result = self.make_client(
                db,
                transport,
                sleeper=delays.append,
                max_attempts=2,
                backoff_base_seconds=2.0,
                jitter_seconds=1.0,
                random_value=lambda: 0.25,
            ).get(pmid=self.pmid, source=oa.PMC_AWS,
                  url="https://aws.example/PMC5001.xml")
            self.assertTrue(result.ok)
            self.assertEqual(delays, [2.25])
            self.assertTrue(any(row["error_class"] == "server_error" for row in self.attempts(db)))

    def test_timeout_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            transport, _ = self.sequence_transport([
                TimeoutError("read timeout"), self.response(200)
            ])
            result = self.make_client(db, transport, max_attempts=2).get(
                pmid=self.pmid, source=oa.EUROPE_PMC,
                url="https://europe.example/PMC5001.xml",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.attempts, 2)
            attempts = self.attempts(db)
            timed_out = next(row for row in attempts if row["error_class"] == "timeout")
            self.assertEqual(timed_out["retryable"], 1)

    def test_max_attempts_stops_after_unified_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            delays = []
            transport, calls = self.sequence_transport([
                self.response(500), self.response(502), self.response(503),
                self.response(200),
            ])
            result = self.make_client(
                db, transport, sleeper=delays.append, max_attempts=3
            ).get(
                pmid=self.pmid, source=oa.PMC_AWS,
                url="https://aws.example/PMC5001.xml",
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.attempts, 3)
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(delays), 2)
            self.assertEqual(result.error_class, "server_error")
            self.assertEqual(len(self.attempts(db)), 3)
            self.assertEqual(self.task(db)["status"], "retryable_error")

    def test_source_priority_matches_a5_sequence(self):
        candidates = [
            {"source": oa.INSTITUTIONAL_REPOSITORY, "url": "https://repo/x.pdf", "format": "pdf"},
            {"source": oa.PUBLISHER, "url": "https://publisher/x.xml", "format": "xml"},
            {"source": oa.PMC_AWS, "url": "https://aws/x.txt", "format": "txt"},
            {"source": oa.UNPAYWALL, "url": "https://oa/x.html", "format": "html"},
            {"source": oa.EUROPE_PMC, "url": "https://europe/x.xml", "format": "xml"},
            {"source": oa.PMC_AWS, "url": "https://aws/x.xml", "format": "xml"},
        ]
        ordered = fetch.order_candidates(candidates)
        self.assertEqual(
            [(row["source"], row["format"]) for row in ordered],
            [
                (oa.PMC_AWS, "xml"),
                (oa.EUROPE_PMC, "xml"),
                (oa.PMC_AWS, "txt"),
                (oa.UNPAYWALL, "html"),
                (oa.PUBLISHER, "xml"),
                (oa.INSTITUTIONAL_REPOSITORY, "pdf"),
            ],
        )
        self.assertNotIn(
            {"source": oa.UNPAYWALL, "url": "https://oa/x.txt", "format": "txt"},
            fetch.order_candidates([
                {"source": oa.UNPAYWALL, "url": "https://oa/x.txt", "format": "txt"}
            ]),
        )

    def test_source_json_is_complete_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "corpus_raw"
            secret = "a5-secret@example.org"
            candidate = {
                "source": oa.UNPAYWALL,
                "url": f"https://oa.example/article?email={secret}&api_key=key-secret",
                "format": "html",
                "pmcid": "PMC5001",
                "license": "CC BY 4.0",
                "version": "publishedVersion",
            }
            response = self.response(200, b"<html>offline</html>")
            with patch.dict(os.environ, {"CROSSREF_MAILTO": secret}, clear=False):
                artifact = fetch.RawCorpusWriter(raw_root).write(
                    pmid=self.pmid,
                    task={"doi": "10.1000/a5"},
                    candidate=candidate,
                    response=response,
                )
            record = json.loads(artifact.source_json.read_text(encoding="utf-8"))
            self.assertEqual(
                set(record),
                {"PMID", "DOI", "PMCID", "source", "URL", "license",
                 "retrieved_at", "sha256", "format", "version"},
            )
            self.assertEqual(record["PMID"], self.pmid)
            self.assertEqual(record["DOI"], "10.1000/a5")
            self.assertEqual(record["PMCID"], "PMC5001")
            self.assertEqual(record["format"], "html")
            self.assertEqual(record["version"], "publishedVersion")
            source_text = artifact.source_json.read_text(encoding="utf-8")
            self.assertNotIn(secret, source_text)
            self.assertNotIn("key-secret", source_text)
            self.assertEqual(record["URL"], "https://oa.example/article?email=[REDACTED]&api_key=[REDACTED]")

    def test_structured_logs_and_attempt_url_redact_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            secret = "log-secret@example.org"
            stream = io.StringIO()
            logger = logging.getLogger(f"a5-test-{id(stream)}")
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            logger.addHandler(handler)
            try:
                transport, _ = self.sequence_transport([self.response(200)])
                with patch.dict(os.environ, {"CROSSREF_MAILTO": secret}, clear=False):
                    self.make_client(db, transport, max_attempts=1, logger=logger).get(
                        pmid=self.pmid,
                        source=oa.PMC_AWS,
                        url=f"https://aws.example/article?contact={secret}&api_key=api-secret",
                    )
            finally:
                logger.removeHandler(handler)
                handler.close()
            log_text = stream.getvalue()
            attempt_url = self.attempts(db)[0]["url"]
            self.assertNotIn(secret, log_text)
            self.assertNotIn("api-secret", log_text)
            self.assertNotIn(secret, attempt_url)
            self.assertNotIn("api-secret", attempt_url)
            self.assertIn("[REDACTED]", log_text)
            self.assertIn("[REDACTED]", attempt_url)

    def test_raw_write_failure_records_storage_error_without_ready_status(self):
        class FailingWriter:
            def write(self, **_kwargs):
                raise OSError("raw write failed")

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            raw_root = Path(tmp) / "corpus_raw"
            self.make_db(db)
            transport, _ = self.sequence_transport([self.response(200)])
            candidate = {
                "source": oa.PMC_AWS,
                "url": "https://aws.example/PMC5001.xml",
                "format": "xml",
                "pmcid": "PMC5001",
                "license": "CC BY 4.0",
            }
            result = fetch.acquire_one(
                self.pmid,
                db_path=db,
                raw_root=raw_root,
                candidates=[candidate],
                client=self.make_client(db, transport, max_attempts=1),
                writer=FailingWriter(),
            )
            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(result["failures"][0]["error_class"], "storage_error")
            task = self.task(db)
            self.assertEqual(task["status"], "metadata_only")
            self.assertIsNone(task["content_path"])
            self.assertIsNone(task["content_sha256"])
            self.assertEqual(self.attempts(db)[0]["error_class"], "storage_error")
            self.assertFalse((raw_root / f"PMID_{self.pmid}").exists())

    def test_config_uses_one_retry_policy_and_env_only_api_contacts(self):
        config = json.loads(
            (ROOT / "直肠癌文献爬取" / "config.json").read_text(encoding="utf-8")
        )
        self.assertFalse(
            {"network_retries", "pdf_retries", "pmc_retries", "crossref_mailto"}
            & set(config)
        )
        self.assertEqual(config["retry"]["max_attempts"], 3)
        mapped = client_module.retry_settings_from_config({
            "network_retries": 2,
            "pdf_retries": 1,
            "pmc_retries": 0,
        })
        self.assertEqual(mapped.max_attempts, 3)
        env_lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        for key in ("CROSSREF_MAILTO", "UNPAYWALL_EMAIL", "NCBI_API_KEY"):
            line = next(line for line in env_lines if line.startswith(f"{key}="))
            self.assertEqual(line.split("=", 1)[1], "")
        self.assertNotIn("research@example.com", "\n".join(env_lines))


    def test_each_fetch_attempt_keeps_its_own_error_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            transport, _ = self.sequence_transport([
                self.response(503, headers={"Content-Type": "text/plain"}),
                TimeoutError("second attempt read timeout"),
            ])
            result = self.make_client(db, transport, max_attempts=2).get(
                pmid=self.pmid,
                source=oa.PMC_AWS,
                url="https://aws.example/PMC5001.xml",
            )
            self.assertFalse(result.ok)
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT http_status, error_class, error_detail "
                    "FROM fetch_attempts ORDER BY rowid"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(
                rows,
                [
                    (503, "server_error", "HTTP 503"),
                    (None, "timeout", "second attempt read timeout"),
                ],
            )
            self.assertEqual(self.task(db)["last_error_detail"], "second attempt read timeout")

    def test_raw_directory_write_and_publish_failures_leave_no_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "corpus_raw"
            writer = fetch.RawCorpusWriter(raw_root)
            candidate = {
                "source": oa.PMC_AWS,
                "url": "https://aws.example/PMC5001.xml",
                "format": "xml",
                "pmcid": "PMC5001",
                "license": "CC BY 4.0",
            }
            response = self.response(200, b"<article>fault injection</article>")
            for patch_target, message in (
                ("_atomic_write_text", "source.json write failed"),
                ("_publish_directory", "raw directory publish failed"),
            ):
                with self.subTest(failure=patch_target):
                    with patch.object(
                        fetch.RawCorpusWriter, patch_target,
                        side_effect=OSError(message),
                    ):
                        with self.assertRaisesRegex(OSError, message):
                            writer.write(
                                pmid=self.pmid,
                                task={"doi": "10.1000/a5"},
                                candidate=candidate,
                                response=response,
                            )
                    target = raw_root / f"PMID_{self.pmid}"
                    self.assertFalse(target.exists())
                    if raw_root.exists():
                        self.assertEqual(list(raw_root.iterdir()), [])

    def test_fetch_cli_builds_shared_client_from_configured_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            raw_root = Path(tmp) / "corpus_raw"
            self.make_db(db)
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO source_candidates "
                "(pmid, source, url, format, version, license, priority, resolved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.pmid, oa.PMC_AWS, "https://aws.example/PMC5001.xml", "xml",
                    "publishedVersion", "CC BY 4.0", 1, "2026-09-16T00:00:00+00:00",
                ),
            )
            conn.commit()
            conn.close()
            config = {
                "user_agent": "configured-a5-agent",
                "retry": {
                    "max_attempts": 1,
                    "connect_timeout": 17.0,
                    "read_timeout": 19.0,
                    "backoff_base_seconds": 0.0,
                    "backoff_max_seconds": 0.0,
                    "jitter_seconds": 0.0,
                },
            }
            transport, calls = self.sequence_transport([
                self.response(200, b"<article>entrypoint</article>"),
            ])
            built_configs = []
            original_from_config = client_module.UnifiedHttpClient.from_config

            def build_client(received_config, **kwargs):
                built_configs.append(received_config)
                return original_from_config(
                    received_config,
                    transport=transport,
                    sleeper=lambda _seconds: None,
                    random_value=lambda: 0.0,
                    **kwargs,
                )

            with patch.object(fetch, "load_project_config", return_value=config), \
                 patch.object(fetch.UnifiedHttpClient, "from_config", side_effect=build_client), \
                 contextlib.redirect_stdout(io.StringIO()):
                exit_code = fetch.run_cli([
                    str(self.pmid), "--db", str(db), "--raw-root", str(raw_root),
                ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(built_configs, [config])
            self.assertEqual(calls[0][1]["connect_timeout"], 17.0)
            self.assertEqual(calls[0][1]["read_timeout"], 19.0)
            self.assertEqual(
                json.loads(
                    (raw_root / f"PMID_{self.pmid}" / "source.json").read_text(
                        encoding="utf-8"
                    )
                )["source"],
                oa.PMC_AWS,
            )

    def test_ncbi_email_is_not_loaded_from_legacy_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            transport, calls = self.sequence_transport([self.response(200, b"<PubmedArticleSet/>")])
            shared_client = self.make_client(db, transport, max_attempts=1)
            with patch.dict(os.environ, {"NCBI_EMAIL": "legacy-secret@example.org"}, clear=False):
                pubmed.PubMedClient(http_client=shared_client, db_path=db).fetch([self.pmid])
            self.assertEqual(len(calls), 1)
            self.assertNotIn("legacy-secret@example.org", calls[0][0])
            self.assertNotIn("email=", calls[0][0])

    def test_legacy_downloader_official_paths_use_shared_configured_client(self):
        spec = importlib.util.spec_from_file_location(
            "a5_downloader", SCRIPTS / "03_downloader.py"
        )
        downloader = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(downloader)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            pdf_dir = Path(tmp) / "pdfs"
            self.make_db(db)
            conn = sqlite3.connect(db)
            conn.execute(
                "UPDATE tasks SET pmc=?, doi=NULL WHERE pmid=?",
                ("PMC5001", self.pmid),
            )
            conn.commit()
            conn.close()
            config = {
                "user_agent": "configured-downloader-agent",
                "min_pdf_size": 1,
                "allow_scihub": False,
                "retry": {
                    "max_attempts": 1,
                    "connect_timeout": 23.0,
                    "read_timeout": 29.0,
                    "backoff_base_seconds": 0.0,
                    "backoff_max_seconds": 0.0,
                    "jitter_seconds": 0.0,
                },
            }
            transport, calls = self.sequence_transport([
                self.response(200, b"%PDF-1.4\\n%%EOF", {"Content-Type": "application/pdf"}),
            ])
            built_configs = []
            original_from_config = downloader.UnifiedHttpClient.from_config

            def build_client(received_config, **kwargs):
                built_configs.append(received_config)
                return original_from_config(
                    received_config,
                    transport=transport,
                    sleeper=lambda _seconds: None,
                    random_value=lambda: 0.0,
                    **kwargs,
                )

            with patch.object(downloader, "setup_logger", return_value=logging.getLogger("a5-downloader-test")), \
                 patch.object(downloader.UnifiedHttpClient, "from_config", side_effect=build_client):
                crawler = downloader.Crawler(
                    config, str(db), str(pdf_dir), workers=1
                )
            try:
                path = crawler.try_europepmc({"pmid": self.pmid, "pmc": "PMC5001"})
            finally:
                crawler.conn.close()
            self.assertEqual(built_configs, [config])
            self.assertEqual(calls[0][1]["connect_timeout"], 23.0)
            self.assertEqual(calls[0][1]["read_timeout"], 29.0)
            self.assertTrue(Path(path).exists())
            attempt = self.attempts(db)[0]
            self.assertEqual(attempt["source"], "EuropePMC")
            self.assertEqual(attempt["route"], "legacy:03:europepmc:pdf")


if __name__ == "__main__":
    unittest.main()
