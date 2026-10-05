"""Clearing test-mode billing state — `scripts/clear_test_billing_state.py`, 2026-10-05.

What must hold:

- a dry run prints counts and writes nothing;
- `--confirm` clears every Stripe column on EVERY agency (soft-deleted too) in
  ONE transaction, and leaves other columns and the webhook ledger alone;
- `--confirm` runs only with a live key (`sk_live_`, `rk_live_`); any other key
  refuses (exit 4) and Stripe is never called;
- the UPDATE is issued first, the Stripe reads (subscriptions, customers) run
  while its locks are held, and a listing, a failed read or a DB error rolls
  back: nothing written;
- nothing it prints carries an agency id, an email, a Stripe id, the key, or
  an exception's text.
"""

from __future__ import annotations

import importlib.util
import io
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import asyncpg
import pytest
import stripe
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from avp_api.config import Settings
from avp_api.ids import AGENCY, new_id
from avp_api.models import Agency, StripeWebhookEvent, StripeWebhookOutcome

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clear_test_billing_state.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clear_test_billing_state", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
KEY_TAIL = "KEYSENTINEL0123456789abcdef"
CUSTOMER = "cus_SENTINELcustomer"
SUBSCRIPTION = "sub_SENTINELsubscription"
AGENCY_NAME = "Sentinel Agency Name"
AGENCY_ID = "agency_sentinel_id_0001"


def _with_key(settings: Settings, key: str | None) -> Settings:
    return settings.model_copy(
        update={"stripe_secret_key": SecretStr(key) if key is not None else None}
    )


class FakeStripe:
    """Stands in for `billing._client`: records calls, lists what it is given."""

    def __init__(
        self,
        *,
        subscriptions: int = 0,
        customers: int = 0,
        error: Exception | None = None,
        on_call: Any = None,
        listed_from_call: int = 1,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.constructed = 0
        self._counts = {"subscriptions": subscriptions, "customers": customers}
        self._error = error
        self._on_call = on_call
        # The nth call of a kind (1-based) is the first to list anything.
        self._listed_from = listed_from_call
        self.v1 = SimpleNamespace(
            subscriptions=SimpleNamespace(list_async=self._lister("subscriptions")),
            customers=SimpleNamespace(list_async=self._lister("customers")),
        )

    def factory(self, settings: Settings) -> FakeStripe:
        self.constructed += 1
        return self

    def _lister(self, kind: str) -> Any:
        async def list_async(params: dict[str, Any]) -> SimpleNamespace:
            self.calls.append((kind, params))
            if self._on_call is not None:
                await self._on_call(kind)
            if self._error is not None:
                raise self._error
            nth = len([c for c in self.calls if c[0] == kind])
            listed = self._counts[kind] if nth >= self._listed_from else 0
            return SimpleNamespace(data=[object()] * listed)

        return list_async


def _use(monkeypatch: pytest.MonkeyPatch, stripe_fake: FakeStripe) -> FakeStripe:
    monkeypatch.setattr(script.billing, "_client", stripe_fake.factory)
    return stripe_fake


def _must_not_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_settings: Settings) -> FakeStripe:
        raise AssertionError("Stripe client was constructed")

    monkeypatch.setattr(script.billing, "_client", boom)


LIVE = "sk_live_" + KEY_TAIL


async def _seed(session: AsyncSession) -> None:
    """Four agencies in mixed states, one soft-deleted, plus a ledger row."""
    full = dict(
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=SUBSCRIPTION,
        subscription_status="active",
        subscription_current_period_end=NOW,
        subscription_cancel_at_period_end=True,
        subscription_trial_end=NOW,
        stripe_event_at=NOW,
    )
    session.add_all(
        [
            Agency(id=AGENCY_ID, name=AGENCY_NAME, slug="a-full", **full),
            Agency(
                id=new_id(AGENCY),
                name="B",
                slug="a-customer-only",
                stripe_customer_id="cus_two",
            ),
            Agency(id=new_id(AGENCY), name="C", slug="a-plain"),
            Agency(
                id=new_id(AGENCY),
                name="D", slug="a-deleted", deleted_at=NOW, **{
                    **full,
                    "stripe_customer_id": "cus_gone",
                    "stripe_subscription_id": "sub_gone",
                    "subscription_status": "canceled",
                }
            ),
        ]
    )
    await session.flush()
    session.add(
        StripeWebhookEvent(
            id="evt_ledger_1",
            event_type="customer.subscription.updated",
            stripe_created_at=NOW,
            agency_id=AGENCY_ID,
            outcome=StripeWebhookOutcome.APPLIED,
        )
    )
    await session.commit()


