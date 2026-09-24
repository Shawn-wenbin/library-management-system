import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.security import create_access_token, verify_password
from app.models.user import Role, User
from app.repositories.users import UserRepository
from app.schemas.user import RegisterInput
from app.services.users import UserService

pytestmark = pytest.mark.integration
PASSWORD = "test-password-123"


async def register(client: AsyncClient, username: str = "reader") -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login(
    client: AsyncClient, username: str = "reader", password: str = PASSWORD
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def admin(client: AsyncClient, username: str = "admin") -> tuple[int, dict[str, str]]:
    async with client.test_app.state.session_factory() as session:
        user, _ = await UserService(session).initialize_admin(
            RegisterInput(username=username, email=f"{username}@example.com", password=PASSWORD)
        )
        user_id = user.id
    return user_id, await login(client, username)


async def test_registration_login_profile_password(client: AsyncClient) -> None:
    user = await register(client)
    assert user["role"] == "READER" and user["is_active"] is True
    assert "password" not in str(user)
    assert user["created_at"].endswith("Z")
    auth = await login(client, " READER ")
    response = await client.get("/api/v1/users/me", headers=auth)
    assert response.json()["id"] == user["id"]
    response = await client.patch(
        "/api/v1/users/me", headers=auth, json={"email": " New@EXAMPLE.COM ", "full_name": "读者"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
    assert response.json()["full_name"] == "读者"
    assert response.json()["updated_at"] >= user["updated_at"]
    response = await client.patch("/api/v1/users/me", headers=auth, json={"full_name": None})
    assert response.json()["full_name"] is None
    response = await client.patch(
        "/api/v1/users/me/password",
        headers=auth,
        json={"old_password": "wrong-password", "new_password": "new-password-123"},
    )
    assert response.status_code == 409
    await login(client)
    response = await client.patch(
        "/api/v1/users/me/password",
        headers=auth,
        json={"old_password": PASSWORD, "new_password": "new-password-123"},
    )
    assert response.status_code == 204 and response.content == b""
    assert (
        await client.post("/api/v1/auth/login", json={"username": "reader", "password": PASSWORD})
    ).status_code == 401
    await login(client, password="new-password-123")
    assert (await client.get("/api/v1/users/me", headers=auth)).status_code == 200
    async with client.test_app.state.session_factory() as session:
        stored = await session.get(User, user["id"])
        assert stored.password_hash.startswith("$argon2id$")
        assert await verify_password("new-password-123", stored.password_hash)
        assert stored.created_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "ADMIN"),
        ("is_active", True),
        ("username", "x"),
        ("username", "bad name"),
        ("email", "bad"),
        ("password", "short"),
        ("password", "x" * 129),
        ("full_name", "x" * 101),
    ],
)
async def test_registration_validation(client: AsyncClient, field: str, value: object) -> None:
    payload = {
        "username": "reader",
        "email": "reader@example.com",
        "password": PASSWORD,
        field: value,
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422
    assert PASSWORD not in response.text


@pytest.mark.parametrize("field,value", [("username", " READER "), ("email", "READER@example.com")])
async def test_duplicate_and_profile_rollback(client: AsyncClient, field: str, value: str) -> None:
    await register(client)
    payload = {
        "username": "another",
        "email": "another@example.com",
        "password": PASSWORD,
        field: value,
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    await register(client, "another")
    auth = await login(client, "another")
    response = await client.patch(
        "/api/v1/users/me", headers=auth, json={"email": "reader@example.com", "full_name": "回滚"}
    )
    assert response.status_code == 409
    user = (await client.get("/api/v1/users/me", headers=auth)).json()
    assert user["email"] == "another@example.com" and user["full_name"] is None


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/api/v1/users", None),
        ("GET", "/api/v1/users/999", None),
        ("PATCH", "/api/v1/users/999/status", {"is_active": False}),
        ("PATCH", "/api/v1/users/999/role", {"role": "ADMIN"}),
    ],
)
async def test_admin_permission(
    client: AsyncClient, method: str, path: str, payload: dict | None
) -> None:
    assert (await client.request(method, path, json=payload)).status_code == 401
    await register(client)
    response = await client.request(method, path, headers=await login(client), json=payload)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/v1/users/me", {"role": "ADMIN"}),
        ("/api/v1/users/me", {"is_active": True}),
        ("/api/v1/users/me", {"email": None}),
        ("/api/v1/users/me", {}),
        ("/api/v1/users/me/password", {"old_password": PASSWORD, "new_password": "short"}),
    ],
)
async def test_self_validation(client: AsyncClient, path: str, payload: dict) -> None:
    await register(client)
    assert (await client.patch(path, headers=await login(client), json=payload)).status_code == 422


