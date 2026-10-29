# Design decisions

## Stage 1 — Central configuration and logging

- **Built:** A shared settings module for local endpoints, ports, model, timeouts, token secret, and database path, plus stderr logging setup.
- **Why:** One source of configuration prevents launchers and clients from drifting when local ports or settings change.
- **Trade-off:** The lightweight environment-based module avoids a new configuration dependency, but provides less validation and secret management than a production configuration service.
- **Interview question:** How would you keep configuration consistent across services and environments without embedding secrets in source code?
- **Production would add:** A managed secret store, environment-specific configuration validation, structured centralized logs, and deployment-managed endpoint discovery.

## Stage 2 — Authentication and least privilege

- **Built:** A local HS256 JWT issuer and reusable MCP token verifier that checks signatures, expiry, exact server audience, and scopes. The existing travel HTTP server requires `travel:read`; hotel booking additionally requires `travel:book` for authenticated HTTP calls. The HTTP demo sends a travel-read-only token, and the CLI issuer does not offer the booking scope.
- **Why:** A bearer token should be valid only for its intended resource and minimum operation set; a read credential must not authorize side effects.
- **Trade-off:** Shared local HMAC secrets are simple for a laptop demo but require every verifier to trust the same secret; the CLI issuer is a development tool, not an identity provider.
- **Interview question:** How do you enforce authentication, audience isolation, and least privilege across multiple services while preventing token passthrough?
- **Production would add:** Asymmetric issuer keys, managed key rotation, an identity provider, per-user authorization policies, TLS, revocation, and centralized audit. Only the travel server currently has an HTTP wrapper in this checkout; policy and work remain STDIO servers.

## Stage 3 — Tool hardening

- **Built:** Strict bounds for travel, hotel, task, and policy inputs; bounded policy/task results; and SQLite idempotency records keyed by operation and caller-provided idempotency key. Reusing a key with different parameters is rejected. Weather requests have a timeout, three attempts with exponential backoff, a response-size limit, and structured retryable/non-retryable results.
- **Why:** Tool calls can be retried after timeouts, and unbounded inputs/results consume resources or create inconsistent side effects.
- **Trade-off:** A local SQLite database is simple and persistent for one laptop, but is not a shared coordination store for multiple replicas; in-memory task listing still resets on process restart.
- **Interview question:** How do you make side-effecting operations safe to retry, and how should a tool distinguish transient failures from invalid requests?
- **Production would add:** A shared transactional idempotency store, retention/cleanup policy, downstream idempotency support, distributed retry budgets/circuit breakers, schema validation, and per-tenant quotas.

## Stage 4 — Human approval workflow

- **Built:** A SQLite `pending_actions` workflow with expiry, approver and decision timestamps, plus audit records. Hotel policy is checked against the shared INR 12,000 nightly limit before an action is created. Approval issues and verifies a short-lived `travel:book` token, then calls the travel MCP server in-memory with a stable idempotency key. Both HTTP and direct STDIO booking calls require this scope, and the agent cannot see the booking tool.
- **Why:** Human approval is a separate authorization boundary: planning can remain autonomous while a consequential action requires a recorded, time-bounded decision.
- **Trade-off:** The CLI and SQLite workflow are single-user/local; approval decisions and booking execution are not coordinated across replicas.
- **Interview question:** How do you combine policy evaluation, human-in-the-loop approval, expiration, and an auditable side effect?
- **Production would add:** An authenticated approval UI, multi-approver policies, transactional/outbox execution, durable retries, tamper-resistant audit storage, and notifications.

## Stage 5 — Offline policy retrieval

- **Built:** Local policy documents have stable citation IDs. Search returns short text chunks and relevance scores using either cached local TF-IDF (`POLICY_SEARCH_MODE=tfidf`) or no-cache keyword matching (`POLICY_SEARCH_MODE=offline`). The TF-IDF index is persisted as JSON and rebuilt only when a SHA-256 content hash changes.
- **Why:** A retrievable, citable policy corpus should work on a laptop without an embeddings API, internet access, or ongoing usage costs.
- **Trade-off:** TF-IDF and keyword matching are inexpensive and deterministic, but do not understand semantic similarity as well as embedding models; the local JSON index is intended only for this small demo corpus.
- **Interview question:** How would you make policy retrieval citable, repeatable, and available offline, and what changes when the corpus grows?
- **Production would add:** Generate embeddings with a selected model, store vectors and metadata in a vector database, filter by tenant and policy version, and evaluate retrieval quality and freshness.

## Stage 6 — Observability and audit

