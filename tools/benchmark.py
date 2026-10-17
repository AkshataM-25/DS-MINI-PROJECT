import argparse
import csv
import json
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def api_json(api_url: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {path} returned HTTP {error.code}: {detail}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"API request failed for {path}: {type(error).__name__}") from error


def scale_workers(workers: int) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--scale",
            f"worker={workers}",
            "api",
            "worker",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def wait_for_consumers(api_url: str, workers: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        system = api_json(api_url, "/api/system")
        if system.get("task_consumers", 0) >= workers:
            return
        time.sleep(1)
    raise TimeoutError(f"Only {system.get('task_consumers', 0)} of {workers} workers registered.")


def submit_and_wait(
    api_url: str,
    dataset_id: str,
    threshold: float,
    block_size: int,
    timeout: float,
) -> tuple[str, float, dict]:
    started = time.perf_counter()
    submitted = api_json(
        api_url,
        "/api/jobs",
        {
            "dataset_id": dataset_id,
            "threshold": threshold,
            "block_size": block_size,
        },
    )
    job_id = submitted["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = api_json(api_url, f"/api/jobs/{job_id}")
        if job["status"] in ("completed", "completed_with_failures"):
            elapsed = time.perf_counter() - started
            if (
                job["status"] != "completed"
                or not job["integrity"]["unique_task_ids"]
                or not job["integrity"]["pair_count"]
            ):
                raise RuntimeError(f"Job {job_id} ended without complete integrity: {job['integrity']}")
            return job_id, elapsed, job
        time.sleep(1)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout:.0f} seconds.")


def write_svg(path: Path, runs: list[dict]) -> None:
    width, height = 720, 320
    left, right, top, bottom = 70, 28, 30, 55
    maximum = max(run["median_seconds"] for run in runs) or 1
    x_step = (width - left - right) / max(1, len(runs) - 1)

    def x(index: int) -> float:
        return left + index * x_step

    def y(value: float) -> float:
        return height - bottom - value / maximum * (height - top - bottom)

    points = " ".join(
        f"{x(index):.1f},{y(run['median_seconds']):.1f}"
        for index, run in enumerate(runs)
    )
    labels = "\n".join(
        f'<circle cx="{x(index):.1f}" cy="{y(run["median_seconds"]):.1f}" r="5" fill="#4c5fc7"/>'
        f'<text x="{x(index):.1f}" y="{height-bottom+22}" text-anchor="middle">{run["workers"]} worker(s)</text>'
        for index, run in enumerate(runs)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Measured benchmark duration by worker count">
<rect width="100%" height="100%" fill="white"/>
<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#aeb8c8"/>
<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#aeb8c8"/>
<polyline points="{points}" fill="none" stroke="#4c5fc7" stroke-width="3"/>
{labels}
<text x="16" y="20">Median end-to-end job time (seconds)</text>
</svg>
'''
    path.write_text(svg, encoding="utf-8")


def run_benchmark(
    api_url: str,
    dataset_id: str,
    max_workers: int,
    repeats: int,
    threshold: float,
    block_size: int,
    timeout: float,
    output_dir: Path,
) -> dict:
    dataset = api_json(api_url, f"/api/datasets/{dataset_id}")
    if dataset["document_count"] < 2:
        raise ValueError("The selected dataset must contain at least two documents.")
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_pairs = dataset["pair_count"]
    records = []
    baseline_seconds = None
    for workers in range(1, max_workers + 1):
        scale_workers(workers)
        wait_for_consumers(api_url, workers, timeout)
        warmup_id, _, _ = submit_and_wait(
            api_url, dataset_id, threshold, block_size, timeout
        )
        times = []
        job_ids = []
        for _ in range(repeats):
            job_id, elapsed, _ = submit_and_wait(
                api_url, dataset_id, threshold, block_size, timeout
            )
            times.append(elapsed)
            job_ids.append(job_id)
        median_seconds = statistics.median(times)
        if workers == 1:
            baseline_seconds = median_seconds
        speedup = baseline_seconds / median_seconds if median_seconds else 0.0
        records.append(
            {
                "workers": workers,
                "warmup_job_id": warmup_id,
                "job_ids": job_ids,
                "run_times_seconds": times,
                "median_seconds": median_seconds,
                "pairs_per_second": expected_pairs / median_seconds if median_seconds else 0.0,
                "speedup": speedup,
                "efficiency": speedup / workers,
            }
        )

    benchmark_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {
        "benchmark_id": benchmark_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset_id,
        "document_count": dataset["document_count"],
        "expected_pairs": expected_pairs,
        "threshold": threshold,
        "block_size": block_size,
        "repeats": repeats,
        "warmup_jobs": 1,
        "host_logical_cpus": os.cpu_count() or 1,
        "configuration": {
            "max_workers": max_workers,
            "api_url": api_url,
            "timing": "host monotonic clock from job submission through completion",
        },
        "runs": records,
    }
    output_path = output_dir / f"benchmark_{benchmark_id}.json"
    csv_path = output_dir / f"benchmark_{benchmark_id}.csv"
    svg_path = output_dir / f"benchmark_{benchmark_id}.svg"
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(
            ["workers", "median_seconds", "pairs_per_second", "speedup", "efficiency", "run_times_seconds", "job_ids"]
        )
        for run in records:
            writer.writerow(
                [
                    run["workers"],
                    f'{run["median_seconds"]:.6f}',
                    f'{run["pairs_per_second"]:.3f}',
                    f'{run["speedup"]:.3f}',
                    f'{run["efficiency"]:.3f}',
                    json.dumps(run["run_times_seconds"]),
                    json.dumps(run["job_ids"]),
                ]
            )
    write_svg(svg_path, records)
    api_json(api_url, "/api/experiments/import", report)
    return {
        "report": report,
        "json_path": output_path,
        "csv_path": csv_path,
        "svg_path": svg_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure real end-to-end job times while scaling Compose workers."
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--dataset-id", default="demo")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--block-size", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    if args.max_workers < 1 or args.repeats < 1 or args.timeout <= 0:
        parser.error("max-workers, repeats, and timeout must be positive.")
    try:
        result = run_benchmark(
            args.api_url,
            args.dataset_id,
            args.max_workers,
            args.repeats,
            args.threshold,
            args.block_size,
            args.timeout,
            args.output_dir,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    report = result["report"]
    print(f"Saved JSON: {result['json_path']}")
    print(f"Saved CSV: {result['csv_path']}")
    print(f"Saved chart: {result['svg_path']}")
    print(f"Imported report into dashboard: {report['benchmark_id']}")
    if len(report["runs"]) > 1 and report["runs"][-1]["speedup"] < report["runs"][-1]["workers"] * 0.5:
        print("Note: scaling flattened; added workers did not produce near-linear speedup.")
    else:
        print("Note: results are measured as recorded; inspect per-run variation before drawing conclusions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
