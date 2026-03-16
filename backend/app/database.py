"""
Database layer — Supabase Python client + async SQLAlchemy engine.

Exports
-------
- ``supabase_client`` : synchronous Supabase client (used for most CRUD).
- ``async_engine``    : async SQLAlchemy engine (for advanced queries).
- ``async_session``   : async session factory.
- ``get_db()``        : FastAPI dependency that yields an async session.
"""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from supabase import create_client, Client

from app.config import settings

logger = logging.getLogger("terratrust.database")

# ---------------------------------------------------------------------------
# Supabase client (REST + Auth)
# ---------------------------------------------------------------------------
supabase_client: Client = create_client(
    settings.SUPABASE_URL,
    settings.SUPABASE_SERVICE_KEY,
)

# ---------------------------------------------------------------------------
# Async SQLAlchemy engine (direct PostgreSQL via asyncpg)
# ---------------------------------------------------------------------------
_dsn = settings.DATABASE_URL or settings.supabase_postgres_dsn

async_engine = create_async_engine(
    _dsn,
    echo=settings.ENVIRONMENT == "development",
    pool_size=5,
    max_overflow=10,
)

async_session = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for a single request."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
