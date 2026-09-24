from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_id(self, user_id: int, *, for_update: bool = False) -> User | None:
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(statement)

    async def by_username(self, username: str) -> User | None:
        return await self.session.scalar(select(User).where(User.username == username))

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def lock_admins(self) -> list[User]:
        # 所有角色/状态写入遵循同一顺序：管理员按主键升序，再锁目标用户。
        result = await self.session.scalars(
            select(User)
            .where(User.role == Role.ADMIN)
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result)

    async def list_users(
        self, page: int, page_size: int, role: Role | None, is_active: bool | None
    ) -> tuple[list[User], int]:
        predicates = []
        if role is not None:
            predicates.append(User.role == role)
        if is_active is not None:
            predicates.append(User.is_active == is_active)
        total = await self.session.scalar(select(func.count(User.id)).where(*predicates))
        users = await self.session.scalars(
            select(User)
            .where(*predicates)
            .order_by(User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(users), int(total or 0)
