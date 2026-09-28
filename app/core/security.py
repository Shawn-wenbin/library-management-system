from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError

password_hasher = PasswordHash.recommended()


async def hash_password(password: str) -> str:
    return await run_in_threadpool(password_hasher.hash, password)


async def verify_password(password: str, hashed: str) -> bool:
    return await run_in_threadpool(password_hasher.verify, password, hashed)


def create_access_token(user_id: int, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_access_token_minutes),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


'''
做 5 重校验 ，任何一个过不了都抛 401：
1. 签名对不对？（防止伪造）
2. 过期没？（`exp` claim）
3. 签发者是不是自己？（`iss` 必须匹配`JWT_ISSUER` ）
4. 受众对不对？（`aud` 必须匹配`JWT_AUDIENCE` ）
5. `sub` claim 是有效的 int 用户 ID 吗？
'''
def decode_access_token(token: str, settings: Settings) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdigit():
            raise ValueError
        user_id = int(subject)
        if not 0 < user_id <= 2**64 - 1:
            raise ValueError
        return user_id
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise AppError(401, "INVALID_TOKEN", "登录凭证无效或已过期") from None
