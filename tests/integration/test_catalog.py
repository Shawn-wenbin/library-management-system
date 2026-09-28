import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from test_auth_users import admin, login, register

from app.models.catalog import Book, BookCopy, Category, CopyStatus, book_authors
from app.models.user import Role, User
from app.repositories.catalog import CatalogRepository

pytestmark = pytest.mark.integration
PREFIX = "/api/v1"


async def post(client: AsyncClient, path: str, auth: dict, data: dict) -> dict:
    response = await client.post(PREFIX + path, headers=auth, json=data)
    assert response.status_code == 201, response.text
    return response.json()


async def catalog(client: AsyncClient) -> tuple[dict, dict, list[dict], dict]:
    _, auth = await admin(client)
    category = await post(client, "/categories", auth, {"name": "计算机"})
    authors = [await post(client, "/authors", auth, {"name": name}) for name in ("张三", "李四")]
    book = await post(
        client,
        "/books",
        auth,
        {
            "title": "异步数据库",
            "category_id": category["id"],
            "author_ids": [a["id"] for a in authors],
            "isbn": "978-0-306-40615-7",
        },
    )
    return auth, category, authors, book


async def test_catalog_lifecycle_and_history_preservation(client: AsyncClient) -> None:
    auth, category, authors, book = await catalog(client)
    book_id = book["id"]
    assert len(book["authors"]) == 2 and book["total_copies"] == book["available_copies"] == 0
    assert book["category"]["id"] == category["id"] and book["isbn"] == "9780306406157"
    assert book["created_at"].endswith("Z")
    copies = [
        await post(
            client, f"/books/{book_id}/copies", auth, {"barcode": f"BC-{i}", "location": "A1"}
        )
        for i in range(3)
    ]
    response = await client.patch(
        f"{PREFIX}/copies/{copies[0]['id']}/status", headers=auth, json={"status": "MAINTENANCE"}
    )
    assert response.status_code == 200
    detail = (await client.get(f"{PREFIX}/books/{book_id}")).json()
    assert (detail["total_copies"], detail["available_copies"]) == (3, 2)
    assert "barcode" not in str(detail) and "location" not in str(detail)
    assert (await client.get(PREFIX + "/categories")).json()[0]["name"] == "计算机"
    assert (await client.get(PREFIX + "/authors?page_size=1&page=2")).json()["items"][0][
        "id"
    ] == authors[1]["id"]
    assert (await client.get(f"{PREFIX}/authors/{authors[0]['id']}")).status_code == 200
    for path in (f"/categories/{category['id']}", f"/authors/{authors[0]['id']}"):
        assert (await client.delete(PREFIX + path, headers=auth)).status_code == 409
    for _ in range(2):
        assert (await client.delete(f"{PREFIX}/books/{book_id}", headers=auth)).status_code == 204
    assert (await client.get(f"{PREFIX}/books/{book_id}")).status_code == 404
    assert (await client.get(PREFIX + "/books")).json()["total"] == 0
    hidden = await client.get(f"{PREFIX}/books/{book_id}?is_active=false", headers=auth)
    assert (
        hidden.status_code == 200
        and hidden.json()["total_copies"] == 3
        and hidden.json()["available_copies"] == 0
    )
    assert len(hidden.json()["authors"]) == 2
    assert (
        await client.delete(f"{PREFIX}/authors/{authors[0]['id']}", headers=auth)
    ).status_code == 409
    assert (
        await client.delete(f"{PREFIX}/categories/{category['id']}", headers=auth)
    ).status_code == 409
    assert (await client.get(PREFIX + "/books?is_active=false", headers=auth)).json()["total"] == 1
    assert (
        await client.get(PREFIX + "/books?is_active=false&available_only=true", headers=auth)
    ).json()["total"] == 0
    response = await client.patch(
        f"{PREFIX}/books/{book_id}",
        headers=auth,
        json={"is_active": True, "author_ids": [], "isbn": None},
    )
    assert (
        response.status_code == 200
        and response.json()["authors"] == []
        and response.json()["available_copies"] == 2
    )
    assert (
        await client.delete(f"{PREFIX}/authors/{authors[0]['id']}", headers=auth)
    ).status_code == 204
    async with client.test_app.state.session_factory() as session:
        assert await session.scalar(select(func.count(BookCopy.id))) == 3
        assert await session.get(Book, book_id) is not None


