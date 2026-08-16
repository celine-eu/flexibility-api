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

Four things it does not prove:

- **That the migrations produce this schema.** The tables come from the models;
  `alembic upgrade head` is a separate path exercised by nothing. A model that has
  drifted from its migrations passes the whole suite. Tracked as
  [#23](https://github.com/celine-eu/flexibility-api/issues/23); `alembic check` is the
  half that matters, and it needs a real PostgreSQL.
- **Timezone behaviour.** `DateTime(timezone=True)` is `TIMESTAMPTZ` on PostgreSQL and a
  naive string on SQLite, so an aware datetime written in a test comes back naive. Every
  comparison in this service is UTC-on-both-sides, so nothing depends on the difference
  today — but a bug that only appears when a stored offset is non-zero would not be
  caught here.
- **Type behaviour.** `Uuid` is native on PostgreSQL and `CHAR(32)` on SQLite.
- **Concurrency.** `StaticPool` is one connection. Two sessions in one test share it, and
  a test that expected them to hold independent transactions would be testing SQLite's
  behaviour, not the service's.

Closing the first needs a CI job with a real PostgreSQL service container.
`../celine-ai-assistant` has one; doing the same here is the obvious next step, and it is
deliberately **not** part of the default `pytest` run — the property that this suite needs
nothing running is what keeps the baseline cheap enough that nobody skips it.
