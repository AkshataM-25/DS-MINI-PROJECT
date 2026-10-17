import json
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from socketserver import ThreadingMixIn
from xmlrpc.server import SimpleXMLRPCServer

import pika

from common.clock import LamportClock
from common.rabbitmq import connection_parameters


RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "similardocs")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "similarDocsLocalOnly")
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
RESULTS_QUEUE = os.getenv("RESULTS_QUEUE", "results")
FAILED_QUEUE = os.getenv("FAILED_QUEUE", "failed_tasks")
RPC_HOST = "0.0.0.0"
RPC_PORT = 9000
WORKER_HEALTH_SECONDS = 15


def configure_logging(registry=None):
    class ServiceContextFilter(logging.Filter):
        def filter(self, record):
            record.worker_id = getattr(record, "worker_id", "-")
            record.lc = getattr(
                record,
                "lc",
                registry.lamport_counter if registry is not None else 0,
            )
            return True

    handler = logging.StreamHandler()
    handler.addFilter(ServiceContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s service=aggregator worker_id=%(worker_id)s "
            "%(levelname)s lc=%(lc)s %(message)s"
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


class JobRegistry:
    def __init__(self, data_root: Path):
        self._data_root = data_root
        self._jobs_dir = data_root / "jobs"
        self._summaries_dir = self._jobs_dir / "summaries"
        self._lock = threading.RLock()
        self._clock = LamportClock()
        self._jobs: dict[str, dict] = {}
        self._workers: dict[str, dict] = {}
        self._migrate_saved_summaries()

    @property
    def lamport_counter(self):
        return self._clock.value

    def register_job(
        self,
        job_id,
        total_tasks,
        n_docs,
        threshold,
        dataset_id,
        expected_pairs,
        task_ids,
        ground_truth,
        block_size,
    ):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
            raise ValueError("job_id must be a UUID")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id):
            raise ValueError("Invalid dataset_id")
        if (
            not isinstance(task_ids, list)
            or len(task_ids) != total_tasks
            or len(set(task_ids)) != total_tasks
            or any(not isinstance(task_id, str) for task_id in task_ids)
        ):
            raise ValueError("task_ids must be unique and match total_tasks")
        if expected_pairs != n_docs * (n_docs - 1) // 2:
            raise ValueError("expected_pairs does not match document count")
        now = time.time()
        with self._lock:
            if job_id in self._jobs:
                raise ValueError("job_id is already registered")
            self._jobs[job_id] = {
                "job_id": job_id,
                "dataset_id": dataset_id,
                "total_tasks": total_tasks,
                "n_docs": n_docs,
                "threshold": threshold,
                "expected_pairs": expected_pairs,
                "block_size": block_size,
                "task_ids": set(task_ids),
                "submitted_tasks": 0,
                "results_by_task": {},
                "results": [],
                "created_at": now,
                "submitted_at": None,
                "completed_at": None,
                "status": "submitting",
                "ground_truth": ground_truth,
                "events": [],
                "pairs_compared": 0,
                "workers": {},
            }
            registration_lc = self._clock.tick()
            self._append_event(
                self._jobs[job_id],
                "aggregator",
                "job_registered",
                registration_lc,
                now,
            )
        logging.info("registered job_id=%s tasks=%s docs=%s", job_id, total_tasks, n_docs)
        status = self.get_job_status(job_id)
        status["lc"] = registration_lc
        return status

    def mark_submitted(self, job_id, submitted_tasks, error=None):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("job_id is not registered")
            if not 0 <= submitted_tasks <= job["total_tasks"]:
                raise ValueError("submitted_tasks is outside the expected range")
            job["submitted_tasks"] = submitted_tasks
            job["submitted_at"] = job["submitted_at"] or time.time()
            if error:
                job["status"] = "submission_failed"
            elif job["status"] == "submitting":
                job["status"] = "queued"
            return self._public_status(job)

    def log_event(self, remote_lc, service, wall_time_reported, job_id, task_id, name):
        if not isinstance(service, str) or not isinstance(name, str):
            raise ValueError("service and event name must be strings")
        with self._lock:
            self._clock.receive(remote_lc)
            event = {
                "lc": remote_lc,
                "service": service,
                "wall_time_reported": float(wall_time_reported),
                "job_id": job_id or "",
                "task_id": task_id or "",
                "event": name,
                "received_at": time.time(),
            }
            job = self._jobs.get(job_id) if job_id else None
            if job is not None:
                job["events"].append(event)
            if service and service != "api" and service != "aggregator":
                worker = self._workers.setdefault(
                    service,
                    {
                        "worker_id": service,
                        "current_task": None,
                        "tasks_processed": 0,
                        "tasks_failed": 0,
                    },
                )
                if name == "task_received":
                    worker["current_task"] = task_id
                elif name in ("task_done", "task_failed", "worker_killed"):
                    worker["current_task"] = None
                worker["last_seen"] = (
                    time.time() - WORKER_HEALTH_SECONDS - 1
                    if name == "worker_killed"
                    else time.time()
                )
                worker["wall_time_reported"] = float(wall_time_reported)
                worker["lc"] = remote_lc
            logging.info(
                "RPC log_event service=%s event=%s job_id=%s task_id=%s lc=%s",
                service,
                name,
                job_id,
                task_id,
                remote_lc,
            )
            return remote_lc

    def worker_heartbeat(
        self,
        worker_id,
        current_task,
        tasks_processed,
        tasks_failed,
        remote_lc,
        wall_time_reported,
    ):
        local_lc = self._clock.receive(remote_lc)
        with self._lock:
            self._workers[worker_id] = {
                "worker_id": worker_id,
                "current_task": current_task or None,
                "tasks_processed": int(tasks_processed),
                "tasks_failed": int(tasks_failed),
                "last_seen": time.time(),
                "wall_time_reported": float(wall_time_reported),
                "lc": local_lc,
            }
        return True

    def get_workers(self):
        now = time.time()
        with self._lock:
            return [
                {
                    **worker,
                    "seconds_since_seen": max(0.0, now - worker["last_seen"]),
                    "healthy": now - worker["last_seen"] <= WORKER_HEALTH_SECONDS,
                }
                for worker in sorted(
                    self._workers.values(), key=lambda item: item["worker_id"]
                )
            ]

    def record_result(self, result: dict) -> bool:
        required = ("job_id", "task_id", "worker_id", "pairs_compared", "matches", "lc")
        if not isinstance(result, dict) or any(key not in result for key in required):
            raise ValueError("Result message is missing required fields")
        job_id = result["job_id"]
        task_id = result["task_id"]
        if not isinstance(task_id, str) or not isinstance(job_id, str):
            raise ValueError("job_id and task_id must be strings")
        if not isinstance(result["pairs_compared"], int) or result["pairs_compared"] < 0:
            raise ValueError("pairs_compared must be a non-negative integer")
        if result.get("status", "completed") not in ("completed", "failed"):
            raise ValueError("result status must be completed or failed")
        if not isinstance(result["matches"], list):
            raise ValueError("matches must be a list")

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("Result references an unknown job")
            if task_id not in job["task_ids"]:
                raise ValueError("Result references an unknown task")
            if task_id in job["results_by_task"]:
                self._complete_if_ready(job)
                logging.info("ignored duplicate result task_id=%s job_id=%s", task_id, job_id)
                return False

            status = result.get("status", "completed")
            if status == "failed" and (result["pairs_compared"] or result["matches"]):
                raise ValueError("Failed task results cannot include compared pairs or matches")
            for match in result["matches"]:
                if (
                    not isinstance(match, list)
                    or len(match) != 3
                    or not isinstance(match[0], str)
                    or not isinstance(match[1], str)
                    or not isinstance(match[2], (int, float))
                    or not 0 <= match[2] <= 1
                ):
                    raise ValueError("Each match must be [document_a, document_b, score]")

            stored = {
                "job_id": job_id,
                "task_id": task_id,
                "worker_id": result["worker_id"],
                "pairs_compared": result["pairs_compared"],
                "matches": result["matches"],
                "duration_ms": float(result.get("duration_ms", 0)),
                "status": status,
                "error": result.get("error"),
            }
            job["results_by_task"][task_id] = stored
            job["pairs_compared"] += result["pairs_compared"]
            job["results"].extend(result["matches"])
            worker = job["workers"].setdefault(
                result["worker_id"], {"tasks_processed": 0, "tasks_failed": 0}
            )
            if status == "failed":
                worker["tasks_failed"] += 1
            else:
                worker["tasks_processed"] += 1
            if job["status"] not in ("submission_failed", "completed", "completed_with_failures"):
                job["status"] = "processing"

            local_lc = self._clock.receive(result["lc"])
            self._append_event(
                job,
                "aggregator",
                "result_received",
                local_lc,
                time.time(),
                task_id,
            )
            self._complete_if_ready(job)
            logging.info(
                "result received job_id=%s task_id=%s status=%s",
                job_id,
                task_id,
                status,
            )
            return True

    def get_job_status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return self._public_status(job)
        return self._read_saved_job(job_id)

    def get_results(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return self._sorted_results(job)
        saved = self._read_saved_job(job_id)
        return saved.get("results", []) if saved else []

    def get_results_page(self, job_id, minimum_score, offset, limit):
        if not 0 <= minimum_score <= 1 or offset < 0 or limit < 1:
            raise ValueError("Invalid results page parameters")
        with self._lock:
            job = self._jobs.get(job_id)
            results = (
                self._sorted_results(job)
                if job is not None
                else (self._read_saved_job(job_id) or {}).get("results", [])
            )
        filtered = [row for row in results if row[2] >= minimum_score]
        return {
            "matches": filtered[offset:offset + limit],
            "filtered_count": len(filtered),
        }

    def get_timeline(self, job_id, order="lamport"):
        if order not in ("wall", "lamport"):
            raise ValueError("order must be wall or lamport")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                events = list(job["events"])
            else:
                saved = self._read_saved_job(job_id)
                events = saved.get("events", []) if saved else []
        sort_key = (
            (lambda event: (event["wall_time_reported"], event["lc"]))
            if order == "wall"
            else (lambda event: (event["lc"], event["service"]))
        )
        return sorted(events, key=sort_key)

    def get_time(self):
        return {"server_time": time.time()}

    def list_jobs(self):
        with self._lock:
            jobs = {job_id: self._public_status(job) for job_id, job in self._jobs.items()}
            for path in self._summaries_dir.glob("*.json"):
                try:
                    report = json.loads(path.read_text(encoding="utf-8"))
                    jobs.setdefault(report["job_id"], self._saved_status(report))
                except (OSError, KeyError, json.JSONDecodeError):
                    logging.exception("Could not read saved job summary path=%s", path)
            return sorted(jobs.values(), key=lambda job: job["created_at"], reverse=True)

    def _migrate_saved_summaries(self):
        self._summaries_dir.mkdir(parents=True, exist_ok=True)
        for path in self._jobs_dir.glob("*.json"):
            summary_path = self._summaries_dir / path.name
            if summary_path.exists():
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                job_id = report.get("job_id")
                if (
                    not isinstance(job_id, str)
                    or not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id)
                    or path.stem != job_id
                ):
                    logging.warning("Skipping invalid saved job report path=%s", path)
                    continue
                temporary = summary_path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(self._saved_status(report), indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(summary_path)
            except (OSError, AttributeError, json.JSONDecodeError):
                logging.exception("Could not migrate saved job report path=%s", path)

    @staticmethod
    def _saved_status(report):
        return {
            key: value
            for key, value in report.items()
            if key not in ("results", "events", "ground_truth", "failed_task_details")
        }

    def _append_event(self, job, service, name, lc, wall_time, task_id=""):
        job["events"].append(
            {
                "lc": lc,
                "service": service,
                "wall_time_reported": wall_time,
                "job_id": job["job_id"],
                "task_id": task_id,
                "event": name,
                "received_at": time.time(),
            }
        )

    @staticmethod
    def _sorted_results(job):
        return sorted(
            job["results"],
            key=lambda match: (-float(match[2]), match[0], match[1]),
        )

    def _public_status(self, job):
        completed = len(job["results_by_task"])
        now = time.time()
        active_task_ids = {
            worker["current_task"]
            for worker in self._workers.values()
            if worker.get("current_task")
            and now - worker["last_seen"] <= WORKER_HEALTH_SECONDS
            and worker["current_task"] in job["task_ids"]
        }
        in_progress = len(active_task_ids - set(job["results_by_task"]))
        failed = sum(
            result["status"] == "failed"
            for result in job["results_by_task"].values()
        )
        if job["status"] in ("completed", "completed_with_failures"):
            status = job["status"]
        else:
            status = job["status"]
        return {
            "job_id": job["job_id"],
            "dataset_id": job["dataset_id"],
            "status": status,
            "n_docs": job["n_docs"],
            "threshold": job["threshold"],
            "block_size": job["block_size"],
            "total_tasks": job["total_tasks"],
            "submitted_tasks": job["submitted_tasks"],
            "completed_tasks": completed - failed,
            "failed_tasks": failed,
            "tasks_done": completed,
            "in_progress_tasks": in_progress,
            "reported_retry_events": sum(
                event["event"] == "task_retry" for event in job["events"]
            ),
            "pairs_compared": job["pairs_compared"],
            "expected_pairs": job["expected_pairs"],
            "results_saved": len(job["results"]),
            "matches_count": len(job["results"]),
            "created_at": job["created_at"],
            "submitted_at": job["submitted_at"],
            "completed_at": job["completed_at"],
            "elapsed_seconds": max(0.0, (job["completed_at"] or now) - job["created_at"]),
            "pairs_per_second": job["pairs_compared"]
            / max(0.001, (job["completed_at"] or now) - job["created_at"]),
            "integrity": {
                "unique_task_ids": completed == job["total_tasks"],
                "pair_count": job["pairs_compared"] == job["expected_pairs"],
                "unique_tasks_received": completed,
                "expected_tasks": job["total_tasks"],
                "pairs_compared": job["pairs_compared"],
                "expected_pairs": job["expected_pairs"],
            },
            "workers": job["workers"],
            "error": job.get("error"),
        }

    def _complete_if_ready(self, job):
        if len(job["results_by_task"]) != job["total_tasks"]:
            return
        if job["completed_at"] is not None:
            self._write_report(job)
            return
        failed = any(
            result["status"] == "failed"
            for result in job["results_by_task"].values()
        )
        job["status"] = "completed_with_failures" if failed else "completed"
        job["completed_at"] = job["completed_at"] or time.time()
        self._append_event(
            job,
            "aggregator",
            "job_completed",
            self._clock.tick(),
            job["completed_at"],
        )
        self._write_report(job)

    def _write_report(self, job):
        output_dir = self._data_root / "jobs"
        output_dir.mkdir(parents=True, exist_ok=True)
        report = self._public_status(job)
        report["results"] = self._sorted_results(job)
        report["events"] = list(job["events"])
        report["ground_truth"] = job["ground_truth"]
        report["failed_task_details"] = [
            result
            for result in job["results_by_task"].values()
            if result["status"] == "failed"
        ]
        path = output_dir / f"{job['job_id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        summary_path = self._summaries_dir / path.name
        summary_temporary = summary_path.with_suffix(".json.tmp")
        summary_temporary.write_text(
            json.dumps(self._saved_status(report), indent=2) + "\n",
            encoding="utf-8",
        )
        summary_temporary.replace(summary_path)

    def _read_saved_job(self, job_id):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id):
            return None
        path = self._data_root / "jobs" / f"{job_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None


class ThreadedXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True


def create_rpc_server(
    registry: JobRegistry,
    host: str = RPC_HOST,
    port: int = RPC_PORT,
) -> ThreadedXMLRPCServer:
    server = ThreadedXMLRPCServer((host, port), allow_none=True, logRequests=False)
    for method_name in (
        "register_job",
        "mark_submitted",
        "log_event",
        "worker_heartbeat",
        "get_workers",
        "get_job_status",
        "get_results",
        "get_results_page",
        "get_timeline",
        "get_time",
        "list_jobs",
    ):
        registry_method = getattr(registry, method_name)

        def log_rpc_call(*args, _name=method_name, _method=registry_method):
            logging.info("RPC call method=%s", _name)
            return _method(*args)

        server.register_function(log_rpc_call, method_name)
    return server


def main():
    registry = JobRegistry(DATA_ROOT)
    configure_logging(registry)

    def handle_result(channel, method, properties, body):
        try:
            result = json.loads(body)
            registry.record_result(result)
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as error:
            logging.exception("invalid result message error=%s", type(error).__name__)
            channel.basic_publish(
                exchange="",
                routing_key=FAILED_QUEUE,
                body=json.dumps(
                    {
                        "error": type(error).__name__,
                        "message": body.decode("utf-8", errors="replace"),
                    }
                ),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                ),
                mandatory=True,
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except OSError as error:
            logging.exception("could not save aggregate result")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    connection = pika.BlockingConnection(
        connection_parameters(
            RABBITMQ_HOST,
            RABBITMQ_USER,
            RABBITMQ_PASSWORD,
            connection_attempts=10,
            retry_delay=2,
            socket_timeout=3,
            blocked_connection_timeout=10,
            heartbeat=30,
        )
    )
    channel = connection.channel()
    channel.queue_declare(queue=RESULTS_QUEUE, durable=True)
    channel.queue_declare(queue=FAILED_QUEUE, durable=True)
    channel.confirm_delivery()
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=RESULTS_QUEUE, on_message_callback=handle_result)

    server = create_rpc_server(registry)
    rpc_thread = threading.Thread(target=server.serve_forever, daemon=True)
    rpc_thread.start()
    logging.info(
        "aggregator ready rpc=%s:%s queue=%s",
        RPC_HOST,
        RPC_PORT,
        RESULTS_QUEUE,
    )
    try:
        channel.start_consuming()
    finally:
        server.shutdown()
        server.server_close()
        connection.close()


if __name__ == "__main__":
    main()
