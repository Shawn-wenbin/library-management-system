import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from test_auth_users import admin, login, register
from test_catalog import post

from app.db.base import utc_now
from app.models.catalog import Book, BookCopy, CopyStatus
from app.models.loan import Loan, LoanStatus
from app.models.user import Role, User
from app.repositories.loans import LoanRepository
from app.repositories.users import UserRepository

pytestmark = pytest.mark.integration
PREFIX = "/api/v1"


async def library(
    client: AsyncClient,
    count: int = 1,
    copies: int = 1,
) -> tuple[dict[str, str], list[int], list[int]]:
    _, auth = await admin(client)
    category = await post(client, "/categories", auth, {"name": "借阅测试"})
    books, copy_ids = [], []
    for index in range(count):
        book = await post(
            client,
            "/books",
            auth,
            {"title": f"图书{index}", "category_id": category["id"]},
        )
        books.append(book["id"])
        for copy_index in range(copies):
            copy = await post(
                client,
                f"/books/{book['id']}/copies",
                auth,
                {"barcode": f"book-{index}-{copy_index}"},
            )
            copy_ids.append(copy["id"])
    return auth, books, copy_ids


async def reader(client: AsyncClient, name: str = "reader") -> tuple[int, dict[str, str]]:
    user = await register(client, name)
    return user["id"], await login(client, name)


async def borrow(client: AsyncClient, auth: dict[str, str], book_id: int) -> dict:
    return await post(client, "/loans", auth, {"book_id": book_id})


async def concurrent(
    client: AsyncClient,
    operations: list[tuple[str, dict[str, str], dict | None]],
) -> list[Response]:
    barrier = asyncio.Barrier(len(operations))
    sessions = set()
    original = UserRepository.by_id

    async def synchronized_lock(
        repository: UserRepository,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> User | None:
        if for_update:
            sessions.add(id(repository.session))
            # 两个独立请求均到达首次加锁点再放行，确保真实事务参与竞争。
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return await original(repository, user_id, for_update=for_update)

    async def request(path: str, auth: dict[str, str], data: dict | None) -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=client.test_app),
            base_url="http://test",
        ) as other:
            return await other.post(PREFIX + path, headers=auth, json=data)

    with patch.object(UserRepository, "by_id", synchronized_lock):
        results = await asyncio.wait_for(
            asyncio.gather(*(request(*operation) for operation in operations)),
            timeout=15,
        )
    assert len(sessions) == len(operations)
    return results


async def assert_consistent(client: AsyncClient, active: int, total: int) -> None:
    async with client.test_app.state.session_factory() as session:
        loans = list(await session.scalars(select(Loan)))
        copies = {c.id: c for c in await session.scalars(select(BookCopy))}
        current = [loan for loan in loans if loan.status == LoanStatus.BORROWED]
        assert len(loans) == total and len(current) == active
        assert len({loan.book_copy_id for loan in current}) == active
        assert sum(c.status == CopyStatus.BORROWED for c in copies.values()) == active
        for loan in loans:
            assert (loan.returned_at is None) == (loan.status == LoanStatus.BORROWED)
        for loan in current:
            assert copies[loan.book_copy_id].status == CopyStatus.BORROWED


