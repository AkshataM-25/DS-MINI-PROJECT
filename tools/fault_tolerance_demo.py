"""Request and observe an explicitly confirmed worker interruption."""

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = os.getenv("SIMILARDOCS_API_URL", "http://127.0.0.1:8000")


def request_json(path, method="GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{API_URL.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {error.code}: {detail}") from error
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Could not read required API telemetry ({type(error).__name__})."
        ) from error


def _require_preconditions(job, system):
    if job is None or job.get("status") != "processing":
        raise ValueError("Select a job whose status is processing.")
    if not system.get("demo_controls"):
        raise ValueError("DEMO_CONTROLS is disabled in the API configuration.")
    if system.get("task_consumers", 0) < 2:
        raise ValueError("At least two live task consumers are required.")
    queue = system.get("queues", {}).get("tasks", {})
    if queue.get("unacknowledged", 0) < 1:
        raise ValueError("RabbitMQ has no unacknowledged task to observe.")
    if not any(
        worker.get("healthy") and worker.get("current_task")
        for worker in system.get("workers", [])
    ):
        raise ValueError("No fresh worker heartbeat reports an active task.")


def run_demo(job_id, confirm, poll_seconds=30):
    if confirm != "YES":
        raise ValueError("Worker interruption requires explicit operator confirmation.")
    job = request_json(f"/api/jobs/{job_id}")
    system = request_json("/api/system")
    _require_preconditions(job, system)
    original_consumers = system["task_consumers"]
    redeliveries_before = system["queues"]["tasks"].get("redeliveries", 0)
    typed_confirmation = input(f"Type KILL {job_id} to target one active worker: ")
    if typed_confirmation != f"KILL {job_id}":
        raise ValueError("Confirmation did not match; no worker kill was requested.")

    response = request_json(
        "/api/system/kill-random-worker",
        method="POST",
        payload={"job_id": job_id},
    )
    worker_id = response["target_worker"]
    deadline = time.monotonic() + poll_seconds
    redelivery_seen = False
    consumer_drop_seen = False
    recovered = False
    latest = system
    while time.monotonic() < deadline:
        time.sleep(0.25)
        latest = request_json("/api/system")
        current_consumers = latest.get("task_consumers", 0)
        if current_consumers < original_consumers:
            consumer_drop_seen = True
        current_redeliveries = (
            latest.get("queues", {}).get("tasks", {}).get("redeliveries", 0)
        )
        redelivery_seen = redelivery_seen or current_redeliveries > redeliveries_before
        if redelivery_seen and current_consumers >= original_consumers:
            recovered = True
            break

    after_job = request_json(f"/api/jobs/{job_id}")
    evidence = {
        "job_id": job_id,
        "worker_id": worker_id,
        "job_status_before": job["status"],
        "job_status_after": after_job["status"],
        "task_consumers_before": original_consumers,
        "task_consumers_after": latest.get("task_consumers", 0),
        "consumer_drop_observed": consumer_drop_seen,
        "redeliveries_before": redeliveries_before,
        "redeliveries_after": (
            latest.get("queues", {}).get("tasks", {}).get("redeliveries", 0)
        ),
        "redelivery_observed": redelivery_seen,
        "all_consumers_recovered": recovered,
        "integrity": after_job.get("integrity"),
    }
    if not redelivery_seen:
        raise RuntimeError(
            "Worker control was sent, but RabbitMQ redelivery was not observed. "
            "Do not claim this run demonstrated redelivery."
        )
    if not recovered:
        raise RuntimeError(
            "RabbitMQ redelivered a task, but the original consumer count did not recover."
        )
    return evidence


def main():
    parser = argparse.ArgumentParser(
        description="Kill one active worker through the guarded demo control API."
    )
    parser.add_argument("--job-id", required=True, help="A currently processing job UUID.")
    parser.add_argument("--confirm", choices=("YES",), help="Required explicit confirmation.")
    parser.add_argument(
        "--observe-seconds",
        type=int,
        default=30,
        help="Maximum time to observe redelivery and consumer recovery.",
    )
    args = parser.parse_args()
    try:
        evidence = run_demo(args.job_id, args.confirm, args.observe_seconds)
    except (RuntimeError, ValueError) as error:
        print(f"Fault demonstration not completed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
