# packages/shared-types

Shared type contracts across the TypeScript frontend and the Python backend
(product-spec.md §5.2: "shared TS/Python type contracts").

Approach decided in Epic 1: the FastAPI service is the source of truth and emits
an OpenAPI schema; TS types are generated from it so the two languages cannot
drift. Hand-written parallel type definitions in two languages are a
correctness hazard and are not used here.

Populated in Epic 1.
