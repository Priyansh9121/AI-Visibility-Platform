"""The suite's outbound-call guard and its .env protection (checklist 3.82)."""

from __future__ import annotations

import contextlib
import urllib.request
from urllib.error import URLError

import httpx
import jwt
import pytest
from fastapi import FastAPI

from avp_api.config import Settings, get_settings


@pytest.fixture
def expect_refusals(outbound_guard):  # noqa: ANN001, ANN201
    """For tests that make refused attempts on purpose: clear the record they leave."""
    yield
    outbound_guard.refused.clear()


def test_sync_request_to_a_public_host_is_refused_and_recorded(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    with httpx.Client() as c, pytest.raises(outbound_guard.error):
        c.get("https://api.example.com/x")
    assert outbound_guard.refused == ["GET api.example.com"]


async def test_async_request_to_a_public_host_is_refused_and_recorded(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    async with httpx.AsyncClient() as c:
        with pytest.raises(outbound_guard.error):
            await c.post("https://api.example.com/x")
    assert outbound_guard.refused == ["POST api.example.com"]


async def test_a_swallowed_attempt_is_still_caught_at_teardown(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    async with httpx.AsyncClient() as c:
        with contextlib.suppress(Exception):  # the swallow our own code does by design
            await c.get("https://api.example.com/x")
    with pytest.raises(pytest.fail.Exception, match="api.example.com"):
        outbound_guard.check()
    assert outbound_guard.refused == []


def test_the_check_passes_when_nothing_was_attempted(outbound_guard) -> None:
    outbound_guard.check()


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]", "localhost"])
async def test_loopback_is_not_refused(host: str, outbound_guard) -> None:
    # Nothing listens on port 9: a TransportError means the guard let the call through.
    async with httpx.AsyncClient() as c:
        with pytest.raises(httpx.TransportError):
            await c.get(f"http://{host}:9/")
    assert outbound_guard.refused == []


async def test_asgi_and_mock_transports_are_unaffected(outbound_guard) -> None:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://public.example.com"
    ) as c:
        assert (await c.get("/ping")).json() == {"ok": True}
    mock = httpx.MockTransport(lambda r: httpx.Response(204))
    async with httpx.AsyncClient(transport=mock) as c:
        assert (await c.get("https://api.example.com/x")).status_code == 204
    with httpx.Client(transport=mock) as c:
        assert c.get("https://api.example.com/x").status_code == 204
    assert outbound_guard.refused == []


# --- urllib: PyJWT fetches Google's key set with `urllib.request.urlopen`, which is not httpx ----
#
# The hosts are under `.test`, the top-level domain reserved for tests, which nothing delegates:
# if a control removes the guard, these tests fail on a name that goes nowhere and not on a real
# server. (Not `.invalid`: `crawlerPage.test.tsx` forbids that anywhere in apps/, as a placeholder.)


def test_urlopen_to_a_public_host_is_refused_and_recorded(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    with pytest.raises(outbound_guard.error):
        urllib.request.urlopen("https://keys.outbound-guard.test/certs")
    assert outbound_guard.refused == ["GET keys.outbound-guard.test"]


def test_a_request_with_a_body_is_recorded_as_a_post(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    request = urllib.request.Request("https://keys.outbound-guard.test/certs", data=b"a=b")
    with pytest.raises(outbound_guard.error):
        urllib.request.urlopen(request)
    assert outbound_guard.refused == ["POST keys.outbound-guard.test"]


def test_a_swallowed_urlopen_attempt_is_still_caught_at_teardown(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    with contextlib.suppress(Exception):  # what a caller that catches everything does
        urllib.request.urlopen("https://keys.outbound-guard.test/certs")
    with pytest.raises(pytest.fail.Exception, match="keys.outbound-guard.test"):
        outbound_guard.check()
    assert outbound_guard.refused == []


def test_pyjwts_own_key_set_fetch_is_refused_and_recorded(
    expect_refusals, outbound_guard
) -> None:  # noqa: ANN001
    """The reason for the guard: a test that reaches the real `google_oauth._jwks()` would fetch
    Google's key set with nothing saying so. PyJWT catches only `URLError` and `TimeoutError`
    around its fetch, so the refusal, an assertion, comes through it."""
    keyset = jwt.PyJWKClient("https://keys.outbound-guard.test/certs")
    with pytest.raises(outbound_guard.error):
        keyset.fetch_data()
    assert outbound_guard.refused == ["GET keys.outbound-guard.test"]


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]", "localhost"])
def test_urlopen_to_loopback_is_not_refused(host: str, outbound_guard) -> None:
    # Nothing listens on port 9: a URLError means the guard let the call through.
    with pytest.raises(URLError):
        urllib.request.urlopen(f"http://{host}:9/", timeout=2)
    assert outbound_guard.refused == []


def test_a_url_with_no_host_is_no_outbound_call(outbound_guard) -> None:
    with pytest.raises(URLError):  # the file is not there; the guard let the call through
        urllib.request.urlopen("file:///no/such/file/for/the/outbound/guard")
    assert outbound_guard.refused == []


def test_settings_never_read_a_dotenv_file(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    # A `.env` in the working directory, where Settings' default ".env" would find it.
    (tmp_path / ".env").write_text("APP_SECRET=sentinel-from-dotenv\nSERPAPI_KEY=sentinel-key\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    get_settings.cache_clear()
    try:
        for built in (Settings(), get_settings()):
            assert built.serpapi_key is None
            assert "sentinel" not in built.app_secret.get_secret_value()
    finally:
        get_settings.cache_clear()
