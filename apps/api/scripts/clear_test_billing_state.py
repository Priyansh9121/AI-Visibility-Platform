"""Clear test-mode Stripe state from every agency, for the live-key swap — 2026-10-05.

Everything the API stored about Stripe so far came from Stripe's TEST mode: a
customer id, a subscription id, a status, period and trial dates. A live key
cannot see any of it, and an agency that still carries a test subscription id
would be treated as already subscribed. So after the live keys are saved and the
API has redeployed, and BEFORE anyone subscribes in live mode, run this.

    cd /app/apps/api && .venv/bin/python scripts/clear_test_billing_state.py
    cd /app/apps/api && .venv/bin/python scripts/clear_test_billing_state.py --confirm

(the Render shell on the API service; `docs/runbook.md` has the steps).

WHAT IT DOES
------------
Without `--confirm` it is a DRY RUN and writes nothing: it prints counts only,
how many agencies there are and how many have each Stripe column set (for the
yes/no column, how many are true). No agency id, email, name or Stripe id is
ever printed.

With `--confirm` it sets, on EVERY agency (soft-deleted ones too), in ONE
transaction: `stripe_customer_id`, `stripe_subscription_id`,
`subscription_status`, `subscription_current_period_end`,
`subscription_trial_end` and `stripe_event_at` to NULL, and
`subscription_cancel_at_period_end` to false (its default). It prints the counts
before and after. If anything fails before the commit, nothing changes. Other
agency columns are not touched (`updated_at` moves, as on any update).

THE LEDGER IS LEFT ALONE
------------------------
`stripe_webhook_events` is not touched. Test-mode and live-mode event ids never
collide, and the rows hold no tenant data, so keeping them costs nothing.

THE ONE PLACE THAT LOOKS AT THE KEY'S MODE
------------------------------------------
`services/billing.py` has no live/test branch on purpose. This script is the
exception, and only to protect data. `--confirm` runs ONLY with a live key
(`sk_live_` or `rk_live_`, after stripping whitespace); a test key, no key or
anything else refuses (exit 4), because clearing test state is only meaningful
when the live key is the one in force. The dry run works with any key.

With a live key the script asks Stripe, through billing's own client
construction, whether the live account has any subscription
(`subscriptions.list`, `status="all"`, `limit=1`) or any customer
(`customers.list`, `limit=1`), each read under a timeout. A restricted key
(`rk_live_`) needs read access to both. Anything listed refuses (exit 2); a
read that fails or times out refuses too (exit 3), since it cannot prove there
is none. The dry run makes the same reads and refuses the same way.

ORDER, SO THERE IS NO GAP
-------------------------
With `--confirm` the two Stripe reads run once first with NO locks held (fail
fast). Then the UPDATE is issued (it locks every agency row), the reads run
AGAIN while those locks are held under one shared 10 s budget for both, and the
transaction commits only if they pass. A refusal, a timeout or any failure rolls
back, so nothing is written. A database that cannot be reached, or fails at any
point, prints one fixed sentence and exits 5; if the connection dropped after
COMMIT was sent the outcome is unknown, so the sentence says to run the dry run.

The key, any part of it, and exception text are never printed or logged.

EXIT CODES
----------
0  done (dry run counted, or `--confirm` cleared)
1  usage error (bad arguments)
2  refused: the live Stripe account already has a subscription or a customer
3  refused: could not check Stripe, so could not prove there is none
4  refused: `--confirm` needs a live key and the configured key is not one
5  a database error stopped the run (cannot connect, or a statement failed);
   run the dry run again to see the current state
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import asyncpg  # noqa: E402
from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from avp_api.config import Settings, get_settings  # noqa: E402
from avp_api.models import Agency  # noqa: E402
from avp_api.services import billing  # noqa: E402

OK = 0
USAGE = 1
REFUSED_LIVE_SUBSCRIPTIONS = 2
REFUSED_UNCHECKED = 3
REFUSED_NOT_LIVE = 4
DATABASE_ERROR = 5

# One shared budget for BOTH reads, per pass. The first pass holds no locks, so it
# may be generous; the second runs while the UPDATE's row locks are held, so it is
# short: a slow Stripe must not hold every agency row for long.
PRECHECK_BUDGET_SECONDS = 30
LOCKED_BUDGET_SECONDS = 10

DB_ERROR_SENTENCE = (
    "Refused: a database error stopped this. Run the dry run again to see the current state."
)

_DATABASE_FAILURES = (
    SQLAlchemyError,
    OSError,
    asyncpg.exceptions.PostgresError,
    asyncpg.exceptions.InterfaceError,
)

LIVE_PREFIXES = ("sk_live_", "rk_live_")

# (label printed, column) in the order the counts are shown.
_NULLABLE_COLUMNS = (
    ("stripe_customer_id", Agency.stripe_customer_id),
    ("stripe_subscription_id", Agency.stripe_subscription_id),
    ("subscription_status", Agency.subscription_status),
    ("subscription_current_period_end", Agency.subscription_current_period_end),
    ("subscription_trial_end", Agency.subscription_trial_end),
    ("stripe_event_at", Agency.stripe_event_at),
)


def _key_is_live(settings: Settings) -> bool:
    """True only for a configured key that, stripped of whitespace, is a live key."""
    secret = settings.stripe_secret_key
    if secret is None:
        return False
    return secret.get_secret_value().strip().startswith(LIVE_PREFIXES)


async def _count(session: AsyncSession) -> dict[str, int]:
    """Counts only, over every agency including soft-deleted ones."""
    columns: list[Any] = [func.count().label("agencies")]
    columns += [func.count(col).label(label) for label, col in _NULLABLE_COLUMNS]
    columns.append(
        func.count().filter(Agency.subscription_cancel_at_period_end.is_(True)).label(
            "subscription_cancel_at_period_end"
        )
    )
    row = (await session.execute(select(*columns))).one()
    return {key: int(value) for key, value in row._mapping.items()}


def _print_counts(title: str, counts: dict[str, int], out: TextIO) -> None:
    print(title, file=out)
    print(f"  agencies in all: {counts['agencies']}", file=out)
    for key, value in counts.items():
        if key != "agencies":
            print(f"  {key} set: {value}", file=out)


async def _check_stripe(settings: Settings, err: TextIO, *, budget: float) -> int | None:
    """Ask the live account whether it holds a subscription or a customer.

    Two reads, under one shared `budget` (seconds) between them. Returns None when
    both list nothing, otherwise the exit code to refuse with (the sentence is printed to `err`).
    Whatever goes wrong, including a client that cannot be built, counts as
    "could not prove there is none". The exception text is never printed: a
    Stripe error can quote the key.
    """
    try:
        client = billing._client(settings)
        async with asyncio.timeout(budget):
            subs = await client.v1.subscriptions.list_async(
                params={"status": "all", "limit": 1}
            )
            customers = await client.v1.customers.list_async(params={"limit": 1})
    except Exception:  # noqa: BLE001 - any failure means "could not prove there is none"
        print(
            "Refused: Stripe could not be asked whether the live account has a "
            "subscription or a customer, so nothing was changed. Check that the key can "
            "read Subscriptions and Customers, then run it again.",
            file=err,
        )
        return REFUSED_UNCHECKED
    if len(subs.data) > 0 or len(customers.data) > 0:
        print(
            "Refused: the live Stripe account already has a subscription or a customer, "
            "so nothing was changed. Test-mode state is only cleared before anyone "
            "subscribes in live mode.",
            file=err,
        )
        return REFUSED_LIVE_SUBSCRIPTIONS
    return None


async def clear(
    session: AsyncSession,
    *,
    settings: Settings,
    confirm: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    """Count (and with `confirm`, clear) every agency's Stripe state. Returns the exit code."""
    try:
        return await _clear(session, settings=settings, confirm=confirm, out=out, err=err)
    except SQLAlchemyError:
        # Fixed sentence: a driver error can quote a statement or a value.
        with contextlib.suppress(SQLAlchemyError):
            await session.rollback()
        print(DB_ERROR_SENTENCE, file=err)
        return DATABASE_ERROR


