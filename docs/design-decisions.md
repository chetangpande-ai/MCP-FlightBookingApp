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
