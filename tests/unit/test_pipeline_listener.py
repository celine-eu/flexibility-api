"""What arrives on `celine/pipelines/runs/+`, and what this service does about it.

This is the only entry point to the service that is not an HTTP request, and the only one
with no caller to report an error to: everything it rejects, it rejects silently into the
log. **Nothing calls this service to make a pipeline event happen** — so a broker that is
down means opportunities stop being scheduled and commitments stop being settled, with no
failing response anywhere to notice. That is recorded in
`.agents/knowledge/what-this-repository-depends-on.md` and it is why the routing below is
pinned rather than trusted.

The three handlers have their own files. The question here is only which one the listener
decided to call, and with what.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from celine.flexibility.services import pipeline_listener as listener
from celine.flexibility.services.pipeline_listener import on_pipeline_run

from tests.fakes import FakeDTClient, FakeNudgingClient, FakeRegistryClient, Message


def run_payload(**overrides) -> dict:
    return {
        "namespace": "celine",
        "flow": "meters-flow",
        "status": "completed",
        "run_id": "3f2c1e00-0000-4000-8000-000000000001",
        "timestamp": "2026-07-03T02:30:00+00:00",
        **overrides,
    }


@pytest.fixture
def wired(monkeypatch, db_sessionmaker):
    """The listener with its startup-time clients in place and its handlers recorded.

    The three module-level clients are created in `create_broker()` at application
    startup, so a handler reached before startup sees `None` — a state each branch
    guards for and which is covered separately below.
    """
    calls: list[tuple[str, dict]] = []

    async def _reminders(session, nudging):
        calls.append(("reminders", {}))
        return 1

    async def _opportunity(dt, registry, nudging):
        calls.append(("opportunity", {}))

    async def _settle(session, dt, period_date):
        calls.append(("settle", {"period_date": period_date}))
        return 1

    monkeypatch.setattr(listener, "send_pending_reminders", _reminders)
    monkeypatch.setattr(listener, "notify_flexibility_opportunity", _opportunity)
    monkeypatch.setattr(listener, "settle_completed_windows", _settle)
    monkeypatch.setattr(listener, "SessionLocal", db_sessionmaker)
    monkeypatch.setattr(listener, "_dt_client", FakeDTClient())
    monkeypatch.setattr(listener, "_registry_client", FakeRegistryClient())
    monkeypatch.setattr(listener, "_nudging_client", FakeNudgingClient())
    return calls


def called(calls: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _ in calls]


# ---------------------------------------------------------------------------
# What is ignored
# ---------------------------------------------------------------------------


# @verifies REQ-0051
async def test_a_message_that_is_not_a_run_event_is_dropped(wired):
    """
    The topic is a wildcard subscription and the payload is another service's. A
    malformed one must not take the subscription down — an exception escaping here kills
    the handler for every subsequent message, not just this one.
    """
    await on_pipeline_run(Message({"nonsense": True}))

    assert wired == []


@pytest.mark.parametrize("status", ["started", "failed", "running"])
# @verifies REQ-0051
async def test_only_a_completed_run_does_anything(wired, status):
    """
    A `started` event carries the same flow name as the `completed` one that follows it.
    Acting on both would settle every window twice and send every reminder twice.
    """
    await on_pipeline_run(Message(run_payload(status=status)))

    assert wired == []


# @verifies REQ-0051
async def test_an_unknown_flow_is_ignored(wired):
    """
    Every pipeline in the platform publishes to this topic. Three flows concern this
    service and the rest are somebody else's.
    """
    await on_pipeline_run(Message(run_payload(flow="some-other-flow")))

    assert wired == []


# ---------------------------------------------------------------------------
# The three flows
# ---------------------------------------------------------------------------


# @verifies REQ-0052
async def test_meters_flow_sends_the_due_reminders(wired):
    """
    `meters-flow` completes roughly every five minutes, which is what makes it the tick
    the reminder loop rides on. There is no scheduler in this service.
    """
    await on_pipeline_run(Message(run_payload(flow="meters-flow")))

    assert called(wired) == ["reminders"]


# @verifies REQ-0052
async def test_forecasting_flow_notifies_the_opportunity(wired):
    await on_pipeline_run(Message(run_payload(flow="rec-forecasting-flow")))

    assert called(wired) == ["opportunity"]


# @verifies REQ-0052
async def test_flexibility_flow_settles_the_previous_day(wired):
    """
    The pipeline runs after midnight, and what it has just computed is *yesterday's*
    metered data. Settling the event's own date would settle a day with no readings yet
    — every commitment would be skipped for lack of data and never revisited, because
    the next run would look at the wrong day too.
    """
    await on_pipeline_run(
        Message(run_payload(flow="rec-flexibility-flow", timestamp="2026-07-03T02:30:00+00:00"))
    )

    (name, kwargs), = wired
    assert name == "settle"
    assert kwargs["period_date"] == date(2026, 7, 2)


# @verifies REQ-0052
async def test_an_unparseable_timestamp_falls_back_to_yesterday(wired):
    """
    The timestamp is a string on the wire and comes from another service. Falling back
    to the wall clock keeps settlement running on a malformed event rather than skipping
    a day silently — which is the failure nobody would notice.
    """
    await on_pipeline_run(
        Message(run_payload(flow="rec-flexibility-flow", timestamp="not-a-timestamp"))
    )

    expected = datetime.now(timezone.utc).date() - timedelta(days=1)
    (_, kwargs), = wired
    assert kwargs["period_date"] == expected


# ---------------------------------------------------------------------------
# Before startup finished
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flow", ["meters-flow", "rec-forecasting-flow", "rec-flexibility-flow"]
)
# @verifies REQ-0053
async def test_a_flow_arriving_before_the_clients_exist_is_dropped(monkeypatch, wired, flow):
    """
    `create_broker()` sets the module-level clients and then connects; a message can in
    principle arrive against a partially built module. Each branch guards for it, and
    the event is lost rather than retried — there is no queue behind this.
    """
    monkeypatch.setattr(listener, "_dt_client", None)
    monkeypatch.setattr(listener, "_registry_client", None)
    monkeypatch.setattr(listener, "_nudging_client", None)

    await on_pipeline_run(Message(run_payload(flow=flow)))

    assert wired == []


# @verifies REQ-0053
def test_the_accessors_expose_what_startup_built(monkeypatch):
    """
    `suggestions.py` reaches the broker and the nudging client through these rather than
    importing the globals, which is what lets the accept path publish and schedule
    without a second set of credentials.
    """
    monkeypatch.setattr(listener, "_broker", "the-broker")
    monkeypatch.setattr(listener, "_nudging_client", "the-nudging-client")

    assert listener.get_broker() == "the-broker"
    assert listener.get_nudging_client() == "the-nudging-client"
