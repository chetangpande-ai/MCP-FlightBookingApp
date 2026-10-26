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