async def test_admin_crud_latest_permissions_and_disabled_tokens(client: AsyncClient) -> None:
    admin_id, auth = await admin(client)
    reader = await register(client)
    reader_auth = await login(client)
    response = await client.get("/api/v1/users?page_size=1", headers=auth)
    assert response.status_code == 200
    assert response.json()["total"] == 2 and len(response.json()["items"]) == 1
    assert "password" not in response.text
    assert (await client.get("/api/v1/users?role=READER&is_active=true", headers=auth)).json()[
        "total"
    ] == 1
    assert (await client.get(f"/api/v1/users/{reader['id']}", headers=auth)).json()["id"] == reader[
        "id"
    ]
    assert (await client.get("/api/v1/users/999999", headers=auth)).status_code == 404
    for path in [
        "/api/v1/users?page=0",
        "/api/v1/users?page_size=101",
        "/api/v1/users?role=INVALID",
        "/api/v1/users/0",
    ]:
        assert (await client.get(path, headers=auth)).status_code == 422
    for suffix, payload in [("status", {"is_active": "false"}), ("role", {"role": "ROOT"})]:
        assert (
            await client.patch(f"/api/v1/users/{reader['id']}/{suffix}", headers=auth, json=payload)
        ).status_code == 422
    for suffix, payload in [("status", {"is_active": False}), ("role", {"role": "READER"})]:
        assert (
            await client.patch(f"/api/v1/users/999999/{suffix}", headers=auth, json=payload)
        ).status_code == 404
        response = await client.patch(
            f"/api/v1/users/{admin_id}/{suffix}", headers=auth, json=payload
        )
        assert response.status_code == 409 and response.json()["code"] == "LAST_ADMIN"
    path = f"/api/v1/users/{reader['id']}"
    assert (
        await client.patch(path + "/status", headers=auth, json={"is_active": False})
    ).status_code == 200
    assert (await client.get("/api/v1/users/me", headers=reader_auth)).status_code == 401
    assert (
        await client.post("/api/v1/auth/login", json={"username": "reader", "password": PASSWORD})
    ).status_code == 401
    assert (
        await client.patch(path + "/status", headers=auth, json={"is_active": True})
    ).status_code == 200
    assert (await client.get("/api/v1/users/me", headers=reader_auth)).status_code == 200
    assert (
        await client.patch(path + "/role", headers=auth, json={"role": "ADMIN"})
    ).status_code == 200
    assert (await client.get("/api/v1/users", headers=reader_auth)).status_code == 200
    assert (
        await client.patch(path + "/role", headers=auth, json={"role": "READER"})
    ).status_code == 200
    assert (await client.get("/api/v1/users", headers=reader_auth)).status_code == 403


async def test_invalid_credentials_and_unknown_subject(
    client: AsyncClient, settings: Settings
) -> None:
    await register(client)
    for username, password in [("reader", "wrong-password"), ("missing", PASSWORD)]:
        response = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 401
    for token in ["invalid", create_access_token(2**64 - 1, settings)]:
        assert (
            await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        ).status_code == 401


async def test_admin_initialization_idempotent_and_conflict(client: AsyncClient) -> None:
    user_id, _ = await admin(client)
    async with client.test_app.state.session_factory() as session:
        original = (await session.get(User, user_id)).password_hash
        service = UserService(session)
        user, created = await service.initialize_admin(
            RegisterInput(
                username="admin", email="admin@example.com", password="different-password"
            )
        )
        assert not created and user.password_hash == original
    await register(client)
    from app.core.errors import AppError

    async with client.test_app.state.session_factory() as session:
        with pytest.raises(AppError) as error:
            await UserService(session).initialize_admin(
                RegisterInput(username="reader", email="reader@example.com", password=PASSWORD)
            )
        assert error.value.code == "ADMIN_CONFLICT"
        assert (
            await session.scalar(select(User).where(User.username == "reader"))
        ).role == Role.READER


async def test_forced_failure_rolls_back_insert(client: AsyncClient) -> None:
    original_add = UserRepository.add

    async def failing_add(repository: UserRepository, user: User) -> User:
        await original_add(repository, user)
        raise RuntimeError("写入后模拟失败")

    with patch.object(UserRepository, "add", failing_add):
        async with client.test_app.state.session_factory() as session:
            with pytest.raises(RuntimeError):
                await UserService(session).register(
                    RegisterInput(
                        username="rollback", email="rollback@example.com", password=PASSWORD
                    )
                )
            assert not session.in_transaction()
    async with client.test_app.state.session_factory() as session:
        assert await session.scalar(select(func.count(User.id))) == 0


