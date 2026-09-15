"""Offline regression tests for A2 PubMed canonical metadata refresh."""

import importlib.util
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "直肠癌文献爬取" / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "pubmed_a2_sample.xml"
sys.path.insert(0, str(SCRIPTS))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema = load_module("a1_schema_for_a2_test", SCRIPTS / "a1_schema.py")
ingest = load_module("ingest_for_a2_test", SCRIPTS / "ingest_pmids.py")
metadata = load_module("pubmed_metadata_test", SCRIPTS / "pubmed_metadata.py")


class A2MetadataTests(unittest.TestCase):
    def make_db(self, path, pmids=(1001, 1002, 1003)):
        schema.migrate_to_v2(path)
        ingest.ingest_pmids(list(pmids), source="test", batch_id="batch-a", db_path=path)

    def read_row(self, db, pmid):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT * FROM tasks WHERE pmid=?", (pmid,)).fetchone())
        finally:
            conn.close()

    def test_xml_parser_reads_structured_metadata_and_article_ids(self):
        parsed = metadata.parse_pubmed_xml(FIXTURE.read_bytes())
        first = parsed[1001]
        self.assertEqual(first["doi"], "10.1038/s41598-024-12345-6")
        self.assertEqual(first["pmcid"], "PMC1234567")
        self.assertEqual(first["title"], "Magnetic resonance imaging in rectal cancer: a pilot study.")
        self.assertEqual(first["title_norm"], "magnetic resonance imaging in rectal cancer a pilot study")
        self.assertEqual(first["authors"], "Smith, Andrew B; Jones, Li")
        self.assertEqual(first["first_author"], "Smith, Andrew B")
        self.assertEqual(first["first_author_family"], "smith")
        self.assertEqual(first["journal"], "Scientific Reports")
        self.assertEqual(first["issn"], "2045-2322")
        self.assertEqual(first["publication_date"], "2024-04-05")
        self.assertEqual(first["year"], 2024)
        self.assertEqual(first["pub_type"], "Journal Article; Research Support, Non-U.S. Gov't")
        self.assertEqual(first["language"], "eng")
        self.assertEqual(first["article_ids"]["pubmed"], "1001")
        self.assertEqual(first["article_ids"]["pmc"], "PMC1234567")

        second = parsed[1002]
        self.assertIsNone(second["doi"])
        self.assertIsNone(second["pmcid"])
        self.assertEqual(second["publication_date"], "2023 Jan-Feb")
        self.assertEqual(second["year"], 2023)
        self.assertEqual(second["first_author_family"], "lee")

        pmcid_alias = FIXTURE.read_text(encoding="utf-8").replace(
            'IdType="pmc"', 'IdType="pmcid"'
        )
        self.assertEqual(
            metadata.parse_pubmed_xml(pmcid_alias)[1001]["pmcid"], "PMC1234567"
        )

    def test_family_normalization_structured_and_fallback_forms(self):
        self.assertEqual(metadata.normalize_author_family("Smith", "Andrew B", "AB"), "smith")
        self.assertEqual(metadata.normalize_author_family("Smith AB"), "smith")
        self.assertEqual(metadata.normalize_author_family("Smith, Andrew B"), "smith")
        self.assertEqual(metadata.normalize_author_family("Andrew B Smith"), "smith")

    def test_upsert_nonempty_and_protects_empty_existing_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db)
            conn = sqlite3.connect(db)
            conn.execute(
                "UPDATE tasks SET doi=?, pmcid=?, title=?, title_norm=?, journal=?, issn=?, "
                "language=?, status=?, attempt_count=?, content_status=?, content_path=?, "
                "last_error_class=?, last_error_detail=?, retrieved_at=?, updated_at=? WHERE pmid=1002",
                ("10.old/doi", "PMCOLD", "Old title", "old title", "Old Journal", "0000-0000",
                 "eng", "done", 7, "ready", "full.pdf", "old_class", "old detail",
                 "2026-09-01T00:00:00+00:00", "old-updated"),
            )
            conn.execute(
                "INSERT INTO fetch_attempts (attempt_id,pmid,source,started_at,outcome) VALUES (?,?,?,?,?)",
                ("attempt-1002", 1002, "test", "t0", "success"),
            )
            conn.commit()
            conn.close()

            result = metadata.refresh_pubmed_metadata(
                [1001, 1002], db_path=db, fetcher=lambda batch: FIXTURE.read_bytes(), batch_size=1
            )
            self.assertEqual(result["updated"], 2)
            row1 = self.read_row(db, 1001)
            self.assertEqual(row1["doi"], "10.1038/s41598-024-12345-6")
            self.assertEqual(row1["pmcid"], "PMC1234567")
            self.assertEqual(row1["status"], "pending")
            self.assertIsNotNone(row1["metadata_updated_at"])

            row2 = self.read_row(db, 1002)
            self.assertEqual(row2["doi"], "10.old/doi")
            self.assertEqual(row2["pmcid"], "PMCOLD")
            self.assertEqual(row2["title"], "Second article with incomplete optional identifiers.")
            self.assertEqual(row2["journal"], "Example J")
            self.assertEqual(row2["issn"], "1234-5678")
            self.assertEqual(row2["status"], "done")
            self.assertEqual(row2["attempt_count"], 7)
            self.assertEqual(row2["content_status"], "ready")
            self.assertEqual(row2["content_path"], "full.pdf")
            self.assertEqual(row2["last_error_class"], "old_class")
            self.assertEqual(row2["last_error_detail"], "old detail")
            self.assertEqual(row2["retrieved_at"], "2026-09-01T00:00:00+00:00")
            self.assertEqual(row2["updated_at"], row2["metadata_updated_at"])
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_discoveries").fetchone()[0], 3)
            finally:
                conn.close()

    def test_new_pmid_batch_scope_and_duplicate_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            schema.migrate_to_v2(db)
            ingest.ingest_pmids([1001, 1002], source="test", batch_id="batch-a", db_path=db)
            ingest.ingest_pmids([1002, 1003], source="test", batch_id="batch-b", db_path=db)
            requested = []

            def fetch(batch):
                requested.append(list(batch))
                return FIXTURE.read_bytes()

            result = metadata.refresh_pubmed_metadata(
                metadata._pmids_for_mode(db_path=db, batch_id="batch-b"),
                db_path=db,
                fetcher=fetch,
            )
            self.assertEqual(result["requested"], 2)
            self.assertEqual(requested, [[1002, 1003]])
            self.assertEqual(result["updated"], 1)
            self.assertEqual(result["missing_response"], [1003])
            self.assertEqual(self.read_row(db, 1002)["title"], "Second article with incomplete optional identifiers.")
            self.assertIsNone(self.read_row(db, 1003)["metadata_updated_at"])

            first_timestamp = self.read_row(db, 1002)["metadata_updated_at"]
            result2 = metadata.refresh_pubmed_metadata([1002], db_path=db, fetcher=fetch)
            self.assertEqual(result2["updated"], 1)
            self.assertEqual(self.read_row(db, 1002)["status"], "pending")
            self.assertEqual(first_timestamp, self.read_row(db, 1002)["metadata_updated_at"])

    def test_temporary_failure_preserves_task_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, pmids=(1001,))
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE tasks SET doi='10.keep/doi', title='Keep me', status='done', attempt_count=4, "
                "content_path='keep.pdf', updated_at='before' WHERE pmid=1001"
            )
            conn.commit()
            conn.close()
            result = metadata.refresh_pubmed_metadata(
                [1001], db_path=db, fetcher=lambda batch: (_ for _ in ()).throw(TimeoutError("offline"))
            )
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["failures"][0]["pmid"], 1001)
            row = self.read_row(db, 1001)
            self.assertEqual(row["doi"], "10.keep/doi")
            self.assertEqual(row["title"], "Keep me")
            self.assertEqual(row["status"], "done")
            self.assertEqual(row["attempt_count"], 4)
            self.assertEqual(row["content_path"], "keep.pdf")
            self.assertEqual(row["updated_at"], "before")

    def test_all_business_and_provenance_fields_are_unchanged(self):
        protected = (
            "status", "attempt_count", "content_status", "content_source",
            "content_format", "content_path", "content_sha256", "retrieved_at",
            "worker_id", "lease_until", "heartbeat_at", "last_error_class",
            "last_error_detail", "next_retry_at",
        )
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, pmids=(1001,))
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE tasks SET status='leased', attempt_count=3, content_status='ready', "
                "content_source='test', content_format='pdf', content_path='keep.pdf', "
                "content_sha256='sha', retrieved_at='retrieved', worker_id='worker', "
                "lease_until='lease', heartbeat_at='heartbeat', last_error_class='old', "
                "last_error_detail='detail', next_retry_at='retry' WHERE pmid=1001"
            )
            conn.execute(
                "INSERT INTO fetch_attempts "
                "(attempt_id,pmid,source,started_at,outcome) VALUES (?,?,?,?,?)",
                ("attempt-1001", 1001, "test", "t0", "success"),
            )
            conn.execute(
                "INSERT INTO source_candidates (pmid,source,url,format) VALUES (?,?,?,?)",
                (1001, "test", "https://example.invalid/source", "pdf"),
            )
            conn.commit()
            before_task = dict(
                conn.execute("SELECT * FROM tasks WHERE pmid=1001").fetchone()
            )
            before_fetch = conn.execute(
                "SELECT * FROM fetch_attempts ORDER BY attempt_id"
            ).fetchall()
            before_candidates = conn.execute(
                "SELECT * FROM source_candidates ORDER BY candidate_id"
            ).fetchall()
            before_batches = conn.execute(
                "SELECT * FROM discovery_batches ORDER BY batch_id"
            ).fetchall()
            before_discoveries = conn.execute(
                "SELECT * FROM task_discoveries ORDER BY batch_id, pmid"
            ).fetchall()
            conn.close()

            result = metadata.refresh_pubmed_metadata(
                [1001], db_path=db, fetcher=lambda batch: FIXTURE.read_bytes()
            )
            self.assertEqual(result["updated"], 1)

            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            try:
                after_task = dict(
                    conn.execute("SELECT * FROM tasks WHERE pmid=1001").fetchone()
                )
                for field in protected:
                    self.assertEqual(after_task[field], before_task[field], field)
                self.assertEqual(
                    conn.execute("SELECT * FROM fetch_attempts ORDER BY attempt_id").fetchall(),
                    before_fetch,
                )
                self.assertEqual(
                    conn.execute("SELECT * FROM source_candidates ORDER BY candidate_id").fetchall(),
                    before_candidates,
                )
                self.assertEqual(
                    conn.execute("SELECT * FROM discovery_batches ORDER BY batch_id").fetchall(),
                    before_batches,
                )
                self.assertEqual(
                    conn.execute("SELECT * FROM task_discoveries ORDER BY batch_id, pmid").fetchall(),
                    before_discoveries,
                )
            finally:
                conn.close()

    def test_batch_upsert_rolls_back_on_database_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, pmids=(1001, 1002))
            conn = sqlite3.connect(db)
            conn.execute(
                """CREATE TRIGGER fail_a2_update BEFORE UPDATE OF title ON tasks
                   WHEN NEW.pmid=1002 BEGIN SELECT RAISE(ABORT, 'forced A2 failure'); END"""
            )
            conn.commit()
            conn.close()
            with self.assertRaises(sqlite3.IntegrityError):
                metadata.refresh_pubmed_metadata([1001, 1002], db_path=db, fetcher=lambda batch: FIXTURE.read_bytes())
            self.assertIsNone(self.read_row(db, 1001)["title"])
            self.assertIsNone(self.read_row(db, 1002)["title"])

    def test_cli_mode_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, pmids=(1001, 1002))
            input_file = Path(tmp) / "pmids.txt"
            input_file.write_text("1002\n1002\n", encoding="utf-8")
            self.assertEqual(metadata._pmids_for_mode(db_path=db, input_path=input_file), [1002])
            self.assertEqual(metadata._pmids_for_mode(db_path=db, all_pmids=True), [1001, 1002])

    def test_cli_executes_single_list_input_batch_and_full_modes_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, pmids=(1001, 1002))
            input_file = Path(tmp) / "pmids.txt"
            input_file.write_text("1002\n", encoding="utf-8")
            calls = []

            def fetch(batch):
                calls.append(list(batch))
                return FIXTURE.read_bytes()

            modes = [
                (["--pmid", "1001"], 1),
                (["--pmid", "1001", "--pmid", "1002"], 2),
                (["--input", str(input_file)], 1),
                (["--batch-id", "batch-a"], 2),
                (["--all"], 2),
            ]
            for args, requested in modes:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(
                        metadata.run_cli([*args, "--db", str(db)], fetcher=fetch), 0
                    )
                result = json.loads(output.getvalue())
                self.assertEqual(result["requested"], requested)
                self.assertEqual(result["failed"], 0)

            self.assertEqual(
                calls,
                [[1001], [1001, 1002], [1002], [1001, 1002], [1001, 1002]],
            )


if __name__ == "__main__":
    unittest.main()
