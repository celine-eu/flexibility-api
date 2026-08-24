# ADR-0003 — the database is real in tests, and it is SQLite

**Date:** 2026-08-15
**Status:** accepted

## Context

The separation between two participants' commitments is SQL, not policy. No
participant-facing route consults `AccessPolicy`, so `WHERE user_id = :sub` in
`src/celine/flexibility/api/commitments.py` is the whole of it (REQ-0021, REQ-0024). The same is true of every
selection this service makes: which commitments are due a reminder, which are open for
settlement, which windows are already committed to.

A faked store would assert the fake's filtering. Whatever else is faked here, this cannot
be.

That leaves how to run the real SQL. PostgreSQL is production and the only dialect the
migrations target; requiring it would mean no test runs without a container, and the suite
exists partly so that the baseline is cheap enough that nobody skips it.

## Decision

Run the real models and the real queries against in-memory SQLite via `aiosqlite`, with
the schema built from `Base.metadata.create_all` — not from the migrations.

One engine per test, on `StaticPool` so every connection reaches the same in-memory
database.

`Base.metadata` carries `schema="flexibility"`, and SQLite has no schemas. It has
**attached databases**, which qualify identically, so the fixture runs
`ATTACH DATABASE ':memory:' AS flexibility` on connect and the production table
definitions execute unmodified. This is why `DATABASE_URL` in `conftest.py` must stay a
PostgreSQL DSN pointing at a closed port: `settings.db_schema` is read at import to build
the metadata, and the tests override the session dependency rather than the URL.

## Consequences

Every isolation and selection test in the suite runs the SQL that ships.

Three things it does not prove:

- **Timezone behaviour.** `DateTime(timezone=True)` is `TIMESTAMPTZ` on PostgreSQL and a
  naive string on SQLite, so an aware datetime written in a test comes back naive. Every
  comparison in this service is UTC-on-both-sides, so nothing depends on the difference
  today — but a bug that only appears when a stored offset is non-zero would not be
  caught here.
- **Type behaviour.** `Uuid` is native on PostgreSQL and `CHAR(32)` on SQLite.
- **Concurrency.** `StaticPool` is one connection. Two sessions in one test share it, and
  a test that expected them to hold independent transactions would be testing SQLite's
  behaviour, not the service's.

A fourth was closed rather than lived with. **That the migrations produce this schema**
was proven by nothing at all — the tables come from the models, `alembic upgrade head` is
a separate path, and a model that had drifted from its revisions passed all 177 tests and
failed at deploy time against a real database. The `migrations` job in
`.github/workflows/test.yaml` now runs `upgrade head`, `alembic check` and
`downgrade base` against a `postgres:17` service container, on the same pushes and pull
requests as the suite ([#23](https://github.com/celine-eu/flexibility-api/issues/23)).
`alembic check` is the half that matters: `upgrade head` proves the revisions run, and
`check` proves they arrive at the schema the models describe.

It is a **separate job**, and deliberately not part of `pytest`. The property that the
default run needs nothing running is what keeps the baseline cheap enough that nobody
skips it, and that property is the whole reason this ADR chose SQLite.

The database tests themselves were **not** given a PostgreSQL leg. `db_sessionmaker`
builds its engine directly, so a second dialect means a new fixture contract rather than
an environment variable, and this service's SQL is `select().where()` with an `order_by` —
nothing whose behaviour divides the two engines. The three gaps above are properties of
this fixture and remain unproven; the day a query needs the real dialect, adding the leg
is a decision with its own record.
