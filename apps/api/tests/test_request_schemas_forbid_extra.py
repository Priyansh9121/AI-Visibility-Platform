"""Every request body refuses a field its model does not declare — A8, mass assignment.

`ApiModel` used to drop an unknown key unread. No request model accepts an agency, a user, a plan,
a price or a status, so nothing privileged could be set that way; the one privilege-bearing request
field is `InviteSeatRequest.role`, and F1 refuses an admin's owner invite. What dropping left was a
trap: the day a model gains one of those fields, every caller already sending it (a stale tab, a
script, someone who guessed the name) is accepted without anybody having decided so. `RequestModel`
(`schemas/common.py`) refuses the key instead, as the same `422 validation-failed` problem any other
bad field gets, with the key in `errors[].field` and one plain sentence in `errors[].message`.

**The sweep reads the LIVE app, not a list.** It walks `avp_api.main.app`'s routes, takes each
body route's model and every model nested inside it, and builds one test case per model name, so a
new route, or a new nested model, cannot escape it. `test_the_sweep_finds_the_whole_request_surface`
fails if it ever finds fewer than the 20 operations and 21 models there are today. What a sweep of
the routes cannot see is code that reads a body itself, so a source guard (f, below) walks the
WHOLE `avp_api` package for it (a dependency in `deps.py` would read a body as surely as a route
does): the Stripe webhook takes the raw bytes and checks a signature, and is the only one.

Per model:

* (a) its config, and that of every model nested in it, forbids extra fields, and it inherits
  `RequestModel`;
* (b) its route answers 422 to a valid body plus one unknown field, and the one error names it;
* (c) no field at any depth has the SHAPE of a name a caller must not choose, or the type of one: an
  id, a time, an author, a hash or a subject (`*_id`, `*_at`, `*_by`, `*_hash`, `*_sub`); a flag or
  a billing word (`is_*`, `stripe*`, `subscription*`, `billing*`, `trial*`); a role, status, plan,
  price or limit anywhere in it; an enum from `avp_api.models`; or a name the path already gives.
  Matching by shape and not by a list is what catches `requested_by_user_id` and `new_role`. What
  earns an exception is allow-listed with its reason;
* (d) a body with a flood of keys is refused whole with one `too_many_fields` error, at every depth,
  and a problem document carries at most 20 errors and 200 characters of each `field` (M1), and
  says `errorsTruncated: true` when it cut the list (A8b);
* (e) no field at any depth has a type that reopens the bag: the types a field may be made of are
  listed, so a `dict`, `Mapping`, `Any`, `Json`, nested `@dataclass` or `TypedDict` fails (A8b);
* (f) nothing in the `avp_api` package reads a request body by hand (`request.json()`, `.body()`,
  `.form()`, `.stream()`), except the Stripe webhook, and that exception still exists;

and `populate_by_name` is unchanged (both spellings of a declared field are still accepted).

**Why (b) does not run the handlers.** A request that gets past validation would run the route: a
scan would start, a client would be crawled. If the guard under test were broken, this test would
do all of that on the way to failing. So once the session exists, each body route's handler is
replaced by one that answers 204 and does nothing. Everything in front of it is the live route's
own: its dependencies (the session, the role check), its body validation and the app's error
handlers. A body that is refused never reaches the handler; one that is not gets a 204, which is
also the control that the hand-written body is valid (the same body without the unknown field must
get the 204, or the 422 proves nothing).
"""

from __future__ import annotations

import ast
import copy
import enum
import importlib
import pkgutil
import re
import types
import typing
from collections import OrderedDict
from collections.abc import Iterator, MutableSequence, MutableSet, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin
from uuid import UUID

import pytest
from fastapi import FastAPI, Response
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, EmailStr, Field, Json
from pydantic.alias_generators import to_camel, to_snake

import avp_api
import avp_api.models
from avp_api import ids
from avp_api import main as api_main
from avp_api.config import Settings
from avp_api.models.engine_result import Engine
from avp_api.models.prompt_run import PromptRunStatus
from avp_api.models.scan import ScanStatus
from avp_api.models.technical_audit import AuditStatus
from avp_api.models.tenancy import UserRole
from avp_api.schemas.common import EXTRA_KEYS_MARGIN, ApiModel, RequestModel

LIVE_APP: FastAPI = api_main.app
BASE = "/api/v1"

# What an unknown field is called in (b). A name no model would ever declare, so that a model that
# gains a privileged field fails (c) alone and not this as well; the privileged names are tried in
# `test_every_unknown_field_is_named_not_only_the_first`.
UNKNOWN = "unknownField"
NOT_ACCEPTED = "This field is not accepted."


# ---------------------------------------------------------------------------
# What may stay as it is. Short on purpose, and each entry says why.
# ---------------------------------------------------------------------------

# A request model that must keep ignoring unknown fields: name -> why. (a) and (b) skip it.
# None does today. A model in here is a mass-assignment door that was left open on purpose.
OPEN_MODELS: dict[str, str] = {}

# A field the rules of (c) refuse that a request may carry anyway: (model, field) -> why. Each
# entry must still be refused by the rules, or it is stale (`test_the_allow_lists_...`).
ALLOWED_FIELDS: dict[tuple[str, str], str] = {
    ("InviteSeatRequest", "role"): (
        "F1: the role a seat is invited to is the one privilege-bearing request field; the invite "
        "refuses a role above the inviter's own (services/invitations.invite_seat, ROLE_RANK, 403)"
    ),
    ("RunScanRequest", "prompt_limit"): (
        "1 to 30 (schemas/scan.py) and applied only as a slice of the question set "
        "(scan_runner.build_prompt_set), so it can only lower what a scan costs"
    ),
    ("RunScanRequest", "engines"): (
        "deduplicated and at most len(Engine) (schemas/scan.py), then checked against the engines "
        "that have an adapter and a key (scan_start.start_scan, 422)"
    ),
}

# A field whose type is free-form (e) and may stay: (model, field) -> why. None does today.
OPEN_TYPE_FIELDS: dict[tuple[str, str], str] = {}

# (c): the SHAPE of a name a caller must not choose, tested on the snake-case spelling (a camelCase
# one is the same name). `verified` is not in the review's list, but `email_verified` is among its
# examples and nothing else in the rules would catch it.
NAME_ENDS = ("_id", "_at", "_by", "_hash", "_sub")
NAME_STARTS = ("is_", "stripe", "subscription", "billing", "trial")
# `owner`, `admin`, `tenant`, `account` and `user` were added with A8b: they name who holds a row or
# what that holder may do, wherever they sit in a name (`co_owner`, `super_admin`, `account_email`).
# No field of any request model contains one today (`agency_name`, `full_name` and `accent_color`
# do not), so nothing needed an exception.
NAME_CONTAINS = (
    "role", "status", "plan", "price", "limit", "verified",
    "owner", "admin", "tenant", "account", "user",
)

# Names the shape does not catch: name -> why. Each is the WHOLE name, so a compound that is only
# a name (`agency_name`, a real field) is not caught by it.
EXACT_NAMES: dict[str, str] = {
    "trigger": (
        "how a scan was started: a non-manual trigger changes the re-submit rule and labels the "
        "row (services/scan_start.start_scan), so only the server's own callers may say it"
    ),
    "agency": (
        "whose agency a row belongs to is the session's (principal.agency_id), never the body's. "
        "Only the whole word: `agency_name` is a name the caller types and is a real field"
    ),
    "seats": (
        "how many seats an agency has is the plan's (settings.default_seat_limit, "
        "SeatLimitReached), never a number a caller sets"
    ),
    "quota": "how much an agency may do is the plan's and the month's (services/scan_allowance)",
    "credits": "what an agency has left to spend is billing's to say, never the body's",
    "allowance": "the scan allowance is computed from the plan and the month (scan_allowance)",
    "exempt": (
        "who is exempt from the scan allowance is the operator's list "
        "(SCAN_ALLOWANCE_EXEMPT_EMAILS), never a flag a caller sends"
    ),
}


