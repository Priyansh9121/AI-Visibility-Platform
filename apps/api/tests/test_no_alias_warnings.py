"""A request body reaches its handler without Pydantic's alias warning — M2, 2026-10-02.

The deploy logs carried an `UnsupportedFieldAttributeWarning` for each body
field, on the first request to each route: "The 'alias' attribute with value
'engines' has no effect ...", and the same for accentColor, agencyName, classify,
competitors, currentPassword, email, fullName, logoUrl, name, newPassword,
password, prompt, promptLimit, role, ticket, token and url.

It is not a defect in our schemas. `ApiModel` gives every field a camelCase alias
(`alias_generator=to_camel`) and every one of them works on the wire; the last
tests below prove it. The warning came from FastAPI 0.116.2 doing work it did
not need. On the FIRST request to a JSON-body route, `request_body_to_args`
called `get_cached_model_fields`, which wraps each field of the body model in a
`TypeAdapter(Annotated[annotation, field_info])` of its own. Those per-field
adapters are used for FORM bodies only (`_extract_form_body`); a JSON body is
validated through the whole model. Pydantic warns that an alias on a `FieldInfo`
inside an `Annotated` has no effect, which is true and beside the point.
FastAPI 0.117.0 builds them only when the body is `FormData`
(fastapi/fastapi#13694), and `pyproject.toml` pins `fastapi>=0.117.1`. The API
takes no form or file body, which `test_no_route_takes_a_form_body` holds, so
nothing here reaches that path.

The suite used to ignore the warning with an ini `filterwarnings` entry. That
entry is gone, and these tests are what keep the warning from coming back by
another door: a FastAPI pin that slips below the fix, a new body route, a new
way of declaring one.

**Why the check calls FastAPI's own function.** A route behind a session answers
a request that has none with a 401 before its body is looked at, so driving every
body route over HTTP would take a signed-in agency and its rows for each. The
warning fires when FastAPI prepares the body, ahead of the handler.
`request_body_to_args` is what FastAPI calls to do that, with these arguments for
a route that takes one model, so calling it with each route's own `body_field`
walks every body route with no session and no rows.

**Why the cache is cleared first.** `get_cached_model_fields` is an `lru_cache`,
so a model it has already seen costs nothing and warns nothing. Almost every
test in this suite posts to these routes, so by the time this file runs the
cache is warm and the check would pass on a FastAPI that is still broken. Cold,
before each route, or it proves nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi._compat import get_cached_model_fields
from fastapi.dependencies.utils import request_body_to_args
from fastapi.params import Form
from fastapi.routing import APIRoute
from httpx import AsyncClient
from pydantic import BaseModel
from pydantic.warnings import UnsupportedFieldAttributeWarning

from avp_api.config import Settings
from avp_api.main import create_app
from avp_api.models.engine_result import Engine

BASE = "/api/v1"
API_ROOT = Path(__file__).resolve().parents[1]

# Where to look when one of these fails. Said once, not in every message.
_WHY = (
    "FastAPI prepares a JSON body by wrapping every field of its model in a "
    "TypeAdapter, which Pydantic warns about when the field has an alias. "
    "FastAPI 0.117.0 stopped doing that for anything but form data; "
    "pyproject.toml pins `fastapi>=0.117.1`. See this file's docstring."
)


@pytest.fixture(scope="module")
def app(settings: Settings) -> FastAPI:
    """The app under the suite's hermetic settings, built once for this file."""
    return create_app(settings)


def _body_routes(app: FastAPI) -> list[APIRoute]:
    """Every route that takes a body which is a Pydantic model."""
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.body_field is not None
        and isinstance(route.body_field.type_, type)
        and issubclass(route.body_field.type_, BaseModel)
    ]


def _label(route: APIRoute) -> str:
    """`POST /auth/login`: the method and the path without the API prefix."""
    return f"{sorted(route.methods)[0]} {route.path.removeprefix(BASE)}"


# ---------------------------------------------------------------------------
# Building the app and its schema
# ---------------------------------------------------------------------------

