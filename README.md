# SimilarDocs — Distributed Document Similarity Lab

SimilarDocs compares each unique pair in a document corpus by distributing block-pair tasks through RabbitMQ to independent worker containers. The API serves an offline dashboard, the workers calculate real similarity scores, and the aggregator de-duplicates results and saves completed reports.

**A similarity score indicates textual overlap; it is not proof of plagiarism. A human must review the results.** The included synthetic corpus is demonstration data, not real student work.

## Start on Windows

Prerequisites: Windows 11, Docker Desktop with the Linux/WSL2 engine running, Docker Compose v2, and Python 3.12 for host tools. Open PowerShell in the project root:

```powershell
docker compose up -d --build --scale worker=4
docker compose ps
```

Open:

- Dashboard/API: <http://localhost:8000/>
- FastAPI/OpenAPI: <http://localhost:8000/docs>
- RabbitMQ management: <http://localhost:15672/> (default local-only login is `similardocs` / `similarDocsLocalOnly`)

Compose publishes only API port `8000` and RabbitMQ management port `15672`. AMQP and aggregator XML-RPC remain on the Compose network. The API, workers, and aggregator share a Docker-managed named volume at `/data`; no Windows data bind mount is used.

The dashboard's **Dataset** tab can generate a seeded synthetic corpus directly into `/data`, or upload UTF-8 `.txt` files. A new install does not need a preloaded corpus. In **Run job**, choose a dataset, threshold, block size, and optionally the explicitly simulated poison task. The dashboard displays the returned job ID and polls real status. **System** shows live service health, queue depths, worker replicas, and Compose DNS addresses; **Timeline** and **Experiments** show recorded job events and measured benchmark reports.

The **Demo center** runs jury demonstrations from the browser: pause and resume active task consumers to show queued work, inject a retry-then-failed poison task, prepare and interrupt a larger worker job, set/reset simulated wall-clock skew, and run a real benchmark over the worker containers already started. Worker pause, simulated clock-skew, and worker-kill controls are enabled in the local `.env`; both the API and RabbitMQ management ports bind to loopback. The worker-failure workload deliberately exits a worker process. The browser benchmark measures currently running worker containers and cannot create or scale Docker containers; start the desired number before the presentation.

Optional local configuration:

```powershell
Copy-Item .env.example .env
```

Edit `.env` before starting Compose to change the host API port, queue names, broker credentials, retries, prefetch, demo controls, clock-skew simulation, or the default block size. The prescribed/default API host port is `8000`; if another local project already owns it, set `API_HOST_PORT=8001` and use `http://localhost:8001` for the dashboard and tools. `DEMO_CONTROLS=true` enables explicit local demonstration actions. Keep the API and broker management interfaces on loopback; do not expose the unauthenticated demo API to a public network. To disable demo actions, set `DEMO_CONTROLS=false` and recreate the API and workers.

```powershell
docker compose up -d --build --scale worker=3 --force-recreate api worker
```

Scale the worker service before a presentation benchmark (the browser Demo center can vary active consumers but does not create containers):

```powershell
docker compose up -d --scale worker=5 worker
docker compose ps
docker compose logs -f worker
```

`docker compose down` stops services but retains the named data and RabbitMQ volumes. Avoid `docker compose down -v` unless intentionally deleting all retained demo data.

## Evaluation flow

1. Generate a 30-document synthetic dataset in Dataset.
2. Submit a job and observe status, per-worker contributions, and both integrity badges in Run job.
3. Inspect ranked comparisons, click a pair to review highlighted shared shingles, and run baseline verification in Results.
4. In Demo center, pause workers, submit a job, watch the ready queue increase in System, then resume workers and watch it drain.
5. Run the simulated poison task and worker-failure demonstrations from Demo center; observe retries, failed queue, broker redelivery, integrity checks, and baseline verification.
6. Run the clock-order demo and compare both Timeline orderings; reset the simulated offset afterward.
7. Run the measured benchmark from Demo center, then open Experiments to inspect the saved report.

The complete, timed script and fault demonstrations are in [`docs/demo-script.md`](docs/demo-script.md). PowerShell helpers are under [`scripts/`](scripts/).

## Host tools

Install the test dependency (the distributed services install their own pinned dependencies in their images):

