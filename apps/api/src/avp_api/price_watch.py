"""Check the Stripe Price at start and then every hour — P2, US$150 a month (2026-10-04).

`services/billing.price_check` reads the Stripe Price that `STRIPE_PRICE_ID` names and logs
whether it is the plan: `billing.price_check` (ok), `billing.price_mismatch` (it is not) or
`billing.price_check_failed` (it could not be read), each described in its docstring. This module
runs it: once as the API starts and then every `CHECK_INTERVAL_S` (an hour), in a background task
for as long as the app runs. A start that came while Stripe was unreachable is tried again, a key
that is revoked while the API stays up shows as `billing.price_check_failed` within the hour, and
a standing mismatch keeps showing in the log and not only once at deploy. The founder creates the
Price; the API only reads it back.

**It can never fail or delay a start.** `start` only schedules the task, and the first check runs
inside it, after the app is up. A check that does not come back (Stripe slow or down) holds up
nothing but itself: `price_check` bounds its wait and never raises. The task ends only when it is
cancelled, which the shutdown does before it closes the connection pools (`main.lifespan`; the
same shape as `redis_watch.py`).

**Only a deployed API runs it** (`Settings.is_deployed`: staging or production), and only with
Stripe configured (the secret key and the Price id both set). A local or test start never calls
Stripe from here, whatever its `.env` holds: that file carries a test key and a Price id, and
starting the API on a laptop must not reach a Stripe account (`.claude/skills/stripe-billing-ops`,
approval gate 1: no Stripe call from the local stack). Everywhere else, a deployment before billing
is set up included, there is no task, no Stripe call and no line.

The Stripe call itself stays in `services/billing.py`; nothing here touches the Stripe SDK.
"""

from __future__ import annotations

import asyncio

from .config import Settings
from .services import billing

# "At most once an hour": the check runs at start and then this often.
CHECK_INTERVAL_S = 3600.0
# The task's name, so a test (or a thread dump) can find it.
TASK_NAME = "stripe-price-check"
# How long `stop` waits for the cancelled task to be gone. `price_check` never swallows a
# cancellation, so in practice this is a few turns of the loop; the bound is for the day something
# does, so that a deploy's shutdown cannot wait for ever on the check and leave the pools open.
STOP_TIMEOUT_S = 5.0


async def watch_price(settings: Settings, *, interval_s: float = CHECK_INTERVAL_S) -> None:
    """Check now, then every `interval_s`, until cancelled. Only cancellation ends it."""
    while True:
        await billing.price_check(settings)
        await asyncio.sleep(interval_s)


def start(settings: Settings) -> asyncio.Task[None] | None:
    """Start the hourly check as a task, on a DEPLOYED API with Stripe configured.

    None, and nothing started, in every other case: a local or test API whatever it has set (it
    never calls Stripe from here: see the module docstring), and an API with the secret key or the
    Price id missing.

    This only schedules the task, so it adds nothing to the start-up: the first check runs in the
    task, once the app is serving. Returns the task for `stop`.
    """
    if not settings.is_deployed or not billing.is_configured(settings):
        return None
    # The interval is read here, at call time, so a test can shorten it for the whole app.
    return asyncio.create_task(watch_price(settings, interval_s=CHECK_INTERVAL_S), name=TASK_NAME)


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the check and wait for it to be gone, at most `STOP_TIMEOUT_S`. Safe with None.

    `asyncio.wait` and not a bare `await` under a `suppress`: it waits for the cancelled task
    without also swallowing a cancellation of the caller, and it gives up after the bound, which
    a `gather` would not. A task that is still running after that is let go of, so the shutdown
    goes on to close the pools.
    """
    if task is None:
        return
    task.cancel()
    done, _still_running = await asyncio.wait({task}, timeout=STOP_TIMEOUT_S)
    for finished in done:
        if not finished.cancelled():
            # Read, so that a failure of the loop itself is not reported a second time, by the
            # event loop, when the task is collected.
            finished.exception()
