import argparse
import json
import random
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


VOCABULARY = """
algorithm application architecture analysis availability bandwidth cache client
cluster cloud communication component computation concurrency configuration
consistency container control coordination deployment design detection distributed
document efficiency execution failure framework function gateway hardware indexing
infrastructure interface latency logging memory message middleware monitoring
network node operation optimization parallel partition performance persistence
pipeline process protocol queue recovery replication request resource response
reliability scheduler server service storage synchronization task thread throughput
transaction validation virtual worker workload access activity address aggregate
analytics backup benchmark capacity catalog checksum classification collection
command comparison connection constraint context conversion coverage dataset
debugging delivery dependency directory discovery dispatch encoding event exchange
experiment extraction filtering format generation graph grouping handling identifier
implementation incident integration isolation iteration job journal management mapping
measurement metadata migration module notification object ordering ownership packet
parameter parsing payload priority procedure profile program query ranking record
reference registry rejection relationship resolution routing runtime sampling scanning
schema scope search segment selection sequence signature simulation stability state
statistics status structure submission support telemetry template termination testing
timestamp tracing transfer transformation transport trigger tuning update usage
verification version visibility volume workflow reconciliation resilience observability
portability reproducibility
""".split()


def create_dataset(
    output_dir: Path,
    document_count: int,
    seed: int,
    planted_pair_count: int,
    overlaps: list[float],
    force: bool = False,
) -> dict:
    if document_count < 2:
        raise ValueError("document_count must be at least 2")
    if planted_pair_count < 0 or planted_pair_count > document_count // 2:
        raise ValueError("planted_pair_count must be between 0 and half the documents")
    if not overlaps or any(not 0.0 < overlap <= 1.0 for overlap in overlaps):
        raise ValueError("overlaps must contain percentages in the range (0, 1]")

    output_dir.mkdir(parents=True, exist_ok=True)
    existing_documents = sorted(output_dir.glob("doc_*.txt"))
    if existing_documents and not force:
        raise FileExistsError(
            f"{output_dir} already contains generated documents; pass --force to replace them."
        )
    if force:
        for document in existing_documents:
            document.unlink()

    rng = random.Random(seed)
    word_count = 240
    corpus = {
        index: [
            rng.choice(VOCABULARY)
            for _ in range(word_count)
        ]
        for index in range(document_count)
    }
    planted_pairs = []
    for pair_index in range(planted_pair_count):
        source_index = pair_index * 2
        target_index = source_index + 1
        overlap = overlaps[pair_index % len(overlaps)]
        copied_word_count = max(5, round(word_count * overlap))
        start = rng.randrange(0, word_count - copied_word_count + 1)
        section = corpus[source_index][start:start + copied_word_count]
        target = corpus[target_index]
        corpus[target_index] = section + target[copied_word_count:]
        planted_pairs.append(
            {
                "document_a": f"doc_{source_index + 1:03d}.txt",
                "document_b": f"doc_{target_index + 1:03d}.txt",
                "nominal_overlap": overlap,
            }
        )

    for index, words in corpus.items():
        (output_dir / f"doc_{index + 1:03d}.txt").write_text(
            " ".join(words) + "\n",
            encoding="utf-8",
        )

    manifest = {
        "dataset_id": output_dir.name,
        "document_count": document_count,
        "seed": seed,
        "synthetic": True,
        "planted_pairs": planted_pairs,
    }
    (output_dir / "ground_truth.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a reproducible synthetic corpus.")
    parser.add_argument("--documents", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--planted-pairs", type=int, default=6)
    parser.add_argument("--overlaps", default="0.3,0.6,0.9")
    parser.add_argument("--output", type=Path, default=Path("data/datasets/demo"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--api-url",
        help="Also create the same seeded dataset inside the API's shared /data volume.",
    )
    args = parser.parse_args()
    try:
        overlaps = [float(value) for value in args.overlaps.split(",")]
        manifest = create_dataset(
            args.output,
            args.documents,
            args.seed,
            args.planted_pairs,
            overlaps,
            args.force,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Generated {manifest['document_count']} synthetic documents in {args.output}.")
    print(f"Seed: {manifest['seed']}; planted pairs: {len(manifest['planted_pairs'])}.")
    print(f"Ground-truth manifest: {args.output / 'ground_truth.json'}")
    if args.api_url:
        request = Request(
            f"{args.api_url.rstrip('/')}/api/datasets/generate",
            data=json.dumps(
                {
                    "name": args.output.name,
                    "documents": args.documents,
                    "seed": args.seed,
                    "planted_pairs": args.planted_pairs,
                    "overlaps": overlaps,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                remote_dataset = json.load(response)
        except HTTPError as error:
            parser.error(
                "API dataset import failed: "
                f"HTTP {error.code} {error.read().decode('utf-8', errors='replace')}"
            )
        except (OSError, URLError, json.JSONDecodeError) as error:
            parser.error(f"Could not create dataset through the API: {type(error).__name__}")
        print(f"Shared-volume dataset ID: {remote_dataset['dataset_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
