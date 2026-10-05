"""Run several awaitables together, and end them all when one fails (checklist 3.77).

`asyncio.gather` returns the first exception to its caller and leaves every other task running:
the caller moves on (a scan is marked failed, a detection ends) while the siblings carry on
making paid provider calls nobody will read. `gather_cancelling` is `gather` with the other
behaviour: the first exception cancels the siblings that are still going, waits for them to
stop, and the caller sees that exception itself, of the type it raised.

Results come back in the order the awaitables were given. A cancellation of the awaiting task
cancels every child too and reaches the caller as `CancelledError`, as it does from `gather`.

Not `asyncio.TaskGroup`: on Python 3.12 a group whose child fails cancels its parent task to get
out of the way and leaves that cancellation counted, and the failure comes back wrapped in an
`ExceptionGroup`. Neither is wanted here: the callers' `except` clauses and the scan deadline's
own cancellation accounting (`asyncio.timeout` reads the count) must see what they always saw.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


async def _end(tasks: list[asyncio.Task[Any]]) -> None:
    """Cancel what is still running and wait until it has stopped; retrieve every outcome."""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def gather_cancelling[T](*coros: Coroutine[Any, Any, T]) -> list[T]:
    """`gather`, except that the first exception cancels the others and is the one raised."""
    if not coros:
        return []  # `asyncio.wait` refuses an empty set; `gather()` returned `[]`
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except BaseException:
        # The awaiting task was cancelled (the scan's deadline, a lost lease): no child outlives it.
        await _end(tasks)
        raise
    for task in tasks:
        if task.done() and (task.cancelled() or task.exception() is not None):
            await _end(tasks)
            # `result()` raises it: the child's own exception, or `CancelledError` if the child
            # was cancelled, as `gather` would.
            task.result()
    return [task.result() for task in tasks]
