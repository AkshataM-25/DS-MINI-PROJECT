import json
import logging
import os
import re
import socket
import threading
import time
import xmlrpc.client
from itertools import combinations
from pathlib import Path

import pika

from common.clock import LamportClock
from common.messages import create_result_message
from common.rabbitmq import connection_parameters
from common.rpc import call_aggregator
from common.shingle import calculate_similarity


RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "similardocs")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "similarDocsLocalOnly")
AGGREGATOR_HOST = os.getenv("AGGREGATOR_HOST", "aggregator")
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
TASK_QUEUE = os.getenv("TASK_QUEUE", "tasks")
RESULTS_QUEUE = os.getenv("RESULTS_QUEUE", "results")
FAILED_QUEUE = os.getenv("FAILED_QUEUE", "failed_tasks")
CONTROL_EXCHANGE = os.getenv("CONTROL_EXCHANGE", "control")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
PREFETCH = int(os.getenv("PREFETCH", "1"))
HEARTBEAT_SECONDS = float(os.getenv("HEARTBEAT_SECONDS", "5"))
CLOCK_SKEW = float(os.getenv("WORKER_CLOCK_SKEW_SECONDS", "0"))
WORKER_ID = os.getenv("WORKER_ID", socket.gethostname())
clock = LamportClock()
state_lock = threading.Lock()
current_task = None
tasks_processed = 0
tasks_failed = 0
logger = logging.getLogger("worker")


def configure_logging():
    handler = logging.StreamHandler()
    handler.addFilter(WorkerLogFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s service=worker worker_id=%(worker_id)s "
            "%(levelname)s lc=%(lc)s %(message)s"
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


class WorkerLogFilter(logging.Filter):
    def filter(self, record):
        record.worker_id = getattr(record, "worker_id", WORKER_ID)
        record.lc = getattr(record, "lc", clock.value)
        return True


class LogContext(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs["extra"] = {
            **kwargs.get("extra", {}),
            "worker_id": WORKER_ID,
            "lc": clock.value,
        }
        return msg, kwargs


def retry_message_plan(
    task: dict,
    error: Exception,
    max_retries: int,
    next_lamport,
    duration_ms: float = 0.0,
):
    attempt = task.get("attempt")
    if not isinstance(attempt, int) or attempt < 0:
        attempt = max_retries
    task_id = task["task_id"]
    if attempt < max_retries:
        return [
            (
                TASK_QUEUE,
                {**task, "attempt": attempt + 1, "lc": next_lamport()},
            )
        ]
    failure = create_result_message(
        task["job_id"],
        task_id,
        WORKER_ID,
        0,
        [],
        duration_ms,
        next_lamport(),
        "failed",
        type(error).__name__,
    )
    return [(FAILED_QUEUE, failure), (RESULTS_QUEUE, failure)]


def reported_wall_time() -> float:
    with state_lock:
        skew = CLOCK_SKEW
    return time.time() + skew


def report_event(name: str, job_id: str = "", task_id: str = "", lc=None):
    event_lc = clock.tick() if lc is None else lc
    try:
        call_aggregator(
            AGGREGATOR_HOST,
            "log_event",
            event_lc,
            WORKER_ID,
            reported_wall_time(),
            job_id,
            task_id,
            name,
        )
    except (OSError, xmlrpc.client.Error) as error:
        logger.warning(
            "Could not report event=%s job_id=%s task_id=%s error=%s",
            name,
            job_id,
            task_id,
            type(error).__name__,
        )
    return event_lc


def process_task(task: dict, data_root: Path = DATA_ROOT, worker_id: str = WORKER_ID) -> dict:
    for key in (
        "job_id",
        "task_id",
        "dataset_id",
        "block_a",
        "block_b",
        "threshold",
        "block_size",
        "attempt",
        "lc",
    ):
        if key not in task:
            raise ValueError(f"Task is missing {key}")
    data_root = Path(data_root)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task["dataset_id"]):
        raise ValueError("Invalid dataset_id")
    processed_path = (
        data_root
        / "datasets"
        / task["dataset_id"]
        / "processed"
        / f"block-{task['block_size']}.json"
    )
    processed = json.loads(processed_path.read_text(encoding="utf-8"))
    blocks = processed["blocks"]
    block_a = task["block_a"]
    block_b = task["block_b"]
    if (
        not isinstance(block_a, int)
        or not isinstance(block_b, int)
        or block_a < 0
        or block_b < block_a
        or block_b >= len(blocks)
    ):
        raise IndexError("Task references an invalid document block")
    documents_a = blocks[block_a]
    documents_b = blocks[block_b]
    document_pairs = (
        combinations(documents_a, 2)
        if block_a == block_b
        else ((first, second) for first in documents_a for second in documents_b)
    )
    threshold = float(task["threshold"])
    matches = []
    pairs_compared = 0
    for document_a, document_b in document_pairs:
        score = calculate_similarity(
            set(processed["shingles"][document_a]),
            set(processed["shingles"][document_b]),
        )
        pairs_compared += 1
        if score >= threshold:
            matches.append([document_a, document_b, score])
    return {
        "job_id": task["job_id"],
        "task_id": task["task_id"],
        "worker_id": worker_id,
        "pairs_compared": pairs_compared,
        "matches": matches,
    }


def publish_json(channel, queue_name: str, payload: dict, message_id: str):
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            message_id=message_id,
        ),
        mandatory=True,
    )