# A fresh interpreter, because `avp_api.main` is already imported by conftest in
# this one and importing it again would check nothing. The import is what builds
# every router (module level `app = create_app()`), and what a deploy does first.
_FRESH_IMPORT = """
import warnings
from pydantic.warnings import UnsupportedFieldAttributeWarning

warnings.simplefilter("error", UnsupportedFieldAttributeWarning)

import avp_api.main

avp_api.main.app.openapi()
"""


def test_a_fresh_interpreter_imports_the_app_and_builds_its_schema_without_the_warning(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _FRESH_IMPORT],
        # An empty directory, so `Settings()` finds no `.env` and a developer's
        # file cannot change what this builds; and an environment of two names,
        # so a shell's ENVIRONMENT or DATABASE_URL cannot either.
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(API_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"{_WHY}\n\nstderr:\n{result.stderr[-2000:]}"


def test_building_the_app_and_its_openapi_schema_raises_no_alias_warning(
    settings: Settings,
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnsupportedFieldAttributeWarning)
        # A new app, so `openapi()` runs rather than returning a cached schema.
        fresh = create_app(settings)
        schema = fresh.openapi()
    assert schema["paths"], "an empty schema would make this check vacuous"


# ---------------------------------------------------------------------------
# Preparing a body
# ---------------------------------------------------------------------------


def test_the_body_routes_walked_here_are_not_vacuous_and_all_one_shape(app: FastAPI) -> None:
    """The checks below pass over nothing if no route is found, and call FastAPI
    wrongly if a route is shaped differently from what they assume."""
    routes = _body_routes(app)
    labels = {_label(route) for route in routes}

    assert len(routes) >= 2
    assert {"POST /auth/login", "POST /clients/{clientId}/scans"} <= labels

    # One model, not embedded: the case `request_body_to_args(body_fields=
    # [route.body_field], ..., embed_body_fields=False)` replicates exactly. A
    # route with two body parameters is embedded, and a body declared by a
    # dependency is not in `dependant.body_params` at all: either takes another
    # call.
    for route in routes:
        body_params = route.dependant.body_params
        assert len(body_params) == 1 and body_params[0] is route.body_field, (
            f"{_label(route)} does not take exactly one body model: the checks in "
            "this file call FastAPI as it calls a route that does, so extend them"
        )


def test_no_route_takes_a_form_body(app: FastAPI) -> None:
    """FastAPI still builds the per-field adapters for a FORM body, and an aliased
    field warns there (6 warnings for the two-field `LoginRequest` on 0.117.1).
    This API has no such route, which is why the JSON-only fix is the whole fix;
    a route that takes one is a different problem and should be met on purpose,
    not discovered in the deploy logs. (Declaring one needs `python-multipart`,
    which is not installed, so today it cannot happen by accident: this holds the
    line for the day that dependency is added.)"""
    forms = [
        _label(route)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.body_field is not None
        and isinstance(route.body_field.field_info, Form)  # `File` is a `Form`
    ]
    assert not forms, f"form or file body on: {', '.join(forms)}. {_WHY}"


async def test_no_body_route_warns_when_fastapi_prepares_its_body(app: FastAPI) -> None:
    offenders: list[str] = []
    for route in _body_routes(app):
        get_cached_model_fields.cache_clear()
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnsupportedFieldAttributeWarning)
            try:
                await request_body_to_args(
                    body_fields=[route.body_field],
                    received_body={},
                    embed_body_fields=False,
                )
            except UnsupportedFieldAttributeWarning as warning:
                # The first sentence names the attribute and its value, which is
                # all a reader needs to find the field.
                offenders.append(f"  {_label(route)}: {str(warning).split(', which')[0]}")

    assert not offenders, (
        f"{len(offenders)} body route(s) warn on the first request. {_WHY}\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


async def test_every_camel_case_alias_is_the_live_key_of_its_field_on_every_body_route(
    app: FastAPI,
) -> None:
    """A value of the wrong type under a field's alias is reported against that
    alias, by every body route. If an alias were ignored the key would be dropped
    and the field would read as absent, never as wrong: the 18 names the warning
    listed would each have gone quietly dead. They have not.

    `[[]]` is not a string, a number, a boolean, an enum member or a model, and
    not a list of any of them, so it is wrong for every field the API takes.
    """
    dead: list[str] = []
    for route in _body_routes(app):
        for name, field in route.body_field.type_.model_fields.items():
            alias = field.alias or name
            _, errors = await request_body_to_args(
                body_fields=[route.body_field],
                received_body={alias: [[]]},
                embed_body_fields=False,
            )
            wrong_type = [
                error
                for error in errors
                if tuple(error["loc"][:2]) == ("body", alias) and error["type"] != "missing"
            ]
            if not wrong_type:
                dead.append(f"  {_label(route)}: {alias}")

    assert not dead, "alias not honoured on the wire:\n" + "\n".join(dead)


@pytest.fixture
def jobs() -> list:
    return []


@pytest.fixture
def scan_executor_factory(jobs: list):  # noqa: ANN201
    """A scan is queued and handed over, not run. What these tests look at is the
    request, and the chain behind it is covered by test_scan_endpoints.py."""

    class _Queued:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    return _Queued


class TestTheWireIsStillCamelCase:
    """What api-contracts.md fixes, read through the routes the warning named."""

    async def test_sign_up_takes_its_names_in_camel_case(
        self, client: AsyncClient, signup_payload: dict[str, str]
    ) -> None:
        resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agency"]["name"] == signup_payload["agencyName"]
        assert body["user"]["fullName"] == signup_payload["fullName"]
        assert body["user"]["email"] == signup_payload["email"]

    async def test_a_bound_on_a_camel_case_field_is_enforced_and_reported_by_that_name(
        self, client: AsyncClient, signup_payload: dict[str, str]
    ) -> None:
        # `SignUpRequest.agency_name` has `min_length=2`. Were `agencyName` not
        # the field's name on the wire, this would be a 201 or a "missing" error
        # against another name, not a too-short one against this.
        resp = await client.post(
            f"{BASE}/auth/sign-up", json={**signup_payload, "agencyName": "A"}
        )

        assert resp.status_code == 422, resp.text
        errors = [(e["field"], e["type"]) for e in resp.json()["errors"]]
        assert errors == [("agencyName", "string_too_short")]

    async def test_a_scan_is_started_with_prompt_limit_and_engines_by_those_names(
        self, client: AsyncClient, signup_payload: dict[str, str], jobs: list
    ) -> None:
        client_id = await _sign_up_and_add_a_client(client, signup_payload)

        resp = await client.post(
            f"{BASE}/clients/{client_id}/scans",
            json={"promptLimit": 3, "engines": ["claude", "chatgpt"]},
        )

        assert resp.status_code == 202, resp.text
        assert len(jobs) == 1
        assert jobs[0].prompt_limit == 3
        assert jobs[0].engines == (Engine.CLAUDE, Engine.CHATGPT)

    async def test_more_engines_than_exist_is_a_422_and_starts_nothing(
        self, client: AsyncClient, signup_payload: dict[str, str], jobs: list
    ) -> None:
        # `engines` has `max_length=len(Engine)`. Duplicates are collapsed before
        # it is applied, so what can exceed it is more DISTINCT values than the
        # enum has members, and the length is judged before any of them is.
        client_id = await _sign_up_and_add_a_client(client, signup_payload)
        too_many = [f"engine-{n}" for n in range(len(Engine) + 1)]

        resp = await client.post(
            f"{BASE}/clients/{client_id}/scans", json={"engines": too_many}
        )

        assert resp.status_code == 422, resp.text
        errors = [(e["field"], e["type"]) for e in resp.json()["errors"]]
        assert errors == [("engines", "too_long")]
        assert jobs == []

    @pytest.mark.parametrize("prompt_limit", [0, 31])
    async def test_a_prompt_limit_outside_its_range_is_a_422_and_starts_nothing(
        self,
        client: AsyncClient,
        signup_payload: dict[str, str],
        jobs: list,
        prompt_limit: int,
    ) -> None:
        client_id = await _sign_up_and_add_a_client(client, signup_payload)

        resp = await client.post(
            f"{BASE}/clients/{client_id}/scans", json={"promptLimit": prompt_limit}
        )

        assert resp.status_code == 422, resp.text
        assert [e["field"] for e in resp.json()["errors"]] == ["promptLimit"]
        assert jobs == []


async def _sign_up_and_add_a_client(client: AsyncClient, signup_payload: dict[str, str]) -> str:
    """Signed in as a new agency's owner, with one client. Returns the client's id."""
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    resp = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]