async def test_search_filters_sort_and_pagination(client: AsyncClient) -> None:
    auth, category, authors, book = await catalog(client)
    second = await post(
        client,
        "/books",
        auth,
        {
            "title": "100%_指南",
            "category_id": category["id"],
            "author_ids": [a["id"] for a in authors],
        },
    )
    third_category = await post(client, "/categories", auth, {"name": "文学"})
    await post(client, "/books", auth, {"title": "小说", "category_id": third_category["id"]})
    await post(client, f"/books/{second['id']}/copies", auth, {"barcode": "search"})
    for query, total in [
        ("keyword=数据库", 1),
        ("keyword=978030", 1),
        ("keyword=张", 2),
        ("keyword=%25_", 1),
        ("keyword=不存在", 0),
        (f"category_id={category['id']}", 2),
        (f"author_id={authors[0]['id']}", 2),
        ("available_only=true", 1),
        (f"author_id={authors[0]['id']}&available_only=true", 1),
        ("keyword=' OR 1=1 --", 0),
    ]:
        result = (await client.get(PREFIX + "/books?" + query)).json()
        assert result["total"] == total, (query, result)
        assert len({b["id"] for b in result["items"]}) == total
    result = (
        await client.get(PREFIX + "/books?page_size=1&page=2&sort_by=id&sort_order=desc")
    ).json()
    assert result["total"] == 3 and result["items"][0]["id"] == second["id"]
    for sort in ("title", "publication_date", "created_at"):
        result = await client.get(PREFIX + f"/books?sort_by={sort}&sort_order=desc")
        assert result.status_code == 200 and result.json()["total"] == 3
    for query in (
        "page=0",
        "page_size=101",
        "sort_by=password_hash",
        "sort_order=drop",
        "category_id=0",
        "author_id=-1",
        "keyword=",
    ):
        assert (await client.get(PREFIX + "/books?" + query)).status_code == 422
    assert (await client.get(PREFIX + "/books?page=99")).json()["items"] == []


async def test_reference_updates_and_conflicts(client: AsyncClient) -> None:
    auth, category, authors, book = await catalog(client)
    same_name = await post(client, "/authors", auth, {"name": "张三"})
    assert same_name["id"] != authors[0]["id"]
    assert (
        await client.post(PREFIX + "/categories", headers=auth, json={"name": "  计算机  "})
    ).status_code == 409
    assert (
        await client.post(
            PREFIX + "/books",
            headers=auth,
            json={"title": "重复", "category_id": category["id"], "isbn": book["isbn"]},
        )
    ).status_code == 409
    new_category = await post(client, "/categories", auth, {"name": "新分类"})
    assert (
        await client.patch(
            f"{PREFIX}/categories/{new_category['id']}", headers=auth, json={"name": "计算机"}
        )
    ).status_code == 409
    response = await client.patch(
        f"{PREFIX}/categories/{new_category['id']}", headers=auth, json={"description": "说明"}
    )
    assert response.status_code == 200 and response.json()["description"] == "说明"
    response = await client.patch(
        f"{PREFIX}/authors/{same_name['id']}", headers=auth, json={"biography": "作者简介"}
    )
    assert response.status_code == 200 and response.json()["biography"] == "作者简介"
    response = await client.patch(
        f"{PREFIX}/books/{book['id']}",
        headers=auth,
        json={
            "category_id": new_category["id"],
            "author_ids": [same_name["id"]],
            "publication_date": "2024-01-02",
            "cover_url": "https://example.com/book.jpg",
        },
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["category_id"] == new_category["id"]
        and response.json()["authors"][0]["id"] == same_name["id"]
    )
    assert (
        await client.delete(f"{PREFIX}/categories/{category['id']}", headers=auth)
    ).status_code == 204
    for payload in ({"category_id": 999999}, {"author_ids": [999999]}):
        assert (
            await client.patch(f"{PREFIX}/books/{book['id']}", headers=auth, json=payload)
        ).status_code == 404
    for payload in (
        {"title": None},
        {"title": " "},
        {"isbn": "9780306406158"},
        {"author_ids": [same_name["id"], same_name["id"]]},
        {},
        {"stock": 1},
    ):
        assert (
            await client.patch(f"{PREFIX}/books/{book['id']}", headers=auth, json=payload)
        ).status_code == 422
    for path in ("/authors/999999", "/books/999999", "/copies/999999", "/books/999999/copies"):
        assert (await client.get(PREFIX + path, headers=auth)).status_code == 404
    for path in ("/authors/999999", "/categories/999999", "/books/999999"):
        assert (await client.delete(PREFIX + path, headers=auth)).status_code == 404


