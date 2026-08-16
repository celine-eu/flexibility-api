"""The suggestion API — what a participant is offered, and what happens when they accept.

`_build_suggestions` is pure and is tested on its own in `tests/unit/`. What is tested
here is everything around it: the two Digital Twin calls, the degradation when either
fails, and the four side effects of an accept — the row, the MQTT publish, the scheduled
nudge, and the response.

`_get_dt_client` builds a real `DTClient` **inside the request**, from the caller's own
token, so there is no dependency to override. It is monkeypatched instead, which is the
one seam in this file that is not the production one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from celine.flexibility.api import suggestions as module
from celine.flexibility.models.commitment import FlexibilityCommitment

from tests.conftest import COMMUNITY, DEVICE, USER_SUB
from tests.fakes import (
    Asset,
    Assets,
    FakeBroker,
    FakeDTClient,
    FakeNudgingClient,
    FetchResult,
    make_profile,
)

WINDOW_START = datetime(2026, 7, 2, 9, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)


@pytest.fixture
def dt(monkeypatch) -> FakeDTClient:
    """The Digital Twin, resolving Alice into a community with one metered device."""
    client = FakeDTClient()
    client.participants.profile_response = make_profile(COMMUNITY)
    client.participants.assets_response = Assets([Asset(DEVICE)])
    client.communities.set("rec_flexibility_windows", FetchResult([]))

    monkeypatch.setattr(module, "_get_dt_client", lambda _token: client)
    return client


def window(_id: str = "w1", *, kwh: float = 120.0) -> dict:
    return {
        "_id": _id,
        "window_start": WINDOW_START.replace(tzinfo=None).isoformat(),
        "window_end": WINDOW_END.replace(tzinfo=None).isoformat(),
        "community_kwh": kwh,
        "confidence": 0.75,
    }


def respond_body(**overrides) -> dict:
    return {
        "response": "accepted",
        "period_start": WINDOW_START.isoformat(),
        "period_end": WINDOW_END.isoformat(),
        "reward_points": 14,
        **overrides,
    }


async def commitments(db) -> list[FlexibilityCommitment]:
    db.expunge_all()
    return list(
        (await db.execute(select(FlexibilityCommitment))).scalars().all()
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


# @verifies REQ-0029
async def test_a_community_window_is_offered_to_a_member(client, alice, dt):
    """
    The window belongs to the community, not to the participant. It is offered on
    membership alone — no personal forecast is required for it to appear.
    """
    dt.communities.set("rec_flexibility_windows", FetchResult([window()]))

    payload = (await client.get("/api/suggestions", headers=alice)).json()

    assert [item["id"] for item in payload] == ["w1"]
    assert payload[0]["community_kwh"] == 120.0
    assert payload[0]["impact_kwh_estimated"] is None


# @verifies REQ-0030
async def test_a_participant_in_no_community_is_offered_nothing(client, alice, dt):
    """
    Without a community there is nothing to fetch windows against, and the route returns
    an empty list rather than an error — a participant not yet enrolled sees an empty
    screen, not a failure.
    """
    dt.participants.profile_response = make_profile(None)
    dt.communities.set("rec_flexibility_windows", FetchResult([window()]))

    assert (await client.get("/api/suggestions", headers=alice)).json() == []


# @verifies REQ-0030
async def test_a_profile_lookup_failure_is_an_empty_list(client, alice, dt):
    """
    Same outcome as no membership, and indistinguishable from it in the response. A
    Digital Twin outage renders as "no suggestions today", which is the wrong story but
    the right failure mode for a screen the participant cannot act on anyway.
    """
    dt.participants.profile_response = RuntimeError("digital twin unavailable")

    assert (await client.get("/api/suggestions", headers=alice)).json() == []


# @verifies REQ-0030
async def test_a_window_fetch_failure_is_an_empty_list(client, alice, dt):
    dt.communities.set("rec_flexibility_windows", RuntimeError("fetcher failed"))

    assert (await client.get("/api/suggestions", headers=alice)).json() == []


# @verifies REQ-0031
async def test_personal_enrichment_decorates_the_window(client, alice, dt):
    """
    The community fetch says *when*; the per-device fetch says what it is worth to this
    participant. The second is keyed on the window bounds, and the keys arrive naive
    from the fetcher while the community rows are normalised to UTC — a mismatch there
    would silently drop every personal figure and leave the window looking unprofitable.
    """
    dt.communities.set("rec_flexibility_windows", FetchResult([window()]))
    dt.participants.fetch_response = FetchResult(
        [
            {
                "window_start": WINDOW_START.replace(tzinfo=None).isoformat(),
                "window_end": WINDOW_END.replace(tzinfo=None).isoformat(),
                "estimated_kwh": 1.8,
                "reward_points_estimated": 14,
                "confidence": 0.8,
            }
        ]
    )

    payload = (await client.get("/api/suggestions", headers=alice)).json()

    assert payload[0]["impact_kwh_estimated"] == 1.8
    assert payload[0]["reward_points"] == 14
    assert payload[0]["confidence"] == 0.8


# @verifies REQ-0031
async def test_enrichment_failure_does_not_hide_the_community_window(client, alice, dt):
    """
    Best-effort, and the ordering of the two calls is what makes that true: the personal
    fetch happens after the community one and its failure is caught. A participant whose
    device is unreachable still sees the community's opportunity, without a personal
    number attached.
    """
    dt.communities.set("rec_flexibility_windows", FetchResult([window()]))
    dt.participants.assets_response = RuntimeError("assets unavailable")

    payload = (await client.get("/api/suggestions", headers=alice)).json()

    assert [item["id"] for item in payload] == ["w1"]
    assert payload[0]["reward_points"] is None


# @verifies REQ-0031
async def test_a_participant_with_no_metered_device_still_sees_windows(client, alice, dt):
    """
    No `sensor_id` means no per-device fetch is attempted at all — the enrichment is
    skipped rather than requested and discarded.
    """
    dt.communities.set("rec_flexibility_windows", FetchResult([window()]))
    dt.participants.assets_response = Assets([Asset(None)])

    payload = (await client.get("/api/suggestions", headers=alice)).json()

    assert len(payload) == 1
    assert [c for c in dt.participants.calls if c["method"] == "fetch_values"] == []


# @verifies REQ-0032
async def test_a_window_already_committed_to_is_not_offered_again(client, alice, dt, db):
    """
    The filter is on `status == "committed"` and on this participant's rows only, so a
    window Alice cancelled reappears and a window Bob accepted still shows for Alice.
    """
    db.add(
        FlexibilityCommitment(
            user_id=USER_SUB,
            suggestion_id="w1",
            suggestion_type="shift-consumption",
            period_start=WINDOW_START,
            period_end=WINDOW_END,
            status="committed",
            reward_points_estimated=14,
        )
    )
    await db.commit()
    dt.communities.set("rec_flexibility_windows", FetchResult([window(), window("w2")]))

    payload = (await client.get("/api/suggestions", headers=alice)).json()

    assert [item["id"] for item in payload] == ["w2"]


# @verifies REQ-0002
async def test_listing_needs_a_token(client):
    assert (await client.get("/api/suggestions")).status_code == 401


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


# @verifies REQ-0033
async def test_accepting_writes_a_commitment_against_the_caller(client, alice, dt, db):
    """
    The community and the device are resolved here and stored on the row, because
    settlement needs both later and neither is recoverable from the suggestion id.
    """
    response = await client.post(
        "/api/suggestions/w1/respond", json=respond_body(), headers=alice
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "committed"
    assert payload["reward_points_estimated"] == 14

    stored, = await commitments(db)
    assert stored.user_id == USER_SUB
    assert stored.suggestion_id == "w1"
    assert stored.community_id == COMMUNITY
    assert stored.device_id == DEVICE
    assert stored.status == "committed"


# @verifies REQ-0033
async def test_the_reward_defaults_to_ten_when_the_client_sends_none(client, alice, dt, db):
    """
    A flat 10 points, unrelated to the window. It is what the participant is told they
    have earned until settlement replaces it with the measured figure — so a client that
    omits the field promises the same reward for every window regardless of size.
    """
    await client.post(
        "/api/suggestions/w1/respond",
        json=respond_body(reward_points=None),
        headers=alice,
    )

    stored, = await commitments(db)
    assert stored.reward_points_estimated == 10


# @verifies REQ-0033
async def test_an_unparseable_window_becomes_the_next_hour(client, alice, dt, db):
    """
    Rather than a `400`. The commitment is recorded against a window the participant did
    not choose, and settlement will later fetch readings for that hour — so a client bug
    here produces a plausible, wrong settlement rather than a visible failure.
    """
    before = datetime.now(timezone.utc)

    await client.post(
        "/api/suggestions/w1/respond",
        json=respond_body(period_start="tomorrow-ish", period_end="later"),
        headers=alice,
    )

    stored, = await commitments(db)
    assert stored.period_start.replace(tzinfo=timezone.utc) >= before
    assert stored.period_end - stored.period_start == timedelta(hours=1)


# @verifies REQ-0034
async def test_an_unresolvable_community_still_records_the_commitment(client, alice, dt, db):
    """
    The participant's decision is the thing of value and it is kept whatever the Digital
    Twin says. What it costs is settlement: a row with no `community_id` is skipped
    forever by `settle_completed_windows`, so this commitment can never be paid out.
    """
    dt.participants.profile_response = RuntimeError("digital twin unavailable")
    dt.participants.assets_response = RuntimeError("digital twin unavailable")

    response = await client.post(
        "/api/suggestions/w1/respond", json=respond_body(), headers=alice
    )

    assert response.status_code == 200
    stored, = await commitments(db)
    assert stored.community_id is None
    assert stored.device_id is None
    assert stored.status == "committed"


# ---------------------------------------------------------------------------
# Declining
# ---------------------------------------------------------------------------


# @verifies REQ-0035
async def test_declining_is_recorded_and_awards_nothing(client, alice, dt, db):
    """
    Declines are persisted so the acceptance rate is computable from `/export`. The
    response reports zero points regardless of what the client asked for — but the row
    keeps the estimate it was sent, so the two disagree.
    """
    response = await client.post(
        "/api/suggestions/w1/respond",
        json=respond_body(response="declined"),
        headers=alice,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "declined"
    assert payload["commitment_id"] is None
    assert payload["reward_points_estimated"] == 0

    stored, = await commitments(db)
    assert stored.status == "rejected"
    assert stored.reward_points_estimated == 14


# @verifies REQ-0035
async def test_declining_asks_the_digital_twin_nothing(client, alice, dt, db):
    """
    No profile lookup, no asset lookup, no publish. A decline is a row and nothing else,
    which is also why a declined row carries neither community nor device.
    """
    await client.post(
        "/api/suggestions/w1/respond",
        json=respond_body(response="declined"),
        headers=alice,
    )

    assert dt.participants.calls == []
    stored, = await commitments(db)
    assert stored.community_id is None


# ---------------------------------------------------------------------------
# The two fire-and-forget side effects
# ---------------------------------------------------------------------------


# @verifies REQ-0036
async def test_accepting_publishes_the_commitment_to_mqtt(client, alice, dt, db, monkeypatch):
    """
    `celine/flexibility/committed/<user>` — how the rest of the platform learns about a
    commitment without polling this service.
    """
    broker = FakeBroker()
    monkeypatch.setattr(module, "get_broker", lambda: broker)

    await client.post("/api/suggestions/w1/respond", json=respond_body(), headers=alice)

    message, = broker.published
    assert message.topic == f"celine/flexibility/committed/{USER_SUB}"
    assert message.payload["community_id"] == COMMUNITY
    assert message.payload["device_id"] == DEVICE
    assert message.payload["reward_points_estimated"] == 14


@pytest.mark.parametrize(
    "broker_state",
    [
        pytest.param(None, id="no broker at all"),
        pytest.param(FakeBroker(connected=False), id="broker not connected"),
        pytest.param(FakeBroker(fails=True), id="publish raises"),
    ],
)
# @verifies REQ-0036
async def test_a_broker_failure_does_not_fail_the_accept(
    client, alice, dt, db, monkeypatch, broker_state
):
    """
    The commitment is already committed to the database before the publish is attempted.
    Failing the response now would tell the participant their commitment was not
    recorded, when it was — and they would accept again, producing a second row.
    """
    monkeypatch.setattr(module, "get_broker", lambda: broker_state)

    response = await client.post(
        "/api/suggestions/w1/respond", json=respond_body(), headers=alice
    )

    assert response.status_code == 200
    assert len(await commitments(db)) == 1


# @verifies REQ-0037
async def test_accepting_schedules_the_pre_window_nudge(client, alice, dt, db, monkeypatch):
    scheduled: list[dict] = []

    async def _schedule(nudging, **kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(module, "get_nudging_client", lambda: FakeNudgingClient())
    monkeypatch.setattr(module, "schedule_pre_window_nudge", _schedule)

    await client.post("/api/suggestions/w1/respond", json=respond_body(), headers=alice)

    call, = scheduled
    assert call["user_id"] == USER_SUB
    assert call["suggestion_id"] == "w1"
    assert call["community_id"] == COMMUNITY
    assert call["reward_points_estimated"] == 14


# @verifies REQ-0037
async def test_a_nudge_failure_does_not_fail_the_accept(client, alice, dt, db, monkeypatch):
    """
    The reasoning is in `.agents/knowledge/nudge-scheduling-is-best-effort.md`: a
    notification service being briefly unreachable is not a reason to reject a
    commitment the participant has already made, and `send_pending_reminders` recovers
    the notification once the window opens.

    **This test proves the accept survived. It proves nothing about the reminder** —
    those are two claims and only the first one is checkable here.
    """
    async def _explode(nudging, **kwargs):
        raise RuntimeError("nudging unavailable")

    monkeypatch.setattr(module, "get_nudging_client", lambda: FakeNudgingClient())
    monkeypatch.setattr(module, "schedule_pre_window_nudge", _explode)

    response = await client.post(
        "/api/suggestions/w1/respond", json=respond_body(), headers=alice
    )

    assert response.status_code == 200
    assert len(await commitments(db)) == 1


# @verifies REQ-0037
async def test_no_nudging_client_means_no_nudge_and_no_error(client, alice, dt, db):
    """
    The client is built at startup. Before then — and in any process where the MQTT
    wiring failed — accepting still works and no reminder is ever scheduled.
    """
    response = await client.post(
        "/api/suggestions/w1/respond", json=respond_body(), headers=alice
    )

    assert response.status_code == 200


# @verifies REQ-0002
async def test_responding_needs_a_token(client):
    response = await client.post("/api/suggestions/w1/respond", json=respond_body())

    assert response.status_code == 401
