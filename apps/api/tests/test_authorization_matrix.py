"""The authorization matrix, generated from the live app's routes — A7.

One file that answers, for every operation the API serves, who may call it and what everyone else
is told. It reads `avp_api.main.app` (`app.routes` and `app.openapi()`) when it runs, never the
committed JSON, so a new route cannot escape it.

**The table.** `EXPECTED` classifies EVERY operation as PUBLIC (no session, by design), MEMBER (any
seat), ADMIN (owner or admin) or OWNER (owner only). The tests fail when a live operation is not in
the table (the message names it and says to classify it here), when the table names an operation
that is gone, and when the access derived from the route's dependencies differs from the table's.
The derivation walks `route.dependant` and compares the callables by identity with the ones
`avp_api.deps` defines (`current_principal`, and the guards inside `RequireAdmin` and
`RequireOwner`), so no source file is touched. A role guard the table does not know is reported as
UNRECOGNISED and fails the same way. Two operations are OWNER (billing checkout and the portal,
launch checklist 3.130); the derivation is also tried on routes built for it, so what it can tell
apart does not depend on what the live app happens to hold.

**What each class is held to**, with the two agencies built once for the module:

* every protected operation, signed out (no cookie, a well-formed cookie nobody was given, a
  session that was ended by logging out), answers `401 authentication-required`, whatever the body
  is (none, `{}`, or a valid one): the session is looked at before the body is. Everything carries
  the app's allowed `Origin`, so the Origin check is not what answers;
* every operation with an id in its path, called by agency A's OWNER with agency B's ids, is told
  what an id nobody was given is told (same status, type, title and detail as a made-up id of the
  same kind), shows none of B's ids, names or domains, and changes nothing, whatever the method:
  not one row of any table and not one key in Redis. A read can write (`GET /scans/{scanId}` fails
  a scan that was submitted and never claimed, and commits), so B has such a scan and A asks for
  it. The made-up id is also the control that the 404 is the agency check's and not something on
  the way to it (a body that does not validate, a feature that is off, a missing prompt set), and
  for a read B's own owner is the control that there is data to leak;
* the same for an id carried in a QUERY parameter (`scanId` on the answer gaps and the crawler
  access): A's own client, asked about B's scan, shows nothing of B;
* the same for a `cursor` (on `GET /clients`, `GET /clients/{clientId}/scans` and
  `GET /scans/{scanId}/results`): it is the id of the last row of the page before, and the routes
  compare it as text, so with one of B's ids as the cursor A is answered as it is for an id nobody
  was given that sorts beside B's, shows nothing of B, and writes nothing;
* the reads with no id in their path list the caller's own and none of the other agency's;
* every ADMIN or OWNER operation, called by a seat below the role with A's own ids, answers `403
  permission-denied` and changes nothing (an OWNER one is called as A's admin too, not only as A's
  member);
* a share token that was never issued, one that has ended and one that was withdrawn all give the
  same 404 on both public routes.

**What a refusal must not reach.** Every provider the API pays or calls (the engines, the prompt
writer, the sentiment model, the classifier, SerpApi's detection, the audit's browser, the fix
generator, Stripe) and the scan executor are replaced, for every test in this file, by a stand-in
that records the call and raises. A refusal that went on to do the work fails the test that made
the call, at its end, whatever the response said. Nothing in this file calls a provider.

**Why the two agencies are built once.** The suite's `_clean_state` empties every table after any
test that asks for the database, and these tests read what the module built, so it is overridden
here with a no-op and the module's fixture empties the tables itself when the last test is done.
The data is made through the API where the API makes it without a provider (sign-up, invitations,
clients, the share link, the competitor list, the other sites, the questions switch) and by rows
where only a scan makes it (a finished scan with its prompts, answers, score and audit, an alert,
a prompt run).

**Settings.** The module's app runs with the monthly rescan and the CSV export on, because with
either off its route answers everybody the 404 of an unknown path, and a 404 for that reason would
prove nothing about the agency check.

`tests/` is not a package, so the few helpers the other API tests share are written out again here.
"""

from __future__ import annotations

import enum
import inspect
import re
import secrets
import typing
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from ulid import ULID

from avp_api import db as db_module
from avp_api import deps, ids
from avp_api import main as api_main
from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.models import (
    AiCrawlerAccess,
    Alert,
    AlertKind,
    AuditStatus,
    Base,
    BrandMention,
    Citation,
    CitationType,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    PromptIntent,
    PromptRun,
    PromptRunStatus,
    PromptSet,
    Scan,
    ScanStatus,
    Score,
    ScoreStatus,
    Sentiment,
    TechnicalAudit,
    UserRole,
)
from avp_api.models.score import DEFAULT_WEIGHTS
from avp_api.security import new_session_token
from avp_api.services import audit_runner, fix_runner
from avp_api.services import billing as billing_service
from avp_api.services import competitors as detection
from avp_api.services import engines as engine_service
from avp_api.services import extraction as extraction_service
from avp_api.services import intake as intake_service
from avp_api.services import prompts as prompt_service
from avp_api.services.ai_crawlers import AccessVerdict, AgentPurpose, RuleSource
from avp_api.services.scan_executor import ScanJob
from avp_api.services.scoring import FORMULA_VERSION

LIVE_APP: FastAPI = api_main.app
# Every path in the table is written without it, as the API's own documentation does. A route that
# is not under it keeps its whole path here, so it is in no table and is reported as unclassified.
BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"


class Access(enum.Enum):
    PUBLIC = "public"
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"
    # Derived only, never in the table: the route sits behind a role guard this file does not
    # know (a `require_role(...)` written inline), so what it lets through cannot be read.
    UNRECOGNISED = "unrecognised"


PUBLIC, MEMBER, ADMIN, OWNER = Access.PUBLIC, Access.MEMBER, Access.ADMIN, Access.OWNER

