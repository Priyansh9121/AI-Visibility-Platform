# Condition-Based Waiting

Flaky tests often guess at timing with arbitrary sleeps: they pass on a fast machine and fail under load or in CI.

**Core principle:** wait for the condition you care about, not a guess about how long it takes.

## Use when
- Tests contain `asyncio.sleep(n)`, `time.sleep(n)`, `setTimeout`, or a fixed `waitForTimeout`.
- Tests are flaky, or time out when run in parallel.
- Waiting for a Celery task, background job, queue drain or async DB write.

## Do not use when
Testing real timing behaviour (debounce, throttle, retry backoff, the 15-minute stranded-scan reaper). Then use a controllable clock or freeze time, and comment why.

## pytest / asyncio
```python
# Bad: guess
await asyncio.sleep(0.5)
assert await scan_status(scan_id) == "COMPLETE"

# Good: wait for the condition, with a timeout and a clear error
async def wait_for(condition, description: str, timeout: float = 5.0, interval: float = 0.01):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        result = await condition()
        if result:
            return result
        if loop.time() > deadline:
            raise AssertionError(f"Timeout waiting for {description} after {timeout}s")
        await asyncio.sleep(interval)

await wait_for(lambda: is_complete(scan_id), "scan to complete")
```

## Celery
Prefer running the task synchronously in tests (call it directly, or eager mode) and assert the result. If a real worker is involved, poll the row's status (ids and status only) with a timeout, never a fixed sleep.

## vitest
```ts
// Bad: await new Promise((r) => setTimeout(r, 50));
// Good: wait for the observable outcome
await vi.waitFor(() => expect(getResult()).toBeDefined(), { timeout: 5000 });
// Components: await screen.findByText(...) or waitFor from Testing Library
```
Use `vi.useFakeTimers()` when the timing itself is under test.

## Common mistakes
- Polling far too fast (1 ms) wastes CPU; about 10 ms is fine.
- No timeout: a loop that never ends. Always raise a clear error.
- Stale data: read state inside the loop, not before it.

## When a fixed delay is right
First wait for the triggering condition, then wait a documented duration based on known timing (for example "two ticks at 100 ms"), with a comment saying why.
