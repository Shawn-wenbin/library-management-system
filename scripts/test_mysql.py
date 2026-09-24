"""迁移明确指定的独立测试库后运行测试；测试会清空该库的 users 表。"""

import os
import secrets
import subprocess
import sys

from dotenv import dotenv_values, load_dotenv
from sqlalchemy.engine import make_url


def assert_test_database(url_text: str) -> None:
    url = make_url(url_text)
    if url.drivername != "mysql+asyncmy" or not url.database or not url.database.endswith("_test"):
        raise ValueError("仅允许操作以 _test 结尾的独立 MySQL 测试库")
    development_url = os.getenv("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")
    if development_url:
        development = make_url(development_url)
        if (url.host, url.port or 3306, url.database) == (
            development.host,
            development.port or 3306,
            development.database,
        ):
            raise ValueError("测试库不能与开发库指向同一目标")


def main() -> int:
    load_dotenv()
    url = os.getenv("TEST_DATABASE_URL")
    if not url or os.getenv("TEST_DATABASE_RESET") != "1":
        print("请设置 TEST_DATABASE_URL 和 TEST_DATABASE_RESET=1；后者确认允许清空测试 users 表")
        return 1
    try:
        assert_test_database(url)
    except ValueError:
        print("拒绝操作：目标不是独立的 _test 数据库")
        return 1
    target = make_url(url)
    print(f"测试目标：{target.host}:{target.port or 3306}/{target.database}", flush=True)
    migration_env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": secrets.token_urlsafe(48)}
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=migration_env, check=False
    )
    if migration.returncode:
        return migration.returncode
    return subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
