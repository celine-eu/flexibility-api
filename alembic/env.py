from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from dotenv import load_dotenv

# Load .env before importing settings so defaults are resolved correctly
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_env_path)

# Ensure src/ is on the path for the editable install layout
_src = os.path.join(os.path.dirname(__file__), "..", "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from celine.flexibility.core.config import settings
from celine.flexibility.models.commitment import Base  # noqa: F401 — registers metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
_SCHEMA = settings.db_schema


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        model_schema = object.schema or _SCHEMA
        db_schema = compare_to.schema if compare_to is not None else _SCHEMA
        return model_schema == db_schema == _SCHEMA
    return True


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == _SCHEMA
    return True


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        include_name=include_name,
        compare_server_default=True,
        compare_type=True,
        include_schemas=True,
        version_table_schema=_SCHEMA,
    )
    context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool, future=True)
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_SCHEMA}"'))
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        include_name=include_name,
        compare_server_default=True,
        compare_type=True,
        version_table_schema=_SCHEMA,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