async def test_borrow_return_and_reborrow(client: AsyncClient) -> None:
    admin_auth, books, copies = await library(client)
    user_id, auth = await reader(client)
    before = utc_now()
    loan = await borrow(client, auth, books[0])
    assert loan["user_id"] == user_id and loan["book_copy_id"] == copies[0]
    assert loan["book_id"] == books[0] and loan["status"] == "BORROWED"
    assert loan["returned_at"] is None and loan["is_overdue"] is False
    borrowed_at = datetime.fromisoformat(loan["borrowed_at"])
    assert before <= borrowed_at <= utc_now()
    assert datetime.fromisoformat(loan["due_at"]) - borrowed_at == timedelta(days=14)
    assert borrowed_at.utcoffset() == timedelta(0)
    assert set(loan) == {
        "id",
        "user_id",
        "book_id",
        "book_copy_id",
        "borrowed_at",
        "due_at",
        "returned_at",
        "status",
        "created_at",
        "updated_at",
        "is_overdue",
    }
    assert (await client.get(f"{PREFIX}/books/{books[0]}")).json()["available_copies"] == 0
    assert (await client.get(PREFIX + "/loans/me", headers=auth)).json()["total"] == 1
    assert (await client.get(f"{PREFIX}/loans/{loan['id']}", headers=auth)).json() == loan
    assert (await client.get(f"{PREFIX}/loans/{loan['id']}", headers=admin_auth)).status_code == 200
    result = await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "RETURNED" and result.json()["returned_at"] is not None
    assert result.json()["is_overdue"] is False
    assert (await client.get(f"{PREFIX}/books/{books[0]}")).json()["available_copies"] == 1
    repeated = await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
    assert repeated.status_code == 409 and repeated.json()["code"] == "LOAN_ALREADY_RETURNED"
    new = await borrow(client, auth, books[0])
    assert new["id"] != loan["id"] and new["book_copy_id"] == loan["book_copy_id"]
    assert (
        await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
    ).status_code == 409
    await assert_consistent(client, active=1, total=2)


async def test_admin_on_behalf_disabled_reader_and_inactive_book(client: AsyncClient) -> None:
    auth, books, _ = await library(client, count=2)
    user_id, user_auth = await reader(client)
    loan = await post(client, "/loans", auth, {"book_id": books[0], "user_id": user_id})
    assert loan["user_id"] == user_id
    assert (await client.delete(f"{PREFIX}/books/{books[0]}", headers=auth)).status_code == 204
    assert (
        await client.patch(
            f"{PREFIX}/users/{user_id}/status", headers=auth, json={"is_active": False}
        )
    ).status_code == 200
    response = await client.post(
        PREFIX + "/loans",
        headers=auth,
        json={"book_id": books[1], "user_id": user_id},
    )
    assert response.status_code == 409 and response.json()["code"] == "USER_INACTIVE"
    assert (await client.get(PREFIX + "/loans/me", headers=user_auth)).status_code == 401
    assert (
        await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=user_auth)
    ).status_code == 401
    assert (
        await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
    ).status_code == 200
    response = await client.post(PREFIX + "/loans", headers=auth, json={"book_id": books[0]})
    assert response.status_code == 409 and response.json()["code"] == "BOOK_NOT_AVAILABLE"
    response = await client.get(f"{PREFIX}/books/{books[0]}?is_active=false", headers=auth)
    assert response.json()["total_copies"] == 1 and response.json()["available_copies"] == 0
    await assert_consistent(client, active=0, total=1)


async def test_reader_returns_after_deactivation(client: AsyncClient) -> None:
    admin_auth, books, _ = await library(client)
    _, auth = await reader(client)
    loan = await borrow(client, auth, books[0])
    await client.delete(f"{PREFIX}/books/{books[0]}", headers=admin_auth)
    assert (
        await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
    ).status_code == 200
    await assert_consistent(client, active=0, total=1)


async def test_ownership_and_admin_only_user_id(client: AsyncClient) -> None:
    admin_auth, books, _ = await library(client)
    user_id, auth = await reader(client)
    other_id, other_auth = await reader(client, "other")
    for target in (user_id, other_id):
        response = await client.post(
            PREFIX + "/loans",
            headers=auth,
            json={"book_id": books[0], "user_id": target},
        )
        assert response.status_code == 403
    loan = await borrow(client, auth, books[0])
    assert (await client.get(f"{PREFIX}/loans/{loan['id']}", headers=other_auth)).status_code == 403
    assert (
        await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=other_auth)
    ).status_code == 403
    assert (await client.get(PREFIX + "/loans/me", headers=other_auth)).json()["total"] == 0
    assert (
        await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=admin_auth)
    ).status_code == 200
    await assert_consistent(client, active=0, total=1)


