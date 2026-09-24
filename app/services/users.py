from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.user import Role, User
from app.repositories.users import UserRepository
from app.schemas.user import ProfileInput, RegisterInput


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserRepository(session)

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[None]:
        # 接续鉴权读取触发的 autobegin，不嵌套 begin；仅顶层写业务使用此边界。
        try:
            yield
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if exc.orig.args and exc.orig.args[0] == 1062:
                raise AppError(409, "USER_EXISTS", "用户名或邮箱已被使用") from None
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def register(self, data: RegisterInput) -> User:
        hashed = await hash_password(data.password.get_secret_value())
        async with self.write_transaction():
            user = await self.repository.add(
                User(
                    username=data.username,
                    email=str(data.email),
                    full_name=data.full_name,
                    password_hash=hashed,
                    role=Role.READER,
                    is_active=True,
                )
            )
        return user

    async def authenticate(self, username: str, password: str, dummy_hash: str) -> User:
        user = await self.repository.by_username(username)
        # 未知用户同样执行哈希校验，减小用户名枚举的时间差。
        valid = await verify_password(password, user.password_hash if user else dummy_hash)
        if user is None or not valid or not user.is_active:
            raise AppError(401, "INVALID_CREDENTIALS", "用户名或密码错误，或账号已禁用")
        return user

    async def current_user(self, user_id: int) -> User:
        user = await self.repository.by_id(user_id)
        if user is None or not user.is_active:
            raise AppError(401, "INVALID_TOKEN", "登录凭证无效或账号已禁用")
        return user

    async def user_detail(self, user_id: int) -> User:
        user = await self.repository.by_id(user_id)
        if user is None:
            raise AppError(404, "USER_NOT_FOUND", "用户不存在")
        return user

    async def list_users(
        self, page: int, page_size: int, role: Role | None, is_active: bool | None
    ) -> tuple[list[User], int]:
        return await self.repository.list_users(page, page_size, role, is_active)

    async def update_profile(self, user_id: int, data: ProfileInput) -> User:
        async with self.write_transaction():
            user = await self._lock_active_user(user_id)
            if "email" in data.model_fields_set:
                user.email = str(data.email)
            if "full_name" in data.model_fields_set:
                user.full_name = data.full_name
            await self.session.flush()
        return user

    async def change_password(self, user_id: int, old_password: str, new_password: str) -> None:
        async with self.write_transaction():
            user = await self.current_user(user_id)
            previous_hash = user.password_hash
            if not await verify_password(old_password, previous_hash):
                raise AppError(409, "WRONG_PASSWORD", "旧密码不正确")
            new_hash = await hash_password(new_password)
            user = await self._lock_active_user(user_id)
            if user.password_hash != previous_hash:
                raise AppError(409, "PASSWORD_CHANGED", "密码已被其他请求修改，请重试")
            user.password_hash = new_hash
            await self.session.flush()

    async def _lock_active_user(self, user_id: int) -> User:
        user = await self.repository.by_id(user_id, for_update=True)
        if user is None or not user.is_active:
            raise AppError(401, "INVALID_TOKEN", "登录凭证无效或账号已禁用")
        return user

    async def update_access(
        self,
        actor_id: int,
        user_id: int,
        *,
        role: Role | None = None,
        is_active: bool | None = None,
    ) -> User:
        async with self.write_transaction():
            admins = await self.repository.lock_admins()
            actor = next((admin for admin in admins if admin.id == actor_id), None)
            if actor is None or not actor.is_active:
                raise AppError(403, "FORBIDDEN", "需要有效管理员权限")
            user = await self.repository.by_id(user_id, for_update=True)
            if user is None:
                raise AppError(404, "USER_NOT_FOUND", "用户不存在")
            next_role = role if role is not None else user.role
            next_active = is_active if is_active is not None else user.is_active
            if (
                user.role == Role.ADMIN
                and user.is_active
                and (next_role != Role.ADMIN or not next_active)
                and sum(admin.is_active for admin in admins) <= 1
            ):
                raise AppError(409, "LAST_ADMIN", "不能禁用或降级最后一个有效管理员")
            user.role = next_role
            user.is_active = next_active
            await self.session.flush()
        return user

    async def initialize_admin(self, data: RegisterInput) -> tuple[User, bool]:
        hashed = await hash_password(data.password.get_secret_value())
        async with self.write_transaction():
            existing = await self.repository.by_username(data.username)
            if existing is not None:
                if (
                    existing.role != Role.ADMIN
                    or not existing.is_active
                    or existing.email != data.email
                ):
                    raise AppError(409, "ADMIN_CONFLICT", "同名账号已存在且不是匹配的有效管理员")
                return existing, False
            user = await self.repository.add(
                User(
                    username=data.username,
                    email=str(data.email),
                    password_hash=hashed,
                    full_name=data.full_name,
                    role=Role.ADMIN,
                    is_active=True,
                )
            )
        return user, True
