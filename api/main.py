import base64
import json
import logging
import math
import os
import random
import re
import socket
import statistics
import time
import uuid
import xmlrpc.client
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pika
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common.clock import LamportClock
from common.messages import create_task_message
from common.rabbitmq import connection_parameters
from common.rpc import call_aggregator
from common.shingle import create_shingles
from common.tasks import (
    count_document_pairs,
    generate_block_tasks,
    partition_documents,
)
from tools.generate_corpus import create_dataset
from tools.verify import verify_distributed_results


DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
AGGREGATOR_HOST = os.getenv("AGGREGATOR_HOST", "aggregator")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "similardocs")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "similarDocsLocalOnly")
RABBITMQ_MANAGEMENT_URL = os.getenv(
    "RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672"
)
TASK_QUEUE = os.getenv("TASK_QUEUE", "tasks")
RESULTS_QUEUE = os.getenv("RESULTS_QUEUE", "results")
FAILED_QUEUE = os.getenv("FAILED_QUEUE", "failed_tasks")
CONTROL_EXCHANGE = os.getenv("CONTROL_EXCHANGE", "control")
DEMO_CONTROLS = os.getenv("DEMO_CONTROLS", "false").lower() == "true"
SIMULATED_CLOCK_SKEW = float(os.getenv("WORKER_CLOCK_SKEW_SECONDS", "0"))
DEFAULT_BLOCK_SIZE = int(os.getenv("BLOCK_SIZE", "8"))
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_DATASET_DOCUMENTS = 500
MAX_DATASET_BYTES = 64 * 1024 * 1024
clock = LamportClock()

STATIC_DIR = Path(__file__).resolve().parent / "static"
app = FastAPI(title="SimilarDocs", version="3.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def configure_logging():
    class ServiceContextFilter(logging.Filter):
        def filter(self, record):
            record.worker_id = getattr(record, "worker_id", "-")
            record.lc = getattr(record, "lc", clock.value)
            return True

    handler = logging.StreamHandler()
    handler.addFilter(ServiceContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s service=api worker_id=%(worker_id)s "
            "%(levelname)s lc=%(lc)s %(message)s"
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


configure_logging()


def _log_event(name, job_id="", task_id="", lc=None):
    event_lc = clock.tick() if lc is None else lc
    try:
        call_aggregator(
            AGGREGATOR_HOST,
            "log_event",
            event_lc,
            "api",
            time.time(),
            job_id,
            task_id,
            name,
        )
    except (OSError, xmlrpc.client.Error) as error:
        logging.warning(
            "Could not report event=%s job_id=%s error=%s",
            name,
            job_id,
            type(error).__name__,
        )
    return event_lc


def _rpc(method, *args):
    try:
        return call_aggregator(AGGREGATOR_HOST, method, *args)
    except (OSError, xmlrpc.client.Error) as error:
        logging.error("Aggregator RPC unavailable method=%s error=%s", method, type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="The results aggregator is unavailable. Check the Compose service logs.",
        ) from error


def _dataset_path(dataset_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dataset_id):
        raise HTTPException(status_code=400, detail="Invalid dataset ID.")
    path = DATA_ROOT / "datasets" / dataset_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return path


def _documents(dataset_dir: Path) -> list[Path]:
    return sorted(dataset_dir.glob("doc_*.txt"))


def _dataset_summary(dataset_dir: Path) -> dict:
    manifest_path = dataset_dir / "ground_truth.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"synthetic": False, "planted_pairs": []}
    )
    documents = _documents(dataset_dir)
    return {
        "dataset_id": dataset_dir.name,
        "document_count": len(documents),
        "pair_count": count_document_pairs(len(documents)),
        "source": "synthetic demo corpus" if manifest.get("synthetic") else "uploaded text corpus",
        "synthetic": bool(manifest.get("synthetic")),
        "planted_pair_count": len(manifest.get("planted_pairs", [])),
        "ground_truth": manifest,
        "documents": [document.name for document in documents],
    }


def _prepare_dataset(dataset_dir: Path, block_size: int) -> tuple[list[str], Path]:
    document_paths = _documents(dataset_dir)
    if len(document_paths) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least two doc_*.txt documents are required in the dataset.",
        )
    names = [path.name for path in document_paths]
    shingles = {
        path.name: sorted(create_shingles(path.read_text(encoding="utf-8")))
        for path in document_paths
    }
    blocks = partition_documents(names, block_size)
    processed_dir = dataset_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_path = processed_dir / f"block-{block_size}.json"
    temporary_path = processed_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "dataset_id": dataset_dir.name,
                "documents": names,
                "blocks": blocks,
                "shingles": shingles,
            }
        ),
        encoding="utf-8",
    )
    temporary_path.replace(processed_path)
    return names, processed_path