async def test_copy_metadata_status_and_private_details(client: AsyncClient) -> None:
    auth, _, _, book = await catalog(client)
    copy = await post(
        client,
        f"/books/{book['id']}/copies",
        auth,
        {"barcode": "  Immutable  ", "location": "A", "acquired_at": "2025-01-01"},
    )
    path = f"{PREFIX}/copies/{copy['id']}"
    assert copy["barcode"] == "Immutable" and copy["status"] == "AVAILABLE"
    duplicate = await client.post(
        f"{PREFIX}/books/{book['id']}/copies", headers=auth, json={"barcode": "immutable"}
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "BARCODE_EXISTS"
    assert (await client.get(path, headers=auth)).status_code == 200
    result = await client.patch(path, headers=auth, json={"location": None, "acquired_at": None})
    assert result.status_code == 200 and result.json()["location"] is None
    for data in ({"barcode": "new"}, {"book_id": book["id"]}, {"status": "LOST"}, {}):
        assert (await client.patch(path, headers=auth, json=data)).status_code == 422
    for state in ("MAINTENANCE", "LOST", "RETIRED", "AVAILABLE", "AVAILABLE"):
        response = await client.patch(path + "/status", headers=auth, json={"status": state})
        assert response.status_code == 200 and response.json()["status"] == state
        result = (
            await client.get(f"{PREFIX}/books/{book['id']}/copies?status={state}", headers=auth)
        ).json()
        assert result["total"] == 1
    assert (
        await client.patch(path + "/status", headers=auth, json={"status": "BORROWED"})
    ).status_code == 409
    assert (
        await client.patch(path + "/status", headers=auth, json={"status": "OTHER"})
    ).status_code == 422
    # 仅测试准备写入借出状态，本阶段不建立借阅业务或表。
    async with client.test_app.state.session_factory() as session:
        await session.execute(
            update(BookCopy).where(BookCopy.id == copy["id"]).values(status=CopyStatus.BORROWED)
        )
        await session.commit()
    for state in CopyStatus:
        assert (
            await client.patch(path + "/status", headers=auth, json={"status": state})
        ).status_code == 409
    assert (await client.patch(path, headers=auth, json={"location": "B"})).status_code == 200
    async with client.test_app.state.session_factory() as session:
        stored = await session.get(BookCopy, copy["id"])
        assert stored.status == CopyStatus.BORROWED and stored.barcode == "Immutable"


@pytest.mark.parametrize(
    "method,path,data",
    [
        ("POST", "/categories", {"name": "x"}),
        ("PATCH", "/categories/1", {"name": "x"}),
        ("DELETE", "/categories/1", None),
        ("POST", "/authors", {"name": "x"}),
        ("PATCH", "/authors/1", {"name": "x"}),
        ("DELETE", "/authors/1", None),
        ("POST", "/books", {"title": "x", "category_id": 1}),
        ("PATCH", "/books/1", {"title": "x"}),
        ("DELETE", "/books/1", None),
        ("GET", "/books/1/copies", None),
        ("POST", "/books/1/copies", {"barcode": "x"}),
        ("GET", "/copies/1", None),
        ("PATCH", "/copies/1", {"location": "x"}),
        ("PATCH", "/copies/1/status", {"status": "AVAILABLE"}),
        ("GET", "/books?is_active=false", None),
        ("GET", "/books/1?is_active=false", None),
    ],
)
async def test_all_management_permissions(
    client: AsyncClient, method: str, path: str, data: dict | None
) -> None:
    assert (await client.request(method, PREFIX + path, json=data)).status_code == 401
    await register(client)
    auth = await login(client)
    assert (await client.request(method, PREFIX + path, headers=auth, json=data)).status_code == 403


async def test_old_admin_token_obeys_current_permissions(client: AsyncClient) -> None:
    user_id, auth = await admin(client)
    async with client.test_app.state.session_factory() as session:
        await session.execute(update(User).where(User.id == user_id).values(role=Role.READER))
        await session.commit()
    assert (
        await client.post(PREFIX + "/categories", headers=auth, json={"name": "x"})
    ).status_code == 403
    async with client.test_app.state.session_factory() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(role=Role.ADMIN, is_active=False)
        )
        await session.commit()
    assert (await client.get(PREFIX + "/books?is_active=false", headers=auth)).status_code == 401
    assert (await client.get(PREFIX + "/copies/1", headers=auth)).status_code == 401


