from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    jwt_secret: SecretStr
    jwt_access_token_minutes: int = Field(default=30, ge=1, le=1440)
    jwt_issuer: str = "library-management-system"
    jwt_audience: str = "library-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "mysql+asyncmy" or not url.database:
                raise ValueError
        except Exception:
            raise ValueError("DATABASE_URL 必须指定 mysql+asyncmy 驱动和数据库名") from None
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 32:
            raise ValueError("JWT_SECRET 至少需要 32 字节，请使用随机生成的密钥")
        return value
