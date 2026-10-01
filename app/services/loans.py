from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.base import utc_now
from app.models.catalog import CopyStatus
from app.models.loan import Loan, LoanStatus
from app.models.user import Role, User
from app.repositories.catalog import CatalogRepository
from app.repositories.loans import LoanRepository
from app.repositories.users import UserRepository
from app.schemas.loan import LoanCreate, LoanOutput


class LoanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = LoanRepository(session)
        self.users = UserRepository(session)
        self.catalog = CatalogRepository(session)

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[None]:
        # 鉴权已触发 autobegin；不重复 begin，也不调用其他 Service 的提交逻辑。
        try:
            yield
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise AppError(409, "LOAN_CONFLICT", "借阅状态或关联资源发生冲突") from None
        except BaseException:
            await self.session.rollback()
            raise

    async def _actor(self, actor_id: int) -> User:
        actor = await self.repository.actor(actor_id)
        if actor is None or not actor.is_active:
            raise AppError(401, "INVALID_TOKEN", "登录凭证无效或账号已禁用")
        return actor

    @staticmethod
    def _check_owner(actor: User, user_id: int) -> None:
        if actor.id != user_id and actor.role != Role.ADMIN:
            raise AppError(403, "FORBIDDEN", "无权访问他人的借阅")

    @staticmethod
    def _output(loan: Loan, now: datetime) -> LoanOutput:
        values = {
            field: getattr(loan, field)
            for field in LoanOutput.model_fields
            if field not in {"book_id", "is_overdue"}
        }
        return LoanOutput(
            **values,
            book_id=loan.copy.book_id,
            is_overdue=loan.status == LoanStatus.BORROWED and now > loan.due_at,
        )

    async def borrow(self, actor: User, data: LoanCreate) -> LoanOutput:
        async with self.write_transaction():
            if data.user_id is not None and actor.role != Role.ADMIN:
                raise AppError(403, "FORBIDDEN", "只有管理员可以指定借阅用户")
            actor_id = actor.id
            user_id = data.user_id if data.user_id is not None else actor_id
            # 加锁顺序：目标用户 → 书目（共享） → 副本 → 借阅；用户锁串行化资格检查。
            borrower = await self.users.by_id(user_id, for_update=True)
            if borrower is None:
                raise AppError(404, "USER_NOT_FOUND", "用户不存在")
            actor = await self._actor(actor_id)
            if data.user_id is not None and actor.role != Role.ADMIN:
                raise AppError(403, "FORBIDDEN", "只有管理员可以指定借阅用户")
            if not borrower.is_active:
                raise AppError(409, "USER_INACTIVE", "目标读者已禁用")
            active_books = await self.repository.active_book_ids(user_id)
            if data.book_id in active_books:
                raise AppError(409, "BOOK_ALREADY_BORROWED", "不能同时借阅同种图书的多个副本")
            if len(active_books) >= 5:
                raise AppError(409, "LOAN_LIMIT_REACHED", "最多同时借阅五本图书")
            book = await self.repository.lock_book(data.book_id)
            if book is None:
                raise AppError(404, "BOOK_NOT_FOUND", "图书不存在")
            if not book.is_active:
                raise AppError(409, "BOOK_NOT_AVAILABLE", "图书已下架")
            copy = await self.repository.available_copy(book.id)
            if copy is None:
                raise AppError(409, "BOOK_NOT_AVAILABLE", "当前图书暂无可借副本")
            now = utc_now()
            loan = Loan(
                user_id=user_id,
                copy=copy,
                borrowed_at=now,
                due_at=now + timedelta(days=14),
                status=LoanStatus.BORROWED,
            )
            copy.status = CopyStatus.BORROWED
            await self.repository.add(loan)
            output = self._output(loan, now)
        return output

    async def return_loan(self, actor: User, loan_id: int) -> LoanOutput:
        async with self.write_transaction():
            loan = await self.repository.by_id(loan_id)
            if loan is None:
                raise AppError(404, "LOAN_NOT_FOUND", "借阅不存在")
            self._check_owner(actor, loan.user_id)
            actor_id = actor.id
            # 前置读取仅用于定位不可变外键；获得全部锁后重新读取借阅状态。
            await self.users.by_id(loan.user_id, for_update=True)
            actor = await self._actor(actor_id)
            self._check_owner(actor, loan.user_id)
            await self.repository.lock_book(loan.copy.book_id)
            copy = await self.catalog.copy(loan.book_copy_id, lock=True)
            loan = await self.repository.by_id(loan_id, lock=True)
            if loan.status != LoanStatus.BORROWED:
                raise AppError(409, "LOAN_ALREADY_RETURNED", "该借阅已归还")
            if copy.status != CopyStatus.BORROWED:
                raise AppError(409, "COPY_STATUS_CONFLICT", "实体副本状态与借阅不一致")
            now = utc_now()
            loan.status = LoanStatus.RETURNED
            loan.returned_at = now
            copy.status = CopyStatus.AVAILABLE
            await self.repository.flush()
            output = self._output(loan, now)
        return output

    async def detail(self, actor: User, loan_id: int) -> LoanOutput:
        loan = await self.repository.by_id(loan_id)
        if loan is None:
            raise AppError(404, "LOAN_NOT_FOUND", "借阅不存在")
        self._check_owner(actor, loan.user_id)
        return self._output(loan, utc_now())

    async def list_loans(
        self,
        page: int,
        page_size: int,
        *,
        user_id: int | None = None,
        book_id: int | None = None,
        status: LoanStatus | None = None,
        overdue: bool = False,
    ) -> tuple[list[LoanOutput], int]:
        now = utc_now()
        loans, total = await self.repository.list_loans(
            page,
            page_size,
            user_id=user_id,
            book_id=book_id,
            status=status,
            overdue_at=now if overdue else None,
        )
        return [self._output(loan, now) for loan in loans], total
