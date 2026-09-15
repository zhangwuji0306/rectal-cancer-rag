"""Offline regression tests for A3 state machine and error taxonomy."""

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
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


schema = load_module("a1_schema_for_a3_test", SCRIPTS / "a1_schema.py")
ingest = load_module("ingest_for_a3_test", SCRIPTS / "ingest_pmids.py")
state = load_module("state_machine_for_a3_test", SCRIPTS / "state_machine.py")


class A3StateMachineTests(unittest.TestCase):
    def make_db(self, path, pmid=3001, *, status="pending", metadata=False):
        schema.migrate_to_v2(path)
        ingest.ingest_pmids([pmid], source="test", batch_id=f"batch-{pmid}", db_path=path)
        if status != "pending" or metadata:
            conn = sqlite3.connect(path)
            fields = ["status=?"]
            values = [status]
            if metadata:
                fields.extend(["title=?", "metadata_updated_at=?"])
                values.extend(["A3 test article", "2026-09-15T00:00:00+00:00"])
            values.append(pmid)
            conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE pmid=?", values)
            conn.commit()
            conn.close()

    def fetch_attempt(self, db, attempt_id):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM fetch_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def task(self, db, pmid):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT * FROM tasks WHERE pmid=?", (pmid,)).fetchone())
        finally:
            conn.close()

    def test_503_is_retryable(self):
        classified = state.classify_http_status(503)
        self.assertEqual(classified.error_class, "server_error")
        self.assertTrue(classified.retryable)
        self.assertEqual(classified.retry_policy, "server_error")

    def test_429_uses_retry_policy(self):
        classified = state.classify_http_status(429)
        policy = state.retry_policy_for(classified.error_class)
        self.assertEqual(classified.error_class, "rate_limit")
        self.assertTrue(classified.retryable)
        self.assertEqual(policy.name, "rate_limit")
        self.assertTrue(policy.honors_retry_after)
        self.assertEqual(policy.backoff, "retry_after_then_exponential")

    def test_404_is_source_level_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, metadata=True)
            result = state.record_fetch_attempt(
                3001, "source-a", db_path=db, http_status=404, url="https://example.invalid/a"
            )
            attempt = self.fetch_attempt(db, result["attempt_id"])
            self.assertEqual(attempt["error_class"], "source_not_found")
            self.assertEqual(attempt["outcome"], "not_found")
            self.assertEqual(attempt["retryable"], 0)
            self.assertEqual(result["source_level"], True)
            self.assertEqual(self.task(db, 3001)["status"], "metadata_only")
            self.assertNotEqual(self.task(db, 3001)["status"], "not_found")

    def test_timeout_is_retryable(self):
        classified = state.classify_exception(TimeoutError("source timed out"))
        self.assertEqual(classified.error_class, "timeout")
        self.assertTrue(classified.retryable)
        self.assertEqual(classified.retry_policy, "timeout")

    def test_success_fetch_attempt_is_written_and_promotes_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="fetching", metadata=True)
            result = state.record_fetch_attempt(
                3001,
                "source-a",
                db_path=db,
                http_status=200,
                outcome="success",
                url="https://example.invalid/a",
                content_type="application/pdf",
                content_length=123,
                file_valid=True,
                bibliographic_match=True,
            )
            attempt = self.fetch_attempt(db, result["attempt_id"])
            row = self.task(db, 3001)
            self.assertEqual(attempt["outcome"], state.PERSISTED_FULLTEXT_OUTCOME)
            self.assertIsNone(attempt["error_class"])
            self.assertEqual(attempt["retryable"], 0)
            self.assertEqual(row["status"], "fulltext_ready")
            self.assertEqual(row["attempt_count"], 1)
            self.assertIsNone(row["last_error_class"])
            self.assertTrue(result["file_valid"])
            self.assertTrue(result["bibliographic_match"])

    def test_http_200_without_dual_validation_does_not_promote_fulltext(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="fetching", metadata=True)
            result = state.record_fetch_attempt(
                3001,
                "source-a",
                db_path=db,
                http_status=200,
                outcome="success",
                url="https://example.invalid/a",
                content_type="application/pdf",
                content_length=123,
            )
            self.assertEqual(result["outcome"], "success")
            self.assertFalse(result["file_valid"])
            self.assertFalse(result["bibliographic_match"])
            self.assertEqual(result["status"], "metadata_only")
            self.assertNotEqual(self.task(db, 3001)["status"], "fulltext_ready")

    def test_http_200_enters_fulltext_ready_only_when_both_validations_are_true(self):
        base_attempt = {
            "http_status": 200,
            "outcome": "success",
            "error_class": None,
        }
        for file_valid, bibliographic_match in (
            (False, True),
            (True, False),
            (None, None),
        ):
            attempt = {
                **base_attempt,
                "file_valid": file_valid,
                "bibliographic_match": bibliographic_match,
            }
            self.assertNotEqual(
                state.derive_document_status(
                    [attempt], current_status="fetching", metadata_available=True
                ),
                "fulltext_ready",
            )

        self.assertEqual(
            state.derive_document_status(
                [
                    {
                        **base_attempt,
                        "file_valid": True,
                        "bibliographic_match": True,
                    }
                ],
                current_status="fetching",
                metadata_available=True,
            ),
            "fulltext_ready",
        )

    def test_direct_fulltext_transition_requires_dual_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="metadata_ready", metadata=True)
            with self.assertRaises(state.StateTransitionError):
                state.transition_task_status(
                    3001,
                    "fulltext_ready",
                    db_path=db,
                    file_valid=True,
                    bibliographic_match=True,
                )
            self.assertEqual(self.task(db, 3001)["status"], "metadata_ready")
            state.record_fetch_attempt(
                3001,
                "source-a",
                db_path=db,
                http_status=200,
                outcome="success",
                file_valid=True,
                bibliographic_match=True,
            )
            result = state.transition_task_status(
                3001, "fulltext_ready", db_path=db
            )
            self.assertEqual(result["to"], "fulltext_ready")
            self.assertEqual(self.task(db, 3001)["status"], "fulltext_ready")

    def test_fulltext_self_transition_requires_dual_validation(self):
        self.assertFalse(
            state.can_transition("fulltext_ready", "fulltext_ready")
        )
        with self.assertRaises(state.StateTransitionError):
            state.transition_status("fulltext_ready", "fulltext_ready")
        self.assertTrue(
            state.can_transition(
                "fulltext_ready",
                "fulltext_ready",
                file_valid=True,
                bibliographic_match=True,
            )
        )

    def test_current_fulltext_without_attempts_degrades_to_metadata_only(self):
        self.assertEqual(
            state.derive_document_status(
                [], current_status="fulltext_ready", metadata_available=True
            ),
            "metadata_only",
        )

    def test_current_fulltext_http_200_without_validation_degrades_to_metadata_only(self):
        self.assertEqual(
            state.derive_document_status(
                [
                    {
                        "http_status": 200,
                        "outcome": "success",
                        "error_class": None,
                    }
                ],
                current_status="fulltext_ready",
                metadata_available=True,
            ),
            "metadata_only",
        )
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="fulltext_ready", metadata=True)
            result = state.record_fetch_attempt(
                3001,
                "source-a",
                db_path=db,
                http_status=200,
                outcome="success",
            )
            self.assertEqual(result["status"], "metadata_only")
            self.assertEqual(self.task(db, 3001)["status"], "metadata_only")

    def test_current_fulltext_with_dual_validation_remains_fulltext_ready(self):
        self.assertEqual(
            state.derive_document_status(
                [
                    {
                        "http_status": 200,
                        "outcome": "success",
                        "error_class": None,
                        "file_valid": True,
                        "bibliographic_match": True,
                    }
                ],
                current_status="fulltext_ready",
                metadata_available=True,
            ),
            "fulltext_ready",
        )

    def test_dual_validation_round_trips_through_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="fetching", metadata=True)
            result = state.record_fetch_attempt(
                3001,
                "source-a",
                db_path=db,
                http_status=200,
                outcome="success",
                file_valid=True,
                bibliographic_match=True,
            )
            attempt = self.fetch_attempt(db, result["attempt_id"])
            self.assertEqual(attempt["outcome"], state.PERSISTED_FULLTEXT_OUTCOME)

            # record_fetch_attempt has closed its write connection. This new
            # read proves derivation uses the persisted marker, not call-local
            # validation flags or the current task status.
            conn = sqlite3.connect(db)
            conn.execute(
                "UPDATE tasks SET status='metadata_only' WHERE pmid=?", (3001,)
            )
            conn.commit()
            conn.close()
            self.assertEqual(
                state.derive_task_status(3001, db_path=db), "fulltext_ready"
            )

    def test_direct_fulltext_transition_cannot_use_caller_flags_as_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="fulltext_ready", metadata=True)
            self.assertEqual(
                state.derive_task_status(3001, db_path=db), "metadata_only"
            )
            with self.assertRaises(state.StateTransitionError):
                state.transition_task_status(
                    3001,
                    "fulltext_ready",
                    db_path=db,
                    file_valid=True,
                    bibliographic_match=True,
                )

    def test_failure_fetch_attempt_is_written_and_marks_retryable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite"
            self.make_db(db, status="fetching", metadata=True)
            result = state.record_fetch_attempt(
                3001,
                "source-a",
                db_path=db,
                http_status=503,
                error_detail="upstream unavailable",
                retry_after="30",
                next_retry_at="2026-09-15T00:01:00+00:00",
            )
            attempt = self.fetch_attempt(db, result["attempt_id"])
            row = self.task(db, 3001)
            self.assertEqual(attempt["outcome"], "failure")
            self.assertEqual(attempt["error_class"], "server_error")
            self.assertEqual(attempt["retryable"], 1)
            self.assertEqual(attempt["retry_after"], "30")
            self.assertEqual(row["status"], "retryable_error")
            self.assertEqual(row["last_error_class"], "server_error")
            self.assertEqual(row["last_error_detail"], "upstream unavailable")
            self.assertEqual(row["next_retry_at"], "2026-09-15T00:01:00+00:00")
            self.assertEqual(row["attempt_count"], 1)

    def test_mixed_source_404_and_success_derives_fulltext_ready(self):
        attempts = [
            {"outcome": "not_found", "error_class": "source_not_found", "retryable": 0},
            {
                "outcome": "success",
                "error_class": None,
                "retryable": 0,
                "file_valid": True,
                "bibliographic_match": True,
            },
        ]
        self.assertEqual(
            state.derive_document_status(
                attempts, current_status="metadata_ready", metadata_available=True
            ),
            "fulltext_ready",
        )

    def test_mixed_source_404_and_503_derives_retryable_error(self):
        attempts = [
            {"outcome": "not_found", "error_class": "source_not_found", "retryable": 0},
            {"outcome": "failure", "error_class": "server_error", "retryable": 1},
        ]
        self.assertEqual(
            state.derive_document_status(
                attempts, current_status="metadata_ready", metadata_available=True
            ),
            "retryable_error",
        )

    def test_mixed_source_all_nonretryable_absence_derives_metadata_only(self):
        attempts = [
            {"outcome": "not_found", "error_class": "source_not_found", "retryable": 0},
            {"outcome": "failure", "error_class": "license_restricted", "retryable": 0},
        ]
        self.assertEqual(
            state.derive_document_status(
                attempts, current_status="metadata_ready", metadata_available=True
            ),
            "metadata_only",
        )

    def test_state_and_taxonomy_contracts_are_machine_readable(self):
        self.assertEqual(len(state.CANONICAL_STATES), 9)
        self.assertEqual(len(state.ERROR_CLASSES), 15)
        self.assertTrue(state.can_transition("pending", "metadata_ready"))
        self.assertFalse(state.can_transition("fetching", "fulltext_ready"))
        self.assertTrue(
            state.can_transition(
                "fetching",
                "fulltext_ready",
                file_valid=True,
                bibliographic_match=True,
            )
        )
        self.assertFalse(state.can_transition("archived", "pending"))
        with self.assertRaises(state.StateTransitionError):
            state.transition_status("archived", "pending")
        self.assertFalse(state.can_transition("metadata_ready", "fulltext_ready"))
        self.assertTrue(
            state.can_transition(
                "metadata_ready",
                "fulltext_ready",
                file_valid=True,
                bibliographic_match=True,
            )
        )
        snapshot = state.taxonomy_snapshot()
        self.assertEqual(snapshot["http_mapping"]["404"]["error_class"], "source_not_found")
        self.assertTrue(snapshot["http_mapping"]["503"]["retryable"])


if __name__ == "__main__":
    unittest.main()
