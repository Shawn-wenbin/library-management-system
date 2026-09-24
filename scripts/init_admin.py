import asyncio
import getpass
import os
import sys

from dotenv import load_dotenv
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import create_engine, create_session_factory
from app.schemas.user import RegisterInput
from app.services.users import UserService


async def initialize(data: RegisterInput) -> bool:
    engine = create_engine(Settings())
    try:
        async with create_session_factory(engine)() as session:
            _, created = await UserService(session).initialize_admin(data)
            return created
    finally:
        await engine.dispose()


def main() -> int:
    load_dotenv()
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        if not sys.stdin.isatty():
            print("非交互模式必须设置 ADMIN_PASSWORD", file=sys.stderr)
            return 1
        password = getpass.getpass("管理员密码：")
        if password != getpass.getpass("再次输入密码："):
            print("两次密码不一致", file=sys.stderr)
            return 1
    try:
        data = RegisterInput(
            username=os.environ.get("ADMIN_USERNAME", ""),
            email=os.environ.get("ADMIN_EMAIL", ""),
            password=password,
        )
        created = asyncio.run(initialize(data))
    except ValidationError:
        print("配置或管理员输入不合法，请检查环境变量和密码长度", file=sys.stderr)
        return 1
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError):
        print("数据库连接或写入失败，请检查配置与迁移状态", file=sys.stderr)
        return 1
    print("管理员已创建" if created else "匹配的管理员已存在，未修改密码或其他属性")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
