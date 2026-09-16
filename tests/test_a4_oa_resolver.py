"""Offline regression tests for the A4 OA resolver."""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "直肠癌文献爬取" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema = load_module("a1_schema_for_a4_test", SCRIPTS / "a1_schema.py")
ingest = load_module("ingest_for_a4_test", SCRIPTS / "ingest_pmids.py")
oa = load_module("oa_resolver_test", SCRIPTS / "oa_resolver.py")


class A4OAResolverTests(unittest.TestCase):
    def make_db(self, path, pmid=4001, *, pmcid="PMC777", doi="10.1000/a4"):
        schema.migrate_to_v2(path)
        ingest.ingest_pmids(
            [pmid], source="a4-test", batch_id=f"batch-{pmid}", db_path=path
        )
        conn = sqlite3.connect(path)
        conn.execute(
            "UPDATE tasks SET pmcid=?, doi=?, title=?, status='metadata_ready' WHERE pmid=?",
            (pmcid, doi, "A4 resolver test article", pmid),
        )
        conn.commit()
        conn.close()

    def rows(self, db):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM source_candidates ORDER BY candidate_id"
            )]
        finally:
            conn.close()

    def task(self, db, pmid=4001):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute(
                "SELECT * FROM tasks WHERE pmid=?", (pmid,)
            ).fetchone())
        finally:
            conn.close()

    def test_source_priority_and_all_candidates_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            providers = {
                "pmc_aws": lambda task: [{
                    "url": "https://aws.example/PMC777.1.xml",
                    "format": "xml",
                    "article_version": "PMC777.1",
                    "license": "CC BY 4.0",
                }],
                "europe_pmc": lambda task: [{
                    "url": "https://europe.example/PMC777.xml",
                    "format": "xml",
                }],
                "unpaywall": lambda task: [{
                    "url": "https://repository.example/a4",
                    "format": "html",
                }],
                "publisher": lambda task: [{
                    "url": "https://publisher.example/a4",
                    "format": "html",
                }],
                "institutional_repository": lambda task: [{
                    "url": "https://repo.example/a4",
                    "format": "pdf",
                }],
            }
            result = oa.resolve_oa(
                [4001], db_path=db, dry_run=True, **providers
            )
            item = result["items"][0]
            self.assertEqual(item["selected_source"], oa.PMC_AWS)
            self.assertEqual(item["candidate_count"], 5)
            self.assertEqual(result["resolved"], 1)
            self.assertEqual(result["pmc_aws_available"], 1)
            self.assertEqual(result["europe_pmc_available"], 1)
            self.assertEqual(result["unpaywall_available"], 1)
            self.assertEqual(result["publisher_available"], 1)
            self.assertEqual(result["institutional_repository_available"], 1)
            self.assertEqual([row["priority"] for row in self.rows(db)], [1, 2, 3, 4, 4])
            self.assertEqual(self.task(db)["status"], "oa_resolved")

    def test_pmcid_version_license_and_urls_are_traceable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            provider = oa.PmcAwsInventoryProvider({
                "PMC777": [{
                    "version": "2",
                    "license_code": "CC BY-SA",
                    "xml_url": "https://aws.example/PMC777.2.xml",
                    "text_url": "https://aws.example/PMC777.2.txt",
                    "pdf_url": "https://aws.example/PMC777.2.pdf",
                    "updated_at": "2026-09-15T01:02:03Z",
                }]
            })
            result = oa.resolve_oa([4001], db_path=db, pmc_aws=provider)
            rows = self.rows(db)
            self.assertEqual(result["candidate_rows"], 3)
            self.assertEqual({row["pmcid"] for row in rows}, {"PMC777"})
            self.assertEqual({row["article_version"] for row in rows}, {"2"})
            self.assertEqual({row["license"] for row in rows}, {"CC BY-SA"})
            self.assertEqual({row["updated_at"] for row in rows}, {"2026-09-15T01:02:03Z"})
            self.assertEqual(rows[0]["xml_url"], "https://aws.example/PMC777.2.xml")
            self.assertEqual(rows[1]["txt_url"], "https://aws.example/PMC777.2.txt")
            self.assertEqual(rows[2]["pdf_url"], "https://aws.example/PMC777.2.pdf")
            self.assertEqual(self.task(db)["license"], "CC BY-SA")

    def test_unpaywall_fields_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, doi="10.1000/unpaywall")
            payload = {
                "is_oa": True,
                "oa_status": "gold",
                "license": "cc-by",
                "best_oa_location": {
                    "url": "https://oa.example/article",
                    "url_for_pdf": "https://oa.example/article.pdf",
                    "host_type": "publisher",
                    "version": "publishedVersion",
                },
                "oa_locations": [{
                    "url": "https://oa.example/article",
                    "url_for_pdf": "https://oa.example/article.pdf",
                    "host_type": "publisher",
                    "version": "publishedVersion",
                }],
                "updated_at": "2026-09-15T02:00:00Z",
            }
            result = oa.resolve_oa(
                [4001],
                db_path=db,
                pmc_aws=lambda task: [],
                europe_pmc=lambda task: [],
                unpaywall=payload,
            )
            self.assertEqual(result["selected_source"] if "selected_source" in result else None, None)
            row = self.rows(db)[0]
            self.assertEqual(row["is_oa"], 1)
            self.assertEqual(row["oa_status"], "gold")
            self.assertEqual(row["best_oa_location"], "https://oa.example/article")
            self.assertEqual(row["pdf_url"], "https://oa.example/article.pdf")
            self.assertEqual(row["url_for_pdf"], "https://oa.example/article.pdf")
            self.assertEqual(row["host_type"], "publisher")
            self.assertEqual(row["version"], "publishedVersion")
            self.assertEqual(row["license"], "cc-by")

    def test_dry_run_never_calls_content_fetcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            calls = []

            def content_fetcher(*args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("A4 must not fetch content")

            result = oa.resolve_oa(
                [4001],
                db_path=db,
                dry_run=True,
                pmc_aws=lambda task: [],
                europe_pmc=lambda task: [{
                    "url": "https://europe.example/PMC777.xml",
                    "format": "xml",
                }],
                content_fetcher=content_fetcher,
            )
            self.assertTrue(result["dry_run"])
            self.assertEqual(calls, [])
            self.assertEqual(len(self.rows(db)), 1)
            self.assertIsNone(self.task(db)["content_path"])

    def test_no_candidate_is_metadata_only_and_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, pmcid=None, doi=None)
            result = oa.resolve_oa([4001], db_path=db, dry_run=True)
            self.assertEqual(result["no_candidates"], 1)
            self.assertEqual(result["metadata_only"], 1)
            self.assertEqual(result["items"][0]["selected_source"], oa.METADATA_ONLY)
            self.assertEqual(self.task(db)["status"], "metadata_only")
            self.assertEqual(self.rows(db), [])

    def test_retryable_provider_failure_without_candidate_is_not_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)

            def europe_pmc_timeout(task):
                raise TimeoutError("Europe PMC timed out")

            result = oa.resolve_oa(
                [4001],
                db_path=db,
                dry_run=True,
                pmc_aws=lambda task: [],
                europe_pmc=europe_pmc_timeout,
                unpaywall=lambda task: [],
                publisher=lambda task: [],
                institutional_repository=lambda task: [],
            )

            item = result["items"][0]
            self.assertEqual(item["candidate_count"], 0)
            self.assertEqual(item["status"], "retryable_error")
            self.assertEqual(result["no_candidates"], 1)
            self.assertEqual(result["metadata_only"], 0)
            self.assertEqual(item["failures"][0]["error_class"], "timeout")
            self.assertTrue(item["failures"][0]["retryable"])
            self.assertEqual(self.task(db)["status"], "retryable_error")
            self.assertEqual(self.rows(db), [])


if __name__ == "__main__":
    unittest.main()
