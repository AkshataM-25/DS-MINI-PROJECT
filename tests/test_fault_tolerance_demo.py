import unittest
from unittest.mock import patch

from tools.fault_tolerance_demo import _require_preconditions, run_demo
from worker.worker import FAILED_QUEUE, RESULTS_QUEUE, TASK_QUEUE, retry_message_plan


class FaultToleranceTests(unittest.TestCase):
    def test_fault_demo_requires_confirmation_before_live_calls(self):
        with patch("tools.fault_tolerance_demo.request_json") as request:
            with self.assertRaisesRegex(ValueError, "explicit operator confirmation"):
                run_demo("job-test", "NO")
            request.assert_not_called()

    def test_fault_demo_requires_processing_job_and_safe_preconditions(self):
        system = {
            "demo_controls": True,
            "task_consumers": 2,
            "queues": {"tasks": {"unacknowledged": 1}},
            "workers": [{"healthy": True, "current_task": "task-1"}],
        }
        with self.assertRaisesRegex(ValueError, "status is processing"):
            _require_preconditions({"status": "completed"}, system)
        with self.assertRaisesRegex(ValueError, "disabled"):
            _require_preconditions(
                {"status": "processing"},
                {**system, "demo_controls": False},
            )
        with self.assertRaisesRegex(ValueError, "unacknowledged task"):
            _require_preconditions(
                {"status": "processing"},
                {
                    **system,
                    "queues": {"tasks": {"unacknowledged": 0}},
                },
            )
        with self.assertRaisesRegex(ValueError, "active task"):
            _require_preconditions(
                {"status": "processing"},
                {
                    **system,
                    "workers": [{"healthy": True, "current_task": None}],
                },
            )

    def test_poison_task_gets_three_retries_then_failure_and_result(self):
        task = {
            "job_id": "job",
            "task_id": "task",
            "dataset_id": "dataset",
            "block_a": 4,
            "block_b": 4,
            "block_size": 2,
            "threshold": 0.1,
            "attempt": 0,
            "lc": 1,
        }
        next_lamport_value = 10

        def next_lamport():
            nonlocal next_lamport_value
            next_lamport_value += 1
            return next_lamport_value

        for expected_attempt in (1, 2, 3):
            plan = retry_message_plan(
                task,
                IndexError("invalid block"),
                3,
                next_lamport,
                duration_ms=2.5,
            )
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0][0], TASK_QUEUE)
            task = plan[0][1]
            self.assertEqual(task["attempt"], expected_attempt)
            self.assertEqual(task["task_id"], "task")
            self.assertGreater(task["lc"], 1)

        final_plan = retry_message_plan(
            task,
            IndexError("invalid block"),
            3,
            next_lamport,
            duration_ms=7.5,
        )
        self.assertEqual([queue for queue, _ in final_plan], [FAILED_QUEUE, RESULTS_QUEUE])
        failure = final_plan[0][1]
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["pairs_compared"], 0)
        self.assertEqual(failure["matches"], [])
        self.assertEqual(failure["error"], "IndexError")
        self.assertEqual(failure["duration_ms"], 7.5)
        self.assertEqual(final_plan[1][1], failure)


if __name__ == "__main__":
    unittest.main()