async def _clear(
    session: AsyncSession,
    *,
    settings: Settings,
    confirm: bool,
    out: TextIO,
    err: TextIO,
) -> int:
    live = _key_is_live(settings)

    if confirm and not live:
        print(
            "Refused: --confirm only runs with a live Stripe key (sk_live_ or rk_live_). "
            "The configured key is not live, or is missing, so Stripe was not checked and "
            "nothing was changed.",
            file=err,
        )
        return REFUSED_NOT_LIVE

    if confirm:
        # Fail fast with NO locks held: nothing is touched if Stripe already says no.
        refusal = await _check_stripe(settings, err, budget=PRECHECK_BUDGET_SECONDS)
        if refusal is not None:
            return refusal

    before = await _count(session)
    _print_counts("Before:", before, out)

    if not confirm:
        if live:
            # End the read transaction first: no connection sits open across Stripe.
            await session.rollback()
            refusal = await _check_stripe(settings, err, budget=PRECHECK_BUDGET_SECONDS)
            if refusal is not None:
                return refusal
            print("Stripe key: live; Stripe lists no subscription and no customer.", file=out)
        else:
            print(
                "Stripe key: not live; Stripe was not checked; --confirm would refuse.",
                file=out,
            )
        await session.rollback()
        print("Dry run: nothing was written. Run again with --confirm to clear.", file=out)
        return OK

    # UPDATE first: it takes every agency's row lock. The Stripe reads then run
    # with those locks held, so nobody can attach a customer or subscription to an
    # agency between the check and the write. Commit only if the reads pass.
    await session.execute(
        update(Agency).values(
            stripe_customer_id=None,
            stripe_subscription_id=None,
            subscription_status=None,
            subscription_current_period_end=None,
            subscription_trial_end=None,
            stripe_event_at=None,
            subscription_cancel_at_period_end=False,
        )
    )
    refusal = await _check_stripe(settings, err, budget=LOCKED_BUDGET_SECONDS)
    if refusal is not None:
        await session.rollback()
        return refusal
    print("Stripe key: live; Stripe lists no subscription and no customer.", file=out)
    after = await _count(session)
    await session.commit()
    _print_counts("After:", after, out)
    print("Cleared.", file=out)
    return OK


async def _run(confirm: bool, settings: Settings) -> int:
    # Any database failure, including one at connect time, is one fixed sentence:
    # no traceback and no exception text (a host name, a DSN, a driver message).
    try:
        engine = create_async_engine(settings.database_url)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
            async with factory() as session:
                return await clear(
                    session, settings=settings, confirm=confirm, out=sys.stdout, err=sys.stderr
                )
        finally:
            await engine.dispose()
    except _DATABASE_FAILURES:
        print(DB_ERROR_SENTENCE, file=sys.stderr)
        return DATABASE_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clear test-mode Stripe state from every agency (dry run unless --confirm)."
    )
    parser.add_argument("--confirm", action="store_true", help="write the change")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE if exc.code not in (0, None) else OK
    return asyncio.run(_run(args.confirm, get_settings()))


if __name__ == "__main__":
    raise SystemExit(main())
