from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.catalog import Book, BookCopy, CopyStatus
from app.models.loan import Loan, LoanStatus
from app.models.user import User


class LoanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def actor(self, user_id: int) -> User | None:
        # 等待目标用户锁后刷新操作者，避免沿用身份映射中陈旧的角色或启用状态。
        return await self.session.scalar(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )

    async def by_id(self, loan_id: int, *, lock: bool = False) -> Loan | None:
        statement = select(Loan).where(Loan.id == loan_id).options(selectinload(Loan.copy))
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(statement)

    async def active_book_ids(self, user_id: int) -> list[int]:
        return list(
            await self.session.scalars(
                select(BookCopy.book_id)
                .join(Loan, Loan.book_copy_id == BookCopy.id)
                .where(Loan.user_id == user_id, Loan.status == LoanStatus.BORROWED)
            )
        )

    async def lock_book(self, book_id: int) -> Book | None:
        # 共享锁阻止下架，又兼容副本状态更新时 InnoDB 对书目外键的隐式共享锁。
        # 不使用书目排他锁，避免与既有馆藏管理的副本行锁形成等待环。
        return await self.session.scalar(
            select(Book)
            .where(Book.id == book_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

    async def available_copy(self, book_id: int) -> BookCopy | None:
        return await self.session.scalar(
            select(BookCopy)
            .where(BookCopy.book_id == book_id, BookCopy.status == CopyStatus.AVAILABLE)
            .order_by(BookCopy.id)
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def add(self, loan: Loan) -> None:
        self.session.add(loan)
        await self.flush()

    async def flush(self) -> None:
        await self.session.flush()

    async def list_loans(
        self,
        page: int,
        page_size: int,
        *,
        user_id: int | None = None,
        book_id: int | None = None,
        status: LoanStatus | None = None,
        overdue_at: datetime | None = None,
    ) -> tuple[list[Loan], int]:
        predicates = []
        if user_id is not None:
            predicates.append(Loan.user_id == user_id)
        if book_id is not None:
            predicates.append(Loan.copy.has(BookCopy.book_id == book_id))
        if status is not None:
            predicates.append(Loan.status == status)
        if overdue_at is not None:
            predicates.extend([Loan.status == LoanStatus.BORROWED, Loan.due_at < overdue_at])
        total = await self.session.scalar(select(func.count(Loan.id)).where(*predicates))
        loans = await self.session.scalars(
            select(Loan)
            .where(*predicates)
            .options(selectinload(Loan.copy))
            .order_by(Loan.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(loans), int(total or 0)
