from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import AdminUser, ServiceDep, SessionDep, SettingsDep, bearer
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models.catalog import CopyStatus
from app.models.user import Role
from app.schemas.catalog import (
    AuthorCreate,
    AuthorOutput,
    AuthorPatch,
    BookCreate,
    BookOutput,
    BookPatch,
    CategoryCreate,
    CategoryOutput,
    CategoryPatch,
    CopyCreate,
    CopyOutput,
    CopyPatch,
    CopyStatusInput,
    Page,
    SortBy,
    SortOrder,
)
from app.services.catalog import CatalogService

router = APIRouter(tags=["图书目录与馆藏"])
ResourceId = Annotated[int, Path(gt=0, le=2**64 - 1)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
FilterId = Annotated[int | None, Query(gt=0, le=2**64 - 1)]


def get_catalog_service(session: SessionDep) -> CatalogService:
    return CatalogService(session)


CatalogDep = Annotated[CatalogService, Depends(get_catalog_service)]


async def book_visibility(
    settings: SettingsDep,
    service: ServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    is_active: bool = True,
) -> bool:
    # 公开默认查询无需登录，只有显式查看下架图书时执行完整的当前权限检查。
    if not is_active:
        if credentials is None:
            raise AppError(401, "NOT_AUTHENTICATED", "请先登录")
        user = await service.current_user(decode_access_token(credentials.credentials, settings))
        if user.role != Role.ADMIN:
            raise AppError(403, "FORBIDDEN", "需要管理员权限")
    return is_active


VisibilityDep = Annotated[bool, Depends(book_visibility)]


@router.get("/categories", response_model=list[CategoryOutput])
async def categories(service: CatalogDep) -> list[CategoryOutput]:
    return [CategoryOutput.model_validate(item) for item in await service.categories()]


@router.post("/categories", response_model=CategoryOutput, status_code=201)
async def create_category(
    data: CategoryCreate, admin: AdminUser, service: CatalogDep
) -> CategoryOutput:
    return CategoryOutput.model_validate(await service.create_category(data))


@router.patch("/categories/{entity_id}", response_model=CategoryOutput)
async def update_category(
    entity_id: ResourceId, data: CategoryPatch, admin: AdminUser, service: CatalogDep
) -> CategoryOutput:
    return CategoryOutput.model_validate(await service.update_category(entity_id, data))


@router.delete("/categories/{entity_id}", status_code=204)
async def delete_category(entity_id: ResourceId, admin: AdminUser, service: CatalogDep) -> Response:
    await service.delete_category(entity_id)
    return Response(status_code=204)


@router.get("/authors", response_model=Page[AuthorOutput])
async def authors(
    service: CatalogDep, page: PageNumber = 1, page_size: PageSize = 20
) -> Page[AuthorOutput]:
    items, total = await service.authors(page, page_size)
    return Page(
        items=[AuthorOutput.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/authors/{entity_id}", response_model=AuthorOutput)
async def author(entity_id: ResourceId, service: CatalogDep) -> AuthorOutput:
    return AuthorOutput.model_validate(await service.author(entity_id))


@router.post("/authors", response_model=AuthorOutput, status_code=201)
async def create_author(data: AuthorCreate, admin: AdminUser, service: CatalogDep) -> AuthorOutput:
    return AuthorOutput.model_validate(await service.create_author(data))


@router.patch("/authors/{entity_id}", response_model=AuthorOutput)
async def update_author(
    entity_id: ResourceId, data: AuthorPatch, admin: AdminUser, service: CatalogDep
) -> AuthorOutput:
    return AuthorOutput.model_validate(await service.update_author(entity_id, data))


@router.delete("/authors/{entity_id}", status_code=204)
async def delete_author(entity_id: ResourceId, admin: AdminUser, service: CatalogDep) -> Response:
    await service.delete_author(entity_id)
    return Response(status_code=204)


@router.get("/books", response_model=Page[BookOutput])
async def books(
    service: CatalogDep,
    is_active: VisibilityDep,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    keyword: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    category_id: FilterId = None,
    author_id: FilterId = None,
    available_only: bool = False,
    sort_by: SortBy = "id",
    sort_order: SortOrder = "asc",
) -> Page[BookOutput]:
    items, total = await service.books(
        page,
        page_size,
        keyword,
        category_id,
        author_id,
        available_only,
        is_active,
        sort_by,
        sort_order,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/books/{entity_id}", response_model=BookOutput)
async def book(entity_id: ResourceId, is_active: VisibilityDep, service: CatalogDep) -> BookOutput:
    return await service.book(entity_id, is_active)


@router.post("/books", response_model=BookOutput, status_code=201)
async def create_book(data: BookCreate, admin: AdminUser, service: CatalogDep) -> BookOutput:
    return await service.create_book(data)


@router.patch("/books/{entity_id}", response_model=BookOutput)
async def update_book(
    entity_id: ResourceId, data: BookPatch, admin: AdminUser, service: CatalogDep
) -> BookOutput:
    return await service.update_book(entity_id, data)


@router.delete("/books/{entity_id}", status_code=204)
async def deactivate_book(entity_id: ResourceId, admin: AdminUser, service: CatalogDep) -> Response:
    await service.deactivate_book(entity_id)
    return Response(status_code=204)


@router.get("/books/{entity_id}/copies", response_model=Page[CopyOutput])
async def copies(
    entity_id: ResourceId,
    admin: AdminUser,
    service: CatalogDep,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    status: CopyStatus | None = None,
) -> Page[CopyOutput]:
    items, total = await service.copies(entity_id, page, page_size, status)
    return Page(
        items=[CopyOutput.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/books/{entity_id}/copies", response_model=CopyOutput, status_code=201)
async def create_copy(
    entity_id: ResourceId, data: CopyCreate, admin: AdminUser, service: CatalogDep
) -> CopyOutput:
    return CopyOutput.model_validate(await service.create_copy(entity_id, data))


@router.get("/copies/{entity_id}", response_model=CopyOutput)
async def copy(entity_id: ResourceId, admin: AdminUser, service: CatalogDep) -> CopyOutput:
    return CopyOutput.model_validate(await service.copy(entity_id))


@router.patch("/copies/{entity_id}", response_model=CopyOutput)
async def update_copy(
    entity_id: ResourceId, data: CopyPatch, admin: AdminUser, service: CatalogDep
) -> CopyOutput:
    return CopyOutput.model_validate(await service.update_copy(entity_id, data))


@router.patch("/copies/{entity_id}/status", response_model=CopyOutput)
async def update_copy_status(
    entity_id: ResourceId, data: CopyStatusInput, admin: AdminUser, service: CatalogDep
) -> CopyOutput:
    return CopyOutput.model_validate(await service.update_copy_status(entity_id, data.status))
