def create_task_message(
    job_id: str,
    task_id: str,
    dataset_id: str,
    block_a: int,
    block_b: int,
    threshold: float,
    attempt: int,
    lamport_counter: int,
    block_size: int,
) -> dict:
    return {
        "job_id": job_id,
        "task_id": task_id,
        "dataset_id": dataset_id,
        "block_a": block_a,
        "block_b": block_b,
        "threshold": threshold,
        "attempt": attempt,
        "lc": lamport_counter,
        "block_size": block_size,
    }


def create_result_message(
    job_id: str,
    task_id: str,
    worker_id: str,
    pairs_compared: int,
    matches: list[list],
    duration_ms: float,
    lamport_counter: int,
    status: str = "completed",
    error: str | None = None,
) -> dict:
    message = {
        "job_id": job_id,
        "task_id": task_id,
        "worker_id": worker_id,
        "pairs_compared": pairs_compared,
        "matches": matches,
        "duration_ms": duration_ms,
        "lc": lamport_counter,
        "status": status,
    }
    if error is not None:
        message["error"] = error
    return message
