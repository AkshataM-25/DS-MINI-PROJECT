# SimilarDocs

SimilarDocs is a distributed document similarity platform designed to evaluate textual overlap between documents at scale. It distributes document-pair workloads across multiple worker containers using RabbitMQ, aggregates the results through a central coordinator, and exposes a browser-based dashboard for dataset management, job execution, validation, and benchmarking.

The platform is intended for research, demonstration, and evaluation workflows rather than production-grade plagiarism detection. A similarity score reflects lexical overlap and should always be reviewed by a human before any decision is made.

## Overview

SimilarDocs processes a corpus of text documents by:

- generating fixed-size shingles from document text,
- comparing document pairs using Jaccard similarity,
- distributing tasks across independent worker services,
- aggregating results into a single validated report,
- exposing the workflow through a local dashboard and API.

The project includes a synthetic evaluation dataset, a distributed processing pipeline, a baseline verification tool, and a benchmarking harness for measuring execution performance across varying worker counts.

## Key Features

- Distributed task execution using RabbitMQ and containerized workers
- Centralized aggregation and result persistence
- Browser-based dashboard for dataset generation, uploads, job control, and reporting
- Integrity checks and baseline verification against a ground-truth manifest
- Benchmarking utilities for measuring runtime performance and scalability
- Demo controls for fault injection, pause/resume behavior, worker failure simulation, and clock skew testing

## System Architecture

The project is composed of four main services:

- API service: manages datasets, jobs, dashboard interactions, and status polling
- Worker services: consume tasks and compute similarity metrics for document pairs
- Aggregator: receives worker results, deduplicates task output, and persists reports
- RabbitMQ: provides durable task, result, and failure queues for asynchronous processing

For N documents, the system evaluates N(N-1)/2 unordered pairs. This makes the workload scale rapidly as larger corpora are introduced.

## Prerequisites

The system is designed to run locally using Docker and Python.

Required software:

- Windows 11 or a compatible local environment
- Docker Desktop with the Linux/WSL2 engine enabled
- Docker Compose v2
- Python 3.12 for host-side tools and tests

## Quick Start

From the project root, start the distributed stack:

```powershell
docker compose up -d --build --scale worker=4
docker compose ps
```

Once the services are running, open the following in a browser:

- Dashboard: http://localhost:8000/
- API documentation: http://localhost:8000/docs
- RabbitMQ management: http://localhost:15672/

Default RabbitMQ credentials:

- Username: `similardocs`
- Password: `similarDocsLocalOnly`

## Typical Usage Workflow

### 1. Create or upload a dataset

The dashboard includes a Dataset tab that can:

- generate a synthetic corpus with seeded values,
- upload UTF-8 text documents,
- validate the dataset prior to job submission.

### 2. Submit a job

The Run job screen allows the user to select:

- dataset,
- similarity threshold,
- block size,
- optional simulated poison task.

The system returns a job ID and updates the status as workers complete tasks.

### 3. Review results

Completed jobs provide:

- ranked document-pair comparisons,
- highlighted shared shingles,
- integrity validation status,
- baseline verification output.

### 4. Demonstrate behavior and performance

The Demo center supports local experimentation and evaluation, including:

- pausing and resuming worker consumers,
- simulating retries and failed tasks,
- interrupting in-flight work,
- adjusting simulated worker clock skew,
- benchmarking the active worker pool.

## Configuration

To create a local environment file:

```powershell
Copy-Item .env.example .env
```

The `.env` file may be used to configure:

- host API port,
- queue names,
- RabbitMQ credentials,
- retry counts,
- task prefetch behavior,
- demo controls,
- simulated clock skew,
- default block size.

By default, the API is exposed on port 8000. If that port is already in use by another local service, set `API_HOST_PORT` to a different value, such as `8001`, and use the corresponding URL.

The demo controls are intended for local-only testing and should not be exposed to a public network. If demonstration actions are not required, set `DEMO_CONTROLS=false` and recreate the API and worker services.

## Scaling and Maintenance

To increase the worker pool before a presentation or benchmark run:

```powershell
docker compose up -d --scale worker=5 worker
docker compose ps
docker compose logs -f worker
```

