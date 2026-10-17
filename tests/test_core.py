import hashlib
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from common.shingle import (
    calculate_similarity,
    create_shingles,
    normalize_text,
)
from common.clock import LamportClock
from common.rabbitmq import connection_parameters
from common.tasks import (
    count_document_pairs,
    generate_block_tasks,
    iter_document_pairs,
    partition_documents,
)
from tools.baseline import run_baseline
from tools.generate_corpus import create_dataset
from tools.verify import verify_distributed_results


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CoreAlgorithmTests(unittest.TestCase):
    def test_normalization_removes_punctuation_and_lowercases(self):
        self.assertEqual(
            normalize_text("Hello, WORLD! It's distributed-systems."),
            ["hello", "world", "its", "distributedsystems"],
        )

    def test_hashes_use_stable_blake2b_64_digests(self):
        words = ["alpha", "beta", "gamma", "delta", "epsilon"]
        digest = hashlib.blake2b("\x1f".join(words).encode(), digest_size=8).hexdigest()
        self.assertEqual(create_shingles(" ".join(words)), {digest})

        script = (
            "from common.shingle import create_shingles; "
            "print(','.join(sorted(create_shingles('alpha beta gamma delta epsilon'))))"
        )
        environment = {"PYTHONPATH": str(PROJECT_ROOT)}
        outputs = [
            subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=PROJECT_ROOT,
                env={**__import__("os").environ, **environment},
                text=True,
            ).strip()
            for _ in range(2)
        ]
        self.assertEqual(outputs, [digest, digest])
        self.assertEqual(len(digest), 16)

    def test_jaccard_and_empty_shingle_edge_cases(self):
        self.assertEqual(calculate_similarity(set(), {"x"}), 0.0)
        self.assertEqual(calculate_similarity({"x"}, {"x"}), 1.0)
        self.assertEqual(calculate_similarity({"x", "y"}, {"y", "z"}), 1 / 3)
        self.assertEqual(
            calculate_similarity(
                create_shingles("one two three four"),
                create_shingles("one two three four"),
            ),
            0.0,
        )

    def test_lamport_tick_and_receive_follow_clock_rules(self):
        first = LamportClock()
        second = LamportClock()
        sent = first.tick()
        received = second.receive(sent)
        self.assertGreater(received, sent)
        self.assertEqual(first.tick(), sent + 1)
        self.assertEqual(second.receive(20), 21)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            second.receive(-1)

    def test_rabbitmq_connections_use_configured_credentials(self):
        parameters = connection_parameters("rabbitmq", "demo-user", "demo-pass")
        self.assertEqual(parameters.host, "rabbitmq")
        self.assertEqual(parameters.credentials.username, "demo-user")
        self.assertEqual(parameters.credentials.password, "demo-pass")

    def test_block_pairing_covers_every_unordered_document_pair_once(self):
        for document_count in range(2, 15):
            documents = [f"doc_{index}.txt" for index in range(document_count)]
            expected = set(itertools.combinations(documents, 2))
            for block_size in range(1, document_count + 1):
                blocks = partition_documents(documents, block_size)
                actual = list(iter_document_pairs(blocks))
                with self.subTest(document_count=document_count, block_size=block_size):
                    self.assertEqual(len(actual), count_document_pairs(document_count))
                    self.assertEqual(len(actual), len(set(actual)))
                    self.assertEqual(set(actual), expected)

    def test_generated_tasks_cover_all_diagonal_and_cross_block_pairs(self):
        tasks = generate_block_tasks(
            [f"doc_{index}.txt" for index in range(7)],
            3,
            "job-test",
            "dataset-test",
            0.25,
            iter(range(1, 20)).__next__,
        )
        self.assertEqual(len(tasks), 6)
        self.assertEqual(len({task["task_id"] for task in tasks}), len(tasks))
        self.assertEqual({(task["block_a"], task["block_b"]) for task in tasks}, {
            (0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2)
        })
        for task in tasks:
            self.assertEqual(task["attempt"], 0)
            self.assertIsInstance(task["lc"], int)
            self.assertEqual(task["dataset_id"], "dataset-test")
            self.assertEqual(task["block_size"], 3)
            self.assertEqual(
                set(task),
                {
                    "job_id",
                    "task_id",
                    "dataset_id",
                    "block_a",
                    "block_b",
                    "threshold",
                    "attempt",
                    "lc",
                    "block_size",
                },
            )

    def test_corpus_is_reproducible_and_planted_pairs_are_recalled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            manifest = create_dataset(first, 12, 314, 3, [0.3, 0.6, 0.9])
            create_dataset(second, 12, 314, 3, [0.3, 0.6, 0.9])
            self.assertEqual(
                sorted(path.read_bytes() for path in first.glob("doc_*.txt")),
                sorted(path.read_bytes() for path in second.glob("doc_*.txt")),
            )
            self.assertEqual(
                manifest["planted_pairs"],
                json.loads((first / "ground_truth.json").read_text())["planted_pairs"],
            )

            report = run_baseline(first, threshold=0.1)
            self.assertEqual(report["comparisons"], 66)
            self.assertEqual(report["expected_pairs"], 66)
            self.assertEqual(report["ground_truth"]["found_planted_pairs"], 3)
            self.assertEqual(report["ground_truth"]["recall"], 1.0)

    def test_generator_will_not_replace_existing_data_without_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dataset"
            create_dataset(output, 2, 1, 0, [0.5])
            with self.assertRaisesRegex(FileExistsError, "--force"):
                create_dataset(output, 2, 1, 0, [0.5])
            create_dataset(output, 2, 1, 0, [0.5], force=True)
            self.assertEqual(len(list(output.glob("doc_*.txt"))), 2)

    def test_verification_rejects_duplicate_distributed_pairs(self):
        with tempfile.TemporaryDirectory() as temporary:
            dataset_dir = Path(temporary)
            (dataset_dir / "doc_001.txt").write_text(
                "one two three four five", encoding="utf-8"
            )
            (dataset_dir / "doc_002.txt").write_text(
                "one two three four six", encoding="utf-8"
            )
            baseline = run_baseline(dataset_dir, threshold=0.0)
            match = baseline["matches"][0]
            result = verify_distributed_results(
                dataset_dir,
                [
                    [match["document_a"], match["document_b"], match["similarity"]],
                    [match["document_a"], match["document_b"], match["similarity"]],
                ],
                0.0,
                1,
                1,
                {"unique_task_ids": True, "pair_count": True},
            )
            self.assertFalse(result["passed"])
            self.assertEqual(result["distributed_duplicate_pair_count"], 1)


if __name__ == "__main__":
    unittest.main()