# ---------------------------------------------------------------------------
# THE TABLE. Every operation the API serves, by method and path (without the base path).
#
# A route added to the API is not in here, and `test_every_live_operation_is_classified` fails and
# names it: classify it here, and say why if it is PUBLIC. A route that is gone is in here and
# `test_the_table_names_only_operations_that_exist` fails. The class is also checked against the
# code, not trusted: the access derived from the route's dependencies must be this one, and the
# HTTP tests call each operation as its class says it is held to.
# ---------------------------------------------------------------------------
EXPECTED: dict[tuple[str, str], Access] = {
    # --- PUBLIC: no session. Each has its own gate, or none to give (17) ---
    ("GET", "/health"): PUBLIC,
    ("GET", "/ready"): PUBLIC,
    ("GET", "/billing/plan"): PUBLIC,  # the plan on offer, for the pricing page
    ("POST", "/billing/webhook"): PUBLIC,  # Stripe's signature is the gate
    ("POST", "/reports/shared"): PUBLIC,  # the share token is the gate
    ("POST", "/reports/shared/pdf"): PUBLIC,  # the share token is the gate
    ("POST", "/auth/sign-up"): PUBLIC,
    ("POST", "/auth/login"): PUBLIC,
    ("POST", "/auth/reset-password/request"): PUBLIC,
    ("POST", "/auth/reset-password/confirm"): PUBLIC,  # the reset token is the gate
    ("POST", "/auth/invitations/accept"): PUBLIC,  # the invitation token is the gate
    ("GET", "/auth/google/start"): PUBLIC,
    ("GET", "/auth/google/callback"): PUBLIC,  # Google's state is the gate
    ("POST", "/auth/google/pending"): PUBLIC,
    ("POST", "/auth/google/complete"): PUBLIC,  # the sign-up ticket is the gate
    ("POST", "/auth/google/join"): PUBLIC,  # an invited address's ticket is the gate (3.126)
    ("POST", "/auth/google/decline"): PUBLIC,  # the same ticket, the other answer (3.126)
    # --- OWNER: the owner alone. What the agency pays, changed (2) ---
    # Founder decision of 2026-10-11 (launch checklist 3.130): the owner is the billing contact. An
    # admin reads the status (the ADMIN row below) and is refused these two.
    ("POST", "/agencies/{agencyId}/billing/checkout"): OWNER,
    ("POST", "/agencies/{agencyId}/billing/portal"): OWNER,
    # --- ADMIN: an owner or an admin. What the agency pays, read; branding and the seats (5) ---
    ("GET", "/agencies/{agencyId}/billing"): ADMIN,
    ("PATCH", "/agencies/{agencyId}/branding"): ADMIN,
    ("POST", "/agencies/{agencyId}/invitations"): ADMIN,
    ("GET", "/agencies/{agencyId}/seats"): ADMIN,
    ("DELETE", "/users/{userId}"): ADMIN,
    # --- MEMBER: any seat of the agency (45) ---
    ("POST", "/auth/logout"): MEMBER,
    ("POST", "/auth/logout-all"): MEMBER,
    ("GET", "/auth/me"): MEMBER,
    ("PUT", "/auth/me/scan-emails"): MEMBER,
    ("POST", "/auth/change-password"): MEMBER,
    ("GET", "/dashboard"): MEMBER,
    ("POST", "/dashboard/getting-started/dismiss"): MEMBER,
    ("GET", "/clients"): MEMBER,
    ("POST", "/clients"): MEMBER,
    ("GET", "/clients/{clientId}"): MEMBER,
    ("GET", "/clients/{clientId}/history"): MEMBER,
    ("GET", "/clients/{clientId}/answer-gaps"): MEMBER,
    ("GET", "/clients/{clientId}/ai-crawler-access"): MEMBER,
    ("POST", "/clients/{clientId}/reclassify"): MEMBER,
    ("GET", "/clients/{clientId}/auto-rescan"): MEMBER,
    # The handler refuses a member of this one (403): see `INLINE_ADMIN_GATES` below.
    ("PUT", "/clients/{clientId}/auto-rescan"): MEMBER,
    ("GET", "/clients/{clientId}/competitors"): MEMBER,
    ("PUT", "/clients/{clientId}/competitors"): MEMBER,
    ("POST", "/clients/{clientId}/competitors/detect"): MEMBER,
    ("GET", "/clients/{clientId}/rival-suggestions"): MEMBER,
    ("GET", "/clients/{clientId}/other-sites"): MEMBER,
    ("PUT", "/clients/{clientId}/other-sites"): MEMBER,
    ("GET", "/clients/{clientId}/questions"): MEMBER,
    ("PUT", "/clients/{clientId}/questions"): MEMBER,
    ("GET", "/clients/{clientId}/prompt-runs"): MEMBER,
    ("POST", "/clients/{clientId}/prompt-runs"): MEMBER,
    ("GET", "/clients/{clientId}/alerts"): MEMBER,
    ("GET", "/clients/{clientId}/scans"): MEMBER,
    ("POST", "/clients/{clientId}/scans"): MEMBER,
    ("POST", "/alerts/{alertId}/acknowledge"): MEMBER,
    ("GET", "/scans/{scanId}"): MEMBER,
    ("GET", "/scans/{scanId}/prompts"): MEMBER,
    ("GET", "/scans/{scanId}/results"): MEMBER,
    ("GET", "/scans/{scanId}/score"): MEMBER,
    ("POST", "/scans/{scanId}/score"): MEMBER,
    ("GET", "/scans/{scanId}/scores"): MEMBER,
    ("GET", "/scans/{scanId}/audit"): MEMBER,
    ("POST", "/scans/{scanId}/audit"): MEMBER,
    ("GET", "/scans/{scanId}/fixes"): MEMBER,
    ("POST", "/scans/{scanId}/fixes"): MEMBER,
    ("GET", "/scans/{scanId}/report"): MEMBER,
    ("GET", "/scans/{scanId}/report.csv"): MEMBER,
    ("GET", "/scans/{scanId}/report.pdf"): MEMBER,
    ("POST", "/scans/{scanId}/share"): MEMBER,
    ("DELETE", "/scans/{scanId}/share"): MEMBER,
}

# A body each protected operation that takes one accepts, written out. The HTTP tests send it where
# the status has to come from the access check (a cross-agency 404, a role's 403), so that a 422
# can never stand in for either, and the signed-out test sends it beside an empty one.
# `test_every_protected_operation_with_a_body_has_a_valid_one_in_the_table` fails when an operation
# is missing from here, and when a body here no longer validates against the request model.
VALID_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/clients"): {"url": "matrix-new-client.com", "classify": False},
    ("POST", "/clients/{clientId}/scans"): {},
    ("POST", "/clients/{clientId}/prompt-runs"): {
        "prompt": "Which studio should a small team choose?"
    },
    ("PUT", "/clients/{clientId}/auto-rescan"): {"enabled": True},
    ("PUT", "/clients/{clientId}/competitors"): {
        "competitors": [{"name": "Matrix Rival Company", "domain": "matrix-rival-company.com"}],
        "version": None,
    },
    ("PUT", "/clients/{clientId}/other-sites"): {"client": [], "rivals": [], "dismissed": []},
    ("PUT", "/clients/{clientId}/questions"): {"newQuestionsNextScan": False},
    ("PATCH", "/agencies/{agencyId}/branding"): {"accentColor": "#336699"},
    ("POST", "/agencies/{agencyId}/invitations"): {
        "email": "matrix-newcomer@matrix.example",
        "role": "member",
    },
    ("POST", "/auth/change-password"): {
        "currentPassword": PASSWORD,
        "newPassword": "another-long-passphrase",
    },
    ("PUT", "/auth/me/scan-emails"): {"enabled": True},
}

# Operations whose HANDLER refuses a seat that the dependencies let through, with the seat it
# refuses. The derivation cannot see these, so the table calls them MEMBER, and the role tests
# call each as that seat and expect the same 403 an ADMIN operation gives.
# `PUT .../auto-rescan` is "owner or admin" in its docstring and is checked in the handler
# (`_may_manage`), after the setting that switches the feature on.
INLINE_ADMIN_GATES: dict[tuple[str, str], str] = {
    ("PUT", "/clients/{clientId}/auto-rescan"): "member",
}

# The sweep must not shrink: a route removed on purpose lowers this number in the same commit, and
# a sweep that quietly found less (a router no longer included, a filter that skips a method) fails
# here instead of passing with fewer rows.
MINIMUM_OPERATIONS = 67

# Paths a route that is not an `APIRoute` may have: FastAPI's own documentation pages. They are
# served only where `docs_served` is on (local and test), and `create_app` says why. Anything else
# that is not an `APIRoute` (a mounted app, a websocket, a plain Starlette route) is a way around
# the dependencies this file reads, so it fails until somebody looks at it.
DOCUMENTATION_PATHS = frozenset({f"{BASE}/openapi.json", f"{BASE}/docs", "/docs/oauth2-redirect"})

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "options", "head", "trace"})
PATH_PARAMETER = re.compile(r"\{(\w+)\}")
# A query parameter that carries an id: `scanId` (the API's spelling) or `scan_id`, or the page
# `cursor`, which is the id of the last row of the page before it (`next_cursor`).
CURSOR = "cursor"
ID_PARAMETER = re.compile(rf"(?:[a-z]Id|_id|^{CURSOR})$")

# The id of each kind a path or a query parameter can carry, and the prefix a made-up one gets.
ID_KINDS: dict[str, str] = {
    "agencyId": ids.AGENCY,
    "alertId": ids.ALERT,
    "clientId": ids.CLIENT,
    "resultId": ids.ENGINE_RESULT,
    "scanId": ids.SCAN,
    "userId": ids.USER,
}

# The routes that take a `cursor`, and the kind of id it is on each: the id of the last row of the
# page before it, which the route compares as text (`id < cursor`, or `id > cursor`). A route that
# takes one and is not here fails `test_every_cursor_is_classified`.
CURSOR_KINDS: dict[tuple[str, str], str] = {
    ("GET", "/clients"): "clientId",
    ("GET", "/clients/{clientId}/scans"): "scanId",
    ("GET", "/scans/{scanId}/results"): "resultId",
}

# `deps.require_role` builds its guard inside the function, so each call makes a new callable with
# this qualified name. The two the app uses are the ones inside the aliases; any other one is a
# role this table has no word for.
ROLE_GUARD_QUALNAME = "require_role.<locals>._guard"
ADMIN_GUARD = typing.get_args(deps.RequireAdmin)[1].dependency
OWNER_GUARD = typing.get_args(deps.RequireOwner)[1].dependency
SESSION = deps.current_principal


# ---------------------------------------------------------------------------
# 1. The table against the live app. No database.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    """One method on one path of the live app, with what the rest of this file needs to call it."""

    method: str
    path: str  # without the base path, as in `EXPECTED`
    route: APIRoute
    access: Access  # DERIVED from the route's dependencies, not read from the table
    path_params: tuple[str, ...]  # the ids in the path, as the OpenAPI document names them
    query_ids: tuple[str, ...]  # the query parameters that carry an id
    has_body: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def expected(self) -> Access | None:
        return EXPECTED.get(self.key)


def _calls_beneath(dependant: Dependant) -> list[Callable[..., Any]]:
    """Every callable a request to this route resolves, at any depth."""
    found: list[Callable[..., Any]] = []
    for sub in dependant.dependencies:
        if sub.call is not None:
            found.append(sub.call)
        found.extend(_calls_beneath(sub))
    return found


