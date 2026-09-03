"""Small, offline regression tests for the ingestion pipeline."""

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "直肠癌文献爬取" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from common import validate_pdf  # noqa: E402
sys.path.insert(0, str(ROOT / "index"))
from build_index import replace_source  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PipelineTests(unittest.TestCase):
    def test_index_replaces_after_successful_upsert(self):
        class FakeCollection:
            def __init__(self):
                self.events = []

            def get(self, where=None, include=None):
                return {"ids": ["old-1", "old-2"]}

            def upsert(self, **kwargs):
                self.events.append("upsert")

            def delete(self, **kwargs):
                self.events.append("delete")

        collection = FakeCollection()
        replace_source(collection, "paper.md", ["new-1"], ["body"],
                       [{"source": "paper.md"}], [[0.1]])
        self.assertEqual(collection.events, ["upsert", "delete"])

    def test_pdf_validation_rejects_truncated_payload(self):
        valid = b"%PDF-1.7\n" + b"x" * 32 + b"\n%%EOF\n"
        truncated = b"%PDF-1.7\n" + b"x" * 32
        self.assertTrue(validate_pdf(valid, min_size=16))
        self.assertFalse(validate_pdf(truncated, min_size=16))

    def test_queue_refreshes_metadata_without_resetting_status(self):
        queue = load_module("build_queue_test", SCRIPTS / "02_build_queue.py")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            first = [{"PMID": "1", "DOI": "", "PMC": "", "Year": "2020",
                      "Title": "Old title", "License": "unverified"}]
            queue.build(str(db), first)
            conn = sqlite3.connect(db)
            try:
                conn.execute("UPDATE tasks SET status='done' WHERE pmid=1")
                conn.commit()
            finally:
                conn.close()
            second = [{"PMID": "1", "DOI": "10.1234/example", "PMC": "PMC1",
                       "Year": "2021", "Title": "Updated title", "License": "CC-BY"}]
            queue.build(str(db), second)
            conn = sqlite3.connect(db)
            try:
                row = conn.execute(
                    "SELECT doi, pmc, year, title, status, license FROM tasks WHERE pmid=1"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("10.1234/example", "PMC1", 2021,
                                   "Updated title", "done", "CC-BY"))


if __name__ == "__main__":
    unittest.main()
