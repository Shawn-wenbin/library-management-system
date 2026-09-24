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

SessionDep = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


ServiceDep = Annotated[UserService, Depends(get_user_service)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDep,
    service: ServiceDep,
) -> User:
    if credentials is None:
        raise AppError(401, "NOT_AUTHENTICATED", "请先登录")
    user_id = decode_access_token(credentials.credentials, settings)
    return await service.current_user(user_id)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN:
        raise AppError(403, "FORBIDDEN", "需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
