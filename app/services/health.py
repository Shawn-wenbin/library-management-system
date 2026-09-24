from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError


async def check_database(session: AsyncSession) -> None:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        raise AppError(503, "DATABASE_UNAVAILABLE", "数据库暂未就绪") from None
