from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models.loan import LoanStatus
from app.schemas.catalog import Page
from app.schemas.loan import LoanCreate, LoanOutput
from app.services.loans import LoanService

router = APIRouter(prefix="/loans", tags=["借阅与归还"])
ResourceId = Annotated[int, Path(gt=0, le=2**64 - 1)]
FilterId = Annotated[int | None, Query(gt=0, le=2**64 - 1)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def get_loan_service(session: SessionDep) -> LoanService:
    return LoanService(session)


LoanDep = Annotated[LoanService, Depends(get_loan_service)]


@router.post("", response_model=LoanOutput, status_code=201)
async def borrow(data: LoanCreate, user: CurrentUser, service: LoanDep) -> LoanOutput:
    return await service.borrow(user, data)


@router.get("/me", response_model=Page[LoanOutput])
async def my_loans(
    user: CurrentUser,
    service: LoanDep,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    book_id: FilterId = None,
    status: LoanStatus | None = None,
) -> Page[LoanOutput]:
    items, total = await service.list_loans(
        page,
        page_size,
        user_id=user.id,
        book_id=book_id,
        status=status,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("", response_model=Page[LoanOutput])
async def all_loans(
    user: AdminUser,
    service: LoanDep,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    user_id: FilterId = None,
    book_id: FilterId = None,
    status: LoanStatus | None = None,
) -> Page[LoanOutput]:
    items, total = await service.list_loans(
        page,
        page_size,
        user_id=user_id,
        book_id=book_id,
        status=status,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/overdue", response_model=Page[LoanOutput])
async def overdue_loans(
    user: AdminUser,
    service: LoanDep,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    user_id: FilterId = None,
    book_id: FilterId = None,
) -> Page[LoanOutput]:
    items, total = await service.list_loans(
        page,
        page_size,
        user_id=user_id,
        book_id=book_id,
        overdue=True,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{loan_id}", response_model=LoanOutput)
async def loan_detail(loan_id: ResourceId, user: CurrentUser, service: LoanDep) -> LoanOutput:
    return await service.detail(user, loan_id)


@router.post("/{loan_id}/return", response_model=LoanOutput)
async def return_loan(loan_id: ResourceId, user: CurrentUser, service: LoanDep) -> LoanOutput:
    return await service.return_loan(user, loan_id)
