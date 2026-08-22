# apps/api — FastAPI (Python)

HTTP API: agency/user auth and seats, clients & prospects, scan orchestration,
report reads, score reads.

**Stack** (product-spec.md §5.1): Python + FastAPI. PostgreSQL for durable state,
Redis for queue/cache. **This is not a Node workspace** and is excluded from
`pnpm-workspace.yaml`; it is managed by Python tooling from Epic 1.

Every endpoint added here must be recorded in `/docs/api-contracts.md`
(method, path, request shape, response shape) in the same task that adds it.

Scaffolded in Epic 1.