# ---------------------------------------------------------------------------
# Hand-written bodies: one valid, minimal body per model a route takes, and where a nested model
# sits inside its route's body. A generic builder would have to know every validator (a password's
# repetitiveness, a colour's pattern, a rival's domain), so the bodies are written down, and
# `test_every_model_found_has_a_hand_written_body` fails for a model that has none.
# ---------------------------------------------------------------------------

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"

VALID_BODIES: dict[str, dict[str, Any]] = {
    "SignUpRequest": {
        "agencyName": "Acme Digital",
        "fullName": "Dana Whitfield",
        "email": "dana@acme-digital.example",
        "password": PASSWORD,
    },
    "LoginRequest": {"email": "dana@acme-digital.example", "password": PASSWORD},
    "ChangePasswordRequest": {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    "ResetPasswordRequest": {"email": "dana@acme-digital.example"},
    "ResetPasswordConfirm": {"token": "a-reset-token", "newPassword": NEW_PASSWORD},
    "AcceptInvitationRequest": {
        "token": "an-invitation-token",
        "fullName": "Jo Iner",
        "password": PASSWORD,
    },
    "GooglePendingRequest": {"ticket": "a-google-ticket"},
    "CompleteGoogleSignUpRequest": {
        "ticket": "a-google-ticket",
        "agencyName": "Acme Digital",
        "fullName": "Dana Whitfield",
    },
    "ScanEmailsRequest": {"enabled": True},
    "InviteSeatRequest": {"email": "jo@acme-digital.example"},
    "BrandingRequest": {"accentColor": "#336699"},
    "CreateClientRequest": {"url": "helpscout.com", "classify": False},
    "SetAutoRescanRequest": {"enabled": True},
    "ReplaceCompetitorsRequest": {"competitors": [{"name": "Zendesk"}]},
    "ReplaceOtherSitesRequest": {
        "client": [],
        "rivals": [{"rivalDomain": "zendesk.com", "sites": []}],
        "dismissed": [],
    },
    "NewQuestionsRequest": {"newQuestionsNextScan": True},
    "RunScanRequest": {"promptLimit": 5},
    "PromptRunIn": {"prompt": "Which help desk is best for a small agency?"},
    "SharedReportRequest": {"token": "a-share-token"},
}

# A model that is only ever nested: the model of the route whose body holds it, and the path to it.
NESTED_AT: dict[str, tuple[str, tuple[str | int, ...]]] = {
    "CompetitorInput": ("ReplaceCompetitorsRequest", ("competitors", 0)),
    "RivalSitesIn": ("ReplaceOtherSitesRequest", ("rivals", 0)),
}

# The path parameters of the body routes, filled so that nothing in (b) depends on a row existing:
# the handler is not run, so the id is never looked up.
PATH_VALUES = {"agencyId": ids.new_id(ids.AGENCY), "clientId": ids.new_id(ids.CLIENT)}


# ---------------------------------------------------------------------------
# Finding the surface in the live app
# ---------------------------------------------------------------------------


def _all_routes(routes: Any) -> Iterator[Any]:
    for route in routes:
        yield route
        yield from _all_routes(getattr(route, "routes", None) or ())


def _body_routes(app: FastAPI) -> list[APIRoute]:
    return [
        route
        for route in _all_routes(app.routes)
        if isinstance(route, APIRoute) and route.body_field is not None
    ]


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    """Every pydantic model an annotation names, however it is wrapped (a list, a union, ...)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _models_in(arg)]


def _closure(model: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """Every model nested in `model`, at any depth, by name."""
    found: dict[str, type[BaseModel]] = {}
    pending = [model]
    while pending:
        for info in pending.pop().model_fields.values():
            for child in _models_in(info.annotation):
                if child.__name__ not in found:
                    found[child.__name__] = child
                    pending.append(child)
    return found


@dataclass
class Found:
    cls: type[BaseModel]
    # The routes whose body IS this model; empty for a model that is only ever nested.
    routes: list[APIRoute] = field(default_factory=list)
    # The models of the routes whose body holds this one, at any depth.
    inside: set[str] = field(default_factory=set)
    # Everything nested in it.
    nested: dict[str, type[BaseModel]] = field(default_factory=dict)


@dataclass
class Surface:
    models: dict[str, Found] = field(default_factory=dict)
    routes: list[APIRoute] = field(default_factory=list)
    # A route whose body is not a model at all, and two different classes with one name.
    not_models: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)

    def entry(self, cls: type[BaseModel]) -> Found:
        found = self.models.setdefault(cls.__name__, Found(cls=cls))
        if found.cls is not cls:
            self.collisions.append(f"{cls.__module__}.{cls.__name__}")
        return found


def _find() -> Surface:
    surface = Surface(routes=_body_routes(LIVE_APP))
    for route in surface.routes:
        roots = _models_in(route.body_field.type_)  # type: ignore[union-attr]
        if not roots:
            surface.not_models.append(_label(route))
        for root in roots:
            surface.entry(root).routes.append(route)
            for child in _closure(root).values():
                surface.entry(child).inside.add(root.__name__)
    for found in surface.models.values():
        found.nested = _closure(found.cls)
    return surface


def _method(route: APIRoute) -> str:
    return sorted(route.methods)[0]


def _label(route: APIRoute) -> str:
    """`POST /auth/login`: the method and the path without the API prefix."""
    return f"{_method(route)} {route.path.removeprefix(BASE)}"


SURFACE = _find()
MODELS = SURFACE.models
BODY_ROUTES = SURFACE.routes
MODEL_NAMES = sorted(MODELS)
ROOT_NAMES = sorted(name for name, found in MODELS.items() if found.routes)


def _routes_reaching(name: str) -> list[APIRoute]:
    """The routes whose body is the model, or holds it."""
    found = MODELS[name]
    routes = list(found.routes)
    for root in found.inside:
        routes.extend(MODELS[root].routes)
    return routes


def _at(body: Any, where: tuple[str | int, ...]) -> Any:
    for step in where:
        body = body[step]
    return body


# ---------------------------------------------------------------------------
# The sweep cannot silently shrink
# ---------------------------------------------------------------------------


def test_the_sweep_finds_the_whole_request_surface() -> None:
    """20 operations and 21 models today (19 bodies, `CompetitorInput` and `RivalSitesIn`)."""
    operations = {(method, route.path) for route in BODY_ROUTES for method in route.methods}
    assert len(operations) >= 20, f"only {len(operations)} operations with a body were found"
    assert len(MODELS) >= 21, f"only {len(MODELS)} request models were found: {MODEL_NAMES}"
    assert {
        "SignUpRequest", "LoginRequest", "InviteSeatRequest", "BrandingRequest",
        "ReplaceCompetitorsRequest", "CompetitorInput", "ReplaceOtherSitesRequest", "RivalSitesIn",
        "SharedReportRequest", "RunScanRequest", "PromptRunIn", "NewQuestionsRequest",
    } <= set(MODELS)
    # The same model may sit behind two routes (the share token's JSON and PDF); two different
    # models may not share a name, because every case below is keyed by it.
    assert not SURFACE.collisions, f"two request models share a name: {SURFACE.collisions}"


def test_every_body_is_a_model() -> None:
    """A route that takes a bare `dict` or `list` takes any key: there is no model to forbid."""
    assert not SURFACE.not_models, f"a body that is not a model on: {SURFACE.not_models}"


def test_every_model_found_has_a_hand_written_body() -> None:
    """(b) needs a valid body for each route's model and a place for each nested one."""
    assert set(ROOT_NAMES) == set(VALID_BODIES), (
        f"VALID_BODIES must hold exactly the models routes take. Missing: "
        f"{sorted(set(ROOT_NAMES) - set(VALID_BODIES))}; stale: "
        f"{sorted(set(VALID_BODIES) - set(ROOT_NAMES))}"
    )
    only_nested = set(MODELS) - set(ROOT_NAMES)
    assert only_nested == set(NESTED_AT), (
        f"NESTED_AT must hold exactly the models no route takes by themselves. Missing: "
        f"{sorted(only_nested - set(NESTED_AT))}; stale: {sorted(set(NESTED_AT) - only_nested)}"
    )
    for name, body in VALID_BODIES.items():
        MODELS[name].cls.model_validate(body)  # raises if the hand-written body is not valid
    for name, (root, where) in NESTED_AT.items():
        assert root in MODELS[name].inside, f"{name} does not sit inside {root}"
        MODELS[name].cls.model_validate(_at(VALID_BODIES[root], where))


def test_the_allow_lists_name_what_exists_and_say_why() -> None:
    for name, reason in OPEN_MODELS.items():
        assert name in MODELS and reason.strip(), f"OPEN_MODELS[{name!r}] is stale or has no reason"
    for (name, fname), reason in ALLOWED_FIELDS.items():
        assert name in MODELS, f"ALLOWED_FIELDS names {name}, which is not a request model"
        assert fname in MODELS[name].cls.model_fields, f"{name} has no field {fname}: drop it"
        assert reason.strip(), f"ALLOWED_FIELDS[{(name, fname)!r}] has no reason"
        # An exception for a rule that no longer applies would excuse the next field to be named so.
        assert _refusals(fname, MODELS[name].cls.model_fields[fname]), (
            f"{name}.{fname} is no longer refused by the rules of (c): drop its entry"
        )
    for (name, fname), reason in OPEN_TYPE_FIELDS.items():
        assert name in MODELS and fname in MODELS[name].cls.model_fields, f"{name}.{fname} is gone"
        assert reason.strip(), f"OPEN_TYPE_FIELDS[{(name, fname)!r}] has no reason"
        assert _free_form_in(MODELS[name].cls.model_fields[fname]), (
            f"{name}.{fname} is no longer free-form: drop its entry"
        )


# ---------------------------------------------------------------------------
# (a) the config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_the_model_and_every_model_nested_in_it_forbid_extra_fields(name: str) -> None:
    if name in OPEN_MODELS:
        pytest.skip(OPEN_MODELS[name])
    found = MODELS[name]
    assert issubclass(found.cls, RequestModel), f"{name} is not a RequestModel"
    for cls in (found.cls, *found.nested.values()):
        assert cls.model_config.get("extra") == "forbid", (
            f"{cls.__name__} (in {name}) does not forbid extra fields"
        )
        assert issubclass(cls, RequestModel), f"{cls.__name__} (in {name}) is not a RequestModel"


def test_the_request_base_adds_the_refusal_and_changes_no_other_setting() -> None:
    """`RequestModel` adds `extra="forbid"` to `ApiModel`'s config and changes no other key."""
    assert RequestModel.model_config.get("extra") == "forbid"
    assert ApiModel.model_config.get("extra") != "forbid"
    assert {k: v for k, v in RequestModel.model_config.items() if k != "extra"} == {
        k: v for k, v in ApiModel.model_config.items() if k != "extra"
    }


def test_no_response_model_is_a_request_model() -> None:
    """Responses stay on `ApiModel`: a response that gains a field must never break a client."""
    offenders: list[str] = []
    for route in _all_routes(LIVE_APP.routes):
        if not isinstance(route, APIRoute):
            continue
        for model in _models_in(route.response_model):
            for cls in (model, *_closure(model).values()):
                if issubclass(cls, RequestModel):
                    offenders.append(f"{_label(route)}: {cls.__name__}")
    assert not offenders, "a request model is returned as a response:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# (b) the wire
# ---------------------------------------------------------------------------


def _fill(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda match: PATH_VALUES[match.group(1)], path)


def _where_it_goes(name: str) -> tuple[str, tuple[str | int, ...]]:
    """The model of the route to send to, and the path inside its body to the model."""
    if name in VALID_BODIES:
        return name, ()
    assert name in NESTED_AT, f"{name}: write a valid body (VALID_BODIES) or a place (NESTED_AT)"
    return NESTED_AT[name]


async def _handler_ran_async(**_: Any) -> Response:
    """Stands in for a handler. Whatever gets here was not refused at the door."""
    return Response(status_code=204)


def _handler_ran(**_: Any) -> Response:
    return Response(status_code=204)


def _stop_the_handlers_running(app: FastAPI) -> None:
    """Replace every body route's handler of THIS app (the test's own, built per test)."""
    for route in _body_routes(app):
        original = route.dependant.call
        route.dependant.call = _handler_ran_async if iscoroutinefunction(original) else _handler_ran


async def _sign_up(client: AsyncClient) -> None:
    """A real session, for the routes that sit behind one. The owner of a new agency."""
    resp = await client.post(f"{BASE}/auth/sign-up", json=VALID_BODIES["SignUpRequest"])
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("name", MODEL_NAMES)
async def test_an_unknown_field_is_refused_with_a_422_that_names_it(
    client: AsyncClient, name: str
) -> None:
    if name in OPEN_MODELS:
        pytest.skip(OPEN_MODELS[name])
    root, where = _where_it_goes(name)
    valid = VALID_BODIES[root]
    refused = copy.deepcopy(valid)
    _at(refused, where)[UNKNOWN] = "anything"

    await _sign_up(client)
    _stop_the_handlers_running(client._transport.app)  # type: ignore[attr-defined]  # noqa: SLF001

    for route in MODELS[root].routes:
        label = _label(route)
        path = _fill(route.path)

        # The control: the body as written, without the unknown field, is let through.
        accepted = await client.request(_method(route), path, json=valid)
        assert accepted.status_code == 204, (
            f"{label}: the valid body was not accepted ({accepted.status_code}): {accepted.text}"
        )

        resp = await client.request(_method(route), path, json=refused)
        reached = "the handler was reached" if resp.status_code == 204 else resp.text
        assert resp.status_code == 422, (
            f"{label}: {name} did not refuse an unknown field ({resp.status_code}): {reached}"
        )
        problem = resp.json()
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert problem["type"] == "/problems/validation-failed"
        assert problem["errors"] == [
            {
                "field": ".".join(str(part) for part in (*where, UNKNOWN)),
                "message": NOT_ACCEPTED,
                "type": "extra_forbidden",
            }
        ], f"{label}: {problem['errors']}"


async def test_every_unknown_field_is_named_not_only_the_first(client: AsyncClient) -> None:
    await _sign_up(client)
    _stop_the_handlers_running(client._transport.app)  # type: ignore[attr-defined]  # noqa: SLF001
    body = {**VALID_BODIES["LoginRequest"], "userId": "user_x", "role": "owner", "plan": "pro"}

    resp = await client.post(f"{BASE}/auth/login", json=body)

    assert resp.status_code == 422
    assert [(e["field"], e["message"]) for e in resp.json()["errors"]] == [
        ("userId", NOT_ACCEPTED),
        ("role", NOT_ACCEPTED),
        ("plan", NOT_ACCEPTED),
    ]


def test_the_openapi_document_says_so_for_every_request_schema() -> None:
    """`additionalProperties: false` is how the contract tells a caller what the API will take."""
    schemas = LIVE_APP.openapi()["components"]["schemas"]
    missing = [name for name in MODEL_NAMES if name not in schemas]
    assert not missing, f"not in the OpenAPI document under their own name: {missing}"
    open_ones = [
        name
        for name in MODEL_NAMES
        if name not in OPEN_MODELS and schemas[name].get("additionalProperties") is not False
    ]
    assert not open_ones, f"the schema allows extra properties: {open_ones}"


# ---------------------------------------------------------------------------
# (c) the names
# ---------------------------------------------------------------------------


def _spellings(name: str, info: Any) -> set[str]:
    """Every name a field answers to on the wire, in snake case."""
    names = {name, info.alias, info.serialization_alias}
    if isinstance(info.validation_alias, str):
        names.add(info.validation_alias)
    return {to_snake(n) for n in names if isinstance(n, str)}


def _why_refused(spelling: str) -> str | None:
    """Why a field that answers to `spelling` (snake or camel) may not be in a request, or None.

    By SHAPE, so that a compound name or a synonym fails as well as the plain one: `new_role` as
    well as `role`, `requested_by_user_id` as well as `user_id`. A bare `id` is an id too.
    """
    name = to_snake(spelling)
    if name in EXACT_NAMES:
        return f"is {name!r}: {EXACT_NAMES[name]}"
    if name == "id" or name.endswith(NAME_ENDS):
        return "names an id, a time, an author, a hash or a subject"
    if name.startswith(NAME_STARTS):
        return "starts as a flag or a billing word does"
    for word in NAME_CONTAINS:
        if word in name:
            return f"contains {word!r}"
    return None


def _model_enums() -> list[type[enum.Enum]]:
    """Every enum defined in a module of `avp_api.models`, each once."""
    found: dict[str, type[enum.Enum]] = {}
    for info in pkgutil.iter_modules(avp_api.models.__path__, "avp_api.models."):
        module = importlib.import_module(info.name)
        for member in vars(module).values():
            if (
                isinstance(member, type)
                and issubclass(member, enum.Enum)
                and member.__module__.startswith("avp_api.models")
            ):
                found[f"{member.__module__}.{member.__qualname__}"] = member
    return list(found.values())


MODEL_ENUMS = _model_enums()


def _enums_in(annotation: Any) -> list[type[enum.Enum]]:
    """Every enum from `avp_api.models` an annotation names, however it is wrapped.

    A `Literal` whose values ALL belong to one such enum is that enum (A8b): `Literal["owner",
    "admin"]` is `UserRole` written out, and a field typed so would let a caller choose a role as
    surely as the enum would. Values that are not all one enum's (`Literal["owner", "pending"]`
    takes one from two) are no enum's, and a `Literal` of values no model enum holds is just a
    `Literal`. Every enum that holds them all is named.
    """
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return [annotation] if annotation.__module__.startswith("avp_api.models") else []
    if get_origin(annotation) is Literal:
        values = set(get_args(annotation))
        return [kind for kind in MODEL_ENUMS if values <= {member.value for member in kind}]
    return [found for arg in get_args(annotation) for found in _enums_in(arg)]


def _refusals(fname: str, info: Any) -> list[str]:
    """Why a field may not be in a request, by its name's shape and by its type; [] if it may."""
    reasons = [
        f"{spelling} {why}"
        for spelling in sorted(_spellings(fname, info))
        if (why := _why_refused(spelling))
    ]
    reasons += [f"its type is the enum {kind.__name__}" for kind in _enums_in(info.annotation)]
    return reasons


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_no_field_has_a_name_or_a_type_the_caller_must_not_choose(name: str) -> None:
    """At any depth: the model's own fields and every model nested in it.

    A request that carried one would let the caller choose what only the session may decide: whose
    agency the row belongs to, who created it, which plan or price it is on, how many seats there
    are, what it costs. `client_id` and `scan_id` are checked against the route's path as well: an
    id in the path and a second one in the body are two answers to one question.
    """
    found = MODELS[name]
    in_the_path = {
        to_snake(param)
        for route in _routes_reaching(name)
        for param in re.findall(r"\{(\w+)\}", route.path)
    }
    offenders: list[str] = []
    for cls in (found.cls, *found.nested.values()):
        for fname, info in cls.model_fields.items():
            if (cls.__name__, fname) in ALLOWED_FIELDS:
                continue
            reasons = _refusals(fname, info)
            reasons += [
                f"{spelling} is named by the path"
                for spelling in sorted(_spellings(fname, info))
                if spelling in in_the_path
            ]
            if reasons:
                offenders.append(f"{cls.__name__}.{fname}: {'; '.join(reasons)}")
    assert not offenders, f"{name} carries a field the caller must not choose: {offenders}"


# The review's examples. Each is refused in both spellings, and none is on any exact list.
REVIEW_EXAMPLES = [
    "requested_by_user_id", "new_role", "target_agency_id", "google_sub", "tenant_id",
    "is_owner", "email_verified", "expires_at", "stripe",
]
# What the first version of (c) listed by name: the shape rules cover all of it.
FIRST_LIST = [
    "agency_id", "user_id", "owner_id", "created_by", "invited_by_user_id", "role", "plan",
    "price", "price_id", "status", "seat_limit", "is_admin", "stripe_customer_id",
    "subscription_id", "subscription_status",
]
# Further names of each shape, and the one name an exact entry catches.
MORE_REFUSED = [
    "created_at", "password_hash", "billing_email", "trial_days", "monthly_limit", "plan_name",
    "price_cents", "account_status", "id", "trigger",
]
# A8b. The words added to the shape rules, alone and in compounds (nothing else in the rules catches
# any of these: none ends or starts as the other rules do), and the whole-word entries.
ADDED_WORDS = [
    "owner", "co_owner", "owner_email", "admin", "super_admin", "admin_note", "tenant",
    "tenant_name", "account", "account_manager", "user", "user_email", "super_user",
]
ADDED_EXACT = ["agency", "seats", "quota", "credits", "allowance", "exempt"]
# Names that only look like an exact entry: the entries are the whole word, so `agency_name`, a
# name the caller types, is not refused. The rest are near misses of the other entries.
NEAR_MISSES = [
    "agency_name", "agencyName", "seat", "credit", "exemption", "quotation", "allowances",
]
# Ordinary names, one of each kind the models use, so the rules are not simply "refuse everything".
ORDINARY = [
    "email", "password", "current_password", "new_password", "token", "ticket", "full_name",
    "agency_name", "url", "name", "domain", "classify", "enabled", "prompt", "competitors",
    "version", "client", "rivals", "dismissed", "sites", "rival_domain", "logo_url",
    "accent_color", "new_questions_next_scan",
]


@pytest.mark.parametrize(
    "name", REVIEW_EXAMPLES + FIRST_LIST + MORE_REFUSED + ADDED_WORDS + ADDED_EXACT
)
def test_the_name_rules_refuse_it_in_both_spellings(name: str) -> None:
    assert _why_refused(name), f"{name} is not refused"
    assert _why_refused(to_camel(name)), f"{to_camel(name)} is not refused"


@pytest.mark.parametrize("name", ADDED_WORDS)
def test_a_word_added_in_a8b_refuses_a_name_only_because_it_contains_it(name: str) -> None:
    """None of these is caught by an exact entry or by the start or end of the name."""
    reason = _why_refused(name)
    assert reason is not None and reason.startswith("contains "), reason


@pytest.mark.parametrize("name", ADDED_EXACT)
def test_an_exact_name_is_refused_for_being_that_name(name: str) -> None:
    assert (_why_refused(name) or "").startswith(f"is {name!r}"), _why_refused(name)
    assert name in EXACT_NAMES and EXACT_NAMES[name].strip()


@pytest.mark.parametrize("name", NEAR_MISSES)
def test_an_exact_name_is_the_whole_word_only(name: str) -> None:
    assert _why_refused(name) is None and _why_refused(to_camel(name)) is None


@pytest.mark.parametrize("name", ORDINARY)
def test_the_name_rules_let_an_ordinary_name_through(name: str) -> None:
    assert _why_refused(name) is None and _why_refused(to_camel(name)) is None


class _Colour(enum.Enum):
    RED = "red"


def test_the_enum_rule_sees_an_enum_of_the_models_however_it_is_wrapped() -> None:
    assert _enums_in(UserRole) == [UserRole]
    assert _enums_in(list[Engine] | None) == [Engine]
    assert _enums_in(Annotated[Engine, Field(description="which")]) == [Engine]
    assert _enums_in(str | None) == []
    assert _enums_in(_Colour) == []  # an enum, but not one of `avp_api.models`


def test_the_registry_of_the_models_enums_finds_them_all_once() -> None:
    names = [f"{kind.__module__}.{kind.__qualname__}" for kind in MODEL_ENUMS]

    assert len(names) == len(set(names))
    assert {UserRole, Engine, ScanStatus} <= set(MODEL_ENUMS)
    assert _Colour not in MODEL_ENUMS


def test_a_literal_whose_values_all_belong_to_one_model_enum_is_that_enum() -> None:
    """A8b. `Literal["owner", "admin"]` is `UserRole` written out, however it is wrapped."""
    assert _enums_in(Literal["owner", "admin"]) == [UserRole]
    assert _enums_in(Literal["member"]) == [UserRole]
    assert _enums_in(list[Literal["admin", "member"]] | None) == [UserRole]
    assert _enums_in(Annotated[Literal["owner"], Field(description="who")]) == [UserRole]
    # Every enum that holds all of them is named: "failed" is a status in three.
    assert {ScanStatus, PromptRunStatus, AuditStatus} <= set(_enums_in(Literal["failed"]))


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param(Literal["owner", "pending"], id="one value from two enums"),
        pytest.param(Literal["owner", "nobody"], id="one a model enum holds, one none does"),
        pytest.param(Literal["red"], id="no model enum holds it"),
        pytest.param(Literal[1, 2], id="numbers"),
        pytest.param(Literal["email", "google"], id="a response's sign-in methods"),
    ],
)
def test_a_literal_that_is_no_model_enums_values_is_not_one(annotation: Any) -> None:
    assert _enums_in(annotation) == []


