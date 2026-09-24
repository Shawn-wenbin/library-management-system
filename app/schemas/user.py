import re
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.models.user import Role

Password = Annotated[SecretStr, Field(min_length=8, max_length=128)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsernameInput(InputModel):
    username: str

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{3,50}", value):
            raise ValueError("用户名需为 3–50 位字母、数字或下划线")
        return value


class RegisterInput(UsernameInput):
    email: EmailStr = Field(max_length=255)
    password: Password
    full_name: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginInput(UsernameInput):
    password: Password


class ProfileInput(InputModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少提供一个资料字段")
        if "email" in self.model_fields_set and self.email is None:
            raise ValueError("邮箱不能置空")
        return self


class PasswordInput(InputModel):
    old_password: Password
    new_password: Password


class StatusInput(InputModel):
    is_active: bool = Field(strict=True)


class RoleInput(InputModel):
    role: Role


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    full_name: str | None
    role: Role
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserPage(BaseModel):
    items: list[UserOutput]
    total: int
    page: int
    page_size: int


class TokenOutput(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
