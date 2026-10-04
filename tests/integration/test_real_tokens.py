"""REQ-0056 with real tokens from a LOCAL Keycloak.

Opt-in and skipped by default, because the rest of the suite needs nothing running
(ADR-0003, ADR-0004). Set ``FLEXIBILITY_IT_KEYCLOAK=1`` with the local dev stack up to
run it. The issuer is ``settings.oidc.base_url`` (``CELINE_OIDC_BASE_URL``), the same one
the service verifies against, and the module refuses anything that is not a ``.localhost``
or loopback host: these tests mint tokens with dev passwords and must never reach a
shared realm.

What it needs from the realm, which is the local dev realm after realm groups were
removed:

- ``admin`` / ``admin``: holds the realm role ``platform-admin`` (and organisation
  ``admins`` groups);
- ``org-admin`` / ``org-admin``: an organisation's ``admins``, not a platform admin;
- the ``oauth2_proxy`` client (dev secret ``oauth2_proxy``, or
  ``FLEXIBILITY_IT_OAUTH2_PROXY_SECRET``), the only client that issues user tokens;
- ``svc-pipelines`` (secret = client id), a service holding
  ``flexibility.commitments.export`` and no other flexibility scope.

A token that still carries the retired realm group ``/admins`` cannot be minted from the
converged realm. Pass one in ``FLEXIBILITY_IT_LEGACY_TOKEN`` — minted by putting the old
group and its two mappers back on a throwaway client for a few seconds — and the legacy
cases run; without it they skip.

Tokens are verified for real: ``JwtUser.from_token`` fetches the realm's JWKS and checks
signature, expiry and audience, and the requests go through the real app with no fake in
the identity path.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import pytest

from celine.sdk.auth import JwtUser, organization_groups

from celine.flexibility.core.config import settings
from celine.flexibility.security.policy import subject_document

pytestmark = pytest.mark.skipif(
    os.environ.get("FLEXIBILITY_IT_KEYCLOAK") != "1",
    reason="real-token tests need a local Keycloak: set FLEXIBILITY_IT_KEYCLOAK=1",
)

PACKAGE = "data.celine.flexibility.access"
HEADER = "x-auth-request-access-token"


def _issuer() -> str:
    issuer = settings.oidc.base_url.rstrip("/")
    host = urllib.parse.urlsplit(issuer).hostname or ""
    if not (host.endswith(".localhost") or host in {"localhost", "127.0.0.1"}):
        pytest.fail(f"refusing to mint dev-password tokens against {host!r}: local only")
    return issuer


def _token(**form: str) -> str:
    data = urllib.parse.urlencode(form).encode()
    url = f"{_issuer()}/protocol/openid-connect/token"
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
        return json.loads(r.read())["access_token"]


def _user_token(username: str) -> str:
    return _token(
        grant_type="password",
        client_id="oauth2_proxy",
        client_secret=os.environ.get("FLEXIBILITY_IT_OAUTH2_PROXY_SECRET", "oauth2_proxy"),
        username=username,
        password=username,
        scope="openid email profile organization:*",
    )


def _service_token(client_id: str) -> str:
    return _token(grant_type="client_credentials", client_id=client_id, client_secret=client_id)


def _verified(token: str) -> JwtUser:
    return JwtUser.from_token(token, oidc=settings.oidc)


@pytest.fixture(scope="module")
def platform_admin_token() -> str:
    return _user_token("admin")


@pytest.fixture(scope="module")
def org_admin_token() -> str:
    return _user_token("org-admin")


@pytest.fixture(scope="module")
def legacy_token() -> str:
    token = os.environ.get("FLEXIBILITY_IT_LEGACY_TOKEN")
    if not token:
        pytest.skip("no FLEXIBILITY_IT_LEGACY_TOKEN: a token carrying the realm group /admins")
    return token


def _decide(engine, subject: dict, *, owner_id: str, action: str = "read") -> dict:
    result = engine.evaluate(PACKAGE, {
        "action": {"name": action},
        "resource": {"type": "flexibility.commitment", "attributes": {"owner_id": owner_id}},
        "subject": subject,
    })
    return result["result"][0]["expressions"][0]["value"]


# ---------------------------------------------------------------------------
# What the three kinds of token are
# ---------------------------------------------------------------------------


# @verifies REQ-0056
def test_an_organisation_admin_is_not_a_platform_admin(org_admin_token):
    user = _verified(org_admin_token)
    subject = subject_document(user)

    assert any("admins" in organization_groups(user.claims, a) for a in user.grants.aliases)
    assert user.is_platform_admin is False
    assert "platform-admin" not in subject["roles"]
    assert "groups" not in subject


# @verifies REQ-0056
def test_a_platform_admin_holder_is_a_platform_admin(platform_admin_token):
    user = _verified(platform_admin_token)
    subject = subject_document(user)

    assert user.is_platform_admin is True
    assert "platform-admin" in subject["roles"]
    assert "groups" not in subject


# @verifies REQ-0056
def test_a_realm_group_still_in_a_token_is_not_read(legacy_token):
    user = _verified(legacy_token)
    subject = subject_document(user)

    assert "/admins" in (user.claims.get("groups") or []), "not a legacy token"
    assert user.is_platform_admin is False
    assert "groups" not in subject
    assert not {"/admins", "admins"} & set(subject["roles"])


# ---------------------------------------------------------------------------
# What they reach: the real bundle, then a real request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["platform_admin_token", "org_admin_token", "legacy_token"])
# @verifies REQ-0056
def test_no_real_token_reaches_another_participants_commitment(policy_engine, request, which):
    """
    Each real subject, as `AccessPolicy` builds it, against a commitment someone else
    owns — and against one it owns, without a flexibility scope (no user token from this
    realm carries one). Neither the role nor any group stands in for ownership or scope.
    """
    user = _verified(request.getfixturevalue(which))
    subject = subject_document(user)

    stranger = _decide(policy_engine, subject, owner_id="someone-else")
    own = _decide(policy_engine, subject, owner_id=user.sub)

    assert (stranger["allow"], stranger["reason"]) == (False, "not resource owner")
    assert (own["allow"], own["reason"]) == (False, "missing flexibility scope")


@pytest.mark.parametrize("which", ["platform_admin_token", "org_admin_token", "legacy_token"])
# @verifies REQ-0056
async def test_no_real_person_token_reaches_a_service_route(client, request, which):
    """
    Through the real app: token verified against the realm's JWKS, then
    `PolicyMiddleware`. A platform admin, an organisation admin and a legacy realm
    `/admins` member are all people, so the service-only route refuses them.
    """
    token = request.getfixturevalue(which)

    response = await client.get("/api/commitments/pending", headers={HEADER: token})

    assert response.status_code == 403
    assert response.json()["detail"] == "not-a-service-account"


# @verifies REQ-0056
async def test_a_real_service_token_is_decided_on_its_scope(client):
    """
    The control: a real service token reaches the bundle, which refuses it on scope —
    `svc-pipelines` holds `flexibility.commitments.export` and nothing that admits it to
    `/pending`. It carries no realm roles at all.
    """
    token = _service_token("svc-pipelines")
    assert subject_document(_verified(token), is_service=True)["roles"] == []

    response = await client.get("/api/commitments/pending", headers={HEADER: token})

    assert response.status_code == 403
    assert response.json()["detail"] == "missing flexibility scope"