@pytest.mark.parametrize("state", ["MAINTENANCE", "LOST", "RETIRED"])
async def test_unavailable_copy(client: AsyncClient, state: str) -> None:
    auth, books, copies = await library(client)
    assert (
        await client.patch(
            f"{PREFIX}/copies/{copies[0]}/status", headers=auth, json={"status": state}
        )
    ).status_code == 200
    result = await client.post(PREFIX + "/loans", headers=auth, json={"book_id": books[0]})
    assert result.status_code == 409 and result.json()["code"] == "BOOK_NOT_AVAILABLE"
    await assert_consistent(client, active=0, total=0)


async def test_empty_stock_duplicate_and_limit_apply_to_admin(client: AsyncClient) -> None:
    auth, books, _ = await library(client, count=6, copies=2)
    user_id, user_auth = await reader(client)
    for book_id in books[:5]:
        await post(client, "/loans", auth, {"book_id": book_id, "user_id": user_id})
    for acting_auth, extra in [(auth, {"user_id": user_id}), (user_auth, {})]:
        for book_id, code in [
            (books[0], "BOOK_ALREADY_BORROWED"),
            (books[5], "LOAN_LIMIT_REACHED"),
        ]:
            result = await client.post(
                PREFIX + "/loans",
                headers=acting_auth,
                json={"book_id": book_id, **extra},
            )
            assert result.status_code == 409 and result.json()["code"] == code
    await assert_consistent(client, active=5, total=5)
    empty = await post(
        client,
        "/books",
        auth,
        {
            "title": "无馆藏",
            "category_id": (await client.get(PREFIX + "/categories")).json()[0]["id"],
        },
    )
    response = await client.post(PREFIX + "/loans", headers=auth, json={"book_id": empty["id"]})
    assert response.status_code == 409 and response.json()["code"] == "BOOK_NOT_AVAILABLE"


@pytest.mark.parametrize(
    "method,path,data,reader_status",
    [
        ("POST", "/loans", {"book_id": 999999}, 404),
        ("POST", "/loans/999999/return", None, 404),
        ("GET", "/loans/me", None, 200),
        ("GET", "/loans", None, 403),
        ("GET", "/loans/overdue", None, 403),
        ("GET", "/loans/999999", None, 404),
    ],
)
async def test_route_permissions(
    client: AsyncClient,
    method: str,
    path: str,
    data: dict | None,
    reader_status: int,
) -> None:
    assert (await client.request(method, PREFIX + path, json=data)).status_code == 401
    _, auth = await reader(client)
    result = await client.request(method, PREFIX + path, headers=auth, json=data)
    assert result.status_code == reader_status
    if reader_status != 200:
        assert set(result.json()) == {"code", "message", "details"}


async def test_invalid_parameters_and_missing_user(client: AsyncClient) -> None:
    auth, books, _ = await library(client)
    for data in (
        {},
        {"book_id": 0},
        {"book_id": 1, "user_id": None},
        {"book_id": "1"},
        {"book_id": books[0], "due_at": "2026-01-01"},
        {"book_id": books[0], "book_copy_id": 1},
    ):
        assert (await client.post(PREFIX + "/loans", headers=auth, json=data)).status_code == 422
    for path in (
        "/loans/0",
        "/loans/-1/return",
        "/loans/18446744073709551616",
        "/loans?page=0",
        "/loans?page_size=101",
        "/loans?status=OVERDUE",
        "/loans?user_id=0",
        "/loans/me?book_id=-1",
        "/loans/me?status=UNKNOWN",
        "/loans/overdue?page_size=0",
        "/loans/overdue?book_id=0",
    ):
        method = "POST" if path.endswith("/return") else "GET"
        assert (await client.request(method, PREFIX + path, headers=auth)).status_code == 422
    response = await client.post(
        PREFIX + "/loans",
        headers=auth,
        json={"book_id": books[0], "user_id": 999999},
    )
    assert response.status_code == 404 and response.json()["code"] == "USER_NOT_FOUND"
    await assert_consistent(client, active=0, total=0)


