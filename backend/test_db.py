import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings

async def test_db():
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            print("Successfully connected to PostgreSQL via SQLAlchemy async and asyncpg")
    except Exception as e:
        print(f"Connection failed: {e}")

asyncio.run(test_db())