_STRIPE_COLUMNS = (
    "stripe_customer_id",
    "stripe_subscription_id",
    "subscription_status",
    "subscription_current_period_end",
    "subscription_cancel_at_period_end",
    "subscription_trial_end",
    "stripe_event_at",
)


async def _snapshot(engine: Any) -> dict[str, tuple[Any, ...]]:
    """Every agency's columns, read on a fresh session so no cached state hides a write."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as fresh:
        rows = (await fresh.execute(select(Agency).order_by(Agency.slug))).scalars().all()
        ledger = (await fresh.execute(select(StripeWebhookEvent.id))).scalars().all()
        snap = {
            a.slug: (
                a.name,
                a.seat_limit,
                a.deleted_at,
                *(getattr(a, c) for c in _STRIPE_COLUMNS),
            )
            for a in rows
        }
        snap["__ledger__"] = tuple(ledger)
        return snap


def _streams() -> tuple[io.StringIO, io.StringIO]:
    return io.StringIO(), io.StringIO()


async def _run(
    session: AsyncSession, settings: Settings, *, confirm: bool
) -> tuple[int, str, str]:
    out, err = _streams()
    rc = await script.clear(session, settings=settings, confirm=confirm, out=out, err=err)
    return rc, out.getvalue(), err.getvalue()


def _assert_no_leaks(*texts: str, key: str | None = None) -> None:
    blob = "\n".join(texts)
    for forbidden in (CUSTOMER, SUBSCRIPTION, "cus_two", AGENCY_ID, AGENCY_NAME, "@", KEY_TAIL):
        assert forbidden not in blob
    if key:
        assert key not in blob


class TestDryRun:
    async def test_prints_counts_and_writes_nothing_with_any_key(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        _must_not_build(monkeypatch)

        rc, out, err = await _run(session, _with_key(settings, None), confirm=False)

        assert rc == 0, err
        assert "agencies in all: 4" in out
        assert "stripe_customer_id set: 3" in out
        assert "stripe_subscription_id set: 2" in out
        assert "subscription_status set: 2" in out
        assert "stripe_event_at set: 2" in out
        assert "subscription_cancel_at_period_end set: 2" in out
        assert "Stripe key: not live; Stripe was not checked; --confirm would refuse." in out
        assert "Dry run: nothing was written" in out
        assert "After:" not in out
        assert await _snapshot(engine) == before
        _assert_no_leaks(out, err)

    async def test_a_live_key_runs_the_check_and_still_writes_nothing(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        stripe_fake = _use(monkeypatch, FakeStripe())

        rc, out, err = await _run(session, _with_key(settings, LIVE), confirm=False)

        assert rc == 0, err
        assert "Stripe key: live; Stripe lists no subscription and no customer." in out
        assert [kind for kind, _ in stripe_fake.calls] == ["subscriptions", "customers"]
        assert await _snapshot(engine) == before
        _assert_no_leaks(out, err, key=LIVE)


class TestConfirm:
    async def test_clears_every_agency_and_leaves_the_rest(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        _use(monkeypatch, FakeStripe())

        rc, out, err = await _run(session, _with_key(settings, LIVE), confirm=True)

        assert rc == 0, err
        after = await _snapshot(engine)
        assert set(after) == set(before)
        for slug, row in after.items():
            if slug == "__ledger__":
                assert row == before[slug] == ("evt_ledger_1",)
                continue
            assert row[:3] == before[slug][:3]
            stripe_cols = dict(zip(_STRIPE_COLUMNS, row[3:], strict=True))
            assert stripe_cols.pop("subscription_cancel_at_period_end") is False
            assert all(value is None for value in stripe_cols.values()), slug
        assert after["a-deleted"][2] is not None  # still soft-deleted, and cleared
        assert "Before:" in out and "After:" in out
        assert "Stripe key: live; Stripe lists no subscription and no customer." in out
        assert "agencies in all: 4" in out
        assert "stripe_customer_id set: 0" in out
        _assert_no_leaks(out, err, key=LIVE)

    async def test_is_one_transaction(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        _use(monkeypatch, FakeStripe())

        real_count = script._count
        calls = {"n": 0}

        async def fail_after_the_update(sess: AsyncSession) -> dict[str, int]:
            calls["n"] += 1
            if calls["n"] == 2:  # the UPDATE has been issued, the commit has not
                raise RuntimeError("boom")
            return await real_count(sess)

        monkeypatch.setattr(script, "_count", fail_after_the_update)
        with pytest.raises(RuntimeError, match="boom"):
            await _run(session, _with_key(settings, LIVE), confirm=True)
        await session.rollback()  # what closing the script's session does

        assert calls["n"] == 2
        assert await _snapshot(engine) == before

    @pytest.mark.parametrize(
        "key",
        [None, "sk_test_" + KEY_TAIL, "rk_test_" + KEY_TAIL, "whsec_" + KEY_TAIL, ""],
        ids=["none", "sk_test", "rk_test", "unrecognised", "empty"],
    )
    async def test_a_key_that_is_not_live_refuses_and_never_calls_stripe(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, key: str | None,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        _must_not_build(monkeypatch)

        rc, out, err = await _run(session, _with_key(settings, key), confirm=True)

        assert rc == script.REFUSED_NOT_LIVE == 4
        assert "only runs with a live Stripe key" in err
        assert await _snapshot(engine) == before
        _assert_no_leaks(out, err, key=key or None)

    async def test_a_live_key_with_stray_whitespace_goes_to_the_check(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        stripe_fake = _use(
            monkeypatch,
            FakeStripe(error=stripe.AuthenticationError("Invalid API Key provided")),
        )

        rc, _out, _err = await _run(session, _with_key(settings, f"  {LIVE}\n"), confirm=True)

        assert rc == script.REFUSED_UNCHECKED
        assert stripe_fake.constructed == 1
        assert await _snapshot(engine) == before


class TestLiveKeyGuard:
    @pytest.mark.parametrize("prefix", ["sk_live_", "rk_live_"])
    @pytest.mark.parametrize(
        "listed", [{"subscriptions": 1}, {"customers": 1}], ids=["subscription", "customer"]
    )
    async def test_anything_listed_refuses_and_rolls_back(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, prefix: str, listed: dict[str, int],
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        stripe_fake = _use(monkeypatch, FakeStripe(**listed))
        key = prefix + KEY_TAIL

        for confirm in (False, True):  # the dry run refuses too
            rc, out, err = await _run(session, _with_key(settings, key), confirm=confirm)
            assert rc == script.REFUSED_LIVE_SUBSCRIPTIONS == 2
            assert "already has a subscription or a customer" in err
            assert "After:" not in out and "Cleared" not in out
            _assert_no_leaks(out, err, key=key)

        assert ("subscriptions", {"status": "all", "limit": 1}) in stripe_fake.calls
        assert ("customers", {"limit": 1}) in stripe_fake.calls
        assert await _snapshot(engine) == before

    @pytest.mark.parametrize(
        "error_for",
        [
            lambda key: stripe.AuthenticationError(f"Invalid API Key provided: {key}"),
            lambda key: RuntimeError(f"stripe down {key}"),
            lambda key: TimeoutError(),
        ],
        ids=["auth-error-quoting-key", "error", "timeout"],
    )
    async def test_an_unreadable_stripe_refuses(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, error_for: Any,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        key = "sk_live_" + KEY_TAIL
        _use(monkeypatch, FakeStripe(error=error_for(key)))

        rc, out, err = await _run(session, _with_key(settings, key), confirm=True)

        assert rc == script.REFUSED_UNCHECKED == 3
        assert out.count("Stripe key: live") == 0
        assert "could not be asked" in err
        _assert_no_leaks(out, err, key=key)
        assert KEY_TAIL not in out + err
        assert await _snapshot(engine) == before

    @pytest.mark.parametrize("hung", ["subscriptions", "customers"])
    async def test_a_hung_read_times_out_and_refuses(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, hung: str,
    ) -> None:
        import asyncio

        await _seed(session)
        before = await _snapshot(engine)
        stripe_fake = _use(monkeypatch, FakeStripe())

        async def hang(params: dict[str, Any]) -> None:
            await asyncio.sleep(60)

        getattr(stripe_fake.v1, hung).list_async = hang
        monkeypatch.setattr(script, "PRECHECK_BUDGET_SECONDS", 0.05)
        monkeypatch.setattr(script, "LOCKED_BUDGET_SECONDS", 0.05)

        rc, _out, _err = await _run(session, _with_key(settings, LIVE), confirm=True)

        assert rc == 3
        assert await _snapshot(engine) == before

    async def test_the_update_is_issued_but_not_committed_when_stripe_is_asked(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        observed: list[tuple[str, int, dict[str, Any]]] = []

        async def on_call(kind: str) -> None:
            # Inside the script's transaction the UPDATE has happened...
            inside = (
                await session.execute(select(func.count(Agency.stripe_customer_id)))
            ).scalar_one()
            # ...but another connection still sees the old, committed rows.
            other = create_async_engine(settings.database_url, poolclass=NullPool)
            try:
                outside = await _snapshot(other)
            finally:
                await other.dispose()
            observed.append((kind, inside, outside))

        _use(
            monkeypatch,
            FakeStripe(subscriptions=1, on_call=on_call, listed_from_call=2),
        )

        rc, _out, _err = await _run(session, _with_key(settings, LIVE), confirm=True)

        assert rc == 2
        # Pass one (no locks) saw nothing; pass two ran under the UPDATE's locks.
        assert [o[0] for o in observed] == ["subscriptions", "customers"] * 2
        assert [o[1] for o in observed] == [3, 3, 0, 0]  # cleared only inside the locked pass
        for _kind, _inside, outside in observed:
            assert outside == before  # another connection never sees an uncommitted change
        assert await _snapshot(engine) == before  # and the refusal rolled it back

    async def test_no_subscription_and_no_customer_commits(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        stripe_fake = _use(monkeypatch, FakeStripe())

        rc, out, err = await _run(session, _with_key(settings, "rk_live_" + KEY_TAIL), confirm=True)

        assert rc == 0, err
        assert len(stripe_fake.calls) == 4  # a pre-check and a re-check, two reads each
        after = await _snapshot(engine)
        assert after["a-full"][3:] == (None, None, None, None, False, None, None)
        _assert_no_leaks(out, err, key="rk_live_" + KEY_TAIL)


class TestPreCheckAndBudget:
    async def test_a_listing_in_the_precheck_refuses_before_any_update(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        seen: list[tuple[bool, int]] = []

        async def on_call(kind: str) -> None:
            # No transaction is open on the script's session, so no lock is held,
            # and the UPDATE has not been issued.
            count = (
                await session.execute(select(func.count(Agency.stripe_customer_id)))
            ).scalar_one()
            await session.rollback()
            seen.append((False, count))

        stripe_fake = _use(monkeypatch, FakeStripe(subscriptions=1, on_call=on_call))
        assert not session.in_transaction()

        rc, out, err = await _run(session, _with_key(settings, LIVE), confirm=True)

        assert rc == 2
        assert [c for _, c in seen] == [3, 3]  # nothing cleared at either read
        assert len(stripe_fake.calls) == 2  # the pre-check only: no second pass
        assert "Before:" not in out
        assert await _snapshot(engine) == before

    async def test_the_locked_budget_is_shared_by_both_reads(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio

        await _seed(session)
        before = await _snapshot(engine)
        n = {"calls": 0}

        async def on_call(kind: str) -> None:
            n["calls"] += 1
            if n["calls"] > 2:  # the pass under the locks: each read alone fits 0.3 < 0.4
                await asyncio.sleep(0.3)

        _use(monkeypatch, FakeStripe(on_call=on_call))
        monkeypatch.setattr(script, "LOCKED_BUDGET_SECONDS", 0.4)

        rc, out, err = await asyncio.wait_for(
            _run(session, _with_key(settings, LIVE), confirm=True), timeout=5
        )

        assert rc == script.REFUSED_UNCHECKED
        assert n["calls"] == 4
        assert "Cleared" not in out
        assert await _snapshot(engine) == before

    async def test_the_dry_run_ends_its_read_transaction_before_calling_stripe(
        self, session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed(session)
        open_during: list[bool] = []

        async def on_call(kind: str) -> None:
            open_during.append(session.in_transaction())

        _use(monkeypatch, FakeStripe(on_call=on_call))

        rc, _out, err = await _run(session, _with_key(settings, LIVE), confirm=False)

        assert rc == 0, err
        assert open_during == [False, False]


class TestUnreachableDatabase:
    async def test_an_unreachable_server_is_exit_5_with_no_detail(
        self, settings: Settings, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = settings.model_copy(
            update={"database_url": "postgresql+asyncpg://avp@127.0.0.1:1/nodb"}
        )

        rc = await script._run(False, bad)

        captured = capsys.readouterr()
        assert rc == script.DATABASE_ERROR == 5
        assert "a database error stopped this" in captured.err
        assert "Run the dry run again to see the current state." in captured.err
        text = captured.out + captured.err
        assert "127.0.0.1" not in text and "nodb" not in text and "Traceback" not in text

    @pytest.mark.parametrize(
        "error",
        [
            OSError("sentinel-host"),
            asyncpg.exceptions.PostgresError("sentinel-asyncpg"),
            asyncpg.exceptions.InterfaceError("sentinel-interface"),
            OperationalError("SELECT 1", {}, Exception("sentinel-sqlalchemy")),
        ],
        ids=["oserror", "asyncpg-postgres", "asyncpg-interface", "sqlalchemy"],
    )
    @pytest.mark.parametrize("confirm", [False, True])
    async def test_any_connect_failure_is_one_fixed_sentence(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], error: Exception, confirm: bool,
    ) -> None:
        async def fail(*_a: Any, **_k: Any) -> int:
            raise error

        monkeypatch.setattr(script, "clear", fail)

        rc = await script._run(confirm, settings)

        captured = capsys.readouterr()
        assert rc == 5
        assert "sentinel" not in captured.out + captured.err
        assert "Traceback" not in captured.err
        assert "a database error stopped this" in captured.err


class TestDatabaseError:
    @pytest.mark.parametrize("confirm", [False, True])
    async def test_a_database_error_prints_a_fixed_sentence_and_writes_nothing(
        self, session: AsyncSession, engine: Any, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, confirm: bool,
    ) -> None:
        await _seed(session)
        before = await _snapshot(engine)
        _use(monkeypatch, FakeStripe())
        real_count = script._count
        calls = {"n": 0}

        async def fail(sess: AsyncSession) -> dict[str, int]:
            calls["n"] += 1
            if calls["n"] == (2 if confirm else 1):  # after the UPDATE when confirming
                raise OperationalError(
                    "UPDATE agencies SET x", {}, Exception(f"DBSENTINEL {AGENCY_ID} {LIVE}")
                )
            return await real_count(sess)

        monkeypatch.setattr(script, "_count", fail)

        rc, out, err = await _run(session, _with_key(settings, LIVE), confirm=confirm)

        assert rc == script.DATABASE_ERROR == 5
        assert "a database error stopped this" in err
        assert "DBSENTINEL" not in out + err
        assert "UPDATE" not in out + err
        _assert_no_leaks(out, err, key=LIVE)
        assert await _snapshot(engine) == before


class TestCommandLine:
    def test_a_bad_argument_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert script.main(["--no-such-flag"]) == script.USAGE == 1
        capsys.readouterr()