def test_a_field_typed_as_a_literal_of_a_role_is_refused_by_its_type() -> None:
    class Hypothetical(RequestModel):
        kind: Literal["owner", "admin"]

    reasons = _refusals("kind", Hypothetical.model_fields["kind"])

    assert reasons == ["its type is the enum UserRole"], "`kind` is an ordinary name"


# ---------------------------------------------------------------------------
# populate_by_name is unchanged
# ---------------------------------------------------------------------------


def _snake_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {to_snake(key): _snake_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snake_keys(item) for item in value]
    return value


@pytest.mark.parametrize("name", ROOT_NAMES)
def test_both_spellings_of_a_declared_field_are_still_accepted(name: str) -> None:
    """Forbidding what is not declared must not forbid the snake_case name of what is."""
    body = VALID_BODIES[name]
    camel = MODELS[name].cls.model_validate(body)
    snake = MODELS[name].cls.model_validate(_snake_keys(body))
    assert camel == snake


# ---------------------------------------------------------------------------
# (d) a flood of keys, and what one problem document carries (M1)
#
# Pydantic builds one `extra_forbidden` error per unknown key at every depth, and a body is
# validated before any rate limit on the route runs. A 12 MB body of a million keys was a million
# error objects and an 80 MB answer on the one worker. `RequestModel` now refuses a dict with more
# than `len(model_fields) + 10` keys whole, before pydantic walks it; the renderer cuts the rest.
# ---------------------------------------------------------------------------

