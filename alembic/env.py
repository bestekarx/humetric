"""Alembic ortam konfigurasyonu — Humetric."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from alembic import context
from dotenv import load_dotenv

load_dotenv()

from humetric import config as humetric_config  # noqa: E402
from humetric.db.database import Base  # noqa: E402
from humetric.db import models  # noqa: F401, E402

target_metadata = Base.metadata
config_obj = context.config


def run_migrations_offline() -> None:
    url = humetric_config.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    connectable = create_engine(
        humetric_config.DATABASE_URL.replace("+asyncpg", "+psycopg"),
        pool_pre_ping=True,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