```powershell
python -m pip install -r requirements-test.txt
python -m pytest -q
```

Generate a reproducible local corpus and run the shared single-process baseline:

```powershell
python -m tools.generate_corpus --output data/datasets/local-demo --documents 30 --seed 42 --planted-pairs 3
python -m tools.baseline --dataset data/datasets/local-demo --threshold 0.1
```

To generate the same seeded corpus locally **and** in the Compose named volume:

```powershell
python -m tools.generate_corpus --output data/datasets/jury-demo --documents 30 --seed 42 --planted-pairs 3 --api-url http://localhost:8000
```

Verify a finished Compose job against the shared algorithm and its ground-truth manifest:

```powershell
python -m tools.verify --job-id <JOB_UUID> --api-url http://localhost:8000
```

Benchmark 1, 2, 3, and 4 workers with one discarded warm-up and three timed jobs per worker count:

```powershell
python -m tools.benchmark --api-url http://localhost:8000 --dataset-id <DATASET_ID> --max-workers 4 --repeats 3
```

The benchmark records host-measured end-to-end times, raw repeats, throughput, speedup, efficiency, CPU count, and job IDs under `results/`; it imports the JSON into the API volume for the dashboard. It requires the Docker Engine and a dataset already visible in the API. A small workload can get slower as workers are added; the tool reports measured values without smoothing them.

## Architecture and contracts

- For `N` documents the system compares `N(N−1)/2` unordered pairs; 200 documents imply 19,900 pairs.
- The API normalizes documents and stores stable five-word shingle sets under `/data/datasets/<dataset_id>/processed/`.
- It partitions documents into blocks. Each block pair `i <= j` is one task; diagonal blocks compare `k < l` and cross-block tasks compare every cross pair.
- RabbitMQ uses durable `tasks`, `results`, and `failed_tasks` queues and a durable `control` fanout exchange. Task/result messages are persistent. Workers use prefetch 1 and manual acknowledgements; a worker publishes the result before acknowledging its task.
- The aggregator is a threaded XML-RPC server on internal port 9000 and a RabbitMQ results consumer. It protects in-memory job state with a lock, ignores duplicate task IDs, and persists completed JSON reports under `/data/jobs/`.
- Each message/event carries a Lamport counter. Optional `WORKER_CLOCK_SKEW_SECONDS` changes only a worker's reported wall timestamp and is visibly labelled simulated.
- Similarity uses lowercase text, removes punctuation, splits on whitespace, forms contiguous five-word shingles, hashes them using BLAKE2b with an 8-byte digest, and computes Jaccard similarity. Empty shingle sets score `0.0`.

More detail: [`docs/architecture.md`](docs/architecture.md), [`docs/concept-evidence.md`](docs/concept-evidence.md), and [`docs/limitations.md`](docs/limitations.md).

## API highlights

- `GET /api/health`, `GET /api/system`, `GET /api/system/dns?name=worker`
- `GET /api/datasets`, `POST /api/datasets/generate`, `POST /api/datasets/upload`
- `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/results`, `POST /api/jobs/{job_id}/verify`
- `GET /api/jobs/{job_id}/timeline?order=wall|lamport`
- `POST /api/system/worker-consumers`, `POST /api/system/clock-skew`, and `POST /api/system/kill-random-worker` (only when `DEMO_CONTROLS=true`)
- `GET /api/experiments`, `POST /api/experiments/import`

## Tests and verification boundaries

```powershell
python -m pytest -q
python -m compileall -q api common worker aggregator tools tests
docker compose config --quiet
```

Unit, API-in-process, XML-RPC loopback, baseline, task-contract, and mocked benchmark tests do not require Docker. Only a live Compose run can prove container startup, broker delivery/redelivery, DNS aliases, scaled consumers, or actual benchmark values. Do not describe those as verified until Docker is running and the demo is observed.

Run `.\scripts\smoke.ps1` to build/start the stack with two workers, create a small synthetic dataset, submit a real job, and require both integrity checks plus baseline verification. To reuse a dataset, pass `-DatasetId <DATASET_ID>`.

`frontend/` is a retained React/Vite prototype from the earlier version. Compose does not build or serve it; the evaluator-ready offline dashboard is `api/static/` and needs no Node installation or external CDN.