TOO_MANY_FIELDS = "This request has more fields than it takes."


def _field_at(where: tuple[str | int, ...]) -> str:
    """Where an error about the object at `where` is reported: its dotted path, or `body`."""
    return ".".join(str(part) for part in where) or "body"


async def _ready(client: AsyncClient) -> None:
    """A real session, then every body route's handler stood down (see the module docstring)."""
    await _sign_up(client)
    _stop_the_handlers_running(client._transport.app)  # type: ignore[attr-defined]  # noqa: SLF001


@pytest.mark.parametrize("name", MODEL_NAMES)
async def test_a_flood_of_unknown_keys_is_one_too_many_fields_error(
    client: AsyncClient, name: str
) -> None:
    """At the root of the body and, for the nested models, inside it: each is a request model."""
    if name in OPEN_MODELS:
        pytest.skip(OPEN_MODELS[name])
    root, where = _where_it_goes(name)
    flooded = copy.deepcopy(VALID_BODIES[root])
    target = _at(flooded, where)
    for i in range(len(MODELS[name].cls.model_fields) + EXTRA_KEYS_MARGIN + 1):
        target[f"unknown{i}"] = i

    await _ready(client)
    for route in MODELS[root].routes:
        resp = await client.request(_method(route), _fill(route.path), json=flooded)
        assert resp.status_code == 422, f"{_label(route)}: {resp.status_code} {resp.text[:200]}"
        assert resp.json()["errors"] == [
            {"field": _field_at(where), "message": TOO_MANY_FIELDS, "type": "too_many_fields"}
        ], _label(route)