async def test_concurrent_registration(client: AsyncClient) -> None:
    application = client.test_app
    payload = {"username": "race", "email": "race@example.com", "password": PASSWORD}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as other:
        responses = await asyncio.gather(
            client.post("/api/v1/auth/register", json=payload),
            other.post("/api/v1/auth/register", json=payload),
        )
    assert sorted(response.status_code for response in responses) == [201, 409]
    async with application.state.session_factory() as session:
        assert await session.scalar(select(func.count(User.id))) == 1


@pytest.mark.parametrize(
    "suffix,payload", [("status", {"is_active": False}), ("role", {"role": "READER"})]
)
async def test_concurrent_last_admin_protection(
    client: AsyncClient, suffix: str, payload: dict
) -> None:
    first_id, first_auth = await admin(client, "admin_one")
    second_id, second_auth = await admin(client, "admin_two")
    application = client.test_app
    barrier = asyncio.Barrier(2)
    original_lock = UserRepository.lock_admins

    async def synchronized_lock(repository: UserRepository) -> list[User]:
        await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original_lock(repository)

    with patch.object(UserRepository, "lock_admins", synchronized_lock):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as other:
            responses = await asyncio.gather(
                client.patch(
                    f"/api/v1/users/{first_id}/{suffix}", headers=first_auth, json=payload
                ),
                other.patch(
                    f"/api/v1/users/{second_id}/{suffix}", headers=second_auth, json=payload
                ),
            )
    assert sorted(response.status_code for response in responses) == [200, 409]
    async with application.state.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(User.id)).where(User.role == Role.ADMIN, User.is_active.is_(True))
            )
            == 1
        )


async def test_health_ready(client: AsyncClient) -> None:
    assert (await client.get("/health/ready")).json() == {"status": "ready"}


async def test_concurrent_password_change(client: AsyncClient) -> None:
    user = await register(client)
    auth = await login(client)
    application = client.test_app
    barrier = asyncio.Barrier(2)
    original_lock = UserService._lock_active_user

    async def synchronized_lock(service: UserService, user_id: int) -> User:
        await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original_lock(service, user_id)

    with patch.object(UserService, "_lock_active_user", synchronized_lock):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as other:
            responses = await asyncio.gather(
                client.patch(
                    "/api/v1/users/me/password",
                    headers=auth,
                    json={"old_password": PASSWORD, "new_password": "new-password-one"},
                ),
                other.patch(
                    "/api/v1/users/me/password",
                    headers=auth,
                    json={"old_password": PASSWORD, "new_password": "new-password-two"},
                ),
            )
    assert sorted(response.status_code for response in responses) == [204, 409]
    winner = "new-password-one" if responses[0].status_code == 204 else "new-password-two"
    async with application.state.session_factory() as session:
        stored = await session.get(User, user["id"])
        assert await verify_password(winner, stored.password_hash)


async def test_admin_authority_rechecked_after_authentication(client: AsyncClient) -> None:
    first_id, first_auth = await admin(client, "first_admin")
    second_id, second_auth = await admin(client, "second_admin")
    reader = await register(client)
    reached_write = asyncio.Event()
    resume_write = asyncio.Event()
    original_update = UserService.update_access

    async def delayed_update(
        service: UserService, actor_id: int, user_id: int, **kwargs: object
    ) -> User:
        if actor_id == first_id:
            reached_write.set()
            await asyncio.wait_for(resume_write.wait(), timeout=10)
        return await original_update(service, actor_id, user_id, **kwargs)

    with patch.object(UserService, "update_access", delayed_update):
        pending = asyncio.create_task(
            client.patch(
                f"/api/v1/users/{reader['id']}/status",
                headers=first_auth,
                json={"is_active": False},
            )
        )
        try:
            await asyncio.wait_for(reached_write.wait(), timeout=10)
            async with AsyncClient(
                transport=ASGITransport(app=client.test_app), base_url="http://test"
            ) as other:
                response = await other.patch(
                    f"/api/v1/users/{first_id}/role", headers=second_auth, json={"role": "READER"}
                )
                assert response.status_code == 200
        finally:
            resume_write.set()
        assert (await pending).status_code == 403
    async with client.test_app.state.session_factory() as session:
        assert (await session.get(User, reader["id"])).is_active
        assert (await session.get(User, second_id)).role == Role.ADMIN
