from typing import Annotated

from fastapi import APIRouter, Path, Query, Response

from app.api.deps import AdminUser, CurrentUser, ServiceDep
from app.models.user import Role
from app.schemas.user import (
    PasswordInput,
    ProfileInput,
    RoleInput,
    StatusInput,
    UserOutput,
    UserPage,
)

router = APIRouter(prefix="/users", tags=["用户"])
UserId = Annotated[int, Path(gt=0, le=2**64 - 1)]


@router.get("/me", response_model=UserOutput)
async def me(user: CurrentUser) -> UserOutput:
    return UserOutput.model_validate(user)


@router.patch("/me", response_model=UserOutput)
async def update_me(data: ProfileInput, user: CurrentUser, service: ServiceDep) -> UserOutput:
    return UserOutput.model_validate(await service.update_profile(user.id, data))


@router.patch("/me/password", status_code=204)
async def change_password(data: PasswordInput, user: CurrentUser, service: ServiceDep) -> Response:
    await service.change_password(
        user.id, data.old_password.get_secret_value(), data.new_password.get_secret_value()
    )
    return Response(status_code=204)


@router.get("", response_model=UserPage)
async def list_users(
    admin: AdminUser,
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    role: Role | None = None,
    is_active: bool | None = None,
) -> UserPage:
    users, total = await service.list_users(page, page_size, role, is_active)
    return UserPage(
        items=[UserOutput.model_validate(user) for user in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=UserOutput)
async def user_detail(user_id: UserId, admin: AdminUser, service: ServiceDep) -> UserOutput:
    return UserOutput.model_validate(await service.user_detail(user_id))


@router.patch("/{user_id}/status", response_model=UserOutput)
async def update_status(
    user_id: UserId, data: StatusInput, admin: AdminUser, service: ServiceDep
) -> UserOutput:
    return UserOutput.model_validate(
        await service.update_access(admin.id, user_id, is_active=data.is_active)
    )


@router.patch("/{user_id}/role", response_model=UserOutput)
async def update_role(
    user_id: UserId, data: RoleInput, admin: AdminUser, service: ServiceDep
) -> UserOutput:
    return UserOutput.model_validate(await service.update_access(admin.id, user_id, role=data.role))