def main():
    global current_task, tasks_processed, tasks_failed
    configure_logging()
    global logger
    logger = LogContext(logging.getLogger("worker"), {})
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
    channel.queue_declare(queue=TASK_QUEUE, durable=True)
    channel.queue_declare(queue=RESULTS_QUEUE, durable=True)
    channel.queue_declare(queue=FAILED_QUEUE, durable=True)
    channel.exchange_declare(exchange=CONTROL_EXCHANGE, exchange_type="fanout", durable=True)
    channel.confirm_delivery()
    channel.basic_qos(prefetch_count=PREFETCH)
    stop_heartbeats = threading.Event()
    control_ready = threading.Event()
    idle_queue = channel.queue_declare(queue="", exclusive=True).method.queue
    task_consumer_tag = None
    idle_consumer_tag = None

    def acknowledge_idle_message(ch, method, properties, body):
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def set_task_consumer(enabled):
        nonlocal task_consumer_tag, idle_consumer_tag
        if enabled:
            if idle_consumer_tag is not None:
                channel.basic_cancel(idle_consumer_tag)
                idle_consumer_tag = None
            if task_consumer_tag is None:
                task_consumer_tag = channel.basic_consume(
                    queue=TASK_QUEUE,
                    on_message_callback=handle_task,
                )
                logger.info("task consumption resumed")
        else:
            if task_consumer_tag is not None:
                channel.basic_cancel(task_consumer_tag)
                task_consumer_tag = None
            if idle_consumer_tag is None:
                idle_consumer_tag = channel.basic_consume(
                    queue=idle_queue,
                    on_message_callback=acknowledge_idle_message,
                )
                logger.info("task consumption paused by demo control")

    def control_loop():
        control_connection = pika.BlockingConnection(
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
        control_channel = control_connection.channel()
        control_channel.exchange_declare(
            exchange=CONTROL_EXCHANGE,
            exchange_type="fanout",
            durable=True,
        )
        queue = control_channel.queue_declare(
            queue="",
            exclusive=True,
            auto_delete=True,
        ).method.queue
        control_channel.queue_bind(exchange=CONTROL_EXCHANGE, queue=queue)
        control_ready.set()

        def handle_control(ch, method, properties, body):
            global CLOCK_SKEW
            message = json.loads(body)
            action = message.get("action")
            if action == "kill_worker" and message.get("worker_id") == WORKER_ID:
                event_lc = clock.receive(message["lc"])
                report_event(
                    "worker_killed",
                    message.get("job_id", ""),
                    lc=event_lc,
                )
                logger.error("Received explicit demo kill control; exiting non-zero")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                os._exit(3)
            if action == "set_active_workers":
                event_lc = clock.receive(message["lc"])
                enabled = WORKER_ID in message.get("active_worker_ids", [])
                connection.add_callback_threadsafe(
                    lambda: set_task_consumer(enabled)
                )
                report_event(
                    "worker_consumption_resumed" if enabled else "worker_consumption_paused",
                    lc=event_lc,
                )
            elif action == "set_clock_skew":
                event_lc = clock.receive(message["lc"])
                with state_lock:
                    CLOCK_SKEW = float(message["seconds"])
                logger.info("reported wall-clock skew updated seconds=%s", CLOCK_SKEW)
                report_event("worker_clock_skew_changed", lc=event_lc)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        control_channel.basic_consume(queue=queue, on_message_callback=handle_control)
        logger.info("worker control listener ready")
        control_channel.start_consuming()

    def send_heartbeat():
        with state_lock:
            active_task = current_task
            processed_count = tasks_processed
            failed_count = tasks_failed
        try:
            call_aggregator(
                AGGREGATOR_HOST,
                "worker_heartbeat",
                WORKER_ID,
                active_task,
                processed_count,
                failed_count,
                clock.tick(),
                reported_wall_time(),
            )
        except (OSError, xmlrpc.client.Error) as error:
            logger.warning("Worker heartbeat RPC failed error=%s", type(error).__name__)

    def heartbeat_loop():
        control_ready.wait()
        send_heartbeat()
        while not stop_heartbeats.wait(HEARTBEAT_SECONDS):
            send_heartbeat()

    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    def handle_task(ch, method, properties, body):
        global current_task, tasks_processed, tasks_failed
        task = None
        task_id = ""
        started = time.perf_counter()
        try:
            task = json.loads(body)
            if not isinstance(task, dict):
                raise ValueError("Task message must be a JSON object")
            task_id = task["task_id"]
            with state_lock:
                current_task = task_id
            receive_lc = clock.receive(task["lc"])
            report_event("task_received", task["job_id"], task_id, lc=receive_lc)
            result = process_task(task)
            result = create_result_message(
                result["job_id"],
                result["task_id"],
                result["worker_id"],
                result["pairs_compared"],
                result["matches"],
                (time.perf_counter() - started) * 1000,
                clock.tick(),
            )
            report_event("task_done", task["job_id"], task_id, lc=result["lc"])
            publish_json(ch, RESULTS_QUEUE, result, task_id)
            with state_lock:
                tasks_processed += 1
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info("published task result task_id=%s pairs=%s", task_id, result["pairs_compared"])
        except Exception as error:
            logger.exception("Task processing failed task_id=%s error=%s", task_id, type(error).__name__)
            if not isinstance(task, dict) or not task.get("task_id") or not task.get("job_id"):
                publish_json(
                    ch,
                    FAILED_QUEUE,
                    {"error": type(error).__name__, "body": body.decode("utf-8", errors="replace")},
                    task_id or "invalid-task",
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            try:
                messages = retry_message_plan(
                    task,
                    error,
                    MAX_RETRIES,
                    clock.tick,
                    (time.perf_counter() - started) * 1000,
                )
                for queue_name, payload in messages:
                    publish_json(ch, queue_name, payload, task_id)
                if messages[0][0] == TASK_QUEUE:
                    report_event(
                        "task_retry",
                        task["job_id"],
                        task_id,
                        lc=messages[0][1]["lc"],
                    )
                    logger.warning(
                        "republished task_id=%s retry=%s/%s",
                        task_id,
                        messages[0][1]["attempt"],
                        MAX_RETRIES,
                    )
                else:
                    with state_lock:
                        tasks_failed += 1
                    report_event(
                        "task_failed",
                        task["job_id"],
                        task_id,
                        lc=messages[-1][1]["lc"],
                    )
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as publish_error:
                logger.exception(
                    "Could not publish retry/failure task_id=%s error=%s",
                    task_id,
                    type(publish_error).__name__,
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        finally:
            with state_lock:
                current_task = None
            send_heartbeat()

    set_task_consumer(True)
    logger.info("worker ready task_queue=%s prefetch=%s", TASK_QUEUE, PREFETCH)
    try:
        channel.start_consuming()
    finally:
        stop_heartbeats.set()
        heartbeat_thread.join(timeout=HEARTBEAT_SECONDS + 1)
        if connection.is_open:
            connection.close()


if __name__ == "__main__":
    main()
