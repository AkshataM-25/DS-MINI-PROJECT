import json
import tempfile
import unittest
import uuid
from pathlib import Path

from aggregator.aggregator import JobRegistry


class AggregatorStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.registry = JobRegistry(self.root)
        self.job_id = str(uuid.uuid4())
        self.task_ids = [str(uuid.uuid4()) for _ in range(2)]
        self.registry.register_job(
            self.job_id,
            2,
            2,
            0.1,
            "dataset-demo",
            1,
            self.task_ids,
            {"synthetic": True, "planted_pairs": []},
            2,
        )
        self.registry.mark_submitted(self.job_id, 2)

    def tearDown(self):
        self.directory.cleanup()

    def _result(self, task_id, *, status="completed", pairs_compared=0, matches=None):
        return {
            "job_id": self.job_id,
            "task_id": task_id,
            "worker_id": "worker-1",
            "pairs_compared": pairs_compared,
            "matches": matches or [],
            "duration_ms": 4.5,
            "status": status,
            "lc": 5,
        }

    def test_duplicate_task_id_is_ignored_and_completed_report_is_saved(self):
        result = self._result(
            self.task_ids[0],
            pairs_compared=1,
            matches=[["doc_001.txt", "doc_002.txt", 0.75]],
        )
        self.assertTrue(self.registry.record_result(result))
        self.assertFalse(self.registry.record_result(result))
        self.registry.record_result(self._result(self.task_ids[1]))
        status = self.registry.get_job_status(self.job_id)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["tasks_done"], 2)
        self.assertEqual(status["pairs_compared"], 1)
        self.assertEqual(status["results_saved"], 1)
        self.assertTrue(status["integrity"]["unique_task_ids"])
        self.assertTrue(status["integrity"]["pair_count"])

        report_path = self.root / "jobs" / f"{self.job_id}.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(len(report["results"]), 1)
        self.assertEqual(report["results"][0], ["doc_001.txt", "doc_002.txt", 0.75])

    def test_failure_is_terminal_and_uses_completed_with_failures(self):
        self.registry.record_result(
            self._result(self.task_ids[0], status="failed")
        )
        self.registry.record_result(self._result(self.task_ids[1]))
        status = self.registry.get_job_status(self.job_id)
        self.assertEqual(status["status"], "completed_with_failures")
        self.assertEqual(status["failed_tasks"], 1)
        self.assertEqual(status["completed_tasks"], 1)
        self.assertFalse(status["integrity"]["pair_count"])

    def test_unknown_tasks_and_invalid_failure_results_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown task"):
            self.registry.record_result(self._result(str(uuid.uuid4())))
        with self.assertRaisesRegex(ValueError, "cannot include compared pairs"):
            self.registry.record_result(
                self._result(
                    self.task_ids[0],
                    status="failed",
                    pairs_compared=1,
                )
            )

    def test_saved_jobs_remain_listable_after_registry_restart(self):
        self.registry.record_result(self._result(self.task_ids[0], pairs_compared=1))
        self.registry.record_result(self._result(self.task_ids[1]))
        summary_path = self.root / "jobs" / "summaries" / f"{self.job_id}.json"
        summary_path.unlink()
        restarted = JobRegistry(self.root)
        listed = restarted.list_jobs()
        self.assertEqual([job["job_id"] for job in listed], [self.job_id])
        self.assertEqual(restarted.get_results(self.job_id), [])
        self.assertEqual(restarted.get_job_status(self.job_id)["status"], "completed")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertNotIn("results", summary)


if __name__ == "__main__":
    unittest.main()
