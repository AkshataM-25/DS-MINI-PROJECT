import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.baseline import run_baseline


def verify_distributed_results(
    dataset_dir: Path,
    distributed_results: list[list],
    threshold: float,
    pairs_compared: int,
    expected_pairs: int,
    integrity: dict,
) -> dict:
    baseline = run_baseline(dataset_dir, threshold)
    distributed = {}
    for row in distributed_results:
        distributed[(row[0], row[1])] = round(float(row[2]), 6)
    baseline_matches = {
        (row["document_a"], row["document_b"]): round(
            float(row["similarity"]), 6
        )
        for row in baseline["matches"]
    }
    duplicate_pair_count = len(distributed_results) - len(distributed)
    pair_set_match = (
        duplicate_pair_count == 0
        and set(distributed) == set(baseline_matches)
    )
    scores_match = pair_set_match and all(
        distributed[pair] == baseline_matches[pair]
        for pair in baseline_matches
    )
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
    distributed_pairs = {tuple(sorted(pair)) for pair in distributed}
    recalled_pairs = len(planted_pairs & distributed_pairs)
    recall = recalled_pairs / len(planted_pairs) if planted_pairs else None
    full_coverage = (
        pairs_compared == expected_pairs == baseline["expected_pairs"]
        and baseline["comparisons"] == baseline["expected_pairs"]
    )
    planted_pairs_pass = recall is None or recalled_pairs == len(planted_pairs)
    passed = (
        pair_set_match
        and scores_match
        and full_coverage
        and integrity.get("unique_task_ids", False)
        and integrity.get("pair_count", False)
        and planted_pairs_pass
    )
    return {
        "passed": passed,
        "pair_set_match": pair_set_match,
        "scores_match_to_6_decimals": scores_match,
        "full_pair_coverage": full_coverage,
        "unique_task_integrity": bool(integrity.get("unique_task_ids", False)),
        "pair_count_integrity": bool(integrity.get("pair_count", False)),
        "distributed_match_count": len(distributed),
        "distributed_duplicate_pair_count": duplicate_pair_count,
        "baseline_match_count": len(baseline_matches),
        "expected_pairs": expected_pairs,
        "pairs_compared": pairs_compared,
        "planted_pair_count": len(planted_pairs),
        "planted_pairs_recalled": recalled_pairs,
        "planted_pair_recall": recall,
        "threshold": threshold,
        "algorithm": baseline["algorithm"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare saved distributed output with the shared baseline."
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--api-url",
        help="Verify a job stored in the Compose named volume through the REST API.",
    )
    args = parser.parse_args()
    if args.api_url:
        request = Request(
            f"{args.api_url.rstrip('/')}/api/jobs/{args.job_id}/verify",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=300) as response:
                verification = json.load(response)
        except HTTPError as error:
            parser.error(
                f"API verification failed: HTTP {error.code} "
                f"{error.read().decode('utf-8', errors='replace')}"
            )
        except (OSError, URLError, json.JSONDecodeError) as error:
            parser.error(f"Could not verify through the API: {type(error).__name__}")
        output_dir = Path("results")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"verification_{args.job_id}.json"
        output_path.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
        print(f"Verification: {'PASS' if verification['passed'] else 'FAIL'}")
        print(f"Report: {output_path}")
        return 0 if verification["passed"] else 1

    report_path = args.data_root / "jobs" / f"{args.job_id}.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        verification = verify_distributed_results(
            args.data_root / "datasets" / report["dataset_id"],
            report["results"],
            report["threshold"],
            report["pairs_compared"],
            report["expected_pairs"],
            report["integrity"],
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        parser.error(f"Could not verify job {args.job_id}: {error}")
    output_dir = args.data_root / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"verification_{args.job_id}.json"
    output_path.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    report["verification"] = verification
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    print(f"Verification: {'PASS' if verification['passed'] else 'FAIL'}")
    print(
        "Distributed/baseline matches: "
        f"{verification['distributed_match_count']}/"
        f"{verification['baseline_match_count']}"
    )
    print(
        "Planted-pair recall: "
        f"{verification['planted_pair_recall']}"
    )
    print(f"Report: {output_path}")
    return 0 if verification["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