def derive_access(route: APIRoute) -> Access:
    """Who the route's dependencies let through, read from the route and nothing else."""
    calls = _calls_beneath(route.dependant)
    if any(call is OWNER_GUARD for call in calls):
        return Access.OWNER
    if any(call is ADMIN_GUARD for call in calls):
        return Access.ADMIN
    if any(getattr(call, "__qualname__", "") == ROLE_GUARD_QUALNAME for call in calls):
        return Access.UNRECOGNISED
    if any(call is SESSION for call in calls):
        return Access.MEMBER
    return Access.PUBLIC


def live_operations(app: FastAPI = LIVE_APP) -> dict[tuple[str, str], Operation]:
    """Every operation the app serves now, from `app.routes` and `app.openapi()`.

    The routes give the handler's dependencies and the methods; the OpenAPI document gives each
    operation's parameters, the way a client reads them. A route that the document does not list
    has no parameters here, and `test_the_openapi_document_and_the_routes_agree` fails for it.
    """
    document = app.openapi()["paths"]
    found: dict[tuple[str, str], Operation] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            parameters = document.get(route.path, {}).get(method.lower(), {}).get("parameters", [])
            path = route.path.removeprefix(BASE)
            found.setdefault(
                (method, path),
                Operation(
                    method=method,
                    path=path,
                    route=route,
                    access=derive_access(route),
                    path_params=tuple(p["name"] for p in parameters if p["in"] == "path"),
                    query_ids=tuple(
                        p["name"]
                        for p in parameters
                        if p["in"] == "query" and ID_PARAMETER.search(p["name"])
                    ),
                    has_body=route.body_field is not None,
                ),
            )
    return found


OPERATIONS: list[Operation] = list(live_operations().values())


def _ops(*classes: Access) -> list[Operation]:
    """The live operations the TABLE puts in these classes, in route order."""
    return [op for op in OPERATIONS if op.expected in classes]


def _params(operations: list[Operation]) -> list[Any]:
    return [pytest.param(op, id=op.label) for op in operations]


PROTECTED = (MEMBER, ADMIN, OWNER)


def test_the_sweep_finds_the_whole_surface() -> None:
    found = live_operations()
    assert len(found) >= MINIMUM_OPERATIONS, (
        f"the sweep found {len(found)} operations, fewer than the {MINIMUM_OPERATIONS} there were "
        "when this file was written. If routes were removed on purpose, lower MINIMUM_OPERATIONS "
        "in the same commit; otherwise a router is no longer included or the sweep skips something."
    )
    seen: dict[tuple[str, str], str] = {}
    for route in LIVE_APP.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            key = (method, route.path.removeprefix(BASE))
            assert key not in seen, (
                f"{method} {route.path} is served by two routes ({seen[key]} and "
                f"{route.endpoint.__name__}); the second can never be reached, and this file "
                "would test only the first."
            )
            seen[key] = route.endpoint.__name__


def test_every_live_operation_is_classified() -> None:
    unclassified = sorted(f"{m} {p}" for (m, p) in live_operations() if (m, p) not in EXPECTED)
    assert not unclassified, (
        "these operations are served but not classified: "
        + ", ".join(unclassified)
        + ". Classify each in EXPECTED in tests/test_authorization_matrix.py as PUBLIC (say why, "
        "and what gates it instead), MEMBER, ADMIN or OWNER, and add its body to VALID_BODIES if "
        "it takes one."
    )


def test_the_table_names_only_operations_that_exist() -> None:
    gone = sorted(f"{m} {p}" for (m, p) in EXPECTED if (m, p) not in live_operations())
    assert not gone, (
        "EXPECTED names operations the app no longer serves: "
        + ", ".join(gone)
        + ". Remove them from the table (and lower MINIMUM_OPERATIONS if that is why the sweep is "
        "smaller)."
    )


def test_the_table_uses_only_the_four_classes() -> None:
    assert set(EXPECTED.values()) <= {PUBLIC, MEMBER, ADMIN, OWNER}


@pytest.mark.parametrize("op", _params(OPERATIONS))
def test_the_access_derived_from_the_routes_dependencies_is_the_tables(op: Operation) -> None:
    if op.expected is None:
        pytest.fail(f"{op.label} is not classified in EXPECTED (test_every_live_operation_...)")
    assert op.access is op.expected, (
        f"{op.label}: the table says {op.expected.value}, but the route's dependencies make it "
        f"{op.access.value}. Fix the route if it is the one that is wrong; the table is not "
        "changed to match the code without a decision."
    )


async def _needs_an_admin(principal: deps.RequireAdmin) -> None:
    """A dependency of its own that sits behind the admin guard, as a helper in a router would."""


def test_the_derivation_tells_the_four_classes_apart() -> None:
    """On routes built for it, so what the derivation can tell apart is proved on its own.

    The live app holds only two OWNER operations; the four classes are shown here whatever it holds.
    """
    built = FastAPI()

    @built.get("/open")
    async def open_to_all() -> None: ...

    @built.get("/seat")
    async def any_seat(principal: deps.PrincipalDep) -> None: ...

    @built.get("/admin")
    async def admins(principal: deps.RequireAdmin) -> None: ...

    @built.get("/owner")
    async def owners(principal: deps.RequireOwner) -> None: ...

    @built.get("/behind-a-helper")
    async def behind_a_helper(helper: Annotated[None, Depends(_needs_an_admin)]) -> None: ...

    @built.get("/a-role-of-its-own")
    async def another_role(
        principal: Annotated[deps.Principal, Depends(deps.require_role(UserRole.MEMBER))],
    ) -> None: ...

    @built.get("/both")
    async def both(a: deps.RequireAdmin, o: deps.RequireOwner) -> None: ...

    derived = {op.path: op.access.value for op in live_operations(built).values()}
    assert derived == {
        "/open": "public",
        "/seat": "member",
        "/admin": "admin",
        "/owner": "owner",
        "/behind-a-helper": "admin",
        "/a-role-of-its-own": "unrecognised",
        "/both": "owner",
    }


def test_no_role_guard_is_unknown_to_the_table() -> None:
    assert [op.label for op in OPERATIONS if op.access is Access.UNRECOGNISED] == [], (
        "a route is behind a role guard other than RequireAdmin and RequireOwner: teach "
        "derive_access (and the role tests) what it lets through"
    )


def test_every_route_is_an_api_route_or_a_documentation_page() -> None:
    odd = sorted(
        f"{type(route).__name__} {getattr(route, 'path', '?')}"
        for route in LIVE_APP.routes
        if not isinstance(route, APIRoute)
        and getattr(route, "path", None) not in DOCUMENTATION_PATHS
    )
    assert not odd, (
        "the app serves routes that are not API routes, so their dependencies are not read by "
        "this file: " + ", ".join(odd)
    )


def test_the_openapi_document_and_the_routes_agree() -> None:
    document = LIVE_APP.openapi()["paths"]
    from_document = {
        (method.upper(), path.removeprefix(BASE))
        for path, item in document.items()
        for method in item
        if method in HTTP_METHODS
    }
    from_routes = set(live_operations())
    assert from_document == from_routes, (
        f"in the OpenAPI document only: {sorted(from_document - from_routes)}; "
        f"served but not in the document: {sorted(from_routes - from_document)}"
    )
    for op in live_operations().values():
        in_template = tuple(PATH_PARAMETER.findall(op.path))
        assert sorted(in_template) == sorted(op.path_params), (
            f"{op.label}: the path names {in_template} and the document {op.path_params}"
        )


def test_the_sweep_finds_the_query_parameters_that_carry_an_id() -> None:
    found = {(op.label, name) for op in OPERATIONS for name in op.query_ids if name != CURSOR}
    assert len(found) >= 2, f"the sweep found {sorted(found)}; there were two when this was written"
    kinds = {name for _, name in found} | {n for op in OPERATIONS for n in op.path_params}
    assert not kinds - ID_KINDS.keys(), (
        f"add {sorted(kinds - ID_KINDS.keys())} to ID_KINDS and to Tenant.ids()"
    )


def test_every_cursor_is_classified() -> None:
    taking_one = {op.key for op in OPERATIONS if CURSOR in op.query_ids}
    unclassified = sorted(f"{m} {p}" for (m, p) in taking_one - CURSOR_KINDS.keys())
    assert not unclassified, (
        "these operations take a cursor and are not in CURSOR_KINDS: "
        + ", ".join(unclassified)
        + ". Say what kind of id the cursor is on each (the id of the last row of the page before "
        "it): the cursor test calls it with another agency's id of that kind."
    )
    gone = sorted(f"{m} {p}" for (m, p) in CURSOR_KINDS.keys() - taking_one)
    assert not gone, "CURSOR_KINDS names operations that take no cursor: " + ", ".join(gone)
    assert set(CURSOR_KINDS.values()) <= ID_KINDS.keys(), "a cursor kind ID_KINDS does not know"


