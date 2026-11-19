# Governed agent runtime

The web deployment runs `agent_runtime:app`. The model worker is started separately with `python agent_runtime.py`; keep it off by default until Gemini credentials, database migrations, cost controls, monitoring, and recovery testing are complete.

Configure the worker secret environment with:

- `GEMINI_API_KEY`
- `GEMINI_AGENT_MODEL`
- Optional: `AGENT_WORKER_ID`, `AGENT_POLL_SECONDS`, `AGENT_LEASE_SECONDS`, `AGENT_MAX_OUTPUT_CHARS`

Tasks are durable and tenant-scoped. Queue entries use idempotency keys, leased claims, bounded retries, persistent results, and audit events. The runtime exposes six advisory agent roles with no executable tools. Gemini output is JSON-validated and cannot call application functions directly. Proposed actions remain `approval_required` until an authorized human approves them; approval records the decision but does not execute the proposal.

A platform administrator can activate the persistent global stop:

```text
PUT /api/admin/agents/emergency-stop?enabled=true
```

The worker refuses new claims and the API refuses new tasks while the stop is active.
