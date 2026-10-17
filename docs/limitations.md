# Known limitations and truthful claims

- **Single host:** Compose demonstrates service isolation and message-based coordination on one Docker host. It is not a multi-machine deployment.
- **Shared named volume:** `/data` is a Docker-managed volume mounted by the API, workers, and aggregator. It is shared container storage, not a distributed file system.
- **In-memory aggregator state:** job/task registry and event timeline live in aggregator memory. Completed job reports are saved as JSON, but an aggregator restart loses in-flight registry state and it does not reconstruct that state from RabbitMQ.
- **Aggregator single point of failure:** it is the only results consumer and RPC registry. Restarting it can interrupt active jobs; there is no election, replication, or consensus.
- **At-least-once delivery:** an unacknowledged task can be redelivered and calculated again. Aggregation is idempotent by task ID; execution itself is not exactly once.
- **Submission boundary:** API registration and task publishing are not a distributed transaction. A broker/API interruption partway through publication can leave a partially submitted job that needs inspection.
- **Clock demonstration:** containers share one host clock. `WORKER_CLOCK_SKEW_SECONDS` alters only worker-reported timestamps for teaching; it is explicitly simulated. Lamport ordering provides causal ordering but not physical elapsed time.
- **Text-only measure:** five-word-shingle Jaccard detects lexical overlap. It does not understand paraphrases, authorship, citations, intent, or external documents. Human review is required.
- **Synthetic ground truth:** only generator-planted pairs have a known manifest. Uploaded text has no known ground truth.
- **No database/authentication/ML/Kubernetes:** job state is in memory plus completed JSON files; application login and external student-record integrations are out of scope. RabbitMQ uses local credentials for broker access.
- **Benchmark variance:** measured speedup depends on host CPU, Docker overhead, worker count, dataset/block configuration, and other workloads. Small jobs may not benefit from added workers. Raw runs are retained; the tool does not smooth results.
- **Legacy artifacts:** the earlier workspace contains a SQLite file and a React/Vite prototype. The new Compose services do not mount or read the SQLite file and do not build/serve the React prototype. They are retained as pre-existing workspace files, not runtime components of this version.
- **Environment verification:** source/unit/API/XML-RPC tests and Compose-file parsing can run without Docker. Container builds, health checks, live redelivery, broker queue depth, DNS scaling, poison retries over RabbitMQ, and real scaling benchmarks require a running Docker Engine and must be verified on that environment.
