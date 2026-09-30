# Defense-in-Depth Validation

A single check after a bug fix can be bypassed by another code path, a refactor or a mock.

**Core principle:** after fixing the root cause, validate at every layer the bad data passes through, so the bug is structurally hard to reintroduce.

## The four layers (AVP shapes)
1. **Entry validation.** Reject invalid input at the boundary: Pydantic v2 models and `Annotated[..., Query/Path(...)]` constraints on FastAPI routes; zod or type guards on the web edge.
2. **Business-logic validation.** The service asserts the data makes sense for this operation (a domain is non-empty and normalised before a scan is queued).
3. **Environment guards.** Refuse dangerous operations in the wrong context: the crawler goes through `crawl.RobotsGate` and never fetches a disallowed URL; tests use only `avp_test` and Redis db 15; production settings are validated at start-up.
4. **Debug instrumentation.** Log context for forensics, ids and counts only, never a secret, token, email or third-party text (`logs.py` redacts; `tests/test_secrets_stay_out_of_logs.py` checks).

```python
# Layer 1: boundary
class ScanCreate(BaseModel):
    domain: Annotated[str, Field(min_length=3, max_length=253)]

# Layer 2: service
def queue_scan(domain: str) -> None:
    if not domain.strip():
        raise ValueError("domain required to queue a scan")
```

## Applying it
1. Trace the data flow: where does the bad value originate, where is it used?
2. List every checkpoint it passes through.
3. Add validation at each layer that owns a decision about it.
4. Test each layer: try to bypass layer 1 and confirm layer 2 catches it (a pytest per layer).

Do not add layers that duplicate a guard test or weaken one. Some tests read source on purpose (`test_facts_only.py`, `providers.test.ts`, `referrerPolicy.test.ts`, `reportIsolation.test.ts`); they are layers already. Do not add a DB column or persisted field to log evidence: facts only, and a schema change needs a model plus an Alembic revision.
