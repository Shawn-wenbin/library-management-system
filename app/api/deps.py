from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.user import Role, User
from app.services.users import UserService

'''
文件定位： deps.py = 整个 API 层的"依赖注入工具箱"
从`app.state.session_factory` （你在`lifespan` 启动时创建的`async_sessionmaker` ）
`async with` 一个新的`AsyncSession` ，
请求结束自动关闭，不会忘记释放连接。
这样可以确保每个请求都有一个独立的数据库连接，避免连接池中的连接被重复使用。
'''
SessionDep = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False) 
# 从 HTTP Header 取令牌（bearer）
# L15：Header 没有 Authorization 时返回 None 不直接抛 401


def get_settings(request: Request) -> Settings:
    '''
    为什么不直接`Settings()` 再实例化一次
    1. 性能 ：`Settings()` 实例化要读 .env、做 Pydantic 校验（比如字节长度、URL 驱动），
    每个请求做一次很浪费；这里直接拿 app.state 里缓存好的那份， 全应用共享单例
    2. 测试注入 ：`create_app(test_settings)` 传进去的测试配置也是通过同一个地方读，
    保证接口看到的和你 fixture 传的是同一份。
    '''
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


ServiceDep = Annotated[UserService, Depends(get_user_service)]

'''
HTTP 进来 → FastAPI 解析参数
  → Depends(get_session)        : new AsyncSession（请求级）
    → Depends(get_user_service) : UserService(session)（把 session 塞进去）
      → Router 函数拿到 service 调 register()、current_user()、grant_admin()
        → Service 内部 new UserRepository(session)，开始查/写 DB
'''


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDep,
    service: ServiceDep,
) -> User:
    if credentials is None:
        raise AppError(401, "NOT_AUTHENTICATED", "请先登录")
    user_id = decode_access_token(credentials.credentials, settings)
    '''
    ⚠️ 这一步是 非常重要的安全设计 ，很多新手会省掉——"既然 JWT 解出来 user_id=5，
    直接信任不就好了？"不行，理由有三个：

    1. 管理员刚把这个用户禁用了 （`is_active=False` ），JWT 还没过期，不能让他继续登录
    2. 用户被删号了 ，JWT 还在
    3. JWT 里的 role 过期 ：用户昨天还是 READER，今天管理员把他升成 ADMIN 了，
    但 JWT 是昨天发的，里面 role 还是 READER——所以绝不能只信 JWT 里存的角色声明，
    每次请求都查 DB 取最新的 role/is_active（
    这就是 AGENTS.md §4 明确写的： 不能只相信 JWT 中过期的角色声明 ）
    '''
    return await service.current_user(user_id)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN:
        raise AppError(403, "FORBIDDEN", "需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
