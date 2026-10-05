"""Refusals leave a record naming the caller; the API documentation is a dev convenience.

Every refusal below goes through the real app and is read back off the `celine.audit`
logger, the way a log pipeline would select it: one JSON object per line.
"""

from __future__ import annotations

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from celine.flexibility.main import create_app
from tests.conftest import USER_SUB
from tests.fakes import make_user

EMAIL = "alice@example.org"
NAME = "Alice Example"


def denials(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "celine.audit" and r.levelno == logging.WARNING
    ]


def assert_no_personal_data(caplog) -> None:
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "celine.audit")
    assert EMAIL not in text
    assert NAME not in text


# @verifies REQ-0057
async def test_a_participant_refused_a_service_route_is_recorded_with_its_sub(
    client, jwt, caplog
):
    headers = jwt.headers(
        make_user(sub=USER_SUB, scope="flexibility.read", email=EMAIL, name=NAME)
    )

    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.get("/api/commitments/pending", headers=headers)

    assert response.status_code == 403
    [record] = denials(caplog)
    assert record["event"] == "denied"
    assert record["outcome"] == "denied"
    assert record["service"] == "flexibility-api"
    assert record["action"] == "flexibility.commitments.pending"
    assert record["sub"] == USER_SUB
    assert record["service_account"] is False
    assert record["method"] == "GET"
    assert record["reason"] == "not-a-service-account"
    assert_no_personal_data(caplog)


# @verifies REQ-0057
async def test_an_unscoped_service_refused_export_is_recorded_with_its_client(
    client, unscoped_service, caplog
):
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.get("/api/commitments/export", headers=unscoped_service)

    assert response.status_code == 403
    [record] = denials(caplog)
    assert record["action"] == "flexibility.commitments.export"
    assert record["sub"] == "service-account-svc-unrelated"
    assert record["client_id"] == "svc-unrelated"
    assert record["service_account"] is True
    assert record["reason"] == "missing flexibility scope"


# @verifies REQ-0057
async def test_a_refused_settle_is_recorded_once(client, alice, caplog):
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.patch(
            "/api/commitments/00000000-0000-0000-0000-000000000001/settle",
            json={"reward_points_actual": 1},
            headers=alice,
        )

    assert response.status_code == 403
    [record] = denials(caplog)
    assert record["action"] == "flexibility.commitments.settle"
    assert record["sub"] == USER_SUB


# @verifies REQ-0057
async def test_a_missing_token_on_a_guarded_route_is_recorded_without_a_caller(
    client, caplog
):
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.get("/api/commitments/pending")

    assert response.status_code == 403
    [record] = denials(caplog)
    assert record["sub"] is None
    assert record["reason"] == "unauthenticated"


# @verifies REQ-0057
@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        ("expired", "token_expired"),
        ("invalid", "token_invalid"),
        ("other", "token_unverified"),
    ],
)
async def test_a_presented_token_that_fails_is_recorded_without_its_claims(
    client, jwt, caplog, raised, reason
):
    import jwt as pyjwt

    jwt.raises = {
        "expired": pyjwt.ExpiredSignatureError("expired"),
        "invalid": pyjwt.InvalidTokenError("bad"),
        "other": RuntimeError("jwks unreachable"),
    }[raised]
    headers = jwt.headers(make_user(sub=USER_SUB, email=EMAIL, name=NAME))

    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.get("/api/commitments", headers=headers)

    assert response.status_code == 401
    [record] = denials(caplog)
    assert record["action"] == "flexibility.authenticate"
    assert record["sub"] is None
    assert record["reason"] == reason
    assert record["route"] == "/api/commitments"
    assert_no_personal_data(caplog)


# @verifies REQ-0057
async def test_no_token_at_all_on_an_unguarded_route_records_nothing(client, caplog):
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.get("/api/commitments")

    assert response.status_code == 401
    assert denials(caplog) == []


# @verifies REQ-0057
async def test_the_service_dependency_records_its_own_refusal(jwt, caplog):
    """`ServiceDep` is the second gate behind the middleware; reached directly here."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from celine.flexibility.security.auth import get_service_token

    headers = jwt.headers(make_user(sub=USER_SUB, email=EMAIL))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/commitments/export",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
    )

    with caplog.at_level(logging.INFO, logger="celine.audit"), pytest.raises(HTTPException) as exc:
        get_service_token(request)

    assert exc.value.status_code == 403
    [record] = denials(caplog)
    assert record["action"] == "flexibility.service"
    assert record["sub"] == USER_SUB
    assert_no_personal_data(caplog)


# @verifies REQ-0057
async def test_an_admitted_service_records_no_refusal(client, service, caplog):
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = await client.get("/api/commitments/pending", headers=service)

    assert response.status_code == 200
    assert denials(caplog) == []


# ---------------------------------------------------------------------------
# API documentation
# ---------------------------------------------------------------------------

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


async def _statuses() -> dict[str, int]:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        return {path: (await c.get(path)).status_code for path in (*DOC_PATHS, "/health")}


# @verifies REQ-0004
async def test_outside_dev_the_documentation_is_not_mounted(monkeypatch):
    monkeypatch.setenv("CELINE_ENV", "staging")
    monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)

    statuses = await _statuses()

    assert {p: statuses[p] for p in DOC_PATHS} == {p: 404 for p in DOC_PATHS}
    assert statuses["/health"] == 200


# @verifies REQ-0004
async def test_outside_dev_the_documentation_can_be_published_on_purpose(monkeypatch):
    monkeypatch.setenv("CELINE_ENV", "staging")
    monkeypatch.setenv("CELINE_PUBLIC_DOCS", "true")

    statuses = await _statuses()

    assert all(statuses[p] == 200 for p in DOC_PATHS)


# @verifies REQ-0004
async def test_in_dev_the_documentation_is_served(monkeypatch):
    monkeypatch.setenv("CELINE_ENV", "dev")
    monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)

    statuses = await _statuses()

    assert all(statuses[p] == 200 for p in DOC_PATHS)
