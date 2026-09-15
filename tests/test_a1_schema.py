"""Offline tests for A1 Schema v2 and PMID discovery ingestion."""

import importlib.util
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


schema = load_module("a1_schema_test", SCRIPTS / "a1_schema.py")
ingest = load_module("ingest_pmids_test", SCRIPTS / "ingest_pmids.py")


class A1SchemaTests(unittest.TestCase):
    def make_old_db(self, path, rows=None):
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE tasks (
                pmid INTEGER PRIMARY KEY, doi TEXT, pmc TEXT, year INTEGER,
                title TEXT, title_norm TEXT, status TEXT, attempts INTEGER,
                route TEXT, last_error TEXT, pdf_path TEXT, updated_at TEXT,
                license TEXT NOT NULL DEFAULT 'unverified'
            )"""
        )
        conn.execute(
            """CREATE TABLE run_history (
                run TEXT, pmid INTEGER, doi TEXT, pmc TEXT, year INTEGER,
                title TEXT, title_norm TEXT, status TEXT, attempts INTEGER,
                route TEXT, last_error TEXT, pdf_path TEXT, updated_at TEXT
            )"""
        )
        for row in rows or []:
            conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        conn.commit()
        conn.close()

    def test_migration_preserves_rows_and_rollback_is_structural(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            original = (123, "10.1/x", "PMC123", 2020, "A title", "a title",
                        "done", 4, "europepmc", "old error", "x.pdf",
                        "2026-09-01 01:02:03", "CC-BY")
            self.make_old_db(db, [original])
            conn = sqlite3.connect(db)
            before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            conn.close()
            result = schema.migrate_to_v2(db)
            self.assertEqual(result["before"], before)
            self.assertEqual(result["after"], before)

            conn = sqlite3.connect(db)
            try:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
                self.assertTrue(schema.REQUIRED_V2_COLUMNS <= columns)
                self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], 2)
                row = conn.execute(
                    "SELECT pmid,doi,pmcid,status,attempt_count,content_source,"
                    "last_error_detail,content_path,created_at FROM tasks"
                ).fetchone()
                self.assertEqual(row, (123, "10.1/x", "PMC123", "done", 4,
                                       "europepmc", "old error", "x.pdf",
                                       "2026-09-01 01:02:03"))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0], 0)
            finally:
                conn.close()

            rollback = schema.rollback_schema(db)
            self.assertTrue(rollback["rolled_back"])
            conn = sqlite3.connect(db)
            try:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
                self.assertNotIn("pmcid", columns)
                self.assertNotIn("attempt_count", columns)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], before)
                self.assertEqual(
                    conn.execute("SELECT doi,pmc,status,attempts,pdf_path FROM tasks").fetchone(),
                    ("10.1/x", "PMC123", "done", 4, "x.pdf"),
                )
                self.assertIsNone(conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='schema_version'"
                ).fetchone())
            finally:
                conn.close()

    def test_new_duplicate_and_cross_batch_ingestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_old_db(db)
            first = ingest.ingest_pmids([" 101 ", "101", 102], source="pubmed",
                                        batch_id="b1", db_path=db)
            self.assertEqual(first["new_tasks"], 2)
            self.assertEqual(first["new_discoveries"], 2)
            second = ingest.ingest_pmids(["101", "102", "102"], source="pubmed",
                                         batch_id="b1", db_path=db)
            self.assertEqual(second["new_tasks"], 0)
            self.assertEqual(second["new_discoveries"], 0)
            third = ingest.ingest_pmids([101, 102], source="pubmed",
                                        batch_id="b2", db_path=db)
            self.assertEqual(third["new_tasks"], 0)
            self.assertEqual(third["new_discoveries"], 2)

            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM discovery_batches").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_discoveries").fetchone()[0], 4)
                self.assertEqual(
                    conn.execute("SELECT status,attempt_count FROM tasks ORDER BY pmid").fetchall(),
                    [("pending", 0), ("pending", 0)],
                )
            finally:
                conn.close()

    def test_existing_task_state_and_fetch_history_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_old_db(db, [(77, "10.1/existing", "PMC77", 2021, "Existing",
                                  "existing", "done", 9, "legacy", "kept", "full.pdf",
                                  "2026-09-02 00:00:00", "CC-BY")])
            schema.migrate_to_v2(db)
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO fetch_attempts (attempt_id,pmid,source,started_at,outcome) "
                "VALUES (?,?,?,?,?)", ("a-77", 77, "legacy", "t0", "success")
            )
            conn.commit()
            before = conn.execute(
                "SELECT status,doi,pmcid,content_path,attempt_count,attempts "
                "FROM tasks WHERE pmid=77"
            ).fetchone()
            conn.close()

            result = ingest.ingest_pmids([77, 88], source="pubmed", batch_id="state",
                                         db_path=db)
            self.assertEqual(result["new_tasks"], 1)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute(
                    "SELECT status,doi,pmcid,content_path,attempt_count,attempts "
                    "FROM tasks WHERE pmid=77"
                ).fetchone(), before)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM fetch_attempts WHERE pmid=77"
                ).fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    "SELECT status FROM tasks WHERE pmid=88"
                ).fetchone()[0], "pending")
            finally:
                conn.close()

    def test_thirty_existing_twenty_new_count_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            rows = []
            for pmid in range(1, 31):
                rows.append((pmid, f"10.1/{pmid}", f"PMC{pmid}", 2020,
                             f"Existing {pmid}", f"existing {pmid}",
                             "done" if pmid % 2 else "failed", pmid % 4,
                             "legacy", f"error {pmid}", f"{pmid}.pdf",
                             f"2026-09-{pmid:02d} 00:00:00", "CC-BY"))
            self.make_old_db(db, rows)
            schema.migrate_to_v2(db)
            result = ingest.ingest_pmids(list(range(1, 51)), source="pubmed",
                                         batch_id="thirty-plus-twenty", db_path=db)
            self.assertEqual(result["input_count"], 50)
            self.assertEqual(result["new_tasks"], 20)
            self.assertEqual(result["new_discoveries"], 50)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 50)
                self.assertEqual(conn.execute(
                    "SELECT status,doi,pmcid,content_path,attempt_count FROM tasks WHERE pmid=7"
                ).fetchone(), ("done", "10.1/7", "PMC7", "7.pdf", 3))
                self.assertEqual(conn.execute(
                    "SELECT status,attempt_count FROM tasks WHERE pmid=50"
                ).fetchone(), ("pending", 0))
            finally:
                conn.close()

    def test_mid_transaction_failure_rolls_back_batch_and_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_old_db(db)
            schema.migrate_to_v2(db)
            conn = sqlite3.connect(db)
            conn.execute(
                """CREATE TRIGGER fail_second_discovery
                   BEFORE INSERT ON task_discoveries WHEN NEW.pmid=2002
                   BEGIN SELECT RAISE(ABORT, 'forced A1 test failure'); END"""
            )
            conn.commit()
            conn.close()

            with self.assertRaises(sqlite3.IntegrityError):
                ingest.ingest_pmids([2001, 2002], source="pubmed", batch_id="broken",
                                    db_path=db)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM discovery_batches").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_discoveries").fetchone()[0], 0)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE pmid IN (2001,2002)"
                ).fetchone()[0], 0)
            finally:
                conn.close()

    def test_invalid_pmids_fail_before_database_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_old_db(db)
            with self.assertRaises(ValueError):
                ingest.ingest_pmids(["123", "0", "abc"], source="pubmed", db_path=db)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
                self.assertIsNone(conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='schema_version'"
                ).fetchone())
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