async def test_lists_filters_pagination_and_overdue_boundary(client: AsyncClient) -> None:
    auth, books, _ = await library(client, count=3, copies=2)
    user_id, user_auth = await reader(client)
    mine = [await borrow(client, user_auth, book_id) for book_id in books]
    own = await borrow(client, auth, books[0])
    now = utc_now()
    async with client.test_app.state.session_factory() as session:
        for loan_id, due, state in (
            (mine[0]["id"], now - timedelta(microseconds=1), LoanStatus.BORROWED),
            (mine[1]["id"], now, LoanStatus.BORROWED),
            (mine[2]["id"], now - timedelta(days=1), LoanStatus.RETURNED),
        ):
            loan = await session.get(Loan, loan_id)
            loan.borrowed_at = now - timedelta(days=15)
            loan.due_at = due
            loan.status = state
            if state == LoanStatus.RETURNED:
                loan.returned_at = now
                copy = await session.get(BookCopy, loan.book_copy_id)
                copy.status = CopyStatus.AVAILABLE
        await session.commit()
    with patch("app.services.loans.utc_now", return_value=now):
        for path, expected in (
            ("/loans", 4),
            (f"/loans?user_id={user_id}", 3),
            (f"/loans?book_id={books[0]}", 2),
            ("/loans?status=RETURNED", 1),
            (f"/loans?user_id={user_id}&book_id={books[1]}&status=BORROWED", 1),
            ("/loans/overdue", 1),
            (f"/loans/overdue?user_id={own['user_id']}", 0),
            (f"/loans/overdue?book_id={books[1]}", 0),
        ):
            result = await client.get(PREFIX + path, headers=auth)
            assert result.status_code == 200 and result.json()["total"] == expected, result.text
        overdue = (await client.get(PREFIX + "/loans/overdue", headers=auth)).json()["items"]
        assert overdue[0]["id"] == mine[0]["id"] and overdue[0]["is_overdue"] is True
        page = (await client.get(PREFIX + "/loans/me?page=2&page_size=1", headers=user_auth)).json()
        assert page["total"] == 3 and page["items"][0]["id"] == mine[1]["id"]
        assert page["items"][0]["is_overdue"] is False
        for query, total in [
            ("status=RETURNED", 1),
            (f"book_id={books[0]}", 1),
            ("status=BORROWED", 2),
        ]:
            assert (await client.get(PREFIX + "/loans/me?" + query, headers=user_auth)).json()[
                "total"
            ] == total
        assert (await client.get(PREFIX + "/loans?page=99", headers=auth)).json()["items"] == []
        returned = (await client.get(f"{PREFIX}/loans/{mine[2]['id']}", headers=user_auth)).json()
        assert returned["is_overdue"] is False
    await assert_consistent(client, active=3, total=4)