def test_every_protected_operation_with_a_body_has_a_valid_one_in_the_table() -> None:
    wanted = {op.key for op in _ops(*PROTECTED) if op.has_body}
    missing = sorted(wanted - VALID_BODIES.keys())
    assert not missing, f"add a valid body to VALID_BODIES for: {missing}"
    stale = sorted(VALID_BODIES.keys() - wanted)
    assert not stale, f"VALID_BODIES has bodies for operations that take none or are gone: {stale}"
    by_key = {op.key: op for op in OPERATIONS}
    for key, body in VALID_BODIES.items():
        model = by_key[key].route.body_field.type_  # type: ignore[union-attr]
        model.model_validate(body)  # raises, naming the field, when the body no longer fits


def test_an_inline_gate_is_on_an_operation_the_table_calls_member() -> None:
    for key in INLINE_ADMIN_GATES:
        assert EXPECTED.get(key) is MEMBER, (
            f"{key}: it is listed as gated inline, so the table must call it MEMBER; if it moved "
            "behind RequireAdmin, remove it from INLINE_ADMIN_GATES"
        )


# ---------------------------------------------------------------------------
# 2. What a refusal must not reach.
# ---------------------------------------------------------------------------

# Everything that pays or calls a provider, or does work that a refusal must not have started.
# Each is replaced, for every test here, by a stand-in that records the call and raises.
PROVIDER_SEAMS: list[tuple[Any, str]] = [
    (prompt_service, "generate_prompts"),  # the model that writes a scan's questions
    (engine_service, "ask_all"),  # the engines: a scan's answers and an ad-hoc prompt's
    (extraction_service, "classify_sentiment"),  # the sentiment model
    (intake_service, "run_classification"),  # the crawl and the classifier
    (detection, "detect_for_client"),  # SerpApi and the co-citation prompts
    (audit_runner, "audit_site"),  # the audit's browser
    (fix_runner, "generate_for_scan"),  # the fix generator's model
    (billing_service, "create_checkout_session"),  # Stripe
    (billing_service, "create_portal_session"),  # Stripe
]
PROVIDER_CALLS: list[str] = []


class ProviderReached(AssertionError):  # noqa: N818 - an assertion, not a runtime error
    """A call that a refusal should have kept from happening."""


class RecordsTheStart:
    """The scan executor of this module's app: a scan started here is recorded and never run."""

    async def submit(self, job: ScanJob, *, settings: Settings) -> None:
        PROVIDER_CALLS.append(f"a scan was started ({job.scan_id})")


@pytest.fixture(autouse=True)
def no_provider_is_reached(
    monkeypatch: pytest.MonkeyPatch, stub_chain_externals: None
) -> Iterator[None]:
    """Replace every provider seam, for every test here, by one that records the call and raises.

    Asks for `stub_chain_externals` so that this runs after it and wins where both replace a seam.
    The check is at the end of the test, so a route that swallows the error still fails it.
    """
    PROVIDER_CALLS.clear()

    def refusing(module: Any, name: str) -> Callable[..., Any]:
        label = f"{module.__name__.rsplit('.', 1)[-1]}.{name}"

        def refuse_now(*args: Any, **kwargs: Any) -> Any:
            PROVIDER_CALLS.append(label)
            raise ProviderReached(f"{label} was called")

        async def refuse_later(*args: Any, **kwargs: Any) -> Any:
            refuse_now()

        return refuse_later if inspect.iscoroutinefunction(getattr(module, name)) else refuse_now

    for module, name in PROVIDER_SEAMS:
        monkeypatch.setattr(module, name, refusing(module, name))
    yield
    reached = list(PROVIDER_CALLS)
    PROVIDER_CALLS.clear()
    assert not reached, f"a request reached what a refusal must not reach: {reached}"


# ---------------------------------------------------------------------------
# 3. The two agencies, built once for the module.
# ---------------------------------------------------------------------------

# Words nothing else in the API says, so a body can be searched for them.
SPECS: dict[str, dict[str, str]] = {
    "A": {
        "agency": "Alpha Atlas Partners",
        "owner": "alpha-owner@alpha-atlas-partners.example",
        "admin": "alpha-admin@alpha-atlas-partners.example",
        "member": "alpha-member@alpha-atlas-partners.example",
        "client": "Alpha Atlas Studio",
        "domain": "alpha-atlas-studio.com",
        "rival": "Anvil Rival Company",
        "rival_domain": "anvil-rival-company.com",
        "other_site": "alpha-atlas-studio.net",
    },
    "B": {
        "agency": "Bravo Basalt Partners",
        "owner": "bravo-owner@bravo-basalt-partners.example",
        "admin": "bravo-admin@bravo-basalt-partners.example",
        "member": "bravo-member@bravo-basalt-partners.example",
        "client": "Bravo Basalt Works",
        "domain": "bravo-basalt-works.com",
        "rival": "Bedrock Rival Company",
        "rival_domain": "bedrock-rival-company.com",
        "other_site": "bravo-basalt-works.net",
    },
}


@dataclass
class Person:
    """One seat, signed in: its own client, so its own cookie."""

    http: AsyncClient
    id: str
    email: str


@dataclass
class Tenant:
    """One agency with its three seats and the data a leak would show."""

    label: str
    spec: Mapping[str, str]
    agency_id: str
    owner: Person
    admin: Person
    member: Person
    client_id: str
    scan_id: str  # SUCCEEDED, scored, audited, with a prompt and an answer
    older_scan_id: str  # the alert's baseline
    # QUEUED, submitted two days ago and never claimed: the one row a READ of a scan changes, since
    # `GET /scans/{scanId}` fails it on the spot (the lazy reap) and commits.
    stranded_scan_id: str
    result_id: str  # the answer in `scan_id`: what a results page's `cursor` is
    alert_id: str
    prompt_run_id: str
    share_token: str  # the live link of `scan_id`

    def ids(self) -> dict[str, str]:
        """The id of each kind this agency has, as a path or a query parameter names it.

        `userId` is the ADMIN: a seat that is neither the owner (whose removal is refused as the
        last owner's) nor the member that the role tests sign in as.
        """
        return {
            "agencyId": self.agency_id,
            "alertId": self.alert_id,
            "clientId": self.client_id,
            "resultId": self.result_id,
            "scanId": self.scan_id,
            "userId": self.admin.id,
        }

    def needles(self) -> list[str]:
        """Everything that names this agency or its data: none of it may reach another agency."""
        seats = (self.owner, self.admin, self.member)
        return [
            self.agency_id, self.spec["agency"], self.client_id, self.spec["client"],
            self.spec["domain"], self.scan_id, self.older_scan_id, self.stranded_scan_id,
            self.result_id, self.alert_id, self.prompt_run_id, self.share_token,
            self.spec["rival"], self.spec["rival_domain"],
            *(p.id for p in seats), *(p.email for p in seats),
        ]


@dataclass
class World:
    a: Tenant
    b: Tenant
    app: FastAPI
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    origin: str
    cookie_key: str
    revoked_token: str  # a session that was ended by `POST /auth/logout`
    _transport: ASGITransport
    _opened: list[AsyncClient] = field(default_factory=list)

    def client(self, *, cookie: str | None = None) -> AsyncClient:
        """A new client that sends the app's allowed Origin, `cookie` being its session if given."""
        headers = {"Origin": self.origin}
        if cookie is not None:
            headers["Cookie"] = f"{self.cookie_key}={cookie}"
        http = AsyncClient(transport=self._transport, base_url="http://testserver", headers=headers)
        self._opened.append(http)
        return http


@pytest.fixture(scope="module")
def matrix_settings(settings: Settings) -> Settings:
    """The suite's settings, with the two features on that answer everybody 404 when they are off.

    And with no email provider, whatever the shell holds: without one an invitation's link comes
    back to the person who made it, which is how the seats below are accepted.
    """
    return settings.model_copy(
        update={"auto_rescan_enabled": True, "report_csv_enabled": True, "resend_api_key": None}
    )


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Overrides the suite's: the two agencies must outlive the test that first asks for them.

    The suite's version empties every table after any test that asks for the database, and every
    test here does, through `world`. The `world` fixture empties them itself when the module ends.
    """


async def _empty_the_stores(settings: Settings) -> None:
    """What the suite's `_clean_state` does after a test: every table, and Redis database 15."""
    engine = db_module._engine
    assert engine is not None
    names = ", ".join(f'"{t}"' for t in Base.metadata.tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()


async def _seat(
    owner: Person, agency_id: str, email: str, role: str, new: Callable[[], AsyncClient]
) -> Person:
    """Invite `email` as the owner and accept as that person: the product's own way to a seat."""
    invited = await owner.http.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": role}
    )
    assert invited.status_code == 201, invited.text
    # No email is configured, so the link comes back to the inviter, token in the fragment.
    token = invited.json()["acceptUrl"].split("#", 1)[1]
    http = new()
    accepted = await http.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": f"{role.title()} Person", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    return Person(http, accepted.json()["user"]["id"], email)


