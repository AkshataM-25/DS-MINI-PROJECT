import tempfile
import threading
import unittest
import xmlrpc.client
from pathlib import Path
from unittest.mock import patch

from aggregator.aggregator import JobRegistry, create_rpc_server
from common.rpc import call_aggregator


class AggregatorRPCTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.registry = JobRegistry(Path(self.directory.name))
        self.server = create_rpc_server(self.registry, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.proxy = xmlrpc.client.ServerProxy(
            f"http://127.0.0.1:{port}",
            allow_none=True,
        )

    def tearDown(self):
        self.proxy.__exit__(None, None, None)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def test_xmlrpc_register_status_and_time_round_trip(self):
        job_id = "4528c0ca-b81b-467f-9d40-a8dce7828a3c"
        result = self.proxy.register_job(
            job_id,
            1,
            2,
            0.1,
            "dataset-test",
            1,
            ["task-1"],
            {"synthetic": False, "planted_pairs": []},
            2,
        )
        self.assertEqual(result["status"], "submitting")
        self.assertGreater(result["lc"], 0)
        self.proxy.mark_submitted(job_id, 1)
        status = self.proxy.get_job_status(job_id)
        self.assertEqual(status["total_tasks"], 1)
        page = self.proxy.get_results_page(job_id, 0.0, 0, 10)
        self.assertEqual(page["matches"], [])
        self.assertEqual(page["filtered_count"], 0)
        self.assertIsInstance(self.proxy.get_time()["server_time"], float)

    def test_shared_client_returns_success_without_remote_close_call(self):
        actual_server_proxy = xmlrpc.client.ServerProxy
        port = self.server.server_address[1]
        with patch(
            "common.rpc.xmlrpc.client.ServerProxy",
            side_effect=lambda url, **kwargs: actual_server_proxy(
                url.replace(
                    "http://aggregator:9000",
                    f"http://127.0.0.1:{port}",
                ),
                **kwargs,
            ),
        ):
            result = call_aggregator("aggregator", "get_time")
        self.assertIsInstance(result["server_time"], float)


if __name__ == "__main__":
    unittest.main()