To stop the system without deleting retained data volumes:

```powershell
docker compose down
```

Avoid `docker compose down -v` unless the retained data and RabbitMQ state should be removed intentionally.

## Host Utilities and Verification

The project includes command-line utilities for dataset generation, baseline evaluation, job verification, and benchmarking.

Install the test dependency:

```powershell
python -m pip install -r requirements-test.txt
python -m pytest -q
```

Generate a reproducible local corpus and run the shared single-process baseline:

```powershell
python -m tools.generate_corpus --output data/datasets/local-demo --documents 30 --seed 42 --planted-pairs 3
python -m tools.baseline --dataset data/datasets/local-demo --threshold 0.1
```

Generate the same seeded dataset locally and in the Compose-managed data volume:

```powershell
python -m tools.generate_corpus --output data/datasets/jury-demo --documents 30 --seed 42 --planted-pairs 3 --api-url http://localhost:8000
```

Verify a completed Compose job against the shared algorithm and the ground-truth manifest:

```powershell
python -m tools.verify --job-id <JOB_UUID> --api-url http://localhost:8000
```

Benchmark worker counts from 1 to 4 with a warm-up run and repeated executions:

```powershell
python -m tools.benchmark --api-url http://localhost:8000 --dataset-id <DATASET_ID> --max-workers 4 --repeats 3
```

The benchmark records end-to-end timing, throughput, speedup, efficiency, CPU information, and related job metadata under the results directory. It requires a visible dataset in the API and a running Docker environment.

## Architecture and Processing Model

The platform follows a deterministic distributed processing model:

- For N documents, it evaluates N(N-1)/2 unordered pairs.
- Documents are normalized and converted into stable five-word shingles.
- Work is partitioned into blocks for parallel evaluation.
- RabbitMQ uses durable queues for tasks, results, and failed-job workflows.
- The aggregator locks job state and ignores duplicate task IDs.
- Similarity is computed using Jaccard similarity over hashed shingles.
- Empty shingle sets are treated as a similarity score of 0.0.

Additional design and evaluation details are available in:

- [docs/architecture.md](docs/architecture.md)
- [docs/concept-evidence.md](docs/concept-evidence.md)
- [docs/limitations.md](docs/limitations.md)
- [docs/demo-script.md](docs/demo-script.md)

## API Highlights

The system exposes the following API areas:

- `GET /api/health`, `GET /api/system`, `GET /api/system/dns?name=worker`
- `GET /api/datasets`, `POST /api/datasets/generate`, `POST /api/datasets/upload`
- `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/results`, `POST /api/jobs/{job_id}/verify`
- `GET /api/jobs/{job_id}/timeline?order=wall`
- `POST /api/system/worker-consumers`, `POST /api/system/clock-skew`, `POST /api/system/kill-random-worker` (demo mode only)
- `GET /api/experiments`, `POST /api/experiments/import`

## Testing and Verification Boundaries

Run the project validation suite:

```powershell
python -m pytest -q
python -m compileall -q api common worker aggregator tools tests
docker compose config --quiet
```

Unit tests, API-level verification, XML-RPC validation, and baseline checks can run without Docker. Live Compose validation is necessary to confirm container startup, broker delivery and redelivery, DNS resolution, scaling behavior, and actual benchmark results.

The project also includes a smoke script for end-to-end validation:

```powershell
./scripts/smoke.ps1
```

This script starts the stack with a small worker configuration, generates a dataset, submits a real job, and validates both integrity checks and baseline results.

## Project Structure

```text
.
├── aggregator/
├── api/
├── common/
├── data/
├── docs/
├── frontend/
├── report/
├── results/
├── scripts/
├── tests/
├── tools/
├── worker/
├── README.md
├── docker-compose.yml
├── pytest.ini
├── requirements-test.txt
├── test_rabbitmq.py
└── .env.example
```

## Important Note

A similarity score represents textual overlap and should not be interpreted as proof of plagiarism or misconduct. The synthetic dataset included in this project is a demonstration corpus and not real academic or personal content. Human review remains essential for interpretation and decision-making.
