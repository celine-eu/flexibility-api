"""Doubles for everything this repository does not own.

Each one is written from what *this* code reaches for, not from the SDK's own classes.
The cut is deliberate and it has a cost: a green run says nothing about whether
`celine-sdk` still returns these shapes. See
`docs/decisions/ADR-0004-every-other-boundary-is-faked-at-the-sdk-client.md`.

The exception is the policy engine, which is real. Faking it would be worse than
useless — `AccessPolicy` allows when its engine is missing, so a faked engine and a
broken one produce identical passes.
"""

from __future__ import annotations

from typing import Any

from celine.sdk.auth.jwt import JwtUser

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def make_user(
    *,
    sub: str,
    scope: str | None = None,
    groups: list[str] | None = None,
    preferred_username: str | None = None,
    email: str | None = None,
    **claims: Any,
) -> JwtUser:
    """Build a `JwtUser` the way `JwtUser.from_token` would from these claims.

    `is_service_account` is a property computed from `claims`, never a field, so a fake
    that set it directly could type a principal the SDK would type the other way. Here
    it is left to the SDK: pass `preferred_username="service-account-…"` and the SDK
    decides, exactly as it does in production.
    """
    payload: dict[str, Any] = {"sub": sub, **claims}
    if scope is not None:
        payload["scope"] = scope
    if groups is not None:
        payload["groups"] = groups
    if preferred_username is not None:
        payload["preferred_username"] = preferred_username
    if email is not None:
        payload["email"] = email

    return JwtUser(
        sub=sub,
        email=payload.get("email"),
        preferred_username=payload.get("preferred_username"),
        claims=payload,
        token="fake-token",
    )


def make_service(client_id: str = "svc-flexibility", *, scope: str = "") -> JwtUser:
    """A client-credentials principal, as Keycloak issues one.

    `preferred_username = service-account-<client_id>` is the signal
    `is_service_account()` treats as authoritative.
    """
    return make_user(
        sub=f"service-account-{client_id}",
        scope=scope,
        preferred_username=f"service-account-{client_id}",
        client_id=client_id,
    )


class FakeJwt:
    """Stands in for the `JwtUser` class as `security/auth.py` uses it.

    Only `from_token` is replaced. Everything `get_user_from_request` does around it —
    reading the configured header, falling back to `Authorization: Bearer`, mapping the
    failure to a 401 — stays under test.

    `raises` makes the next decode fail with a chosen exception, which is how the
    expired- and invalid-token branches are reached without minting real JWTs.
    """

    def __init__(self) -> None:
        self._users: dict[str, JwtUser] = {}
        self.raises: BaseException | None = None
        self.decoded: list[str] = []

    # -- the part `auth.py` calls --

    def from_token(self, token: str, oidc: Any = None, **_: Any) -> JwtUser:
        self.decoded.append(token)
        if self.raises is not None:
            raise self.raises
        user = self._users.get(token)
        if user is None:
            raise ValueError(f"no fake user registered for token {token!r}")
        return user

    # -- test-facing helpers --

    def mint(self, user: JwtUser) -> str:
        token = f"token-{user.sub}-{len(self._users)}"
        self._users[token] = user
        return token

    def headers(self, user: JwtUser, *, header: str | None = None) -> dict[str, str]:
        """Headers carrying a token for *user*, in the production header by default."""
        return {header or "x-auth-request-access-token": self.mint(user)}

    def bearer(self, user: JwtUser) -> dict[str, str]:
        return {"authorization": f"Bearer {self.mint(user)}"}


# ---------------------------------------------------------------------------
# Digital Twin
# ---------------------------------------------------------------------------


