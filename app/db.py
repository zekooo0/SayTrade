from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

# NullPool: one connection per session, no cross-session pool reuse. Correct
# scale for this MVP and avoids the classic "Task attached to a different event
# loop" failures when tests spin up a fresh loop per TestClient.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models. Alembic autogenerate reads Base.metadata."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, committed on success.

    Routes that deliberately commit mid-flight (approve, receive) leave
    nothing pending here; routes that only flush (user provisioning) get a
    deterministic commit; exceptions roll back the whole request.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
