"""Shared fixtures.

**No test reaches a real service — with one deliberate exception.** This repository
talks to PostgreSQL, the Digital Twin, rec-registry, nudging-tool, an MQTT broker and
Keycloak; all six are faked here at the narrowest boundary that still exercises our code.

OPA is the exception, and it is on purpose. `celine.sdk.policies` evaluates Rego **in
process** via `regorus` — no server, no socket — so the real `policies/flexibility.rego`
is what the suite evaluates. Faking it would be worse than useless: `AccessPolicy` falls
back to `allow=True` when the bundle will not load, so a suite that faked the engine and
a suite whose engine silently failed to load would produce identical passes. See
`docs/decisions/ADR-0002-the-policy-bundle-is-real-in-tests.md`.

The environment is set *before* `celine.flexibility` is imported anywhere. `config.py`
builds its `Settings()` at import time and `db/session.py` builds the engine from it, so
by the time a test module is collected the wiring has already happened and cannot be
undone by a fixture.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Must run before the first `celine.flexibility` import. Do not move below them.
# ---------------------------------------------------------------------------

# Parsed by SQLAlchemy at import to build the module-level engine; never connected to,
# because every test that needs a database gets the SQLite session from `db_sessionmaker`.
# It stays a *postgres* DSN: the model's `MetaData(schema=…)` and the async engine are
# both built from this string at import and a sqlite URL here would change the schema
# under the tables the fixtures create.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:1/test"
os.environ["CELINE_MQTT_HOST"] = "127.0.0.1"
os.environ["CELINE_MQTT_PORT"] = "1"
os.environ["NUDGING_API_URL"] = "http://nudging.invalid"
os.environ["DIGITAL_TWIN_API_URL"] = "http://dt.invalid"
os.environ["REC_REGISTRY_URL"] = "http://registry.invalid"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from celine.flexibility.core.config import settings  # noqa: E402
from celine.flexibility.db import get_session  # noqa: E402
from celine.flexibility.main import create_app  # noqa: E402
from celine.flexibility.security import auth as auth_module  # noqa: E402
from celine.flexibility.security import policy as policy_module  # noqa: E402

from tests.fakes import FakeJwt, make_service, make_user  # noqa: E402

COMMUNITY = "it-energy-community"
USER_SUB = "user-alice"
OTHER_SUB = "user-bob"
DEVICE = "sensor-alice-1"


# ---------------------------------------------------------------------------
# The policy engine must be real, and must be loaded
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def policy_engine():
    """Refuse to run the policy tests if the Rego bundle did not load.

    `AccessPolicy._evaluate` returns `Decision(True, "no-policy-engine")` when
    `self._engine is None`. Every authorisation assertion downstream would then pass
    while proving nothing at all. This fixture is the difference between "the policy
    permits it" and "no policy ran".
    """
    engine = policy_module.AccessPolicy()._engine
    assert engine is not None, (
        "the OPA bundle did not load, so AccessPolicy is in its permissive fallback "
        "and no authorisation assertion in this suite means anything"
    )
    assert engine.has_package("celine.flexibility.access")
    return engine


# ---------------------------------------------------------------------------
# Database — real SQLAlchemy, real SQL, SQLite instead of PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_sessionmaker():
    """A SQLite engine carrying the real `Base.metadata` schema.

    One engine per test, so a test never sees another test's rows. `StaticPool` is what
    makes an in-memory SQLite database usable at all here: the default pool hands each
    connection its own private database, which would lose the tables between
    `create_all` and the first query.

    `Base.metadata` carries `schema="flexibility"`, and SQLite has no schemas — it has
    attached databases, which qualify identically. Attaching a second in-memory database
    under that name is what lets the production table definitions run unmodified.

    This is not PostgreSQL, and the schema is built from the models rather than from the
    migrations. What that costs is stated in
    `docs/decisions/ADR-0003-the-database-is-real-and-sqlite.md`.
    """
    from celine.flexibility.models.commitment import Base

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    schema = settings.db_schema

    @event.listens_for(engine.sync_engine, "connect")
    def _attach_schema(dbapi_connection, _record):
        dbapi_connection.execute(f"ATTACH DATABASE ':memory:' AS {schema}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
async def db(db_sessionmaker) -> AsyncSession:
    """A session for arranging rows and asserting on them directly.

    Separate from the one a request gets, because every route body does
    `async with db as session:` — which closes the session on the way out. A shared
    session would be dead after the first request.
    """
    async with db_sessionmaker() as session:
        yield session


# ---------------------------------------------------------------------------
# Identity — the JWKS fetch and the signature check are faked, nothing else is
# ---------------------------------------------------------------------------


@pytest.fixture
def jwt(monkeypatch) -> FakeJwt:
    """Mint tokens that `get_user_from_request` will accept.

    Only `JwtUser.from_token` is replaced — signing a real RS256 token and serving a
    JWKS would be testing PyJWT. Header selection, the Bearer fallback and the 401
    mapping all stay real.
    """
    fake = FakeJwt()
    monkeypatch.setattr(auth_module, "JwtUser", fake)
    return fake


@pytest.fixture
def alice(jwt: FakeJwt) -> dict[str, str]:
    """Headers for an ordinary participant."""
    return jwt.headers(make_user(sub=USER_SUB, scope="flexibility.read flexibility.write"))


@pytest.fixture
def bob(jwt: FakeJwt) -> dict[str, str]:
    """Headers for a *different* participant — the one who must not see Alice's rows."""
    return jwt.headers(make_user(sub=OTHER_SUB, scope="flexibility.read flexibility.write"))


@pytest.fixture
def service(jwt: FakeJwt) -> dict[str, str]:
    """Headers for a service account carrying every flexibility scope."""
    return jwt.headers(
        make_service(scope="flexibility.read flexibility.write flexibility.admin "
                           "flexibility.commitments.export")
    )


@pytest.fixture
def unscoped_service(jwt: FakeJwt) -> dict[str, str]:
    """Headers for a service account holding no flexibility scope at all.

    The principal the Rego bundle exists to refuse, and the one no test could reach until
    #21 was fixed — every decision was an allow, so a service token was admitted on being
    a service token. Any Keycloak client with a valid token looked like this one.
    """
    return jwt.headers(make_service(client_id="svc-unrelated", scope="openid grid.read"))


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db_sessionmaker):
    """The real `create_app()`, with only the database session overridden.

    The lifespan creates the MQTT broker and connects it; httpx's `ASGITransport` never
    emits lifespan events, which is what keeps that from running. `PolicyMiddleware`,
    the routers and every `Depends` chain are the real ones.
    """
    application = create_app()

    async def _get_session():
        async with db_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_session] = _get_session
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
