"""The commitment API — the surface `../celine-webapp` composes into the participant app.

Every request below goes through the real app: `PolicyMiddleware`, the real `Depends`
chain, the real SQL. Only the database engine and `JwtUser.from_token` are substituted.

**Isolation between two participants is SQL here, not policy.** `AccessPolicy` is never
consulted for these routes — the middleware only guards `/pending` and `/settle` — so
`WHERE user_id = :sub` is the whole of the separation between Alice's commitments and
Bob's. That is why it is tested from both sides.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from celine.flexibility.models.commitment import FlexibilityCommitment

from tests.conftest import COMMUNITY, OTHER_SUB, USER_SUB
from tests.fakes import make_user

NOW = datetime.now(timezone.utc)


def body(**overrides) -> dict:
    return {
        "user_id": USER_SUB,
        "suggestion_id": "w1",
        "suggestion_type": "shift-consumption",
        "community_id": COMMUNITY,
        "device_id": "sensor-1",
        "period_start": (NOW + timedelta(hours=2)).isoformat(),
        "period_end": (NOW + timedelta(hours=5)).isoformat(),
        "reward_points_estimated": 14,
        **overrides,
    }


async def row(
    db,
    *,
    user_id: str = USER_SUB,
    status: str = "committed",
    suggestion_id: str = "w1",
    starts_in: timedelta = timedelta(hours=2),
    ends_in: timedelta = timedelta(hours=5),
    reminded_at: datetime | None = None,
) -> FlexibilityCommitment:
    now = datetime.now(timezone.utc)
    commitment = FlexibilityCommitment(
        user_id=user_id,
        suggestion_id=suggestion_id,
        suggestion_type="shift-consumption",
        community_id=COMMUNITY,
        device_id="sensor-1",
        period_start=now + starts_in,
        period_end=now + ends_in,
        status=status,
        reminded_at=reminded_at,
        reward_points_estimated=14,
    )
    db.add(commitment)
    await db.commit()
    await db.refresh(commitment)
    return commitment


async def stored(db, commitment_id) -> FlexibilityCommitment:
    db.expunge_all()
    return (
        await db.execute(
            select(FlexibilityCommitment).where(
                FlexibilityCommitment.id == uuid.UUID(str(commitment_id))
            )
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------


# @verifies REQ-0019
async def test_a_participant_creates_a_commitment_for_themselves(client, alice, db):
    response = await client.post("/api/commitments", json=body(), headers=alice)

    assert response.status_code == 201
    payload = response.json()
    assert payload["user_id"] == USER_SUB
    assert payload["status"] == "committed"
    assert payload["reward_points_estimated"] == 14
    assert payload["reward_points_actual"] is None
    assert payload["settled_at"] is None


# @verifies REQ-0019
async def test_a_participant_cannot_create_a_commitment_for_someone_else(client, alice, db):
    """
    `user_id` in the body is ignored for a user token and replaced with the caller's
    `sub`. It is not rejected — a client that sends the wrong one gets a commitment for
    itself, not a `400`.

    This is the only thing standing between a participant and a commitment written
    against another participant's account, because no policy is consulted on this route.
    """
    response = await client.post(
        "/api/commitments", json=body(user_id=OTHER_SUB), headers=alice
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == USER_SUB


# @verifies REQ-0020
async def test_a_service_account_creates_on_behalf_of_a_named_participant(client, service):
    """
    The BFF and the pipeline both write commitments this way. A service token is trusted
    with the `user_id` in the body precisely because it has no `sub` of its own worth
    writing.
    """
    response = await client.post(
        "/api/commitments", json=body(user_id="user-carol"), headers=service
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == "user-carol"


# @verifies REQ-0002
async def test_creating_without_a_token_is_refused(client):
    assert (await client.post("/api/commitments", json=body())).status_code == 401


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


# @verifies REQ-0021
async def test_a_participant_sees_only_their_own(client, alice, db):
    """
    Both directions, because the filter is the whole of the isolation: Alice's row is
    present and Bob's is absent from the same response.
    """
    mine = await row(db, user_id=USER_SUB)
    await row(db, user_id=OTHER_SUB)

    payload = (await client.get("/api/commitments", headers=alice)).json()

    assert [item["id"] for item in payload["items"]] == [str(mine.id)]
    assert payload["total"] == 1


# @verifies REQ-0021
async def test_a_participant_cannot_ask_for_another_participants_rows(client, alice, db):
    """
    The `user_id` query parameter is honoured only for a service token. For a user token
    it is discarded — silently, so a caller passing it gets their own rows back rather
    than an error.
    """
    await row(db, user_id=OTHER_SUB)

    payload = (
        await client.get(f"/api/commitments?user_id={OTHER_SUB}", headers=alice)
    ).json()

    assert payload["items"] == []


# @verifies REQ-0022
async def test_a_service_account_filters_by_participant(client, service, db):
    await row(db, user_id=USER_SUB)
    theirs = await row(db, user_id=OTHER_SUB)

    payload = (
        await client.get(f"/api/commitments?user_id={OTHER_SUB}", headers=service)
    ).json()

    assert [item["id"] for item in payload["items"]] == [str(theirs.id)]


# @verifies REQ-0022
async def test_a_service_account_asking_for_nobody_gets_nobody(client, service, db):
    """
    Worth stating because the natural reading is the opposite: a service token with no
    `user_id` filters on `user_id IS NULL`, which matches nothing. There is no
    "all participants" listing — `/export` is that, and it is scoped separately.
    """
    await row(db, user_id=USER_SUB)

    payload = (await client.get("/api/commitments", headers=service)).json()

    assert payload["items"] == []


# @verifies REQ-0023
async def test_listing_filters_by_status(client, alice, db):
    await row(db, status="committed", suggestion_id="open")
    await row(db, status="cancelled", suggestion_id="gone")

    payload = (
        await client.get("/api/commitments?status=cancelled", headers=alice)
    ).json()

    assert [item["suggestion_id"] for item in payload["items"]] == ["gone"]


# @verifies REQ-0023
async def test_listing_is_paged_and_total_counts_the_page_not_the_table(client, alice, db):
    """
    `total` is `len(rows)` — the size of the page returned, not the number of matching
    rows. A client paging on it will stop early, and it cannot render "showing 2 of 30".

    Characterised rather than fixed: `../celine-webapp` reads this field today and the
    correction is a contract change.
    """
    for n in range(3):
        await row(db, suggestion_id=f"w{n}")

    payload = (await client.get("/api/commitments?limit=2", headers=alice)).json()

    assert len(payload["items"]) == 2
    assert payload["total"] == 2


# @verifies REQ-0023
async def test_the_page_size_is_capped(client, alice):
    """
    `le=200`. An unbounded limit on a table that grows with every accept and decline is
    a way to ask this service to read its whole history into memory.
    """
    assert (await client.get("/api/commitments?limit=500", headers=alice)).status_code == 422


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


# @verifies REQ-0024
async def test_the_owner_cancels_their_commitment(client, alice, db):
    commitment = await row(db)

    response = await client.delete(f"/api/commitments/{commitment.id}", headers=alice)

    assert response.status_code == 204
    assert (await stored(db, commitment.id)).status == "cancelled"


# @verifies REQ-0024
async def test_another_participants_commitment_is_not_found_rather_than_forbidden(
    client, bob, db
):
    """
    The ownership check is folded into the `WHERE`, so a commitment belonging to someone
    else is indistinguishable from one that does not exist. That is the right answer
    here: a `403` would confirm the id is real.
    """
    commitment = await row(db, user_id=USER_SUB)

    response = await client.delete(f"/api/commitments/{commitment.id}", headers=bob)

    assert response.status_code == 404
    assert (await stored(db, commitment.id)).status == "committed"


@pytest.mark.parametrize("status", ["settled", "cancelled", "rejected"])
# @verifies REQ-0024
async def test_only_a_committed_commitment_can_be_cancelled(client, alice, db, status):
    """
    A settled commitment has already been paid out in points; cancelling it would leave
    a balance nothing accounts for.
    """
    commitment = await row(db, status=status)

    response = await client.delete(f"/api/commitments/{commitment.id}", headers=alice)

    assert response.status_code == 409


# @verifies REQ-0024
async def test_cancelling_an_unknown_id_is_a_404(client, alice):
    response = await client.delete(f"/api/commitments/{uuid.uuid4()}", headers=alice)

    assert response.status_code == 404


# @verifies REQ-0024
async def test_a_malformed_id_is_rejected_before_the_query(client, alice):
    assert (await client.delete("/api/commitments/not-a-uuid", headers=alice)).status_code == 422


# ---------------------------------------------------------------------------
# Pending — service only
# ---------------------------------------------------------------------------


# @verifies REQ-0025
async def test_pending_returns_the_windows_that_are_open_now(client, service, db):
    open_now = await row(db, starts_in=timedelta(hours=-1), ends_in=timedelta(hours=1))
    await row(db, starts_in=timedelta(hours=2), ends_in=timedelta(hours=4))
    await row(db, starts_in=timedelta(hours=-4), ends_in=timedelta(hours=-2))

    payload = (await client.get("/api/commitments/pending", headers=service)).json()

    assert [item["id"] for item in payload] == [str(open_now.id)]


# @verifies REQ-0026
async def test_pending_stamps_what_it_returned(client, service, db):
    """
    DT polls this on every meters tick. The stamp is what turns a three-hour window into
    one notification instead of thirty-six, and it is written in the same request that
    hands the row out — so a caller that crashes before sending has still consumed it.
    """
    commitment = await row(db, starts_in=timedelta(hours=-1), ends_in=timedelta(hours=1))

    first = (await client.get("/api/commitments/pending", headers=service)).json()
    second = (await client.get("/api/commitments/pending", headers=service)).json()

    assert len(first) == 1
    assert second == []
    assert (await stored(db, commitment.id)).reminded_at is not None


# @verifies REQ-0013
async def test_pending_is_refused_to_a_participant(client, alice, db):
    """
    Two independent gates say no here: `PolicyMiddleware.allow_service` and the
    `ServiceDep` on the route. The middleware answers first, and the `not-a-service-account`
    reason is what proves it — that branch returns before `_evaluate` is reached, which
    is why it works at all while the scope check behind it does not (REQ-0011).
    """
    await row(db, starts_in=timedelta(hours=-1), ends_in=timedelta(hours=1))

    response = await client.get("/api/commitments/pending", headers=alice)

    assert response.status_code == 403
    assert response.json()["detail"] == "not-a-service-account"


# @verifies REQ-0054
async def test_a_service_account_without_a_flexibility_scope_is_refused(
    client, unscoped_service, db
):
    """
    **The end-to-end authorisation test.** It runs a real request through
    `PolicyMiddleware`, which builds the input document, queries the real bundle, and
    returns the bundle's own reason string.

    It could not exist before #21. Until then `_evaluate` raised on every call and
    returned an allow, so this caller — a valid Keycloak service token belonging to some
    other client entirely — reached the two guarded routes and the whole export table.
    The `is_service_account` check in front of the bundle let it through, because it *is*
    a service account.

    The reason travelling all the way from the `.rego` to the response body is what
    proves the bundle was consulted, rather than something else having refused it.
    """
    await row(db, starts_in=timedelta(hours=-1), ends_in=timedelta(hours=1))

    response = await client.get("/api/commitments/pending", headers=unscoped_service)

    assert response.status_code == 403
    assert response.json()["detail"] == "missing flexibility scope"


# @verifies REQ-0054
async def test_a_scoped_service_account_still_reaches_the_guarded_routes(
    client, service, db
):
    """
    The other direction. On its own it proves nothing — an allow is what a broken policy
    produces too — but paired with the denial above it says the scope is what decides.
    """
    commitment = await row(db, starts_in=timedelta(hours=-1), ends_in=timedelta(hours=1))

    response = await client.get("/api/commitments/pending", headers=service)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(commitment.id)]


# @verifies REQ-0054
async def test_settling_is_refused_to_an_unscoped_service_account(
    client, unscoped_service, db
):
    """
    The route that writes a points balance. Same gate, and worth asserting separately
    because the middleware matches it by a different rule — `"/settle" in path` and
    `PATCH` — rather than by suffix.
    """
    commitment = await row(db)

    response = await client.patch(
        f"/api/commitments/{commitment.id}/settle",
        json={"reward_points_actual": 9999},
        headers=unscoped_service,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "missing flexibility scope"
    assert (await stored(db, commitment.id)).reward_points_actual is None


# @verifies REQ-0028
async def test_export_is_still_reachable_by_any_service_account(
    client, unscoped_service, db
):
    """
    Not a fix — a gap that survives #21 and is stated as it is.

    `/export` returns every commitment of every participant, and `PolicyMiddleware` does
    not match its path, so the bundle is never consulted for it. The
    `flexibility.commitments.export` scope (REQ-0010) is defined and unenforced: the
    `ServiceDep` is the whole check, and this caller passes it.
    """
    await row(db)

    response = await client.get("/api/commitments/export", headers=unscoped_service)

    assert response.status_code == 200
    assert len(response.json()) == 1


# @verifies REQ-0014
async def test_a_missing_token_on_a_service_route_is_403_not_401(client):
    """
    Everywhere else in this service a missing token is a `401`. On the two routes
    `PolicyMiddleware` guards, it is a `403 unauthenticated` — the middleware runs ahead
    of the dependency, catches the `HTTPException` the token extraction raises, and
    converts every failure into one status.

    A client cannot tell "present a token" from "your token is not enough" on these two
    routes, and a proxy that refreshes on `401` will not refresh here.
    """
    response = await client.get("/api/commitments/pending")

    assert response.status_code == 403
    assert response.json()["detail"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Settling over HTTP — service only
# ---------------------------------------------------------------------------


# @verifies REQ-0027
async def test_a_service_account_settles_a_commitment(client, service, db):
    """
    The HTTP path exists alongside `settle_completed_windows`, which writes the same
    three fields directly. Both are live: this one is called by the pipeline mirror, the
    other by the MQTT listener.
    """
    commitment = await row(db)

    response = await client.patch(
        f"/api/commitments/{commitment.id}/settle",
        json={"reward_points_actual": 9, "actual_kwh": 0.9},
        headers=service,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "settled"
    assert payload["reward_points_actual"] == 9
    assert payload["settled_at"] is not None


# @verifies REQ-0027
async def test_the_settled_kwh_is_accepted_and_discarded(client, service, db):
    """
    `CommitmentSettle.actual_kwh` is in the schema, is parsed, and is written nowhere —
    the model has no column for it. Only the points survive, so the reading that
    produced them cannot be audited from this service.
    """
    commitment = await row(db)

    await client.patch(
        f"/api/commitments/{commitment.id}/settle",
        json={"reward_points_actual": 9, "actual_kwh": 123.4},
        headers=service,
    )

    settled = await stored(db, commitment.id)
    assert settled.reward_points_actual == 9
    assert not hasattr(settled, "actual_kwh")


@pytest.mark.parametrize("status", ["settled", "cancelled", "rejected"])
# @verifies REQ-0027
async def test_only_a_committed_commitment_can_be_settled(client, service, db, status):
    """
    Settling twice would overwrite the first reading, and the pipeline that calls this
    retries.
    """
    commitment = await row(db, status=status)

    response = await client.patch(
        f"/api/commitments/{commitment.id}/settle",
        json={"reward_points_actual": 9},
        headers=service,
    )

    assert response.status_code == 409


# @verifies REQ-0013
async def test_settling_is_refused_to_a_participant(client, alice, db):
    """
    The route a participant would use to award themselves points. As with `/pending`,
    the middleware answers first — the path match is `"/settle" in path and PATCH`.
    """
    commitment = await row(db)

    response = await client.patch(
        f"/api/commitments/{commitment.id}/settle",
        json={"reward_points_actual": 9999},
        headers=alice,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not-a-service-account"
    assert (await stored(db, commitment.id)).reward_points_actual is None


# @verifies REQ-0027
async def test_settling_an_unknown_commitment_is_a_404(client, service):
    response = await client.patch(
        f"/api/commitments/{uuid.uuid4()}/settle",
        json={"reward_points_actual": 9},
        headers=service,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Export — service only
# ---------------------------------------------------------------------------


# @verifies REQ-0028
async def test_export_returns_every_participant_and_every_status(client, service, db):
    """
    Unlike the list endpoint, this is not scoped to anybody. It exists for the pipeline
    mirror and it returns declines too — the acceptance rate is only computable because
    `rejected` rows are kept.
    """
    await row(db, user_id=USER_SUB, status="committed")
    await row(db, user_id=OTHER_SUB, status="rejected")
    await row(db, user_id="user-carol", status="settled")

    payload = (await client.get("/api/commitments/export", headers=service)).json()

    assert len(payload) == 3
    assert {item["status"] for item in payload} == {"committed", "rejected", "settled"}


# @verifies REQ-0013
async def test_export_is_refused_to_a_participant(client, alice, db):
    """
    Every commitment of every participant, behind one route. `PolicyMiddleware` does not
    guard this path at all — it matches only `/pending` and `PATCH …/settle` — so the
    `ServiceDep` is the whole of the check, and the `flexibility.commitments.export`
    scope the Rego demands (REQ-0010) is never consulted. **Any** service token reaches
    every commitment in the table.
    """
    await row(db)

    response = await client.get("/api/commitments/export", headers=alice)

    assert response.status_code == 403
    assert response.json()["detail"] == "Service account required"


# @verifies REQ-0028
async def test_export_filters_by_creation_time(client, service, db):
    """
    The mirror pages through history with this. It filters on `committed_at`, which is a
    server default — so the boundary is the row's insertion time, not its window.

    The parameter is passed through `params=` rather than interpolated into the path on
    purpose: an ISO timestamp ends in `+00:00`, and a `+` in a query string decodes to a
    space. A caller that concatenates the URL by hand gets a `422`, not a wrong window —
    which is the better of the two failures, but it is a trap either way.
    """
    await row(db)
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=1)

    payload = (
        await client.get(
            "/api/commitments/export",
            params={"created_after": cutoff.isoformat()},
            headers=service,
        )
    ).json()

    assert payload == []

    included = (
        await client.get(
            "/api/commitments/export",
            params={"created_after": (cutoff - timedelta(hours=1)).isoformat()},
            headers=service,
        )
    ).json()

    assert len(included) == 1


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


# @verifies REQ-0001
async def test_a_bearer_header_is_accepted_as_well_as_the_proxy_header(client, jwt, db):
    """
    oauth2-proxy forwards the token in `x-auth-request-access-token`; a direct caller —
    the AI assistant, or a service — sends `Authorization: Bearer`. Both reach the same
    principal, and the proxy header wins when both are present.
    """
    user = make_user(sub=USER_SUB, scope="flexibility.read")

    response = await client.get("/api/commitments", headers=jwt.bearer(user))

    assert response.status_code == 200


# @verifies REQ-0003
async def test_an_undecodable_token_is_a_401_not_a_500(client, jwt):
    """
    Every decode failure — expired, malformed, wrong issuer — is mapped to a `401`. A
    `500` here would be a token problem reported as a service problem, and it would page
    the wrong people.
    """
    jwt.raises = RuntimeError("jwks unreachable")
    headers = jwt.headers(make_user(sub=USER_SUB))

    assert (await client.get("/api/commitments", headers=headers)).status_code == 401


# @verifies REQ-0004
async def test_health_needs_no_token(client):
    """
    `PolicyMiddleware` lets `/health`, `/docs`, `/redoc` and `/openapi.json` past
    unauthenticated. The liveness probe has no token to present.
    """
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# @verifies REQ-0004
async def test_the_openapi_document_is_public(client):
    assert (await client.get("/openapi.json")).status_code == 200
