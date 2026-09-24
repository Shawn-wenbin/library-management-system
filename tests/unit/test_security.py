from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.schemas.user import ProfileInput, RegisterInput


async def test_argon2() -> None:
    password = "test-password-123"
    hashed = await hash_password(password)
    assert hashed.startswith("$argon2id$")
    assert await verify_password(password, hashed)
    assert not await verify_password("wrong-password", hashed)


def test_token_roundtrip(settings: Settings) -> None:
    assert decode_access_token(create_access_token(42, settings), settings) == 42


@pytest.mark.parametrize(
    "change", ["expired", "signature", "algorithm", "subject", "missing", "issuer", "audience"]
)
def test_invalid_tokens(settings: Settings, change: str) -> None:
    payload = {
        "sub": "1",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    key = settings.jwt_secret.get_secret_value()
    algorithm = "HS256"
    if change == "expired":
        payload["exp"] = datetime.now(UTC) - timedelta(minutes=1)
    elif change == "signature":
        key = "different-test-key" * 4
    elif change == "algorithm":
        algorithm = "HS512"
    elif change == "subject":
        payload["sub"] = "-1"
    elif change == "missing":
        del payload["exp"]
    elif change == "issuer":
        payload["iss"] = "other"
    elif change == "audience":
        payload["aud"] = "other"
    with pytest.raises(AppError) as error:
        decode_access_token(jwt.encode(payload, key, algorithm=algorithm), settings)
    assert error.value.status == 401


def test_input_normalization() -> None:
    data = RegisterInput(username=" Alice ", email=" Alice@EXAMPLE.com ", password="password123")
    assert data.username == "alice"
    assert data.email == "alice@example.com"
    assert "password123" not in repr(data)


@pytest.mark.parametrize(
    "payload", [{}, {"email": None}, {"role": "ADMIN"}, {"is_active": True}, {"email": "bad"}]
)
def test_invalid_profile(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProfileInput.model_validate(payload)


def test_config_rejects_weak_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, database_url="mysql+asyncmy://u:p@localhost/library", jwt_secret="short"
        )
