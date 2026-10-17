# Distributed-systems concept evidence

Run commands from the project root in PowerShell after Docker Desktop is ready. UI evidence is live backend telemetry, not a design mock. If the service is unavailable, the UI labels it unavailable/degraded.

## What the concepts mean for SimilarDocs

The project should be explained using its own workload, not by copying the hospital example's topology. SimilarDocs has no hospitals, no separately owned institutional resource state, and no peer-to-peer worker network.

| Concept | Purpose in SimilarDocs | What to point at |
|---|---|---|
| Independent service nodes | FastAPI, RabbitMQ, the aggregator, and each worker run as separate container processes. Worker replicas have separate process memory, but read the same dataset from the single-host Docker named volume. | `docker compose ps`; `docker compose up -d --scale worker=3` |
| Client-server | The browser is an HTTP client of FastAPI. FastAPI serves the dashboard and REST API; the dashboard requests dataset/job/status/results data instead of performing comparisons itself. | Open the dashboard and submit a job; inspect `/docs`. |
| Message-oriented middleware | FastAPI publishes block-pair tasks to RabbitMQ. Worker replicas consume tasks and publish results. The broker buffers work and selects consumers; the API does not call a particular worker. | Demo center → Pause all workers → Submit job to queue → inspect `tasks` in System → Resume all workers. |
| Peer-to-peer (not used) | P2P would mean workers address and coordinate directly with peer workers. They do not: workers communicate through RabbitMQ and do not own separate document collections. P2P is not the right description of this broker-mediated producer/consumer design. | Explain using the topology above; API and worker/aggregator logs show broker and RPC paths. |
| Parallelism and scalability | The API partitions the `N(N−1)/2` unique document pairs into block-pair tasks. Multiple worker replicas can process independent tasks concurrently. Added replicas do not guarantee speedup; the benchmark records actual measurements. | Start the desired replicas before the demo; Demo center → Run measured benchmark → Experiments. |
| Fault tolerance and delivery semantics | An unacknowledged task may be redelivered after a worker exits. The worker retries processing exceptions; the aggregator de-duplicates accepted results by task ID. This is at-least-once delivery, not exactly-once execution. | Demo center → Worker crash and redelivery / Retries and failed-task handling; confirm broker redelivery, integrity, and baseline checks. |
| RPC | FastAPI uses internal XML-RPC request/response calls to register a job and read aggregator-owned status/results/timeline data. This is distinct from the task queue. | `docker compose logs api aggregator`; RPC port 9000 is internal. |
| Service naming and addressing | Containers find one another through Compose DNS service names, not fixed IPs. UUIDs identify jobs/tasks and named queues identify message channels. | System → Compose DNS; `GET /api/system/dns?name=worker`. |
| Lamport logical clocks | Services attach counters to events and messages. Receive events advance beyond the received counter, preserving causal order when reported wall-clock values disagree. | Demo center → Run clock-order demo → Timeline; skewed worker timestamps are explicitly SIMULATED. |
| Partition correctness | Each block-pair task covers a disjoint region of unordered document pairs. The aggregator checks task completion and total pair coverage; baseline verification checks the actual result set and scores. | Run job integrity badges and Results → Run baseline verification. |
| Containerization boundary | Compose isolates services and provides a repeatable local deployment. All containers run on one Docker host; this is not a multi-machine cluster or a claim of geographically distributed nodes. | `docker compose ps`; see the single-host limitation below. |

| Syllabus concept | Implementation location | Exact demonstration / evidence |
|---|---|---|
| Virtualization and service separation | `docker-compose.yml`; `api/Dockerfile`; `worker/Dockerfile`; `aggregator/Dockerfile` | `docker compose ps` shows RabbitMQ, API, aggregator, and worker containers separately. |
| Middleware / message-oriented communication | `api/main.py` publishes `tasks`; `worker/worker.py` consumes and publishes `results`; RabbitMQ durable declarations; `api/static/app.js` Demo center | In the browser, pause all task consumers, submit a job, observe `ready` increase in System, resume consumers, and watch the same job drain. |
| Asynchronous processing and persistence | RabbitMQ durable queues/messages and named broker volume | Demo center demonstrates a real queue backlog without stopping or scaling containers. `docker compose down` preserves named volumes. |
| RPC | `common/rpc.py`; `aggregator/aggregator.py` | `docker compose logs api aggregator` shows outgoing XML-RPC method requests and the aggregator's matching method-call logs. The aggregator port 9000 is not published to the host. |
| Names, identifiers, and addresses | Compose service DNS; UUIDs in API/task builder | `Invoke-RestMethod 'http://localhost:8000/api/system/dns?name=worker'` from a host reports addresses resolved by the API container. The System tab shows worker, aggregator, RabbitMQ, and API lookups. Job and task IDs are UUIDs. |
| Fault tolerance: worker redelivery | `worker/worker.py`; `api/main.py`; Demo center | Create the 500-document test dataset, start its workload, then click **Kill one active worker** while processing. Confirm the queue redelivery count increases, both integrity checks pass, and baseline verification passes. The control deliberately terminates one worker process. |
| Fault tolerance: retry and terminal failure | `retry_message_plan` in `worker/worker.py`; aggregator `record_result`; Demo center | Click **Run simulated poison-task job**. The extra invalid-block task retries three times, appears in `failed_tasks`, and finishes as `completed_with_failures`. Normal tasks still cover all pairs, so pair-count integrity remains true. |
| Idempotent result aggregation | `JobRegistry.record_result`; `tests/test_storage.py` | Duplicate one task result in the storage unit test; it is ignored and does not increment the result/pair count twice. |
| Lamport logical clocks | `common/clock.py`; event calls in API, worker, aggregator; Demo center | Click **Run clock-order demo**, then compare both Timeline lists. The browser control changes reported worker wall time only and labels the offset SIMULATED. The unit/API tests assert a send event precedes its receive event in Lamport order. |
| Distributed web application | `api/main.py`; `api/static/` | Open `http://localhost:8000/`. The API serves HTML/CSS/JS locally; no external CDN, separate dev server, or browser build is needed. |
| Scalability measurement | `tools/benchmark.py`; Demo center; `/api/experiments`; Experiments tab | Run the browser benchmark. It performs one warm-up plus the selected timed repeats for each available active consumer count, saves raw job IDs/times, throughput, speedup, and efficiency. Worker containers must already be running; browser controls do not create containers. |
| Correctness evidence | `tools/verify.py`; `POST /api/jobs/{id}/verify`; Results tab | Click **Run baseline verification** after completion, or run `python -m tools.verify --job-id <UUID> --api-url http://localhost:8000`. PASS requires exact match-set agreement, six-decimal score agreement, complete pair coverage, integrity checks, and recall of planted pairs when present. |
| Human review / algorithm limit | Results tab; `common/shingle.py`; README | Select a result to see the two documents with shared five-word shingles highlighted. The interface repeatedly states textual overlap is not proof of plagiarism. |

## Queue and status interpretation

- `ready` means messages waiting for delivery, as reported by RabbitMQ management.
- `unacknowledged` means delivered task messages still outstanding, as reported by RabbitMQ management.
- `consumers` is the broker's task-queue consumer count.
- Worker health is based on recent aggregator heartbeats. `active_workers` is capped by the live consumer count. Job in-progress counts are derived from recent worker current-task reports; retry counts are reported retry events.
- Job `tasks_done`, `pairs_compared`, and integrity checks come from unique terminal results observed by the aggregator.
- A poison job is not a correctness PASS: the terminal failed task counts as completed work for job lifecycle but contributes no compared pairs.