@pytest.mark.parametrize("name", ["SignUpRequest", "CompetitorInput"])
async def test_ten_stray_keys_are_named_one_by_one_and_the_next_is_a_flood(
    client: AsyncClient, name: str
) -> None:
    """The margin is ten keys past the model's declared fields, at the root and when nested."""
    root, where = _where_it_goes(name)
    body = copy.deepcopy(VALID_BODIES[root])
    target = _at(body, where)
    room = len(MODELS[name].cls.model_fields) + 10 - len(target)  # keys the model still reads
    route = MODELS[root].routes[0]

    async def send() -> Any:
        return await client.request(_method(route), _fill(route.path), json=body)

    await _ready(client)
    assert (await send()).status_code == 204  # a valid body is untouched
    for i in range(room):
        target[f"stray{i}"] = i
    named = await send()
    assert named.status_code == 422
    assert "errorsTruncated" not in named.json(), "every stray key is in the list"
    assert named.json()["errors"] == [
        {
            "field": ".".join(str(part) for part in (*where, f"stray{i}")),
            "message": NOT_ACCEPTED,
            "type": "extra_forbidden",
        }
        for i in range(room)
    ]

    target["one_more"] = 1
    flood = await send()
    assert flood.status_code == 422
    assert [e["type"] for e in flood.json()["errors"]] == ["too_many_fields"]