- **Built:** Tool wrappers write one JSON-line event per invocation with UTC timestamp, generated request ID, caller subject when authenticated, tool name, SHA-256 hash of canonical arguments, outcome, and elapsed time. A bounded in-process metric snapshot exposes call/error counts and p50/p95 latency; the travel HTTP server exposes `/health` and `/metrics`.
- **Why:** Operators need enough evidence to diagnose reliability and latency without placing potentially sensitive tool arguments in logs.
- **Trade-off:** Metrics and health are process-local and the audit log is a local append-only file, not a centralized or tamper-proof telemetry system. The authenticated travel server is the only HTTP wrapper currently present.
- **Interview question:** What telemetry helps locate slow or failing tool calls while minimizing sensitive data collection?
- **Production would add:** Distributed tracing with propagated request IDs, a metrics backend, centralized structured logs, retention/access controls, audit integrity protection, and authenticated health/metrics surfaces.

## Stage 7 — Local scaling simulation

- **Built:** A bounded localhost cluster launcher starts one to eight travel server processes. An ASGI gateway validates its audience-specific read token, applies a per-subject token bucket, health-checks backends, round-robins requests, and mints a fresh least-privilege downstream token. The load script reports request success, latency percentiles, and per-instance traffic.
- **Why:** Multiple processes demonstrate load distribution and instance failure handling without cloud infrastructure or container tooling.
- **Trade-off:** Health checks run locally per request, rate-limit state and counts live only in one gateway process, and SQLite/idempotency are not a distributed production store. The gateway forwards MCP protocol headers/body but is intentionally a small single-host proxy.
- **Interview question:** How do you distribute traffic, enforce per-client limits, and preserve authorization when one service has multiple instances?
- **Production would add:** Managed load balancing, service discovery, persistent distributed rate limits, shared idempotency, circuit breakers, TLS, autoscaling, and protocol-level integration tests for sessionful streaming.

## Stage 8 — Prompt-injection checks

- **Built:** Resource, prompt, and tool-result text is escaped and wrapped as untrusted context; the system prompt says those delimited strings are evidence, not instructions. The agent's exposed tool list excludes hotel booking, and tests include a malicious policy document.
- **Why:** Retrieved content and tool outputs may contain instruction-like text and should not be trusted as control-plane instructions.
- **Trade-off:** Delimiters and escaping reduce ambiguity but cannot guarantee model compliance; authorization on the booking function remains the actual security boundary.
- **Interview question:** How do you keep retrieved documents from changing agent behavior, and how should high-impact tools be protected if the model is manipulated?
- **Production would add:** Content classification, policy-engine checks at the tool boundary, adversarial evaluations, human approval, scoped credentials, and model/provider-specific defense-in-depth.

## Stage 9 — One-command verification

- **Built:** `scripts/check_all.py` runs preflight, pytest, the existing STDIO smoke check, and a short-lived authenticated HTTP-server/client smoke test. The live Groq call is separately opt-in.
- **Why:** A repeatable local check catches regressions across transport, authentication, and tool behavior before a demo or interview.
- **Trade-off:** External weather may be unavailable, and the optional LLM smoke can use quota; deterministic pytest and local HTTP/STDIO checks remain the default.
- **Interview question:** How do you build a fast, deterministic verification pipeline that still exercises important service boundaries?
- **Production would add:** Isolated CI environments, dependency/security scans, parallelized contract tests, deployment smoke tests, and redacted test telemetry.

## Stage 10 — Docker Compose (not implemented)

- **Built:** This optional stage was not enabled because Docker was not installed in the Stage 0 laptop preflight. No Dockerfile or Compose services were generated.
- **Why:** The project requirement made containerization conditional on a working local Docker installation and explicit approval.
- **Trade-off:** The process-based Stage 7 simulation covers local scaling without the extra memory use and setup of a container runtime.
- **Interview question:** When does containerization add value over running local processes, and how do resource limits affect the choice?
- **Production would add:** A tested container build, health-checked Compose/deployment manifests, image scanning, resource requests/limits, and secrets injection. Recheck Docker and request approval before this optional stage.

## Stage 11 — System design documentation

- **Built:** The README now includes a current local architecture diagram, stage run commands, local-vs-production boundaries, and a production-gaps comparison table.
- **Why:** Explicit trade-offs make the demo easier to explain and prevent a laptop simulation from being mistaken for a production-ready service.
- **Trade-off:** Documentation reflects a single-machine, dummy-data implementation and must evolve with code or deployment changes.
- **Interview question:** How do you communicate an architecture's guarantees, bottlenecks, and migration path without overstating what a prototype proves?
- **Production would add:** Versioned architecture decision records, operational runbooks, service-level objectives, threat models, capacity estimates, and diagrams tied to deployed infrastructure.
