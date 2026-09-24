from fastapi import APIRouter, Request

from app.api.deps import ServiceDep, SettingsDep
from app.core.security import create_access_token
from app.schemas.user import LoginInput, RegisterInput, TokenOutput, UserOutput

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=UserOutput, status_code=201)
async def register(data: RegisterInput, service: ServiceDep) -> UserOutput:
    return UserOutput.model_validate(await service.register(data))


@router.post("/login", response_model=TokenOutput)
async def login(
    data: LoginInput, request: Request, service: ServiceDep, settings: SettingsDep
) -> TokenOutput:
    user = await service.authenticate(
        data.username, data.password.get_secret_value(), request.app.state.dummy_password_hash
    )
    return TokenOutput(access_token=create_access_token(user.id, settings))