async def _finished_scans(
    factory: async_sessionmaker[AsyncSession],
    *,
    spec: Mapping[str, str],
    agency_id: str,
    client_id: str,
    owner_id: str,
) -> tuple[str, str, str, str, str]:
    """A baseline scan, and a newer one with everything a report and its screens read.

    Rows, because only a scan makes these and a scan calls the providers. Returns the newer scan's
    id, the baseline's, an alert on the newer one, a prompt run of the client and the id of the
    answer in the newer scan.
    """
    now = datetime.now(UTC)

    def scan(when: datetime) -> Scan:
        # The id is made from the time the scan ran, so the older scan's id sorts first (ids made
        # in the same millisecond would sort in no order, and both rows get one `created_at`).
        return Scan(
            id=f"{ids.SCAN}_{ULID.from_datetime(when)}", client_id=client_id, agency_id=agency_id,
            requested_by_user_id=owner_id, status=ScanStatus.SUCCEEDED, started_at=when,
            finished_at=when + timedelta(minutes=5), formula_version=FORMULA_VERSION,
            prompt_count=1, engine_result_count=1, engine_versions={"chatgpt": "stub"},
        )

    async with factory() as s:
        older, newer = scan(now - timedelta(days=3)), scan(now - timedelta(hours=1))
        s.add(older)
        await s.flush()
        s.add(newer)
        await s.flush()

        prompt_set = PromptSet(
            id=ids.new_id(ids.PROMPT_SET), scan_id=newer.id, generated_by="stub",
            generation_params={}, prompts=[],
        )
        s.add(prompt_set)
        await s.flush()
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=prompt_set.id,
            text="Which studio should a small team choose?",
            intent=PromptIntent.COMPARISON, position=1,
        )
        s.add(prompt)
        await s.flush()
        answer = EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT), scan_id=newer.id, prompt_id=prompt.id,
            engine=Engine.CHATGPT, engine_version="stub", status=EngineResultStatus.OK,
            mentioned=True, position=1, prominence=Decimal("0.900"),
            sentiment=Sentiment.POSITIVE, sentiment_confidence=Decimal("0.900"),
            brands_mentioned=2, latency_ms=5, citations=[], brand_mentions=[],
        )
        s.add(answer)
        await s.flush()
        s.add_all([
            BrandMention(
                id=ids.new_id(ids.BRAND_MENTION), engine_result_id=answer.id,
                entity_name=spec["client"], entity_domain=spec["domain"], is_subject=True,
                position=1,
            ),
            BrandMention(
                id=ids.new_id(ids.BRAND_MENTION), engine_result_id=answer.id,
                entity_name=spec["rival"], entity_domain=spec["rival_domain"],
                is_subject=False, position=2,
            ),
            Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=answer.id,
                source_domain=spec["domain"], source_url=f"https://{spec['domain']}/about",
                source_type=CitationType.OWNED, position=1, cites_subject=True,
            ),
            Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=answer.id,
                source_domain=spec["rival_domain"], source_url=f"https://{spec['rival_domain']}/",
                source_type=CitationType.COMPETITOR, position=2, cites_subject=False,
            ),
            Score(
                id=ids.new_id(ids.SCORE), scan_id=newer.id, status=ScoreStatus.SCORED,
                composite=Decimal("61.50"), mention_rate=Decimal("100.00"),
                share_of_voice=Decimal("50.00"), citation_strength=Decimal("50.00"),
                sentiment=Decimal("80.00"), technical_foundation=Decimal("70.00"),
                formula_version=FORMULA_VERSION, weights=dict(DEFAULT_WEIGHTS),
                excluded_dimensions={}, degradation_flags=[], inputs_digest="a" * 64,
                computed_at=now,
            ),
        ])
        audit = TechnicalAudit(
            id=ids.new_id(ids.TECHNICAL_AUDIT), scan_id=newer.id,
            url_audited=f"https://{spec['domain']}", status=AuditStatus.OK,
            technical_foundation=Decimal("70.00"), audited_at=now, pages_crawled=1,
            schema_types=["Organization"], has_organization_schema=True, is_indexable=True,
            robots_allows_crawl=True, has_sitemap=True, canonical_present=True, h1_count=1,
            word_count=900, content_age_days=10,
        )
        s.add(audit)
        await s.flush()
        s.add(AiCrawlerAccess(
            id=ids.new_id(ids.AI_CRAWLER_ACCESS), audit_id=audit.id, agent_token="GPTBot",
            vendor="OpenAI", purpose=AgentPurpose.TRAINING, verdict=AccessVerdict.BLOCKED,
            rule_source=RuleSource.EXPLICIT, matched_token="gptbot", disallow_rules=1,
        ))
        alert = Alert(
            id=ids.new_id(ids.ALERT), client_id=client_id, agency_id=agency_id,
            kind=AlertKind.OWNED_CITATION_LOST, scan_id=newer.id, baseline_scan_id=older.id,
            detail="No answer cited the client this scan.",
        )
        run = PromptRun(
            id=ids.new_id(ids.PROMPT_RUN), client_id=client_id, agency_id=agency_id,
            asked_by_user_id=owner_id, prompt_text="Which studio do rivals prefer?",
            status=PromptRunStatus.OK, subject_name=spec["client"],
            subject_domain=spec["domain"], results=[],
        )
        s.add_all([alert, run])
        await s.commit()
        return newer.id, older.id, alert.id, run.id, answer.id


async def _stranded_scan(
    factory: async_sessionmaker[AsyncSession], *, agency_id: str, client_id: str, owner_id: str
) -> str:
    """A scan that was submitted and never claimed: QUEUED, `submitted_at` long past the window.

    The one row a read of a scan changes: `GET /scans/{scanId}` fails it on the spot
    (`fail_never_started_scans`, the lazy reap) and commits. Dated two days back in its id and its
    `created_at` too, so it is never the newest or the latest scan of anything, and the other
    tests' idea of "the latest scan" is the one they had without it.
    """
    when = datetime.now(UTC) - timedelta(days=2)
    scan = Scan(
        id=f"{ids.SCAN}_{ULID.from_datetime(when)}", client_id=client_id, agency_id=agency_id,
        requested_by_user_id=owner_id, status=ScanStatus.QUEUED, created_at=when,
        submitted_at=when, formula_version=FORMULA_VERSION,
    )
    async with factory() as s:
        s.add(scan)
        await s.commit()
    return scan.id


async def _tenant(
    label: str, *, new: Callable[[], AsyncClient], factory: async_sessionmaker[AsyncSession]
) -> Tenant:
    spec = SPECS[label]
    http = new()
    signed_up = await http.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": spec["agency"], "fullName": "Owner Person",
            "email": spec["owner"], "password": PASSWORD,
        },
    )
    assert signed_up.status_code == 201, signed_up.text
    agency_id = signed_up.json()["agency"]["id"]
    owner = Person(http, signed_up.json()["user"]["id"], spec["owner"])
    admin = await _seat(owner, agency_id, spec["admin"], "admin", new)
    member = await _seat(owner, agency_id, spec["member"], "member", new)

    made = await http.post(
        f"{BASE}/clients", json={"url": spec["domain"], "name": spec["client"], "classify": False}
    )
    assert made.status_code == 201, made.text
    client_id = made.json()["id"]

    scan_id, older_id, alert_id, run_id, result_id = await _finished_scans(
        factory, spec=spec, agency_id=agency_id, client_id=client_id, owner_id=owner.id
    )
    stranded_id = await _stranded_scan(
        factory, agency_id=agency_id, client_id=client_id, owner_id=owner.id
    )

    # Through the API, which keeps its own invariants (the list's version, the other sites' rules).
    shared = await http.post(f"{BASE}/scans/{scan_id}/share")
    assert shared.status_code == 200, shared.text
    listed = await http.put(
        f"{BASE}/clients/{client_id}/competitors",
        json={
            "competitors": [{"name": spec["rival"], "domain": spec["rival_domain"]}],
            "version": None,
        },
    )
    assert listed.status_code == 200, listed.text
    sites = await http.put(
        f"{BASE}/clients/{client_id}/other-sites",
        json={"client": [spec["other_site"]], "rivals": [], "dismissed": []},
    )
    assert sites.status_code == 200, sites.text
    questions = await http.put(
        f"{BASE}/clients/{client_id}/questions", json={"newQuestionsNextScan": True}
    )
    assert questions.status_code == 200, questions.text

    return Tenant(
        label=label, spec=spec, agency_id=agency_id, owner=owner, admin=admin, member=member,
        client_id=client_id, scan_id=scan_id, older_scan_id=older_id, stranded_scan_id=stranded_id,
        result_id=result_id, alert_id=alert_id, prompt_run_id=run_id,
        share_token=shared.json()["token"],
    )


