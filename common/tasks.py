from collections.abc import Callable, Iterable, Iterator, Sequence
from itertools import combinations
from uuid import uuid4

from common.messages import create_task_message


def partition_documents(documents: Sequence[str], block_size: int) -> list[list[str]]:
    if block_size < 1:
        raise ValueError("block_size must be at least 1")
    return [
        list(documents[start:start + block_size])
        for start in range(0, len(documents), block_size)
    ]


def iter_document_pairs(blocks: Sequence[Sequence[str]]) -> Iterator[tuple[str, str]]:
    for block_a_index, block_b_index in combinations(range(len(blocks)), 2):
        for document_a in blocks[block_a_index]:
            for document_b in blocks[block_b_index]:
                yield document_a, document_b
    for block_index, block in enumerate(blocks):
        yield from combinations(block, 2)


def count_document_pairs(document_count: int) -> int:
    return document_count * (document_count - 1) // 2


def generate_block_tasks(
    documents: Sequence[str],
    block_size: int,
    job_id: str,
    dataset_id: str,
    threshold: float,
    next_lamport: Callable[[], int],
) -> list[dict]:
    blocks = partition_documents(documents, block_size)
    tasks = []
    for block_a, block_b in combinations(range(len(blocks)), 2):
        tasks.append(
            create_task_message(
                job_id,
                str(uuid4()),
                dataset_id,
                block_a,
                block_b,
                threshold,
                0,
                next_lamport(),
                block_size,
            )
        )
    tasks.extend(
        create_task_message(
            job_id,
            str(uuid4()),
            dataset_id,
            block_index,
            block_index,
            threshold,
            0,
            next_lamport(),
            block_size,
        )
        for block_index in range(len(blocks))
    )
    return tasks


def iter_blocks_pair_indices(block_count: int) -> Iterable[tuple[int, int]]:
    yield from (
        (block_a, block_b)
        for block_a in range(block_count)
        for block_b in range(block_a, block_count)
    )