# More than a setting may be set to (1 MiB, `config.REQUEST_BODY_LIMIT_RANGE`). Assigned to the
# settings object after it was made, which is not checked, to take the size limit out of the way.
NO_SIZE_LIMIT = 64 * 1024 * 1024


@pytest.fixture
def the_size_limit_out_of_the_way(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> int:
    """A8b. A body of 100,000 keys is 1.6 MB, and the default size limit (64 KiB, `body_limit.py`)
    now refuses it first, with a 413, before this guard sees it. The test below is about the guard,
    so the limit is lifted, past what a setting may be. It changes the `settings` the app is BUILT
    from, so the test lists it before `client`. `test_request_body_limit.py` holds the other half:
    that at the default the limit does refuse such a body, and the guard still answers one under it.
    """
    monkeypatch.setattr(settings, "max_request_body_bytes", NO_SIZE_LIMIT)
    return NO_SIZE_LIMIT


async def test_a_hundred_thousand_keys_are_answered_with_one_small_error(
    the_size_limit_out_of_the_way: int, client: AsyncClient
) -> None:
    await _ready(client)
    body = {**VALID_BODIES["LoginRequest"], **{f"key{i}": i for i in range(100_000)}}

    resp = await client.post(f"{BASE}/auth/login", json=body)

    assert resp.status_code == 422
    assert [e["type"] for e in resp.json()["errors"]] == ["too_many_fields"]
    assert "errorsTruncated" not in resp.json(), "one error is not a cut list"
    assert len(resp.content) < 1_000, f"the answer is {len(resp.content)} bytes"


async def test_a_problem_carries_the_first_twenty_errors_in_the_order_they_were_found(
    client: AsyncClient,
) -> None:
    """25 sites too long to be a domain: 25 distinct errors, `client.0` to `client.24`."""
    await _ready(client)
    body = {"client": ["d" * 300 for _ in range(25)], "rivals": [], "dismissed": []}

    resp = await client.put(f"{BASE}/clients/{PATH_VALUES['clientId']}/other-sites", json=body)

    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert [e["field"] for e in errors] == [f"client.{i}" for i in range(20)]
    assert {e["type"] for e in errors} == {"string_too_long"}
    assert resp.json()["errorsTruncated"] is True, "the list was cut and does not say so"


OTHER_SITES = f"{BASE}/clients/{PATH_VALUES['clientId']}/other-sites"


@pytest.mark.parametrize(
    ("count", "cut"),
    [(1, False), (19, False), (20, False), (21, True), (25, True), (50, True)],
    ids=["1", "19", "exactly the cap", "one past it", "25", "50, the longest list allowed"],
)
async def test_the_problem_says_so_when_the_cap_cut_the_list_and_only_then(
    client: AsyncClient, count: int, cut: bool
) -> None:
    """A8b. `errorsTruncated: true` is a top-level member of the problem: there when the 20-error
    cap dropped errors, absent otherwise. Exactly 20 errors is the whole list, not a cut one."""
    await _ready(client)
    body = {"client": ["d" * 300 for _ in range(count)], "rivals": [], "dismissed": []}

    resp = await client.put(OTHER_SITES, json=body)

    assert resp.status_code == 422
    problem = resp.json()
    assert len(problem["errors"]) == min(count, 20)
    assert set(problem) == {"type", "title", "status", "detail", "instance", "errors"} | (
        {"errorsTruncated"} if cut else set()
    )
    if cut:
        assert problem["errorsTruncated"] is True


async def test_a_list_past_the_schemas_bound_is_one_error_and_not_one_for_each_entry(
    client: AsyncClient,
) -> None:
    """A8b. The other-sites lists are cut off at 50 (`other_sites_bounds`). Past that, pydantic
    reports the length of the list and does not look at its entries: 51 sites, each too long to be
    a domain, are ONE `too_long` error, where 50 are fifty."""
    await _ready(client)
    body = {"client": ["d" * 300 for _ in range(51)], "rivals": [], "dismissed": []}

    resp = await client.put(OTHER_SITES, json=body)

    assert resp.status_code == 422
    problem = resp.json()
    assert [(e["field"], e["type"]) for e in problem["errors"]] == [("client", "too_long")]
    assert "errorsTruncated" not in problem


async def test_errors_cut_at_depth_say_so_too(client: AsyncClient) -> None:
    """10 rivals with 11 stray keys each are 110 `extra_forbidden` errors (a rival may carry 10
    keys beyond its 2 declared fields before it is a flood, and `name` + 11 is 12): 20 shown."""
    await _ready(client)
    rival = {"name": "Zendesk", **{f"stray{i}": i for i in range(11)}}
    body = {"competitors": [dict(rival) for _ in range(10)]}

    resp = await client.put(f"{BASE}/clients/{PATH_VALUES['clientId']}/competitors", json=body)

    assert resp.status_code == 422
    problem = resp.json()
    assert len(problem["errors"]) == 20
    assert {e["type"] for e in problem["errors"]} == {"extra_forbidden"}
    assert problem["errorsTruncated"] is True


async def test_a_problem_that_is_not_about_validation_never_has_the_member(
    client: AsyncClient,
) -> None:
    await _ready(client)

    resp = await client.get(f"{BASE}/clients/{PATH_VALUES['clientId']}/no-such-sub-route")

    assert resp.status_code == 404
    assert "errorsTruncated" not in resp.json()


@pytest.mark.parametrize(
    "prefix", ["", "competitors.0."], ids=["at the root of the body", "inside a nested object"]
)
async def test_a_long_unknown_key_is_echoed_cut_to_two_hundred_characters(
    client: AsyncClient, prefix: str
) -> None:
    key = "k" * 500
    if prefix:
        body: dict[str, Any] = {"competitors": [{"name": "Zendesk", key: 1}]}
        route = f"{BASE}/clients/{PATH_VALUES['clientId']}/competitors"
        send = client.put
    else:
        body = {**VALID_BODIES["LoginRequest"], key: 1}
        route = f"{BASE}/auth/login"
        send = client.post
    await _ready(client)

    resp = await send(route, json=body)

    assert resp.status_code == 422
    assert resp.json()["errors"] == [
        {"field": (prefix + key)[:200], "message": NOT_ACCEPTED, "type": "extra_forbidden"}
    ]
    assert len(resp.json()["errors"][0]["field"]) == 200


@pytest.mark.parametrize(
    "raw",
    [
        b'{"email":"a@b.co","password":"x","\\ud800":1}',
        b'{"email":"a@b.co","password":"\\ud800"}',
    ],
    ids=["a lone surrogate as a key", "a lone surrogate as a value"],
)
async def test_a_lone_surrogate_is_a_422_and_never_a_500(client: AsyncClient, raw: bytes) -> None:
    """The JSON escape as sent. A surrogate cannot be encoded as UTF-8, so one that reached the
    problem document would fail the renderer. Pydantic refuses the string first (`string_unicode`),
    so it never does. This holds that, with the transport set to hand back a 500, not raise it."""
    _stop_the_handlers_running(client._transport.app)  # type: ignore[attr-defined]  # noqa: SLF001
    transport = ASGITransport(
        app=client._transport.app,  # type: ignore[attr-defined]  # noqa: SLF001
        raise_app_exceptions=False,
    )
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": client.headers["Origin"]},
    ) as sender:
        resp = await sender.post(
            f"{BASE}/auth/login", content=raw, headers={"Content-Type": "application/json"}
        )

    assert resp.status_code in (400, 422), resp.status_code
    assert resp.json()["status"] == resp.status_code


