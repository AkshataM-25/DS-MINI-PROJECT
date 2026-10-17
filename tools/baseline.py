import argparse
import csv
import itertools
import json
import time
from pathlib import Path

from common.shingle import calculate_similarity, create_shingles
from common.tasks import count_document_pairs


def run_baseline(dataset_dir: Path, threshold: float = 0.1) -> dict:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    documents = sorted(dataset_dir.glob("doc_*.txt"))
    if len(documents) < 2:
        raise ValueError("At least two doc_*.txt documents are required.")

    started = time.perf_counter()
    shingle_sets = {
        path.name: create_shingles(path.read_text(encoding="utf-8"))
        for path in documents
    }
    comparisons = []
    for first, second in itertools.combinations(documents, 2):
        comparisons.append(
            {
                "document_a": first.name,
                "document_b": second.name,
                "similarity": calculate_similarity(
                    shingle_sets[first.name],
                    shingle_sets[second.name],
                ),
            }
        )
    comparisons.sort(
        key=lambda result: (
            result["document_a"],
            result["document_b"],
        )
    )
    elapsed_seconds = time.perf_counter() - started
    manifest_path = dataset_dir / "ground_truth.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"planted_pairs": []}
    )
    planted_pairs = {
        tuple(sorted((pair["document_a"], pair["document_b"])))
        for pair in manifest.get("planted_pairs", [])
    }
    found_pairs = {
        tuple(sorted((row["document_a"], row["document_b"])))
        for row in comparisons
        if row["similarity"] >= threshold
    }
    true_positives = len(planted_pairs & found_pairs)
    return {
        "dataset_id": dataset_dir.name,
        "document_count": len(documents),
        "expected_pairs": count_document_pairs(len(documents)),
        "comparisons": len(comparisons),
        "threshold": threshold,
        "elapsed_seconds": elapsed_seconds,
        "matches": [
            row for row in comparisons if row["similarity"] >= threshold
        ],
        "all_comparisons": comparisons,
        "ground_truth": {
            "planted_pairs": len(planted_pairs),
            "found_planted_pairs": true_positives,
            "recall": true_positives / len(planted_pairs) if planted_pairs else None,
        },
        "algorithm": "normalized five-word shingles; BLAKE2b-64; Jaccard",
    }


def write_baseline(dataset_dir: Path, report: dict) -> tuple[Path, Path]:
    json_path = dataset_dir / "baseline_results.json"
    csv_path = dataset_dir / "baseline_results.csv"
    temporary_json = json_path.with_suffix(".json.tmp")
    temporary_csv = csv_path.with_suffix(".csv.tmp")
    temporary_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with temporary_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["document_a", "document_b", "similarity"])
        writer.writerows(
            (
                row["document_a"],
                row["document_b"],
                f'{row["similarity"]:.6f}',
            )
            for row in report["matches"]
        )
    temporary_json.replace(json_path)
    temporary_csv.replace(csv_path)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the shared single-process baseline.")
    parser.add_argument("--dataset", type=Path, default=Path("data/datasets/demo"))
    parser.add_argument("--threshold", type=float, default=0.1)
    args = parser.parse_args()
    try:
        report = run_baseline(args.dataset, args.threshold)
        json_path, csv_path = write_baseline(args.dataset, report)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(f"Documents processed: {report['document_count']}")
    print(f"Pairs compared: {report['comparisons']} / {report['expected_pairs']}")
    print(f"Matches above threshold: {len(report['matches'])}")
    print(f"Planted-pair recall: {report['ground_truth']['recall']}")
    print(f"Wall-clock runtime: {report['elapsed_seconds']:.6f} seconds")
    print(f"JSON report: {json_path}")
    print(f"CSV matches: {csv_path}")
    return 0 if report["comparisons"] == report["expected_pairs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
