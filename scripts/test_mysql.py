"""迁移明确指定的独立测试库后运行测试；测试会清空该库的七张业务表。"""

import argparse
import asyncio
import os
import secrets
import subprocess
import sys

from dotenv import dotenv_values, load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.core.config import Settings
from app.db.session import create_engine


def assert_test_database(url_text: str) -> None:
    try:
        url = make_url(url_text)
    except ArgumentError:
        raise ValueError("测试数据库 URL 不合法") from None
    if url.drivername != "mysql+asyncmy" or not url.database or not url.database.endswith("_test"):
        raise ValueError("仅允许操作以 _test 结尾的独立 MySQL 测试库")
    development_url = os.getenv("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")
    if development_url:
        development = make_url(development_url)
        # 不依赖主机名判断隔离，避免 localhost、IP、代理等别名绕过保护。
        if url.database == development.database:
            raise ValueError("测试库名称必须与开发库不同")


async def assert_empty_database(url_text: str) -> None:
    assert_test_database(url_text)
    engine = create_engine(
        Settings(_env_file=None, database_url=url_text, jwt_secret=secrets.token_urlsafe(48))
    )
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text("SELECT DATABASE()")) != make_url(url_text).database:
                raise ValueError("实际数据库与测试目标不一致")
            count = await connection.scalar(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
            )
            if count:
                raise ValueError("空库验收拒绝已有表或视图的数据库；请使用新的独立测试实例")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-empty", action="store_true", help="要求目标为空库，升级并核对模型后运行测试"
    )
    args, pytest_args = parser.parse_known_args()
    load_dotenv()
    url = os.getenv("TEST_DATABASE_URL")
    if not url or os.getenv("TEST_DATABASE_RESET") != "1":
        print(
            "请设置 TEST_DATABASE_URL 和 TEST_DATABASE_RESET=1；"
            "后者确认允许清空测试库的用户、目录、馆藏及借阅表"
        )
        return 1
    try:
        assert_test_database(url)
    except ValueError:
        print("拒绝操作：目标不是独立的 _test 数据库")
        return 1
    target = make_url(url)
    print(f"测试目标：{target.host}:{target.port or 3306}/{target.database}", flush=True)
    if args.verify_empty:
        try:
            asyncio.run(assert_empty_database(url))
        except ValueError as exc:
            print(str(exc))
            return 1
        except (SQLAlchemyError, OSError):
            print("无法核验空库，请检查测试数据库连接")
            return 1
    migration_env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": secrets.token_urlsafe(48)}
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=migration_env, check=False
    )
    if migration.returncode:
        return migration.returncode
    if args.verify_empty:
        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"], env=migration_env, check=False
        )
        if check.returncode:
            return check.returncode
    return subprocess.run([sys.executable, "-m", "pytest", *pytest_args], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
