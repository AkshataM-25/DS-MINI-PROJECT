import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from aggregator.aggregator import JobRegistry
from api import main as api
from common.clock import LamportClock
from worker.worker import process_task
from tools.generate_corpus import create_dataset


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.previous_data_root = api.DATA_ROOT
        self.previous_clock = api.clock
        self.previous_clock_skew = api.SIMULATED_CLOCK_SKEW
        api.DATA_ROOT = self.root
        api.clock = LamportClock()
        self.registry = JobRegistry(self.root)
        self.rpc_patch = patch(
            "api.main.call_aggregator",
            side_effect=lambda host, method, *args: getattr(self.registry, method)(*args),
        )
        self.rpc_patch.start()
        self.channel = Mock()
        self.connection = Mock()
        self.connection.channel.return_value = self.channel
        self.connection.is_open = True
        self.connection_patch = patch(
            "api.main.pika.BlockingConnection",
            return_value=self.connection,
        )
        self.connection_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.connection_patch.stop()
        self.rpc_patch.stop()
        api.DATA_ROOT = self.previous_data_root
        api.clock = self.previous_clock
        api.SIMULATED_CLOCK_SKEW = self.previous_clock_skew
        self.directory.cleanup()

    def test_generate_dataset_and_manifest_are_reproducible(self):
        response = self.client.post(
            "/api/datasets/generate",
            json={
                "name": "test-corpus",
                "documents": 8,
                "seed": 12,
                "planted_pairs": 2,
                "overlaps": [0.3, 0.9],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()
        self.assertEqual(dataset["document_count"], 8)
        self.assertEqual(dataset["pair_count"], 28)
        self.assertEqual(dataset["planted_pair_count"], 2)
        self.assertTrue(dataset["synthetic"])
        manifest = json.loads(
            (self.root / "datasets" / dataset["dataset_id"] / "ground_truth.json").read_text()
        )
        self.assertEqual(len(manifest["planted_pairs"]), 2)

    def test_upload_accepts_only_supported_text_files(self):
        response = self.client.post(
            "/api/datasets/upload",
            data={"name": "uploaded"},
            files=[
                ("files", ("first.txt", b"one two three four five", "text/plain")),
                ("files", ("second.txt", b"one two three four six", "text/plain")),
            ],
        )
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()
        self.assertEqual(dataset["document_count"], 2)
        self.assertFalse(dataset["synthetic"])

        invalid = self.client.post(
            "/api/datasets/upload",
            data={"name": "invalid"},
            files=[
                ("files", ("first.txt", b"first", "text/plain")),
                ("files", ("second.pdf", b"second", "application/pdf")),
            ],
        )
        self.assertEqual(invalid.status_code, 400)

    def test_job_submission_publishes_block_tasks_then_aggregates_exact_pairs(self):
        generated = self.client.post(
            "/api/datasets/generate",
            json={
                "name": "pipeline",
                "documents": 5,
                "seed": 24,
                "planted_pairs": 1,
                "overlaps": [0.6],
            },
        )
        dataset_id = generated.json()["dataset_id"]
        response = self.client.post(
            "/api/jobs",
            json={
                "dataset_id": dataset_id,
                "threshold": 0.0,
                "block_size": 2,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job["expected_pairs"], 10)
        self.assertEqual(job["total_tasks"], 6)
        self.assertEqual(job["submitted_tasks"], 6)
        messages = [
            json.loads(call.kwargs["body"])
            for call in self.channel.basic_publish.call_args_list
        ]
        self.assertEqual(len(messages), 6)
        self.assertEqual(len({message["task_id"] for message in messages}), 6)
        self.assertTrue(all(message["job_id"] == job["job_id"] for message in messages))
        self.assertTrue(
            all(
                {
                    "dataset_id",
                    "block_a",
                    "block_b",
                    "threshold",
                    "attempt",
                    "lc",
                }.issubset(message)
                for message in messages
            )
        )

        for message in messages:
            self.registry.log_event(
                message["lc"] + 1,
                "worker-test",
                1.0,
                job["job_id"],
                message["task_id"],
                "task_received",
            )
            self.assertGreaterEqual(
                self.registry.get_job_status(job["job_id"])["in_progress_tasks"],
                1,
            )
            self.registry.log_event(
                message["lc"] + 2,
                "worker-test",
                2.0,
                job["job_id"],
                message["task_id"],
                "task_done",
            )
            result = process_task(
                message,
                self.root,
                worker_id="worker-test",
            )
            result.update(
                {
                    "status": "completed",
                    "duration_ms": 1.0,
                    "lc": message["lc"] + 3,
                }
            )
            self.registry.record_result(result)
        status = self.client.get(f"/api/jobs/{job['job_id']}").json()
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["pairs_compared"], 10)
        self.assertEqual(status["results_saved"], 10)
        self.assertEqual(status["in_progress_tasks"], 0)
        self.assertTrue(status["integrity"]["unique_task_ids"])
        self.assertTrue(status["integrity"]["pair_count"])
        self.assertTrue(
            (self.root / "jobs" / f"{job['job_id']}.json").is_file()
        )
        verification = self.client.post(
            f"/api/jobs/{job['job_id']}/verify"
        )
        self.assertEqual(verification.status_code, 200, verification.text)
        self.assertTrue(verification.json()["passed"])
        self.assertEqual(
            verification.json()["planted_pair_recall"],
            1.0,
        )
        first_page = self.client.get(
            f"/api/jobs/{job['job_id']}/results?limit=3&offset=0"
        ).json()
        last_page = self.client.get(
            f"/api/jobs/{job['job_id']}/results?limit=3&offset=9"
        ).json()
        self.assertEqual(len(first_page["matches"]), 3)
        self.assertEqual(first_page["filtered_count"], 10)
        self.assertTrue(first_page["has_more"])
        self.assertEqual(len(last_page["matches"]), 1)
        self.assertFalse(last_page["has_more"])
        lamport = self.client.get(
            f"/api/jobs/{job['job_id']}/timeline?order=lamport"
        ).json()["events"]
        wall = self.client.get(
            f"/api/jobs/{job['job_id']}/timeline?order=wall"
        ).json()["events"]
        sent = next(
            event for event in lamport
            if event["task_id"] == messages[0]["task_id"]
            and event["event"] == "task_published"
        )
        received = next(
            event for event in lamport
            if event["task_id"] == messages[0]["task_id"]
            and event["event"] == "task_received"
        )
        self.assertLess(lamport.index(sent), lamport.index(received))
        wall_received = next(
            event for event in wall
            if event["task_id"] == messages[0]["task_id"]
            and event["event"] == "task_received"
        )
        wall_sent = next(
            event for event in wall
            if event["task_id"] == messages[0]["task_id"]
            and event["event"] == "task_published"
        )
        self.assertLess(wall.index(wall_received), wall.index(wall_sent))

    def test_ui_is_served_locally_and_dns_returns_resolved_addresses(self):
        page = self.client.get("/")
        script = self.client.get("/static/app.js")
        style = self.client.get("/static/app.css")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(style.status_code, 200)
        self.assertNotIn("https://", page.text)
        self.assertIn("refreshResults", script.text)
        with patch(
            "api.main.socket.gethostbyname_ex",
            return_value=("worker", [], ["172.20.0.2", "172.20.0.3"]),
        ):
            dns = self.client.get("/api/system/dns?name=worker")
        self.assertEqual(dns.json()["addresses"], ["172.20.0.2", "172.20.0.3"])

    def test_benchmark_import_validates_and_saves_reports_for_experiments_tab(self):
        create_dataset(
            self.root / "datasets" / "dataset-demo",
            2,
            9,
            0,
            [0.5],
        )
        report = {
            "benchmark_id": "test-benchmark",
            "created_at": "2026-01-01T00:00:00+00:00",
            "dataset_id": "dataset-demo",
            "document_count": 2,
            "expected_pairs": 1,
            "threshold": 0.1,
            "block_size": 2,
            "repeats": 2,
            "warmup_jobs": 1,
            "host_logical_cpus": 8,
            "configuration": {"timing": "host monotonic"},
            "runs": [
                {
                    "workers": 1,
                    "warmup_job_id": "warmup",
                    "job_ids": ["job-1", "job-2"],
                    "run_times_seconds": [1.0, 2.0],
                    "median_seconds": 1.5,
                    "pairs_per_second": 1 / 1.5,
                    "speedup": 1.0,
                    "efficiency": 1.0,
                }
            ],
        }
        completed_status = {
            "status": "completed",
            "dataset_id": "dataset-demo",
            "n_docs": 2,
            "threshold": 0.1,
            "block_size": 2,
            "expected_pairs": 1,
            "elapsed_seconds": 1.0,
            "integrity": {"unique_task_ids": True, "pair_count": True},
        }
        with patch("api.main._rpc", return_value=completed_status):
            imported = self.client.post("/api/experiments/import", json=report)
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertTrue(imported.json()["saved"])
        experiments = self.client.get("/api/experiments").json()["experiments"]
        self.assertEqual(experiments[0]["configuration"]["timing"], "host monotonic")

        invalid = self.client.post("/api/experiments/import", json={**report, "expected_pairs": 99})
        self.assertEqual(invalid.status_code, 400)

    def test_invalid_job_input_and_unknown_job_are_reported(self):
        invalid = self.client.post(
            "/api/jobs",
            json={"dataset_id": "../outside", "threshold": 1.1, "block_size": 0},
        )
        self.assertEqual(invalid.status_code, 422)
        missing = self.client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
        self.assertEqual(missing.status_code, 404)

    def test_controlled_poison_task_is_included_in_submission(self):
        generated = self.client.post(
            "/api/datasets/generate",
            json={
                "name": "poison",
                "documents": 2,
                "seed": 2,
                "planted_pairs": 0,
            },
        ).json()
        response = self.client.post(
            "/api/jobs",
            json={
                "dataset_id": generated["dataset_id"],
                "block_size": 1,
                "inject_poison": True,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["total_tasks"], 4)
        messages = [
            json.loads(call.kwargs["body"])
            for call in self.channel.basic_publish.call_args_list
        ]
        self.assertEqual(sum(message["block_a"] >= 2 for message in messages), 1)

    def test_worker_kill_is_disabled_by_default_and_targets_one_active_worker(self):
        disabled = self.client.post(
            "/api/system/kill-random-worker",
            json={"job_id": str(uuid.uuid4())},
        )
        self.assertEqual(disabled.status_code, 403)

        generated = self.client.post(
            "/api/datasets/generate",
            json={
                "name": "kill-demo",
                "documents": 4,
                "seed": 77,
                "planted_pairs": 0,
            },
        ).json()
        submitted = self.client.post(
            "/api/jobs",
            json={"dataset_id": generated["dataset_id"], "block_size": 2},
        ).json()
        task_messages = [
            json.loads(call.kwargs["body"])
            for call in self.channel.basic_publish.call_args_list
        ]
        first_result = process_task(task_messages[0], self.root, "worker-one")
        first_result.update(
            {
                "status": "completed",
                "duration_ms": 1.0,
                "lc": task_messages[0]["lc"] + 1,
            }
        )
        self.registry.record_result(first_result)
        self.registry.worker_heartbeat(
            "worker-one",
            "active-task",
            1,
            0,
            100,
            123.0,
        )

        with (
            patch.object(api, "DEMO_CONTROLS", True),
            patch.object(
                api,
                "_rabbitmq_queue",
                side_effect=[
                    {
                        "status": "healthy",
                        "consumers": 2,
                        "unacknowledged": 0,
                        "redeliveries": 4,
                    },
                    {
                        "status": "healthy",
                        "consumers": 2,
                        "unacknowledged": 1,
                        "redeliveries": 4,
                    },
                ],
            ) as queue_probe,
            patch("api.main.time.sleep"),
            patch("api.main.random.choice", return_value=self.registry.get_workers()[0]),
        ):
            response = self.client.post(
                "/api/system/kill-random-worker",
                json={"job_id": submitted["job_id"]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(queue_probe.call_count, 2)
        self.assertEqual(response.json()["target_worker"], "worker-one")
        published = json.loads(self.channel.basic_publish.call_args.kwargs["body"])
        self.assertEqual(published["action"], "kill_worker")
        self.assertEqual(published["worker_id"], "worker-one")

    def test_demo_controls_set_active_consumers_and_reject_unavailable_count(self):
        self.registry.worker_heartbeat("worker-b", None, 0, 0, 1, 123.0)
        self.registry.worker_heartbeat("worker-a", None, 0, 0, 1, 123.0)
        with (
            patch.object(api, "DEMO_CONTROLS", True),
            patch("api.main._publish_demo_control") as publish,
        ):
            response = self.client.post(
                "/api/system/worker-consumers",
                json={"active_workers": 1},
            )
            unavailable = self.client.post(
                "/api/system/worker-consumers",
                json={"active_workers": 3},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["available_workers"], 2)
        self.assertEqual(response.json()["active_worker_ids"], ["worker-a"])
        self.assertEqual(unavailable.status_code, 409)
        message = publish.call_args.args[0]
        self.assertEqual(message["action"], "set_active_workers")
        self.assertEqual(message["active_worker_ids"], ["worker-a"])

    def test_demo_clock_skew_is_explicit_and_reflected_in_timeline(self):
        with (
            patch.object(api, "DEMO_CONTROLS", True),
            patch("api.main._publish_demo_control") as publish,
        ):
            response = self.client.post(
                "/api/system/clock-skew",
                json={"seconds": -3600},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["simulated"])
        self.assertEqual(api.SIMULATED_CLOCK_SKEW, -3600)
        self.assertEqual(publish.call_args.args[0]["action"], "set_clock_skew")
        dataset = self.client.post(
            "/api/datasets/generate",
            json={"name": "skew-demo", "documents": 2, "planted_pairs": 0},
        ).json()
        job = self.client.post(
            "/api/jobs",
            json={"dataset_id": dataset["dataset_id"], "block_size": 1},
        ).json()
        api.SIMULATED_CLOCK_SKEW = 0
        timeline = self.client.get(f"/api/jobs/{job['job_id']}/timeline").json()
        self.assertTrue(timeline["simulated_clock_skew"])
        self.assertIn(
            "simulated_clock_skew_active",
            {event["event"] for event in timeline["events"]},
        )


if __name__ == "__main__":
    unittest.main()