# ---------------------------------------------------------------------------
# (e) no field reopens the bag (L2, and A8b)
#
# `extra="forbid"` refuses a key the model does not declare. A field typed `dict`, `Mapping`, `Any`
# or `Json` is a declared key that takes any shape inside it, so the refusal stops at its edge. So
# does a nested `@dataclass`, `TypedDict` or `NamedTuple`: pydantic checks the fields they name and
# drops the rest in silence, whatever the model around them says.
#
# The first version listed what is forbidden. This lists what is ALLOWED, so a type nobody thought
# of fails until somebody decides it may stay: a scalar, an enum, a `Literal`, a model (the nested
# ones are held to `RequestModel` by (a)), and the lists, sets, tuples, unions and `Annotated`
# wrappers around them. A container with no item type takes anything, so it fails too.
# ---------------------------------------------------------------------------

ALLOWED_CLASSES = (
    str, int, float, bool, bytes, Decimal, date, datetime, time, timedelta, UUID, EmailStr,
    enum.Enum, BaseModel,
)
CONTAINERS = (list, set, frozenset, tuple, Sequence, MutableSequence, AbstractSet, MutableSet)


def _type_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", None) or repr(annotation)


def _free_form_metadata(item: Any) -> list[str]:
    """`Json[...]` is `Annotated[..., Json()]`, and pydantic hands the `Json()` back as metadata."""
    return ["Json"] if isinstance(item, Json) else []


def _free_form_parts(annotation: Any) -> list[str]:
    """What in an annotation is not an allowed leaf or container, at any depth; [] if all is."""
    origin = get_origin(annotation)
    if origin is Annotated:
        inner, *metadata = get_args(annotation)
        return _free_form_parts(inner) + [
            part for item in metadata for part in _free_form_metadata(item)
        ]
    if origin is Literal or annotation is None or annotation is type(None):
        return []
    if origin in (typing.Union, types.UnionType):
        return [part for arg in get_args(annotation) for part in _free_form_parts(arg)]
    if origin in CONTAINERS:
        items = [arg for arg in get_args(annotation) if arg is not Ellipsis]
        if not items:  # `typing.Sequence` on its own: a sequence of anything
            return [f"a bare {_type_name(origin)}"]
        return [part for item in items for part in _free_form_parts(item)]
    if origin is not None:  # a dict, a Mapping, a `type[...]`: a generic that is no container
        return [_type_name(origin)]
    if annotation in CONTAINERS:
        return [f"a bare {_type_name(annotation)}"]
    if isinstance(annotation, type) and issubclass(annotation, ALLOWED_CLASSES):
        return []
    return [_type_name(annotation)]


def _free_form_in(info: Any) -> list[str]:
    """The same for a field: its type, and the metadata pydantic split off it."""
    parts = _free_form_parts(info.annotation)
    for item in info.metadata:
        parts += _free_form_metadata(item)
    return parts


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_no_field_has_a_free_form_type(name: str) -> None:
    """At any depth: the model's own fields and every model nested in it."""
    found = MODELS[name]
    offenders: list[str] = []
    for cls in (found.cls, *found.nested.values()):
        for fname, info in cls.model_fields.items():
            if (cls.__name__, fname) in OPEN_TYPE_FIELDS:
                continue
            if parts := _free_form_in(info):
                offenders.append(f"{cls.__name__}.{fname}: {parts}")
    assert not offenders, (
        f"{name} has a field that takes any shape inside it, or a type this check has not been "
        f"told is fine (add it to ALLOWED_CLASSES only if it cannot reopen the bag): {offenders}"
    )


@dataclass
class _Nested:
    x: int


class _Typed(typing.TypedDict):
    x: int


class _Pair(typing.NamedTuple):
    a: int


class _Strict(RequestModel):
    x: int


@pytest.mark.parametrize(
    ("annotation", "free_form"),
    [
        pytest.param(dict[str, int], True, id="dict[str, int]"),
        pytest.param(dict, True, id="bare dict"),
        pytest.param(typing.Dict[str, str], True, id="typing.Dict"),  # noqa: UP006
        pytest.param(typing.Mapping[str, int], True, id="typing.Mapping"),
        pytest.param(OrderedDict[str, int], True, id="OrderedDict"),
        pytest.param(Any, True, id="Any"),
        pytest.param(object, True, id="object"),
        pytest.param(list[dict[str, str]], True, id="a dict inside a list"),
        pytest.param(list[Any] | None, True, id="Any inside a list or None"),
        pytest.param(Json, True, id="bare Json"),
        pytest.param(Json[list[int]], True, id="Json[list[int]]"),
        pytest.param(list[Json[int]], True, id="Json inside a list"),
        # A8b: what the first version let through.
        pytest.param(_Nested, True, id="a nested dataclass"),
        pytest.param(list[_Nested], True, id="a dataclass in a list"),
        pytest.param(_Nested | None, True, id="a dataclass or None"),
        pytest.param(_Typed, True, id="a TypedDict"),
        pytest.param(list[_Typed], True, id="a TypedDict in a list"),
        pytest.param(_Pair, True, id="a NamedTuple"),
        pytest.param(list, True, id="bare list"),
        pytest.param(set, True, id="bare set"),
        pytest.param(tuple, True, id="bare tuple"),
        pytest.param(typing.Sequence, True, id="bare typing.Sequence"),
        pytest.param(type[str], True, id="a type"),
        pytest.param(typing.Callable[[int], int], True, id="a callable"),
        # What a request field may be.
        pytest.param(str, False, id="str"),
        pytest.param(str | None, False, id="str or None"),
        pytest.param(int | None, False, id="int or None"),
        pytest.param(bool, False, id="bool"),
        pytest.param(float, False, id="float"),
        pytest.param(Decimal, False, id="Decimal"),
        pytest.param(datetime, False, id="datetime"),
        pytest.param(UUID, False, id="UUID"),
        pytest.param(EmailStr, False, id="EmailStr"),
        pytest.param(list[Engine], False, id="list of an enum"),
        pytest.param(list[str], False, id="list of str"),
        pytest.param(set[str], False, id="set of str"),
        pytest.param(frozenset[int], False, id="frozenset of int"),
        pytest.param(tuple[int, ...], False, id="tuple of int"),
        pytest.param(tuple[int, str], False, id="tuple of two"),
        pytest.param(typing.Sequence[str], False, id="typing.Sequence of str"),
        pytest.param(list[list[str]], False, id="a list of lists"),
        pytest.param(Literal["a", "b"], False, id="Literal"),
        pytest.param(Annotated[str, Field(max_length=3)], False, id="Annotated str"),
        pytest.param(Annotated[list[str], Field(max_length=3)], False, id="Annotated list"),
        pytest.param(_Strict, False, id="a request model"),
        pytest.param(list[_Strict] | None, False, id="request models in a list or None"),
        pytest.param(None, False, id="None"),
    ],
)
def test_the_free_form_check_sees_what_it_is_meant_to_see(
    annotation: Any, free_form: bool
) -> None:
    assert bool(_free_form_parts(annotation)) is free_form


def test_the_free_form_check_names_what_it_found() -> None:
    assert _free_form_parts(dict[str, int]) == ["dict"]
    assert _free_form_parts(list[_Nested]) == ["_Nested"]
    assert _free_form_parts(_Typed | None) == ["_Typed"]
    assert _free_form_parts(list) == ["a bare list"]
    assert _free_form_parts(Json[list[int]]) == ["Json"]
    assert _free_form_parts(dict[str, _Nested]) == ["dict"], "the container is what is named"