async def test_failed_book_relation_update_rolls_back(client: AsyncClient) -> None:
    auth, category, authors, book = await catalog(client)
    original = CatalogRepository.flush

    async def fail_after_flush(repository: CatalogRepository) -> None:
        await original(repository)
        raise RuntimeError("模拟关联更新后的事务失败")

    with patch.object(CatalogRepository, "flush", fail_after_flush):
        result = await client.patch(
            f"{PREFIX}/books/{book['id']}",
            headers=auth,
            json={"title": "不应保存", "author_ids": []},
        )
    assert result.status_code == 500 and result.json()["code"] == "INTERNAL_ERROR"
    async with client.test_app.state.session_factory() as session:
        stored = await session.get(Book, book["id"])
        assert stored.title == book["title"] and stored.category_id == category["id"]
        assert await session.scalar(select(func.count()).select_from(book_authors)) == 2
    result = await client.patch(
        f"{PREFIX}/books/{book['id']}",
        headers=auth,
        json={"title": "仍不保存", "author_ids": [999999]},
    )
    assert result.status_code == 404
    assert (await client.get(f"{PREFIX}/books/{book['id']}")).json()["title"] == book["title"]


@pytest.mark.parametrize("resource", ["category", "isbn", "barcode"])
async def test_concurrent_unique_constraints(client: AsyncClient, resource: str) -> None:
    auth, category, _, book = await catalog(client)
    path, data, model = {
        "category": ("/categories", {"name": "唯一"}, Category),
        "isbn": (
            "/books",
            {"title": "唯一", "category_id": category["id"], "isbn": "9783161484100"},
            Book,
        ),
        "barcode": (f"/books/{book['id']}/copies", {"barcode": "唯一"}, BookCopy),
    }[resource]

    async def request() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=client.test_app), base_url="http://test"
        ) as other:
            return (await other.post(PREFIX + path, headers=auth, json=data)).status_code

    assert sorted(await asyncio.gather(request(), request())) == [201, 409]
    async with client.test_app.state.session_factory() as session:
        expected = 1 if resource == "barcode" else 2
        assert await session.scalar(select(func.count(model.id))) == expected


