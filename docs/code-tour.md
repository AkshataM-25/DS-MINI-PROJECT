# Code tour

This guide describes the files used by the current Compose application. The running UI is the plain local frontend under `api/static/`; `frontend/` is an unused earlier React/Vite prototype.

## Root and configuration

| File | Purpose |
|---|---|
| `README.md` | Windows start, API, UI, tools, testing, and operational overview. |
| `docker-compose.yml` | RabbitMQ, API, scalable worker, aggregator, health checks, ports, environment, named volumes. |
| `.dockerignore` | Keeps old data, frontend dependencies, tests, and docs out of Python image build contexts. |
| `.env.example` | Local editable settings for queue names, broker credentials, retries, prefetch, block size, and opt-in demos. |
| `requirements-test.txt` | Pinned API and pytest dependencies required for host-side tests. |
| `pytest.ini` | Restricts discovery to the intentional `tests/` suite and sets the asyncio fixture-loop scope. |
| `.gitignore` | Ignores local environment settings, test/build caches, generated outputs, pre-existing SQLite runtime files, and temporary CSV output. |
| `test_rabbitmq.py` | Legacy manual broker round-trip probe, guarded by `__main__` so pytest collection never connects to RabbitMQ. Compose does not publish AMQP to the host. |

## API and browser UI

| File | Purpose |
|---|---|
| `api/main.py` | FastAPI routes, dataset validation/preprocessing, job registration, persistent task publication, RPC, telemetry, DNS, verification, and experiment import. |
| `api/requirements.txt` | Exact API runtime dependency versions. |
| `api/Dockerfile` | Builds the Python 3.12 API image and copies `common/`, `tools/`, and static UI files. |
| `api/static/index.html` | Six-tab UI shell: Dataset, Run job, Results, System, Timeline, Experiments. |
| `api/static/app.css` | Responsive, offline dashboard styling. |
| `api/static/app.js` | REST calls, form submission, one-second progress polling, system telemetry, baseline verification, pair review/highlights, timelines, and SVG chart. |

## Distributed services

| File | Purpose |
|---|---|
| `worker/worker.py` | RabbitMQ consumer; reads processed shingle blocks, compares pairs, reports events/heartbeats, retries failures, publishes results before ack, and listens for guarded demo control. |
| `worker/requirements.txt` | Pinned worker dependency. |
| `worker/Dockerfile` | Python 3.12 worker image; no fixed container name so Compose can scale replicas. |
| `aggregator/aggregator.py` | Thread-safe in-memory job registry, XML-RPC server, result consumer, idempotency, integrity checks, and completed JSON persistence. |
| `aggregator/requirements.txt` | Pinned aggregator dependency. |
| `aggregator/Dockerfile` | Python 3.12 aggregator image; RPC port remains internal. |

## Shared library

| File | Purpose |
|---|---|
| `common/shingle.py` | Text normalization, stable BLAKE2b five-word shingle hashes, Jaccard score, and comparison fingerprint helper. |
| `common/tasks.py` | Document block partitioning, exact pair iterator/count, block-pair task generation. |
| `common/messages.py` | Consistent task and result message constructors shared by API/worker. |
| `common/clock.py` | Thread-safe Lamport `tick()` and `receive()` implementation. |
| `common/rpc.py` | Timeout-enabled XML-RPC API/worker client with request/error logs. |
| `common/rabbitmq.py` | RabbitMQ connection-parameter builder using the configured Compose credentials. |

## Host tools

| File | Purpose |
|---|---|
| `tools/generate_corpus.py` | Seeded synthetic text generation, planted-pair manifest, safe overwrite check; optionally asks the API to create the same dataset in the named volume. |
| `tools/baseline.py` | Single-process all-pairs reference using the same shingle/Jaccard module; writes JSON and CSV locally. |
| `tools/verify.py` | Compares saved distributed results to the baseline and planted-pair manifest; can call API verification when job files are in the Docker named volume. |
| `tools/benchmark.py` | Scales Compose workers, checks consumers, runs discarded warm-up plus timed repetitions, records raw measurements, writes JSON/CSV/SVG, imports report for the UI. |
| `tools/fault_tolerance_demo.py` | Guarded API-based worker interruption helper with precondition and recovery observation. |

## Tests

| File | Coverage |
|---|---|
| `tests/test_core.py` | Normalization, stable hashes across Python processes, Jaccard edges, Lamport rules, exact pair partitioning, corpus reproducibility, baseline recall, and safe generator overwrite. |
| `tests/test_api.py` | Dataset generation/upload, job publishing/aggregation, API verification, wall/Lamport ordering, DNS result shape, UI assets, benchmark import, poison registration, and kill-control gates. |
| `tests/test_storage.py` | Aggregator duplicate-result idempotency, completion/integrity, saved JSON, restart-visible completed reports, and terminal failures. |
| `tests/test_fault_tolerance_demo.py` | Kill-demo confirmation/preconditions and three retries followed by terminal failed/result messages. |
| `tests/test_rpc.py` | A real loopback threaded XML-RPC register/status/time round-trip. |
| `tests/test_benchmark.py` | Benchmark runner calculations/file outputs/API import contract using controlled test doubles; not live performance evidence. |

## Documentation and retained workspace data

- `docs/architecture.md`, `concept-evidence.md`, `demo-script.md`, `code-tour.md`, `viva-qa.md`, and `limitations.md` are the evaluator materials.
- `scripts/smoke.ps1` starts the stack with two workers, creates/reuses a dataset, runs an end-to-end job and baseline verification; the `scripts/demo_*.ps1` files drive queue, failure, skew, and benchmark demonstrations.
- `report/ARCHITECTURE.md` redirects readers from the obsolete SQLite design to the current architecture.
- `frontend/` contains its earlier React/Vite package files, TypeScript configuration, entry point, dashboard, styles, and static assets. Docker Compose does not build or serve these files.
- Existing `data/doc_001.txt`–`data/doc_200.txt`, CSV exports, SQLite file, and other old workspace artifacts are not mounted by Compose. The services use only the Docker named `/data` volume. The pre-existing SQLite file was left untouched and is not an application dependency.
