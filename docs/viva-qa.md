# Viva questions and concise answers

1. **Why use a message queue instead of calling workers directly?**  
   The API can publish work and return without knowing which worker will process it. RabbitMQ buffers tasks when consumers are unavailable and distributes deliveries among independent consumers.

2. **What does a durable queue guarantee?**  
   RabbitMQ can recover the queue definition after broker restart. Message persistence and publisher confirms are also used; durability alone does not make every possible failure atomic.

3. **Is delivery exactly once?**  
   No. It is at least once. A worker can finish a calculation and fail before its acknowledgement is observed, so a task may be delivered again. The aggregator de-duplicates terminal results by `task_id`.

4. **Why acknowledge only after publishing a result?**  
   If the worker acknowledges first and then crashes before publishing, the work disappears from the task queue without a result. Publish-confirm-before-ack narrows that loss window.

5. **Why `prefetch_count=1`?**  
   Each worker holds at most one unacknowledged task, improving fairness for block tasks with uneven runtimes and making active work easier to observe.

6. **How many document comparisons are required?**  
   `N(N−1)/2`, because each unordered pair is considered once. At 200 documents, that is 19,900 pairs.

7. **Why partition by blocks?**  
   One task per block pair reduces message overhead while still covering every unique document pair. Diagonal blocks use `k<l`; cross blocks use the full Cartesian product.

8. **How do you verify no pair is missed or duplicated?**  
   At completion the aggregator checks unique result task IDs against registered task IDs and sums `pairs_compared` against `N(N−1)/2`. Unit tests also enumerate pairs over many document counts and block sizes.

9. **What does the baseline check?**  
   It uses the same normalization, stable shingle hashing, and Jaccard function. It compares the distributed threshold-match set and six-decimal scores, checks full pair coverage, and measures recall of generator-planted pairs.

10. **Why not use Python's built-in `hash()`?**  
    Its values are randomized between interpreter processes. BLAKE2b with an explicit digest size is stable, so preprocessing and workers agree.

11. **Why BLAKE2b and five-word shingles?**  
    The fixed digest provides compact stable shingle identifiers; five-word windows approximate contiguous lexical overlap. The size is a project design choice, not a semantic plagiarism model.

12. **What does Jaccard similarity mean?**  
    `|A∩B|/|A∪B|` for the two shingle sets. It measures overlap of those sets and cannot prove intent, authorship, or plagiarism.

13. **What is a Lamport clock?**  
    A logical counter that captures a causal partial order. Local/send events tick; receive events set `max(local, remote)+1`. It does not estimate physical time.

14. **Why can wall-clock ordering be misleading?**  
    Reported wall timestamps can be skewed or otherwise differ. This demo offsets only worker-reported wall time and labels the offset as simulated; Lamport order preserves message causality.

15. **What is XML-RPC used for here?**  
    The stateless API calls aggregator methods for registration, status, results, and timelines. RabbitMQ remains the asynchronous task/result transport. Port 9000 is internal to Compose.

16. **Why is the XML-RPC server threaded and locked?**  
    Calls can arrive concurrently from API, workers, and result processing. The server handles connections in threads, while a re-entrant lock protects shared registry state.

17. **Why no database?**  
    The project specification excludes one. In-flight state is in aggregator memory and completed jobs are atomically saved as JSON in `/data`; this is simpler but loses in-flight registry state on aggregator restart.

18. **What is a single point of failure?**  
    The aggregator: it owns the in-memory registry and is the sole result consumer. Replication/election would be needed to remove that dependency; those features are out of scope.

19. **How does a worker crash recover?**  
    Its unacknowledged RabbitMQ task can be redelivered when the channel closes. The demo control is guarded and the UI/tool must observe redelivery and consumer recovery before calling the demo successful.

20. **How do retries terminate?**  
    The worker republishes processing exceptions with incremented `attempt` up to three times. A final failure goes to `failed_tasks` and `results` so the aggregator can complete the job with a failure status.

21. **Why compare blocks rather than one task per pair?**  
    Block tasks amortize broker overhead. Smaller blocks create more tasks and more scheduling granularity; larger blocks reduce messaging but can produce more uneven task durations.

22. **What limits speedup?**  
    Amdahl's law, host CPU count, memory/broker overhead, task granularity, serialization, and shared-host contention. The benchmark uses measured values and may show flattening or slowdown.

23. **How is this different from Hadoop?**  
    SimilarDocs is a small application-level work queue, not a distributed storage and batch-compute framework. It has no HDFS, scheduler, or cluster fault model.

24. **How is this different from Kubernetes/serverless?**  
    Docker Compose starts a fixed local service topology. It does not schedule across a cluster or provide serverless invocation/autoscaling.

25. **Is `/data` a distributed file system?**  
    No. It is a Docker named volume shared by containers on one host.

26. **What happens if the aggregator restarts mid-job?**  
    Its in-memory job/task/event state is lost. The completed report survives, but in-flight jobs are not reconstructed from queued messages.

27. **What does the ground-truth manifest represent?**  
    The generator's deliberately planted synthetic document pairs. It allows recall measurement but does not label arbitrary uploaded documents or prove plagiarism.

28. **Why does a poison job fail pair-count integrity?**  
    The injected invalid-block task is part of registered tasks and ends as a terminal failed result with zero compared pairs. Unique-task completion can be true while total-pair coverage is false.

29. **What makes the kill control safe?**  
    It is disabled by default and requires explicit opt-in, a processing job, two consumers, fresh worker activity, and an unacknowledged task. It targets a worker through a control message rather than stopping an arbitrary host container.

30. **What is not verified by unit tests?**  
    Live image builds, Docker health checks, real broker delivery/redelivery, multi-replica DNS responses, and benchmark timings. They require a running Docker Engine and must be reported from a live demo.

31. **Are the workers peer-to-peer nodes?**  
    No. They are independent worker processes, but they do not communicate directly with each other or own separate document collections. RabbitMQ distributes tasks and collects results. This is a broker-mediated producer/consumer architecture.

32. **Why not use peer-to-peer communication here?**  
    Each document-pair task is independent, so workers do not need to negotiate with peers. A durable task queue buffers work, assigns deliveries, and supports redelivery; direct worker links would add coordination without helping this workload.

33. **What is the client-server relationship?**  
    The browser is the client and FastAPI is the HTTP server. The browser submits jobs and reads status/results through REST; the API coordinates backend services. The dashboard is not the computation engine or source of job state.

34. **What does “independent node” mean in this project, and what does it not mean?**  
    API, broker, aggregator, and worker replicas run as separate container processes with separate lifecycles. They share a Docker named data volume and run on one host, so this is not a multi-machine cluster with independently owned data.
