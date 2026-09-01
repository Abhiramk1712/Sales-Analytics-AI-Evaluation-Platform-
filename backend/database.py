"""
database.py — SQLAlchemy async engine + session factory (lazy initialization)
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

# Engine and session factory are created lazily on first use so that
# importing this module does not require a live database connection.
_engine = None
_async_session_factory = None


class Base(DeclarativeBase):
    pass


def get_engine():
    """Return (and lazily create) the async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        from backend.config import settings
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory():
    """Return (and lazily create) the async session factory."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


async def get_db():
    """FastAPI dependency — yields a db session, ensures cleanup."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