# ---------------------------------------------------------------------------
# (f) nothing reads a request body by hand (L2, and A8b)
#
# A route whose body is read with `request.json()` has no model for the sweep above to find, so
# nothing forbids anything in it. This reads the source instead, and since A8b all of it: every
# `.py` file of the `avp_api` package, not only `routers/`. A dependency in `deps.py`, a service, a
# middleware that read the body would be found as surely as a route is. The exception is keyed by
# the file's path in the package, so a function of the same name elsewhere is not excused by it.
# ---------------------------------------------------------------------------

PACKAGE = Path(avp_api.__file__).parent
RAW_BODY_METHODS = frozenset({"json", "body", "form", "stream"})

# The code allowed to read a request body by hand: (file, as a path in the package, function) ->
# why. Each must still do it, or the entry is stale.
RAW_BODY_READS: dict[tuple[str, str], str] = {
    ("routers/billing.py", "stripe_webhook"): (
        "Stripe signs the exact bytes it sends, so the webhook verifies the signature over the raw "
        "body (services/billing.verify_webhook) before any of it is read as JSON"
    ),
}


def _is_request(annotation: ast.expr | None) -> bool:
    """`Request`, `fastapi.Request`, `starlette.requests.Request`: the last name is `Request`."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.rsplit(".", 1)[-1].strip() == "Request"
    if isinstance(annotation, ast.Name):
        return annotation.id == "Request"
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == "Request"
    return False


class _RawBodyReads(ast.NodeVisitor):
    """Every `.json()`, `.body()`, `.form()` or `.stream()` called on a request, and where."""

    def __init__(self) -> None:
        self.found: list[tuple[str, int]] = []
        self._scopes: list[tuple[str, frozenset[str]]] = []

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        outer = self._scopes[-1][1] if self._scopes else frozenset({"request", "req"})
        named = frozenset(a.arg for a in args if _is_request(a.annotation))
        self._scopes.append((node.name, outer | named))
        self.generic_visit(node)
        self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - the visitor's name
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._function(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in RAW_BODY_METHODS
            and isinstance(func.value, ast.Name)
        ):
            where, requests = (
                self._scopes[-1] if self._scopes else ("<module>", frozenset({"request", "req"}))
            )
            if func.value.id in requests:
                self.found.append((where, node.lineno))
        self.generic_visit(node)


def _raw_body_reads(source: str) -> list[tuple[str, int]]:
    visitor = _RawBodyReads()
    visitor.visit(ast.parse(source))
    return visitor.found


def _files_under(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _reads_in(root: Path) -> dict[tuple[str, str], list[int]]:
    """Every raw body read under `root`: (file as a path under `root`, function) -> its lines."""
    found: dict[tuple[str, str], list[int]] = {}
    for path in _files_under(root):
        for function, line in _raw_body_reads(path.read_text()):
            found.setdefault((path.relative_to(root).as_posix(), function), []).append(line)
    return found


def _unexpected(found: dict[tuple[str, str], list[int]]) -> dict[str, list[int]]:
    return {
        f"{file}:{function}": lines
        for (file, function), lines in found.items()
        if (file, function) not in RAW_BODY_READS
    }


def test_nothing_in_the_package_reads_a_request_body_by_hand_except_the_stripe_webhook() -> None:
    unexpected = _unexpected(_reads_in(PACKAGE))
    assert not unexpected, (
        f"code reads a request body by hand, so no model forbids anything in it: {unexpected}"
    )


def test_the_scan_covers_the_whole_package_and_not_only_the_routers() -> None:
    scanned = {path.relative_to(PACKAGE).as_posix() for path in _files_under(PACKAGE)}

    outside_the_routers = {"deps.py", "main.py", "origin_check.py", "services/billing.py"}
    assert outside_the_routers | {"routers/billing.py"} <= scanned
    assert len(scanned) >= 100, f"only {len(scanned)} files are scanned"


def test_a_dependency_that_read_the_body_would_be_found_and_refused(tmp_path: Path) -> None:
    """The case the routers-only scan missed: a dependency, a service and a middleware."""
    (tmp_path / "services").mkdir()
    (tmp_path / "deps.py").write_text(
        "from fastapi import Request\n\n\n"
        "async def body_dependency(request: Request) -> bytes:\n"
        "    return await request.body()\n"
    )
    (tmp_path / "services" / "audit.py").write_text(
        "async def log_it(request):\n    return await request.json()\n"
    )
    (tmp_path / "middleware.py").write_text(
        "from starlette.requests import Request\n\n\n"
        "class Reads:\n"
        "    async def __call__(self, scope, receive, send):\n"
        "        request = Request(scope, receive)\n"
        "        return [chunk async for chunk in request.stream()]\n"
    )

    found = _reads_in(tmp_path)

    assert set(found) == {
        ("deps.py", "body_dependency"),
        ("services/audit.py", "log_it"),
        ("middleware.py", "__call__"),
    }
    assert set(_unexpected(found)) == {
        "deps.py:body_dependency", "services/audit.py:log_it", "middleware.py:__call__",
    }


def test_the_exception_is_keyed_by_file_so_a_function_of_the_same_name_elsewhere_is_not_excused(
    tmp_path: Path,
) -> None:
    (tmp_path / "routers").mkdir()
    (tmp_path / "routers" / "billing.py").write_text(
        "async def stripe_webhook(request):\n    return await request.body()\n"
    )
    (tmp_path / "deps.py").write_text(
        "async def stripe_webhook(request):\n    return await request.body()\n"
    )

    unexpected = _unexpected(_reads_in(tmp_path))

    assert set(unexpected) == {"deps.py:stripe_webhook"}, "the webhook's own file is excused"


def test_the_raw_body_exception_still_exists_and_is_the_stripe_webhook() -> None:
    found = _reads_in(PACKAGE)
    stale = [key for key in RAW_BODY_READS if key not in found]
    assert not stale, f"RAW_BODY_READS names a read that is gone: {stale}"
    assert all(reason.strip() for reason in RAW_BODY_READS.values())
    webhook = [
        route
        for route in _all_routes(LIVE_APP.routes)
        if isinstance(route, APIRoute) and route.endpoint.__name__ == "stripe_webhook"
    ]
    assert [(r.path, sorted(r.methods)) for r in webhook] == [(f"{BASE}/billing/webhook", ["POST"])]


@pytest.mark.parametrize(
    "source",
    [
        "async def h(request: Request):\n    return await request.json()\n",
        "async def h(request: Request):\n    return await request.body()\n",
        "async def h(request: Request):\n    return await request.form()\n",
        "async def h(request: Request):\n    async for chunk in request.stream():\n        pass\n",
        "async def h(http_request: Request):\n    return await http_request.json()\n",
        "async def h(r: starlette.requests.Request):\n    return await r.body()\n",
        "async def h(request):\n    return await request.json()\n",
        "def outer(req: Request):\n    async def inner():\n        return await req.json()\n",
    ],
    ids=["json", "body", "form", "stream", "another name", "dotted type", "by name", "nested"],
)
def test_the_scanner_sees_a_body_read_by_hand(source: str) -> None:
    assert _raw_body_reads(source)


@pytest.mark.parametrize(
    "source",
    [
        "async def h(payload: SignUpRequest):\n    return payload.json()\n",
        "async def h(request: Request):\n    return request.cookies.get('x')\n",
        "async def h(request: Request):\n    return await session.stream(request.url)\n",
        "def h(resp):\n    return resp.json()\n",
    ],
    ids=["a model", "cookies", "a call that takes the request", "a response"],
)
def test_the_scanner_leaves_everything_else_alone(source: str) -> None:
    assert not _raw_body_reads(source)