@pytest_asyncio.fixture(scope="module")
async def world(engine: AsyncEngine, matrix_settings: Settings) -> AsyncIterator[World]:
    """Agency A and agency B, each with an owner, an admin, a member and the data a leak would show.

    Wired as the suite's `client` fixture wires its app (the test database and Redis, the settings
    above), with one difference: the scan executor records a start and runs nothing.
    """
    get_settings.cache_clear()
    db_module._engine = engine
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db_module._sessionmaker = factory
    redis_module._pool = None
    app = api_main.create_app(matrix_settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_settings] = lambda: matrix_settings
    app.dependency_overrides[deps.redis_client] = lambda: redis_module.get_redis(matrix_settings)
    app.dependency_overrides[deps.scan_executor] = lambda: RecordsTheStart()
    app.dependency_overrides[deps.db_session] = _session

    # An error inside the app comes back as the 500 a caller would get, so the test that made the
    # request says what it was and the stand-in above says what it reached.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    origin = matrix_settings.cors_allow_origins[0]
    opened: list[AsyncClient] = []

    def new() -> AsyncClient:
        http = AsyncClient(
            transport=transport, base_url="http://testserver", headers={"Origin": origin}
        )
        opened.append(http)
        return http

    try:
        a = await _tenant("A", new=new, factory=factory)
        b = await _tenant("B", new=new, factory=factory)

        # A session that was ended the way a person ends one: sign in again, then sign out.
        stale = new()
        login = await stale.post(
            f"{BASE}/auth/login", json={"email": a.member.email, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        revoked = stale.cookies[matrix_settings.session_cookie_key]
        assert (await stale.post(f"{BASE}/auth/logout")).status_code == 204

        yield World(
            a=a, b=b, app=app, settings=matrix_settings, engine=engine, sessions=factory,
            origin=origin, cookie_key=matrix_settings.session_cookie_key, revoked_token=revoked,
            _transport=transport, _opened=opened,
        )
    finally:
        for http in opened:
            await http.aclose()
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await _empty_the_stores(matrix_settings)


# ---------------------------------------------------------------------------
# 4. Calling an operation, and looking at what came back.
# ---------------------------------------------------------------------------

NO_BODY = object()


def url_of(op: Operation, with_ids: Mapping[str, str]) -> str:
    def the_id(match: re.Match[str]) -> str:
        if match.group(1) not in with_ids:
            pytest.fail(f"{op.label}: no id called {match.group(1)!r}; add it to Tenant.ids()")
        return with_ids[match.group(1)]

    return BASE + PATH_PARAMETER.sub(the_id, op.path)


async def call(
    http: AsyncClient,
    op: Operation,
    with_ids: Mapping[str, str],
    body: Any = NO_BODY,
    params: Mapping[str, str] | None = None,
) -> Response:
    if body is NO_BODY:
        return await http.request(op.method, url_of(op, with_ids), params=params)
    return await http.request(op.method, url_of(op, with_ids), json=body, params=params)


def _body_for(op: Operation) -> Any:
    """The valid body of an operation that takes one, or none."""
    if not op.has_body:
        return NO_BODY
    if op.key not in VALID_BODIES:
        pytest.fail(f"{op.label} takes a body and VALID_BODIES has none for it")
    return VALID_BODIES[op.key]


def _describe(body: Any) -> str:
    return "no body" if body is NO_BODY else f"body {body!r}"[:80]


def problem_of(resp: Response) -> dict[str, Any]:
    """The problem document, without `instance` (it is the path the caller sent, id included)."""
    assert resp.headers["content-type"].split(";")[0] == "application/problem+json", resp.text
    return {k: v for k, v in resp.json().items() if k != "instance"}


def problem_type(resp: Response) -> str | None:
    if resp.headers.get("content-type", "").split(";")[0] != "application/problem+json":
        return None
    return str(resp.json().get("type"))


def made_up(kind: str) -> str:
    """An id of this kind that was never issued."""
    if kind not in ID_KINDS:
        pytest.fail(f"no id called {kind!r}; add it to ID_KINDS and to Tenant.ids()")
    return ids.new_id(ID_KINDS[kind])


async def database_state(engine: AsyncEngine) -> dict[str, list[tuple[str, ...]]]:
    """Every row of every table, as text and in a fixed order: what a refusal must leave alone."""
    state: dict[str, list[tuple[str, ...]]] = {}
    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = (await conn.execute(select(table))).all()
            state[table.name] = sorted(tuple(repr(v) for v in row) for row in rows)
    return state


async def redis_state(settings: Settings) -> dict[str, Any]:
    """Every key of Redis database 15 with its value: the sessions and the rate-limit counters."""
    redis = redis_module.get_redis(settings)
    state: dict[str, Any] = {}
    async for key in redis.scan_iter("*"):
        kind = await redis.type(key)
        if kind == "string":
            state[key] = await redis.get(key)
        elif kind == "set":
            state[key] = sorted(await redis.smembers(key))
        elif kind == "hash":
            state[key] = await redis.hgetall(key)
        elif kind == "zset":
            state[key] = await redis.zrange(key, 0, -1, withscores=True)
        elif kind == "list":
            state[key] = await redis.lrange(key, 0, -1)
        else:
            state[key] = kind
    return state


def what_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))


async def test_the_two_agencies_are_built_as_designed(world: World) -> None:
    """Each agency's owner reads its own data, so a 404 below is about the OTHER agency's ids."""
    assert set(live_operations(world.app)) == set(live_operations()), (
        "the app these tests call does not serve the operations the table describes"
    )
    for tenant in (world.a, world.b):
        for kind, value in tenant.ids().items():
            assert ids.is_valid(value, ID_KINDS[kind]), (tenant.label, kind, value)
        mine = tenant.owner.http
        assert (await mine.get(f"{BASE}/clients/{tenant.client_id}")).status_code == 200
        assert (await mine.get(f"{BASE}/scans/{tenant.scan_id}/score")).status_code == 200
        report = await mine.get(f"{BASE}/scans/{tenant.scan_id}/report")
        assert report.status_code == 200, report.text
        assert report.json()["score"]["status"] == "scored"
        assert tenant.spec["domain"] in report.text
        link = await world.client().post(
            f"{BASE}/reports/shared", json={"token": tenant.share_token}
        )
        assert link.status_code == 200, link.text
        assert (await mine.get(f"{BASE}/clients/{tenant.client_id}/competitors")).status_code == 200
        assert (await mine.get(f"{BASE}/clients/{tenant.client_id}/alerts")).json()["alerts"]
    # They share nothing: no id, no name, no domain.
    assert not set(world.a.needles()) & set(world.b.needles())
    ended = world.client(cookie=world.revoked_token)
    assert (await ended.get(f"{BASE}/auth/me")).status_code == 401


# ---------------------------------------------------------------------------
# 5. Signed out: every protected operation is a 401, before anything else is looked at.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", _params(_ops(*PROTECTED)))
async def test_signed_out_a_protected_operation_answers_401(op: Operation, world: World) -> None:
    """No cookie, a well-formed cookie nobody was given, a session that was ended: all `401`.

    Whatever the body is: none, `{}` (which a route that takes one would call invalid) or a valid
    one. A `422` here would mean the body was read before the session, which tells a stranger what
    the route takes. The ids are agency B's, so a `404` would also say what exists.
    """
    callers = {
        "no cookie": world.client(),
        "a session never issued": world.client(cookie=new_session_token()),
        "a session ended by logging out": world.client(cookie=world.revoked_token),
    }
    bodies = [NO_BODY, {}, _body_for(op)] if op.has_body else [NO_BODY]
    wrong: list[str] = []
    for state, http in callers.items():
        for body in bodies:
            resp = await call(http, op, world.b.ids(), body)
            if resp.status_code != 401 or problem_type(resp) != "/problems/authentication-required":
                wrong.append(f"{state}, {_describe(body)}: {resp.status_code} {problem_type(resp)}")
    assert not wrong, f"{op.label} should answer 401 authentication-required, but: {wrong}"


