import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app
from app.models.user import User
from scripts.test_mysql import assert_test_database


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("未设置 TEST_DATABASE_URL，真实 MySQL 集成测试未运行")
    assert_test_database(test_url)
    if os.getenv("TEST_DATABASE_RESET") != "1":
        pytest.fail("必须设置 TEST_DATABASE_RESET=1 才可清空独立测试库")
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.session_factory() as session:
            actual = await session.scalar(text("SELECT DATABASE()"))
            if actual != make_url(test_url).database:
                raise RuntimeError("实际连接数据库与测试目标不符")
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0001_users":
                raise RuntimeError("请先对独立测试库执行 Alembic upgrade head")
            await session.execute(delete(User))
            await session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as http:
            http.test_app = application
            yield http
        async with application.state.session_factory() as session:
            await session.execute(delete(User))
            await session.commit()
