import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from tools.benchmark import run_benchmark


class BenchmarkToolTests(unittest.TestCase):
    def test_records_real_run_values_and_imports_report_for_dashboard(self):
        with tempfile.TemporaryDirectory() as temporary:
            times = iter(
                [
                    ("warmup-1", 30.0, {}),
                    ("job-1a", 2.0, {}),
                    ("job-1b", 4.0, {}),
                    ("warmup-2", 20.0, {}),
                    ("job-2a", 1.5, {}),
                    ("job-2b", 2.5, {}),
                ]
            )

            def api_side_effect(api_url, path, payload=None):
                if path == "/api/datasets/dataset-demo":
                    return {"document_count": 4, "pair_count": 6}
                self.assertEqual(path, "/api/experiments/import")
                self.assertEqual(payload["runs"][1]["job_ids"], ["job-2a", "job-2b"])
                return {"saved": True}

            with (
                patch("tools.benchmark.api_json", side_effect=api_side_effect) as api_call,
                patch("tools.benchmark.scale_workers") as scale,
                patch("tools.benchmark.wait_for_consumers") as wait,
                patch("tools.benchmark.submit_and_wait", side_effect=lambda *args: next(times)),
            ):
                result = run_benchmark(
                    "http://localhost:8000",
                    "dataset-demo",
                    2,
                    2,
                    0.1,
                    2,
                    60,
                    Path(temporary),
                )

            runs = result["report"]["runs"]
            self.assertEqual([run["median_seconds"] for run in runs], [3.0, 2.0])
            self.assertEqual([run["speedup"] for run in runs], [1.0, 1.5])
            self.assertEqual(runs[1]["efficiency"], 0.75)
            self.assertEqual(scale.call_count, 2)
            self.assertEqual(wait.call_args_list, [call("http://localhost:8000", 1, 60), call("http://localhost:8000", 2, 60)])
            self.assertEqual(api_call.call_count, 2)
            self.assertTrue(result["json_path"].is_file())
            self.assertTrue(result["csv_path"].is_file())
            self.assertTrue(result["svg_path"].is_file())
            self.assertIn("<polyline", result["svg_path"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