# ---------------------------------------------------------------------------
# 6. Agency A's owner, agency B's ids.
# ---------------------------------------------------------------------------

# What an id nobody was given is told, by the checks that look an id up in the caller's own
# agency. A 404 with any other sentence is not one of those checks answering (a feature that is
# off says "Not Found", a missing prompt set says so), and proves nothing about the agency.
THE_AGENCY_CHECKS_404 = frozenset({
    "No agency with that identifier.",
    "No client with that identifier.",
    "No scan with that identifier.",
    "No user with that identifier.",
    "No such alert.",
})


async def _scan_row(world: World, scan_id: str) -> Scan:
    async with world.sessions() as s:
        return (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()


@pytest.mark.parametrize("op", _params([o for o in _ops(*PROTECTED) if o.path_params]))
async def test_agency_a_owner_with_agency_bs_ids_gets_the_404_of_an_unissued_id(
    op: Operation, world: World
) -> None:
    """A `404` that is the agency check's, the same as for an id nobody has, showing nothing of B.

    * the control, for a read: B's own owner reads the same URL and gets `200`, so the data is
      there to be leaked;
    * the made-up id of the same kind gets a `404` with one of the agency checks' own sentences
      (`THE_AGENCY_CHECKS_404`), so the body validated and no early exit stood in for the check;
    * B's id gets the very same status, type, title and detail: A cannot tell B's id from an id
      that does not exist;
    * the body names none of B's ids, names, domains, rivals or addresses;
    * not one row of any table and not one key of Redis changed, WHATEVER THE METHOD. A read can
      write: `GET /scans/{scanId}` fails a scan that was submitted and never claimed, and commits.
      So for an operation with a scan in its path the call is made a second time with B's
      stranded scan, and a reap that ran before the agency check, or on the raw id, would fail
      B's scan on A's request and change the tables.

    The caller is A's OWNER, so no role guard stands between the request and the agency check.
    """
    a, b = world.a, world.b
    body = _body_for(op)

    if op.method == "GET":
        own = await call(b.owner.http, op, b.ids())
        assert own.status_code == 200, (
            f"{op.label}: B's own owner does not get a 200 for B's own data ({own.status_code} "
            f"{own.text[:200]}), so the fixture does not exercise this route"
        )

    nobody = await call(a.owner.http, op, {k: made_up(k) for k in op.path_params}, body)
    assert nobody.status_code == 404, (
        f"{op.label}, an unissued id: {nobody.status_code} {nobody.text}"
    )
    assert problem_of(nobody)["detail"] in THE_AGENCY_CHECKS_404, (
        f"{op.label}: an unissued id is told {problem_of(nobody)['detail']!r}, which is not an "
        "agency check's sentence, so the same 404 for B's id would not show the check ran"
    )

    theirs = [("B's ids", b.ids())]
    if "scanId" in op.path_params:
        theirs.append(("B's stranded scan", {**b.ids(), "scanId": b.stranded_scan_id}))
    for what, with_ids in theirs:
        if with_ids["scanId"] == b.stranded_scan_id:
            assert (await _scan_row(world, b.stranded_scan_id)).status is ScanStatus.QUEUED, (
                f"{op.label}: B's stranded scan is no longer QUEUED before this request, so the "
                "request could not show that it leaves it alone"
            )
        db_before = await database_state(world.engine)
        redis_before = await redis_state(world.settings)
        foreign = await call(a.owner.http, op, with_ids, body)
        assert foreign.status_code == 404, (
            f"{op.label}: A's owner with {what} got {foreign.status_code}: {foreign.text[:300]}"
        )
        assert problem_of(foreign) == problem_of(nobody), (
            f"{op.label}: {what} is told something an unissued id is not: "
            f"{problem_of(foreign)} against {problem_of(nobody)}"
        )
        shown = [n for n in b.needles() if n in str(problem_of(foreign))]
        assert not shown, f"{op.label}: the 404 for {what} names {shown} of agency B"

        changed_db = what_changed(db_before, await database_state(world.engine))
        assert changed_db == [], (
            f"{op.label} with {what}: a refused request changed tables {changed_db}"
        )
        changed_redis = what_changed(redis_before, await redis_state(world.settings))
        assert changed_redis == [], (
            f"{op.label} with {what}: a refused request changed Redis {changed_redis}"
        )


async def test_a_read_of_a_stranded_scan_by_its_own_agency_does_fail_it(world: World) -> None:
    """The control for B's stranded scan: B's own owner reading one like it is what fails it.

    A new row built the way B's is, because the read changes it, and B's own must stay QUEUED for
    the test above (which shows that a read by ANOTHER agency does not change it). It is on a
    client of its own, since a client holds one open scan at a time
    (`uq_scans_one_open_per_client`). If the reap or its window changes so that a row like this is
    no longer failed by a read, this fails and says that B's stranded scan has stopped proving
    anything.
    """
    b = world.b
    made = await b.owner.http.post(
        f"{BASE}/clients", json={"url": "matrix-stranded-control.com", "classify": False}
    )
    assert made.status_code == 201, made.text
    scan_id = await _stranded_scan(
        world.sessions, agency_id=b.agency_id, client_id=made.json()["id"], owner_id=b.owner.id
    )
    resp = await b.owner.http.get(f"{BASE}/scans/{scan_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "failed", resp.text[:300]
    row = await _scan_row(world, scan_id)
    assert (row.status, row.error_code) == (ScanStatus.FAILED, "EXECUTOR_NEVER_STARTED")
    assert (await _scan_row(world, b.stranded_scan_id)).status is ScanStatus.QUEUED


QUERY_ID_OPERATIONS = [
    pytest.param(op, name, id=f"{op.label}?{name}")
    for op in _ops(*PROTECTED)
    for name in op.query_ids
    if name != CURSOR
]
CURSOR_OPERATIONS = _params([op for op in _ops(*PROTECTED) if CURSOR in op.query_ids])


def _answer_without_instance(resp: Response) -> Any:
    body = resp.json()
    return problem_of(resp) if isinstance(body, dict) and "instance" in body else body


@pytest.mark.parametrize(("op", "name"), QUERY_ID_OPERATIONS)
async def test_a_foreign_id_in_a_query_parameter_shows_nothing_of_agency_b(
    op: Operation, name: str, world: World
) -> None:
    """A's own client, asked about B's scan: nothing of B, and nothing an unissued id would not get.

    Today the routes answer `200` and `null` for a scan that is not the client's (the id names one
    of the client's own scans, and another agency's is not one). The test pins the property and not
    the sentence: a `404`, or the id ignored, would pass; B's data would not. The control is A's
    own id, which must show something, so that a null for B's id is not a screen that is always
    empty.
    """
    a, b = world.a, world.b
    mine = a.ids()
    where = f"{op.label}?{name}"

    own = await call(a.owner.http, op, mine, params={name: mine[name]})
    assert own.status_code == 200 and own.json() is not None, (
        f"{where}: A's own id shows nothing ({own.status_code} {own.text[:200]}), so a null for "
        "B's id would prove nothing"
    )
    plain = await call(a.owner.http, op, mine)
    foreign = await call(a.owner.http, op, mine, params={name: b.ids()[name]})
    unissued = await call(a.owner.http, op, mine, params={name: made_up(name)})

    assert foreign.status_code in (200, 404), f"{where}: {foreign.status_code} {foreign.text[:300]}"
    shown = [n for n in b.needles() if n in foreign.text]
    assert not shown, f"{where}: the answer names {shown} of agency B"
    if foreign.status_code == 200:
        assert foreign.json() is None or foreign.json() == plain.json(), (
            f"{where}: B's id gives something that is neither null nor the answer without it"
        )
    assert (foreign.status_code, _answer_without_instance(foreign)) == (
        unissued.status_code, _answer_without_instance(unissued)
    ), f"{where}: B's id is told something an unissued id is not"


def _beside(an_id: str) -> str:
    """An id nobody was given that sorts exactly where `an_id` does: its last character changed."""
    return an_id[:-1] + ("0" if an_id[-1] != "0" else "1")


@pytest.mark.parametrize("op", CURSOR_OPERATIONS)
async def test_agency_bs_id_as_a_cursor_is_a_position_and_shows_nothing_of_agency_b(
    op: Operation, world: World
) -> None:
    """A's request with one of B's ids as the `cursor` is answered as an id nobody was given is.

    A cursor is the id of the last row of the page before it, and the routes compare it as text
    (`id < cursor`), so any id is a position in A's own list and none is looked up. That is the
    property: B's id is not resolved to anything of B's. So its twin, B's id with its last
    character changed (nobody was given it, and it sorts exactly beside B's, whichever agency was
    made first), must be answered with the same status and body; the answer names nothing of B;
    and nothing is written. A route that looked the cursor up without the agency check would
    answer B's id and not its twin.

    The controls: the route answers A with no cursor and shows A's own data, and with A's own id
    as the cursor.
    """
    a, b = world.a, world.b
    kind = CURSOR_KINDS[op.key]
    mine, theirs = a.ids(), b.ids()
    where = f"{op.label}?{CURSOR}"

    plain = await call(a.owner.http, op, mine)
    assert plain.status_code == 200, f"{where}: {plain.status_code} {plain.text[:200]}"
    assert any(n in plain.text for n in a.needles()), (
        f"{where}: the page without a cursor shows nothing of agency A, so the fixture does not "
        "exercise this route"
    )
    own = await call(a.owner.http, op, mine, params={CURSOR: mine[kind]})
    assert own.status_code == 200, f"{where}: A's own id: {own.status_code} {own.text[:200]}"

    db_before = await database_state(world.engine)
    redis_before = await redis_state(world.settings)
    foreign = await call(a.owner.http, op, mine, params={CURSOR: theirs[kind]})
    unissued = await call(a.owner.http, op, mine, params={CURSOR: _beside(theirs[kind])})

    shown = [n for n in b.needles() if n in foreign.text]
    assert not shown, f"{where}: the answer names {shown} of agency B"
    assert (foreign.status_code, _answer_without_instance(foreign)) == (
        unissued.status_code, _answer_without_instance(unissued)
    ), f"{where}: B's id as a cursor is told something an id nobody was given is not"
    assert what_changed(db_before, await database_state(world.engine)) == [], (
        f"{where}: a request with B's id as the cursor changed tables"
    )
    assert what_changed(redis_before, await redis_state(world.settings)) == [], (
        f"{where}: a request with B's id as the cursor changed Redis"
    )


@pytest.mark.parametrize(
    "op",
    _params([o for o in _ops(*PROTECTED) if o.method == "GET" and not o.path_params]),
)
async def test_a_collection_read_of_agency_a_shows_nothing_of_agency_b(
    op: Operation, world: World
) -> None:
    """The reads with no id in their path list the caller's own: A's data, and none of B's."""
    a, b = world.a, world.b
    resp = await call(a.owner.http, op, a.ids())
    assert resp.status_code == 200, f"{op.label}: {resp.status_code} {resp.text[:200]}"
    shown = [n for n in b.needles() if n in resp.text]
    assert not shown, f"{op.label} names {shown} of agency B"
    assert any(n in resp.text for n in a.needles()), (
        f"{op.label} shows nothing of agency A either, so it proves nothing about B"
    )


# ---------------------------------------------------------------------------
# 7. A seat below the role.
# ---------------------------------------------------------------------------

BELOW = {ADMIN: ("member",), OWNER: ("member", "admin")}
ROLE_GATED = [
    pytest.param(op, seat, id=f"{op.label} as {seat}")
    for op in _ops(ADMIN, OWNER)
    for seat in BELOW[op.expected]
    if op.expected is not None
]
INLINE_GATED = [
    pytest.param(op, INLINE_ADMIN_GATES[op.key], id=f"{op.label} as {INLINE_ADMIN_GATES[op.key]}")
    for op in OPERATIONS
    if op.key in INLINE_ADMIN_GATES
]


async def _refused_for_the_role(op: Operation, seat: str, world: World) -> None:
    a = world.a
    person: Person = getattr(a, seat)
    db_before = await database_state(world.engine)
    redis_before = await redis_state(world.settings)
    resp = await call(person.http, op, a.ids(), _body_for(op))

    assert resp.status_code == 403, (
        f"{op.label} as A's {seat} with A's own ids: {resp.status_code} {resp.text[:300]}"
    )
    assert problem_type(resp) == "/problems/permission-denied", resp.text[:300]
    changed_db = what_changed(db_before, await database_state(world.engine))
    assert changed_db == [], f"{op.label} as {seat}: a refused request changed tables {changed_db}"
    changed_redis = what_changed(redis_before, await redis_state(world.settings))
    assert changed_redis == [], f"{op.label} as {seat}: changed Redis keys {changed_redis}"


@pytest.mark.parametrize(("op", "seat"), ROLE_GATED)
async def test_a_seat_below_the_role_is_refused_with_403_and_changes_nothing(
    op: Operation, seat: str, world: World
) -> None:
    """Every ADMIN operation as A's member, every OWNER one as A's member and admin: `403`.

    With A's own ids and a valid body, so the refusal is the role's and not a `404` or a `422`.
    The caller is in the agency the ids belong to, so only the role is in question.
    """
    await _refused_for_the_role(op, seat, world)


def test_an_owner_only_operation_is_called_as_the_admin_as_well_as_the_member() -> None:
    """The seat just below the owner is the one the owner-only routes are about.

    Without this, `BELOW` could lose "admin" for an OWNER operation, or the table could lose its
    last OWNER operation, and the test above would go on passing with no case in it where an admin
    is refused. Every OWNER operation must be among the cases for both seats.
    """
    owner_only = _ops(OWNER)
    assert owner_only, "the table has no OWNER operation, so this file never refuses an admin"
    called = {(case.values[0].key, case.values[1]) for case in ROLE_GATED}
    missing = sorted(
        f"{op.label} as {seat}"
        for op in owner_only
        for seat in ("member", "admin")
        if (op.key, seat) not in called
    )
    assert not missing, f"these cases are not among the role tests: {missing}"


@pytest.mark.parametrize(("op", "seat"), INLINE_GATED)
async def test_an_operation_the_handler_gates_refuses_the_seat_below_it_the_same_way(
    op: Operation, seat: str, world: World
) -> None:
    """The handler's own role check answers as the guard does (and only once the setting is on)."""
    await _refused_for_the_role(op, seat, world)


# ---------------------------------------------------------------------------
# 8. Share tokens: a stranger is told one thing about every link that does not open.
# ---------------------------------------------------------------------------

SHARED = [
    pytest.param("/reports/shared", id="POST /reports/shared"),
    pytest.param("/reports/shared/pdf", id="POST /reports/shared/pdf"),
]


async def _a_scan_with_a_live_link(world: World) -> tuple[str, str]:
    """A new scan of A's client with a link of its own, so no other test's link is touched."""
    a = world.a
    async with world.sessions() as s:
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=a.client_id, agency_id=a.agency_id,
            requested_by_user_id=a.owner.id, status=ScanStatus.SUCCEEDED,
            formula_version=FORMULA_VERSION,
        )
        s.add(scan)
        await s.commit()
    link = await a.owner.http.post(f"{BASE}/scans/{scan.id}/share")
    assert link.status_code == 200, link.text
    return scan.id, link.json()["token"]


@pytest.mark.parametrize("path", SHARED)
async def test_a_link_never_issued_ended_or_withdrawn_is_one_404(path: str, world: World) -> None:
    """The same status, type, title and detail for all three; only `instance` may differ.

    A live link opens (the control, so each 404 is a link that stopped and not a route that never
    opened). The ended link is moved into the past on its row; the withdrawn one is taken down
    through the API, as a person does it.
    """
    stranger = world.client()

    async def open_with(token: str) -> Response:
        return await stranger.post(f"{BASE}{path}", json={"token": token})

    ended_scan, ended_token = await _a_scan_with_a_live_link(world)
    withdrawn_scan, withdrawn_token = await _a_scan_with_a_live_link(world)

    for token in (ended_token, withdrawn_token):
        opens = await open_with(token)
        assert opens.status_code == 200, f"{path}, a live link: {opens.status_code} {opens.text}"

    async with world.sessions() as s:
        await s.execute(
            update(Scan)
            .where(Scan.id == ended_scan)
            .values(share_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    taken_down = await world.a.owner.http.delete(f"{BASE}/scans/{withdrawn_scan}/share")
    assert taken_down.status_code == 204

    answers = {
        "never issued": await open_with(secrets.token_urlsafe(32)),
        "ended": await open_with(ended_token),
        "withdrawn": await open_with(withdrawn_token),
    }
    for what, resp in answers.items():
        assert resp.status_code == 404, f"{path}, a link that is {what}: {resp.status_code}"
    documents = {what: problem_of(resp) for what, resp in answers.items()}
    assert documents["ended"] == documents["never issued"], documents
    assert documents["withdrawn"] == documents["never issued"], documents
    assert documents["never issued"]["type"] == "/problems/not-found"
    for token in (ended_token, withdrawn_token):
        assert all(token not in resp.text for resp in answers.values())