async def test_copy_failure_rolls_back_status(client: AsyncClient) -> None:
    auth, _, _, book = await catalog(client)
    copy = await post(client, f"/books/{book['id']}/copies", auth, {"barcode": "rollback"})
    original = CatalogRepository.flush

    async def fail_after_flush(repository: CatalogRepository) -> None:
        await original(repository)
        raise RuntimeError("模拟馆藏更新失败")

    with patch.object(CatalogRepository, "flush", fail_after_flush):
        result = await client.patch(
            f"{PREFIX}/copies/{copy['id']}/status", headers=auth, json={"status": "LOST"}
        )
    assert result.status_code == 500
    async with client.test_app.state.session_factory() as session:
        stored = await session.get(BookCopy, copy["id"])
        assert stored.status == CopyStatus.AVAILABLE


async def test_copy_status_reads_latest_locked_state(client: AsyncClient) -> None:
    auth, _, _, book = await catalog(client)
    copy = await post(client, f"/books/{book['id']}/copies", auth, {"barcode": "locked"})
    entered = asyncio.Event()
    original = CatalogRepository.copy

    async def observed_read(
        repository: CatalogRepository, entity_id: int, *, lock: bool = False
    ) -> BookCopy | None:
        if lock:
            entered.set()
        return await original(repository, entity_id, lock=lock)

    async def request() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=client.test_app), base_url="http://test"
        ) as other:
            response = await other.patch(
                f"{PREFIX}/copies/{copy['id']}/status", headers=auth, json={"status": "MAINTENANCE"}
            )
            return response.status_code

    # 独立事务先持锁写入借出状态，验证管理请求等待后按最新状态拒绝修改。
    async with client.test_app.state.session_factory() as session:
        await session.execute(
            update(BookCopy).where(BookCopy.id == copy["id"]).values(status=CopyStatus.BORROWED)
        )
        with patch.object(CatalogRepository, "copy", observed_read):
            task = asyncio.create_task(request())
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert not task.done()
                await session.commit()
                assert await asyncio.wait_for(task, timeout=5) == 409
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with client.test_app.state.session_factory() as session:
        stored = await session.get(BookCopy, copy["id"])
        assert stored.status == CopyStatus.BORROWED


@pytest.mark.parametrize(
    "path,data,expected",
    [
        ("/categories", {"name": " "}, 422),
        ("/categories", {"name": "x", "description": "x" * 501}, 422),
        ("/authors", {"name": ""}, 422),
        ("/authors", {"name": "x", "unknown": True}, 422),
        ("/books", {"title": "x", "category_id": 999999}, 404),
        ("/books/999999/copies", {"barcode": "x"}, 404),
        ("/books/1/copies", {"barcode": "x", "status": "BORROWED"}, 422),
        ("/books/1/copies", {"barcode": " "}, 422),
    ],
)
async def test_create_validation(client: AsyncClient, path: str, data: dict, expected: int) -> None:
    _, auth = await admin(client)
    response = await client.post(PREFIX + path, headers=auth, json=data)
    assert response.status_code == expected
    assert set(response.json()) == {"code", "message", "details"}


async def test_unique_update_conflict_restores_authors(client: AsyncClient) -> None:
    auth, category, authors, book = await catalog(client)
    second = await post(
        client,
        "/books",
        auth,
        {"title": "第二本", "category_id": category["id"], "author_ids": [authors[0]["id"]]},
    )
    response = await client.patch(
        f"{PREFIX}/books/{second['id']}",
        headers=auth,
        json={"isbn": book["isbn"], "author_ids": [], "title": "冲突"},
    )
    assert response.status_code == 409 and response.json()["code"] == "ISBN_EXISTS"
    response = await client.get(f"{PREFIX}/books/{second['id']}")
    assert response.json()["title"] == "第二本" and response.json()["isbn"] is None
    assert response.json()["authors"][0]["id"] == authors[0]["id"]