def _rabbitmq_queue(name: str) -> dict:
    url = (
        f"{RABBITMQ_MANAGEMENT_URL.rstrip('/')}/api/queues/%2F/"
        f"{quote(name, safe='')}"
    )
    request = Request(url)
    credentials = f"{RABBITMQ_USER}:{RABBITMQ_PASSWORD}".encode("utf-8")
    request.add_header(
        "Authorization",
        f"Basic {base64.b64encode(credentials).decode('ascii')}",
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
        return {
            "status": "healthy",
            "name": name,
            "ready": payload.get("messages_ready", 0),
            "unacknowledged": payload.get("messages_unacknowledged", 0),
            "consumers": payload.get("consumers", 0),
            "redeliveries": payload.get("message_stats", {}).get("redeliver", 0),
        }
    except (OSError, URLError, json.JSONDecodeError) as error:
        return {
            "status": "unavailable",
            "name": name,
            "error": type(error).__name__,
        }


class GenerateDatasetRequest(BaseModel):
    name: str = Field(default="synthetic", min_length=1, max_length=32)
    documents: int = Field(default=30, ge=2, le=MAX_DATASET_DOCUMENTS)
    seed: int = Field(default=42, ge=0)
    planted_pairs: int = Field(default=3, ge=0)
    overlaps: list[float] = Field(default_factory=lambda: [0.3, 0.6, 0.9])


class CreateJobRequest(BaseModel):
    dataset_id: str
    threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    block_size: int = Field(default=DEFAULT_BLOCK_SIZE, ge=1, le=100)
    inject_poison: bool = False


class KillWorkerRequest(BaseModel):
    job_id: str = ""


class WorkerConsumersRequest(BaseModel):
    active_workers: int = Field(ge=0, le=16)


class ClockSkewRequest(BaseModel):
    seconds: float = Field(ge=-86400, le=86400)


class BenchmarkRun(BaseModel):
    workers: int = Field(ge=1)
    warmup_job_id: str
    job_ids: list[str] = Field(min_length=1)
    run_times_seconds: list[float] = Field(min_length=1)
    median_seconds: float = Field(gt=0)
    pairs_per_second: float = Field(gt=0)
    speedup: float = Field(gt=0)
    efficiency: float = Field(gt=0)


class BenchmarkReport(BaseModel):
    benchmark_id: str = Field(min_length=1, max_length=80)
    created_at: str
    dataset_id: str
    document_count: int = Field(ge=2)
    expected_pairs: int = Field(ge=1)
    threshold: float = Field(ge=0, le=1)
    block_size: int = Field(ge=1, le=100)
    repeats: int = Field(ge=1)
    warmup_jobs: int = Field(ge=1)
    host_logical_cpus: int = Field(ge=1)
    configuration: dict[str, str | int | float]
    runs: list[BenchmarkRun] = Field(min_length=1)


def _publish_demo_control(message: dict) -> None:
    connection = None
    try:
        connection = pika.BlockingConnection(
            connection_parameters(
                RABBITMQ_HOST,
                RABBITMQ_USER,
                RABBITMQ_PASSWORD,
                socket_timeout=3,
            )
        )
        channel = connection.channel()
        channel.exchange_declare(
            exchange=CONTROL_EXCHANGE,
            exchange_type="fanout",
            durable=True,
        )
        channel.confirm_delivery()
        channel.basic_publish(
            exchange=CONTROL_EXCHANGE,
            routing_key="",
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
                message_id=str(uuid.uuid4()),
            ),
            mandatory=True,
        )
    except Exception as error:
        logging.exception("Could not publish demo control action=%s", message.get("action"))
        raise HTTPException(
            status_code=503,
            detail=f"Could not publish the demo control message ({type(error).__name__}).",
        ) from error
    finally:
        if connection is not None and connection.is_open:
            connection.close()


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "healthy", "service": "api"}