class Item:
    """A fetcher row. The code only ever calls `.to_dict()` on one."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


class FetchResult:
    """Shaped like the SDK's fetch result as this service reads one: `.count`, `.items`.

    `count` is stored rather than derived, because every caller here guards on
    `result.count == 0` and a fake that computed it could not produce the
    count-disagrees-with-items case.
    """

    def __init__(self, rows: list[dict[str, Any]], *, count: int | None = None) -> None:
        self.items = [Item(r) for r in rows]
        self.count = len(rows) if count is None else count


class FakeCommunities:
    """The `dt.communities` namespace — only `fetch_values` is reached.

    Responses are keyed by `fetcher_id`. A response that is an exception instance is
    raised, which is how the "DT is down" branch of settlement and opportunity nudging
    is exercised.
    """

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    def set(self, fetcher_id: str, value: Any) -> None:
        self.responses[fetcher_id] = value

    async def fetch_values(
        self, *, community_id: str, fetcher_id: str, payload: dict | None = None
    ) -> Any:
        self.calls.append(
            {"community_id": community_id, "fetcher_id": fetcher_id, "payload": payload}
        )
        value = self.responses.get(fetcher_id, FetchResult([]))
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(payload or {})
        return value


class FakeParticipants:
    """The `dt.participants` namespace — `profile`, `assets`, `fetch_values`."""

    def __init__(self) -> None:
        self.profile_response: Any = None
        self.assets_response: Any = None
        self.fetch_response: Any = FetchResult([])
        self.calls: list[dict[str, Any]] = []

    async def profile(self, participant_id: str) -> Any:
        self.calls.append({"method": "profile", "participant_id": participant_id})
        if isinstance(self.profile_response, BaseException):
            raise self.profile_response
        return self.profile_response

    async def assets(self, participant_id: str) -> Any:
        self.calls.append({"method": "assets", "participant_id": participant_id})
        if isinstance(self.assets_response, BaseException):
            raise self.assets_response
        return self.assets_response

    async def fetch_values(
        self, *, participant_id: str, fetcher_id: str, payload: dict | None = None
    ) -> Any:
        self.calls.append(
            {
                "method": "fetch_values",
                "participant_id": participant_id,
                "fetcher_id": fetcher_id,
                "payload": payload,
            }
        )
        if isinstance(self.fetch_response, BaseException):
            raise self.fetch_response
        return self.fetch_response


class FakeDTClient:
    """Stands in for `celine.sdk.dt.client.DTClient`."""

    def __init__(self) -> None:
        self.communities = FakeCommunities()
        self.participants = FakeParticipants()


# -- the participant profile shape `suggestions.py` unwraps ------------------


class Community:
    def __init__(self, key: str) -> None:
        self.key = key


class Asset:
    def __init__(self, sensor_id: str | None) -> None:
        self.sensor_id = sensor_id


class Assets:
    def __init__(self, items: list[Asset]) -> None:
        self.items = items


def make_profile(community_key: str | None):
    """A participant profile whose `.membership` is a real `UserMembershipSchema`.

    The route narrows with `isinstance(_m, UserMembershipSchema)`, so a duck-typed
    membership would take the *other* branch and silently return no community. The SDK
    class is therefore used, not imitated.
    """
    from celine.sdk.openapi.dt.models import UserMembershipSchema

    class Profile:
        pass

    profile = Profile()
    if community_key is None:
        profile.membership = None
    else:
        # The generated model is a plain attrs class and validates nothing, so the two
        # required fields can carry stand-ins. Only `.community.key` is ever read.
        profile.membership = UserMembershipSchema(
            community=Community(community_key), member=object()
        )
    return profile


# ---------------------------------------------------------------------------
# rec-registry
# ---------------------------------------------------------------------------


class Member:
    def __init__(self, user_id: str | None, community_key: str | None = None) -> None:
        self.user_id = user_id
        self.community_key = community_key


class FakeRegistryClient:
    """Stands in for `RecRegistryAdminClient` — only `list_members` is reached."""

    def __init__(self, members: Any = None) -> None:
        self.members: Any = [] if members is None else members
        self.calls: list[str] = []

    async def list_members(self, community_id: str) -> Any:
        self.calls.append(community_id)
        if isinstance(self.members, BaseException):
            raise self.members
        return self.members


# ---------------------------------------------------------------------------
# nudging-tool
# ---------------------------------------------------------------------------


class FakeNudgingClient:
    """Stands in for `NudgingAdminClient`.

    Events are kept as the `DigitalTwinEvent` the caller built, so a test asserting on a
    payload is asserting on something that survived `DigitalTwinEvent.from_dict` — the
    one place a malformed payload shows up.

    `_token_provider` is the private attribute `schedule_nudge.py` reaches into. It is
    `None` by default, which is the branch that refuses to schedule.
    """

    def __init__(self, *, fails: bool = False, token: str | None = None) -> None:
        self.events: list[Any] = []
        self.fails = fails
        self.fails_for: set[str] = set()
        self._token_provider = _FakeTokenProvider(token) if token else None

    async def ingest_event(self, event: Any) -> None:
        if self.fails:
            raise RuntimeError("nudging unavailable")
        if self.fails_for and event.to_dict().get("user_id") in self.fails_for:
            raise RuntimeError("nudging rejected this event")
        self.events.append(event)

    def payloads(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events]


class _AccessToken:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token


class _FakeTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> _AccessToken:
        return _AccessToken(self._token)


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------


class PublishResult:
    def __init__(self, success: bool = True, error: str | None = None) -> None:
        self.success = success
        self.error = error


class FakeBroker:
    """Stands in for `MqttBroker` as `_publish_committed` uses it."""

    def __init__(self, *, connected: bool = True, fails: bool = False) -> None:
        self.is_connected = connected
        self.fails = fails
        self.published: list[Any] = []

    async def publish(self, message: Any) -> PublishResult:
        if self.fails:
            raise RuntimeError("broker gone")
        self.published.append(message)
        return PublishResult(success=True)


class Message:
    """Shaped like `celine.sdk.broker.ReceivedMessage` as `on_pipeline_run` reads one."""

    def __init__(self, payload: dict, topic: str = "celine/pipelines/runs/1") -> None:
        self.payload = payload
        self.topic = topic