async def test_last_copy_competition(client: AsyncClient) -> None:
    _, books, _ = await library(client)
    _, first = await reader(client, "first")
    _, second = await reader(client, "second")
    results = await concurrent(
        client,
        [
            ("/loans", first, {"book_id": books[0]}),
            ("/loans", second, {"book_id": books[0]}),
        ],
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "BOOK_NOT_AVAILABLE"
    await assert_consistent(client, active=1, total=1)


async def test_concurrent_fifth_loan_limit(client: AsyncClient) -> None:
    admin_auth, books, _ = await library(client, count=6)
    user_id, auth = await reader(client)
    for book_id in books[:4]:
        await borrow(client, auth, book_id)
    results = await concurrent(
        client,
        [
            ("/loans", auth, {"book_id": books[4]}),
            ("/loans", admin_auth, {"book_id": books[5], "user_id": user_id}),
        ],
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "LOAN_LIMIT_REACHED"
    await assert_consistent(client, active=5, total=5)


async def test_concurrent_duplicate_book(client: AsyncClient) -> None:
    _, books, _ = await library(client, copies=2)
    _, auth = await reader(client)
    results = await concurrent(client, [("/loans", auth, {"book_id": books[0]})] * 2)
    assert sorted(r.status_code for r in results) == [201, 409]
    assert (
        next(r for r in results if r.status_code == 409).json()["code"] == "BOOK_ALREADY_BORROWED"
    )
    await assert_consistent(client, active=1, total=1)


async def test_concurrent_returns(client: AsyncClient) -> None:
    admin_auth, books, _ = await library(client)
    _, auth = await reader(client)
    loan = await borrow(client, auth, books[0])
    results = await concurrent(
        client,
        [
            (f"/loans/{loan['id']}/return", auth, None),
            (f"/loans/{loan['id']}/return", admin_auth, None),
        ],
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    await assert_consistent(client, active=0, total=1)


@pytest.mark.parametrize("operation", ["borrow", "return"])
@pytest.mark.parametrize("failure", ["flush", "commit", "integrity"])
async def test_transaction_failure_rolls_back(
    client: AsyncClient,
    operation: str,
    failure: str,
) -> None:
    auth, books, copies = await library(client)
    loan = await borrow(client, auth, books[0]) if operation == "return" else None
    original = LoanRepository.flush

    async def fail_flush(repository: LoanRepository) -> None:
        await original(repository)
        if failure == "integrity":
            raise IntegrityError("不应暴露的 SQL", {}, Exception("模拟数据库约束冲突"))
        raise RuntimeError("模拟所有写入已 flush 后失败")

    async def fail_commit(session: AsyncSession) -> None:
        raise RuntimeError("模拟提交前失败")

    target, name, effect = (
        (LoanRepository, "flush", fail_flush)
        if failure != "commit"
        else (AsyncSession, "commit", fail_commit)
    )
    with patch.object(target, name, effect):
        result = await client.post(
            PREFIX + (f"/loans/{loan['id']}/return" if loan else "/loans"),
            headers=auth,
            json=None if loan else {"book_id": books[0]},
        )
    assert result.status_code == (409 if failure == "integrity" else 500)
    assert result.json()["code"] == (
        "LOAN_CONFLICT" if failure == "integrity" else "INTERNAL_ERROR"
    )
    assert result.json()["details"] is None
    await assert_consistent(client, active=int(loan is not None), total=int(loan is not None))
    async with client.test_app.state.session_factory() as session:
        assert (await session.get(BookCopy, copies[0])).status == (
            CopyStatus.BORROWED if loan else CopyStatus.AVAILABLE
        )
    # 失败后继续业务，验证锁释放及原事务未部分提交。
    if loan:
        assert (
            await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
        ).status_code == 200
    else:
        await borrow(client, auth, books[0])


async def test_borrowed_copy_management_and_history_constraints(client: AsyncClient) -> None:
    auth, books, copies = await library(client)
    loan = await borrow(client, auth, books[0])
    for state in CopyStatus:
        result = await client.patch(
            f"{PREFIX}/copies/{copies[0]}/status",
            headers=auth,
            json={"status": state},
        )
        assert result.status_code == 409
    await client.post(f"{PREFIX}/loans/{loan['id']}/return", headers=auth)
    for model, entity_id in [(User, loan["user_id"]), (BookCopy, copies[0]), (Book, books[0])]:
        async with client.test_app.state.session_factory() as session:
            with pytest.raises(IntegrityError):
                await session.execute(delete(model).where(model.id == entity_id))
            await session.rollback()
    for values in (
        {"status": "OVERDUE"},
        {"status": "BORROWED"},
        {"returned_at": None},
        {"due_at": datetime.fromisoformat(loan["borrowed_at"])},
    ):
        async with client.test_app.state.session_factory() as session:
            # asyncmy 将 MySQL CHECK 违例 3819 归类为 OperationalError。
            with pytest.raises(OperationalError) as error:
                await session.execute(update(Loan).where(Loan.id == loan["id"]).values(**values))
            assert error.value.orig.args[0] == 3819
            await session.rollback()
    await assert_consistent(client, active=0, total=1)


async def test_changed_admin_role_rejects_old_token(client: AsyncClient) -> None:
    auth, books, _ = await library(client)
    user_id, _ = await reader(client)
    async with client.test_app.state.session_factory() as session:
        await session.execute(update(User).where(User.username == "admin").values(role=Role.READER))
        await session.commit()
    for path in ("/loans", "/loans/overdue"):
        assert (await client.get(PREFIX + path, headers=auth)).status_code == 403
    assert (
        await client.post(
            PREFIX + "/loans", headers=auth, json={"book_id": books[0], "user_id": user_id}
        )
    ).status_code == 403
    await assert_consistent(client, active=0, total=0)


@pytest.mark.parametrize(
    "change", ["self_disabled", "target_disabled", "actor_demoted", "book", "copy"]
)
async def test_borrow_rechecks_state_after_waiting_for_lock(
    client: AsyncClient, change: str
) -> None:
    admin_auth, books, copies = await library(client)
    user_id, user_auth = await reader(client)
    entered = asyncio.Event()
    if change in {"self_disabled", "target_disabled", "actor_demoted"}:
        repository, method = UserRepository, "by_id"
        original = UserRepository.by_id

        async def observe_user(
            repo: UserRepository,
            entity_id: int,
            *,
            for_update: bool = False,
        ) -> User | None:
            if for_update:
                entered.set()
            return await original(repo, entity_id, for_update=for_update)

        observed = observe_user
    elif change == "book":
        repository, method = LoanRepository, "lock_book"
        original = LoanRepository.lock_book

        async def observe_book(repo: LoanRepository, entity_id: int) -> Book | None:
            entered.set()
            return await original(repo, entity_id)

        observed = observe_book
    else:
        repository, method = LoanRepository, "available_copy"
        original = LoanRepository.available_copy

        async def observe_copy(repo: LoanRepository, entity_id: int) -> BookCopy | None:
            entered.set()
            return await original(repo, entity_id)

        observed = observe_copy

    acting_auth = admin_auth if change in {"target_disabled", "actor_demoted"} else user_auth
    data = {"book_id": books[0]}
    if acting_auth is admin_auth:
        data["user_id"] = user_id

    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=client.test_app),
            base_url="http://test",
        ) as other:
            return await other.post(PREFIX + "/loans", headers=acting_auth, json=data)

    async with client.test_app.state.session_factory() as session:
        if change in {"self_disabled", "target_disabled"}:
            await session.execute(update(User).where(User.id == user_id).values(is_active=False))
        elif change == "actor_demoted":
            await session.execute(update(User).where(User.id == user_id).values(full_name="等待锁"))
        elif change == "book":
            await session.execute(update(Book).where(Book.id == books[0]).values(is_active=False))
        else:
            await session.execute(
                update(BookCopy)
                .where(BookCopy.id == copies[0])
                .values(status=CopyStatus.MAINTENANCE)
            )
        with patch.object(repository, method, observed):
            task = asyncio.create_task(request())
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert not task.done()
                if change == "actor_demoted":
                    await session.execute(
                        update(User).where(User.username == "admin").values(role=Role.READER)
                    )
                await session.commit()
                result = await asyncio.wait_for(task, timeout=5)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    expected = {"self_disabled": 401, "actor_demoted": 403}.get(change, 409)
    assert result.status_code == expected, result.text
    await assert_consistent(client, active=0, total=0)


async def test_concurrent_return_and_reborrow(client: AsyncClient) -> None:
    _, books, _ = await library(client)
    _, auth = await reader(client)
    loan = await borrow(client, auth, books[0])
    returned, borrowed = await concurrent(
        client,
        [
            (f"/loans/{loan['id']}/return", auth, None),
            ("/loans", auth, {"book_id": books[0]}),
        ],
    )
    assert returned.status_code == 200
    assert borrowed.status_code in (201, 409)
    if borrowed.status_code == 409:
        assert borrowed.json()["code"] == "BOOK_ALREADY_BORROWED"
    active = int(borrowed.status_code == 201)
    await assert_consistent(client, active=active, total=1 + active)


async def test_two_readers_can_borrow_two_copies(client: AsyncClient) -> None:
    _, books, _ = await library(client, copies=2)
    _, first = await reader(client, "first")
    _, second = await reader(client, "second")
    results = await concurrent(
        client,
        [
            ("/loans", first, {"book_id": books[0]}),
            ("/loans", second, {"book_id": books[0]}),
        ],
    )
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json()["book_copy_id"] != results[1].json()["book_copy_id"]
    await assert_consistent(client, active=2, total=2)