@app.get("/api/datasets")
def list_datasets():
    root = DATA_ROOT / "datasets"
    if not root.exists():
        return {"datasets": []}
    return {
        "datasets": [
            _dataset_summary(path)
            for path in sorted(root.iterdir())
            if path.is_dir() and _documents(path)
        ]
    }


@app.get("/api/datasets/{dataset_id}")
def dataset_details(dataset_id: str):
    return _dataset_summary(_dataset_path(dataset_id))


@app.post("/api/datasets/generate", status_code=201)
def generate_dataset(request: GenerateDatasetRequest):
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", request.name).strip("-_")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Dataset name must contain letters or numbers.")
    if request.planted_pairs > request.documents // 2:
        raise HTTPException(
            status_code=400,
            detail="Planted pair count cannot exceed half the document count.",
        )
    dataset_id = f"{safe_name[:40]}-{uuid.uuid4().hex[:8]}"
    output = DATA_ROOT / "datasets" / dataset_id
    try:
        create_dataset(
            output,
            request.documents,
            request.seed,
            request.planted_pairs,
            request.overlaps,
        )
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _dataset_summary(output)


@app.post("/api/datasets/upload", status_code=201)
async def upload_dataset(
    name: str = Form(..., min_length=1, max_length=32),
    files: list[UploadFile] = File(...),
):
    if not 2 <= len(files) <= MAX_DATASET_DOCUMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Upload between 2 and {MAX_DATASET_DOCUMENTS} text files.",
        )
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-_")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Dataset name must contain letters or numbers.")
    dataset_id = f"{safe_name[:40]}-{uuid.uuid4().hex[:8]}"
    directory = DATA_ROOT / "datasets" / dataset_id
    directory.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    try:
        for index, upload in enumerate(files, start=1):
            filename = Path(upload.filename or "").name
            if Path(filename).suffix.lower() != ".txt":
                raise HTTPException(status_code=400, detail="Only .txt uploads are supported.")
            content = await upload.read(MAX_DOCUMENT_BYTES + 1)
            if len(content) > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail="Each text file is limited to 2 MiB.")
            total_bytes += len(content)
            if total_bytes > MAX_DATASET_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="An uploaded dataset is limited to 64 MiB total.",
                )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise HTTPException(status_code=400, detail=f"{filename} must be UTF-8 text.") from error
            (directory / f"doc_{index:03d}.txt").write_text(text, encoding="utf-8")
        (directory / "ground_truth.json").write_text(
            json.dumps({"synthetic": False, "planted_pairs": []}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        for path in directory.glob("doc_*.txt"):
            path.unlink(missing_ok=True)
        (directory / "ground_truth.json").unlink(missing_ok=True)
        directory.rmdir()
        raise
    return _dataset_summary(directory)


@app.get("/api/datasets/{dataset_id}/documents/{filename}")
def get_document(dataset_id: str, filename: str):
    if not re.fullmatch(r"doc_[A-Za-z0-9_-]+\.txt", filename):
        raise HTTPException(status_code=400, detail="Invalid document filename.")
    path = _dataset_path(dataset_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"filename": filename, "text": path.read_text(encoding="utf-8")}


@app.post("/api/jobs", status_code=202)
def submit_job(request: CreateJobRequest):
    dataset_dir = _dataset_path(request.dataset_id)
    try:
        documents, _ = _prepare_dataset(dataset_dir, request.block_size)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not preprocess the selected dataset.") from error
    job_id = str(uuid.uuid4())
    task_clock = LamportClock(clock.value)
    tasks = generate_block_tasks(
        documents,
        request.block_size,
        job_id,
        request.dataset_id,
        request.threshold,
        task_clock.tick,
    )
    if request.inject_poison:
        invalid_block = len(partition_documents(documents, request.block_size))
        tasks.append(
            create_task_message(
                job_id,
                str(uuid.uuid4()),
                request.dataset_id,
                invalid_block,
                invalid_block,
                request.threshold,
                0,
                task_clock.tick(),
                request.block_size,
            )
        )
    clock.receive(task_clock.value)
    manifest_path = dataset_dir / "ground_truth.json"
    ground_truth = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"synthetic": False, "planted_pairs": []}
    )
    expected_pairs = count_document_pairs(len(documents))
    registration = _rpc(
        "register_job",
        job_id,
        len(tasks),
        len(documents),
        request.threshold,
        request.dataset_id,
        expected_pairs,
        [task["task_id"] for task in tasks],
        ground_truth,
        request.block_size,
    )
    clock.receive(registration["lc"])
    _log_event("job_created", job_id)
    if SIMULATED_CLOCK_SKEW != 0:
        _log_event("simulated_clock_skew_active", job_id)
    for task in tasks:
        task["lc"] = clock.tick()

    published = 0
    connection = None
    try:
        connection = pika.BlockingConnection(
            connection_parameters(
                RABBITMQ_HOST,
                RABBITMQ_USER,
                RABBITMQ_PASSWORD,
                connection_attempts=3,
                retry_delay=1,
                socket_timeout=3,
                blocked_connection_timeout=10,
                heartbeat=30,
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue=TASK_QUEUE, durable=True)
        channel.queue_declare(queue=RESULTS_QUEUE, durable=True)
        channel.queue_declare(queue=FAILED_QUEUE, durable=True)
        channel.exchange_declare(
            exchange=CONTROL_EXCHANGE,
            exchange_type="fanout",
            durable=True,
        )
        channel.confirm_delivery()
        for task in tasks:
            channel.basic_publish(
                exchange="",
                routing_key=TASK_QUEUE,
                body=json.dumps(task),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                    message_id=task["task_id"],
                    headers={"attempt": 0},
                ),
                mandatory=True,
            )
            published += 1
            _log_event("task_published", job_id, task["task_id"], task["lc"])
    except Exception as error:
        logging.exception("Task submission failed job_id=%s", job_id)
        _rpc("mark_submitted", job_id, published, type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Broker accepted {published} of {len(tasks)} tasks; inspect job status.",
        ) from error
    finally:
        if connection is not None and connection.is_open:
            connection.close()
    status = _rpc("mark_submitted", job_id, published)
    return {
        "job_id": job_id,
        "dataset_id": request.dataset_id,
        "documents": len(documents),
        "expected_pairs": expected_pairs,
        "total_tasks": len(tasks),
        "submitted_tasks": published,
        "threshold": request.threshold,
        "block_size": request.block_size,
        "inject_poison": request.inject_poison,
        "status": status["status"],
    }


