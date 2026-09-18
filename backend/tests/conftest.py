"""
conftest.py — async test fixtures for ReconcileAI database tests.

Design decision:
  asyncpg connections are bound to the event loop in which they are created.
  pytest-asyncio creates a new event loop per test function by default.
  Therefore, we create a new SQLAlchemy engine (and a fresh schema) per test
  function rather than sharing a session-scoped engine.

  This is slightly slower (one schema create/drop per test) but is the correct,
  race-condition-free approach with asyncpg on Windows' ProactorEventLoop.
"""
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:password@127.0.0.1:5432/reconcileai_test"
ADMIN_DATABASE_URL = "postgresql+asyncpg://postgres:password@127.0.0.1:5432/postgres"


@pytest_asyncio.fixture
async def session():
    """
    Provides a fresh AsyncSession backed by a clean schema for each test.
    The schema is created before the test and dropped after it.
    Each test therefore operates in total isolation.
    """
    # Ensure the test DB exists (idempotent)
    admin = create_async_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname='reconcileai_test'")
        )
        if not exists.scalar():
            await conn.execute(text("CREATE DATABASE reconcileai_test"))
    await admin.dispose()

    # Import Base + all models
    from app.db.base import Base  # noqa: F401
    import app.models  # noqa: F401

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Fresh schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as s:
        yield s
        await s.rollback()

    # Tear down schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
