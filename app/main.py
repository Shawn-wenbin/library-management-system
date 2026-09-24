import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, health, users
from app.core.config import Settings
from app.core.errors import register_error_handlers
from app.core.security import hash_password
from app.db.session import create_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        config = settings or Settings()
        logging.basicConfig(
            level=config.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        # 避免调试级 SQL 日志及访问日志携带查询参数中的敏感值。
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").disabled = True
        engine = create_engine(config)
        application.state.settings = config
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
        try:
            application.state.dummy_password_hash = await hash_password(secrets.token_urlsafe(32))
            logging.getLogger(__name__).info("应用启动")
            yield
        finally:
            await engine.dispose()
            logging.getLogger(__name__).info("应用关闭")

    application = FastAPI(title="图书管理系统", version="0.1.0", lifespan=lifespan)
    register_error_handlers(application)
    application.include_router(health.router)
    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(users.router, prefix="/api/v1")
    return application


app = create_app()