@app.get("/api/jobs")
def jobs():
    return {"jobs": _rpc("list_jobs")}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _rpc("get_job_status", job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/api/jobs/{job_id}/results")
def job_results(
    job_id: str,
    minimum_score: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    job = _rpc("get_job_status", job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    page = _rpc("get_results_page", job_id, minimum_score, offset, limit)
    verification_path = DATA_ROOT / "results" / f"verification_{job_id}.json"
    verification = (
        json.loads(verification_path.read_text(encoding="utf-8"))
        if verification_path.is_file()
        else None
    )
    return {
        "job_id": job_id,
        "status": job["status"],
        "matches": page["matches"],
        "results_saved": job["results_saved"],
        "filtered_count": page["filtered_count"],
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page["matches"]) < page["filtered_count"],
        "integrity": job["integrity"],
        "verification": verification,
    }


@app.post("/api/jobs/{job_id}/verify")
def verify_job(job_id: str):
    job = _rpc("get_job_status", job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["status"] not in ("completed", "completed_with_failures"):
        raise HTTPException(status_code=409, detail="Wait for the job to finish before verification.")
    results = _rpc("get_results", job_id)
    try:
        verification = verify_distributed_results(
            DATA_ROOT / "datasets" / job["dataset_id"],
            results,
            job["threshold"],
            job["pairs_compared"],
            job["expected_pairs"],
            job["integrity"],
        )
        output_dir = DATA_ROOT / "results"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"verification_{job_id}.json"
        temporary_output = output_path.with_name(
            f"{output_path.stem}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_output.write_text(
                json.dumps(verification, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_output.replace(output_path)
        finally:
            temporary_output.unlink(missing_ok=True)
        report_path = DATA_ROOT / "jobs" / f"{job_id}.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["verification"] = verification
            temporary_path = report_path.with_name(
                f"{report_path.stem}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary_path.write_text(
                    json.dumps(report, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary_path.replace(report_path)
            finally:
                temporary_path.unlink(missing_ok=True)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logging.exception("Baseline verification failed job_id=%s", job_id)
        raise HTTPException(
            status_code=500,
            detail=f"Could not verify job results ({type(error).__name__}).",
        ) from error
    return verification


@app.get("/api/jobs/{job_id}/timeline")
def job_timeline(
    job_id: str,
    order: str = Query(default="lamport", pattern="^(wall|lamport)$"),
):
    job = _rpc("get_job_status", job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    events = _rpc("get_timeline", job_id, order)
    return {
        "job_id": job_id,
        "order": order,
        "simulated_clock_skew": SIMULATED_CLOCK_SKEW != 0
        or any(event["event"] == "simulated_clock_skew_active" for event in events),
        "events": events,
    }


@app.get("/api/system")
def system_status():
    queues = {
        queue: _rabbitmq_queue(queue)
        for queue in (TASK_QUEUE, RESULTS_QUEUE, FAILED_QUEUE)
    }
    try:
        workers = _rpc("get_workers")
        aggregator = _rpc("get_time")
        aggregator_status = "healthy" if aggregator else "unavailable"
    except HTTPException:
        workers = []
        aggregator_status = "unavailable"
    task_consumers = queues[TASK_QUEUE].get("consumers", 0)
    healthy_workers = sum(worker["healthy"] for worker in workers)
    return {
        "status": "healthy"
        if all(queue["status"] == "healthy" for queue in queues.values())
        and aggregator_status == "healthy"
        else "degraded",
        "queues": queues,
        "workers": workers,
        "active_workers": min(healthy_workers, task_consumers),
        "healthy_worker_heartbeats": healthy_workers,
        "task_consumers": task_consumers,
        "aggregator": {"status": aggregator_status},
        "demo_controls": DEMO_CONTROLS,
        "rpc_port": 9000,
    }


@app.get("/api/system/dns")
def resolve_service(name: str = Query(..., pattern="^(api|worker|aggregator|rabbitmq)$")):
    try:
        canonical, aliases, addresses = socket.gethostbyname_ex(name)
        return {
            "name": name,
            "canonical_name": canonical,
            "aliases": aliases,
            "addresses": addresses,
        }
    except socket.gaierror as error:
        raise HTTPException(
            status_code=503,
            detail=f"Compose DNS could not resolve {name}: {error.__class__.__name__}",
        ) from error


@app.post("/api/system/worker-consumers")
def set_worker_consumers(request: WorkerConsumersRequest):
    if not DEMO_CONTROLS:
        raise HTTPException(
            status_code=403,
            detail="Worker demo controls are disabled. Set DEMO_CONTROLS=true and restart the API.",
        )
    workers = sorted(
        (
            worker
            for worker in _rpc("get_workers")
            if worker["healthy"]
        ),
        key=lambda worker: worker["worker_id"],
    )
    if request.active_workers > len(workers):
        raise HTTPException(
            status_code=409,
            detail=f"Requested {request.active_workers} workers, but only {len(workers)} healthy worker containers are available.",
        )
    active_worker_ids = [
        worker["worker_id"] for worker in workers[:request.active_workers]
    ]
    lc = clock.tick()
    _publish_demo_control(
        {
            "action": "set_active_workers",
            "active_worker_ids": active_worker_ids,
            "lc": lc,
        }
    )
    return {
        "requested": request.active_workers,
        "active_worker_ids": active_worker_ids,
        "available_workers": len(workers),
    }


@app.post("/api/system/clock-skew")
def set_worker_clock_skew(request: ClockSkewRequest):
    global SIMULATED_CLOCK_SKEW
    if not DEMO_CONTROLS:
        raise HTTPException(
            status_code=403,
            detail="Worker demo controls are disabled. Set DEMO_CONTROLS=true and restart the API.",
        )
    lc = clock.tick()
    _publish_demo_control(
        {
            "action": "set_clock_skew",
            "seconds": request.seconds,
            "lc": lc,
        }
    )
    SIMULATED_CLOCK_SKEW = request.seconds
    _log_event("simulated_clock_skew_changed", lc=lc)
    return {"simulated": True, "worker_clock_skew_seconds": request.seconds}


@app.post("/api/system/kill-random-worker")
def kill_random_worker(request: KillWorkerRequest):
    if not DEMO_CONTROLS:
        raise HTTPException(
            status_code=403,
            detail="Worker kill controls are disabled. Set DEMO_CONTROLS=true to enable.",
        )
    if not request.job_id:
        raise HTTPException(status_code=400, detail="An active job ID is required.")
    deadline = time.monotonic() + 5
    task_queue = {}
    workers = []
    while True:
        job = _rpc("get_job_status", request.job_id)
        if job is None or job["status"] != "processing":
            raise HTTPException(status_code=409, detail="Select a job that is processing.")
        task_queue = _rabbitmq_queue(TASK_QUEUE)
        if task_queue.get("status") != "healthy":
            raise HTTPException(status_code=503, detail="RabbitMQ queue telemetry is unavailable.")
        workers = [
            worker
            for worker in _rpc("get_workers")
            if worker["healthy"] and worker["current_task"]
        ]
        if (
            task_queue.get("consumers", 0) >= 2
            and task_queue.get("unacknowledged", 0) >= 1
            and workers
        ):
            break
        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=409,
                detail="No active task was observed within 5 seconds; try again while the job is processing.",
            )
        time.sleep(0.1)
    worker = random.choice(workers)
    lc = clock.tick()
    message = {
        "action": "kill_worker",
        "worker_id": worker["worker_id"],
        "job_id": request.job_id,
        "lc": lc,
    }
    connection = None
    try:
        connection = pika.BlockingConnection(
            connection_parameters(
                RABBITMQ_HOST,
                RABBITMQ_USER,
                RABBITMQ_PASSWORD,
                socket_timeout=3,
            )
        )
        channel = connection.channel()
        channel.exchange_declare(
            exchange=CONTROL_EXCHANGE,
            exchange_type="fanout",
            durable=True,
        )
        channel.confirm_delivery()
        channel.basic_publish(
            exchange=CONTROL_EXCHANGE,
            routing_key="",
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
            ),
            mandatory=True,
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"Could not publish the worker control message ({type(error).__name__}).",
        ) from error
    finally:
        if connection is not None and connection.is_open:
            connection.close()
    _log_event("worker_kill_requested", request.job_id, lc=lc)
    return {
        "target_worker": worker["worker_id"],
        "requested": True,
        "redeliveries_before": task_queue.get("redeliveries", 0),
    }


@app.get("/api/experiments")
def experiments():
    root = DATA_ROOT / "results"
    reports = []
    if root.is_dir():
        for path in sorted(root.glob("benchmark_*.json")):
            reports.append(json.loads(path.read_text(encoding="utf-8")))
    return {"experiments": reports}


@app.post("/api/experiments/import", status_code=201)
def import_experiment(report: BenchmarkReport):
    if report.expected_pairs != count_document_pairs(report.document_count):
        raise HTTPException(status_code=400, detail="Benchmark pair count does not match document count.")
    if len({run.workers for run in report.runs}) != len(report.runs):
        raise HTTPException(status_code=400, detail="Benchmark worker counts must be unique.")
    if any(
        len(run.job_ids) != report.repeats
        or len(run.run_times_seconds) != report.repeats
        for run in report.runs
    ):
        raise HTTPException(
            status_code=400,
            detail="Each worker-count run must include exactly the configured number of timed jobs.",
        )
    worker_counts = [run.workers for run in report.runs]
    if worker_counts != list(range(1, len(worker_counts) + 1)):
        raise HTTPException(
            status_code=400,
            detail="Benchmark worker counts must be consecutive, starting at one.",
        )
    dataset = _dataset_summary(_dataset_path(report.dataset_id))
    if (
        dataset["document_count"] != report.document_count
        or dataset["pair_count"] != report.expected_pairs
    ):
        raise HTTPException(status_code=400, detail="Benchmark dataset metadata does not match the stored dataset.")

    completed_job_ids = []
    for run in report.runs:
        if any(value <= 0 for value in run.run_times_seconds):
            raise HTTPException(status_code=400, detail="Benchmark timings must be positive.")
        run_job_ids = [run.warmup_job_id, *run.job_ids]
        if (
            len(set(run_job_ids)) != len(run_job_ids)
            or any(job_id in completed_job_ids for job_id in run_job_ids)
        ):
            raise HTTPException(status_code=400, detail="Benchmark job IDs must be unique.")
        for job_id, measured_seconds in [
            (run.warmup_job_id, None),
            *zip(run.job_ids, run.run_times_seconds),
        ]:
            status = _rpc("get_job_status", job_id)
            if (
                status is None
                or status["status"] != "completed"
                or status["dataset_id"] != report.dataset_id
                or status["n_docs"] != report.document_count
                or status["threshold"] != report.threshold
                or status["block_size"] != report.block_size
                or status["expected_pairs"] != report.expected_pairs
                or not status["integrity"]["unique_task_ids"]
                or not status["integrity"]["pair_count"]
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Benchmark job {job_id} is not a matching, complete job.",
                )
            if measured_seconds is not None and measured_seconds + 0.001 < status["elapsed_seconds"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Recorded time for job {job_id} is shorter than the aggregator-observed job duration.",
                )
        completed_job_ids.extend(run_job_ids)

        expected_median = statistics.median(run.run_times_seconds)
        if not math.isclose(run.median_seconds, expected_median, rel_tol=1e-6):
            raise HTTPException(status_code=400, detail="Reported median does not match the raw repetitions.")
        if not math.isclose(
            run.pairs_per_second,
            report.expected_pairs / expected_median,
            rel_tol=1e-6,
        ):
            raise HTTPException(status_code=400, detail="Reported throughput does not match the measured median.")

    baseline_seconds = report.runs[0].median_seconds
    for run in report.runs:
        expected_speedup = baseline_seconds / run.median_seconds
        if (
            not math.isclose(run.speedup, expected_speedup, rel_tol=1e-6)
            or not math.isclose(
                run.efficiency,
                expected_speedup / run.workers,
                rel_tol=1e-6,
            )
        ):
            raise HTTPException(status_code=400, detail="Reported speedup or efficiency is inconsistent.")

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", report.benchmark_id)
    root = DATA_ROOT / "results"
    root.mkdir(parents=True, exist_ok=True)
    output_path = root / f"benchmark_{safe_id}.json"
    if output_path.exists():
        raise HTTPException(status_code=409, detail="A benchmark with this ID is already saved.")
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(report.model_dump(), indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return {"saved": True, "benchmark_id": safe_id, "path": str(output_path)}
