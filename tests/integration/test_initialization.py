import os

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from test_auth_users import PASSWORD, login

from app.models.catalog import Author, Book, BookCopy, Category, CopyStatus
from app.models.loan import Loan
from app.models.user import User
from app.schemas.user import RegisterInput
from scripts.init_admin import initialize
from scripts.seed_dev import mock_isbn, seed
from scripts.test_mysql import assert_empty_database

pytestmark = pytest.mark.integration


async def test_initialization_to_borrow_return(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = client.test_app.state.settings
    url = settings.database_url.get_secret_value()
    database = make_url(url).database
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JWT_SECRET", settings.jwt_secret.get_secret_value())
    data = RegisterInput(username="admin", email="admin@example.com", password=PASSWORD)
    assert await initialize(data) is True
    assert await initialize(data) is False
    assert await seed(settings, database) == {
        "categories": 5,
        "authors": 10,
        "books": 24,
        "book_copies": 69,
    }
    auth = await login(client, "admin")
    books = (await client.get("/api/v1/books?available_only=true")).json()
    book_id = books["items"][0]["id"]
    borrowed = await client.post("/api/v1/loans", json={"book_id": book_id}, headers=auth)
    assert borrowed.status_code == 201
    loan = borrowed.json()
    async with client.test_app.state.session_factory() as session:
        book = await session.get(Book, book_id)
        book.title = "保留人工修改"
        await session.commit()
        password_hash = await session.scalar(select(User.password_hash))
    assert all(count == 0 for count in (await seed(settings, database)).values())
    assert await initialize(data) is False
    async with client.test_app.state.session_factory() as session:
        assert await session.scalar(select(func.count(User.id))) == 1
        assert await session.scalar(select(User.password_hash)) == password_hash
        assert (await session.get(Book, book_id)).title == "保留人工修改"
        assert (await session.get(BookCopy, loan["book_copy_id"])).status == CopyStatus.BORROWED
        assert await session.scalar(select(func.count(Loan.id))) == 1
    returned = await client.post(f"/api/v1/loans/{loan['id']}/return", headers=auth)
    assert returned.status_code == 200 and returned.json()["status"] == "RETURNED"
    assert returned.headers["X-Request-ID"]
    async with client.test_app.state.session_factory() as session:
        assert (await session.get(BookCopy, loan["book_copy_id"])).status == CopyStatus.AVAILABLE


@pytest.mark.parametrize("conflict", ["isbn", "barcode"])
async def test_seed_conflict_rolls_back_all_new_rows(client: AsyncClient, conflict: str) -> None:
    settings = client.test_app.state.settings
    async with client.test_app.state.session_factory() as session:
        category = Category(name="已有分类")
        session.add(category)
        await session.flush()
        book = Book(
            title="保留已有图书",
            category_id=category.id,
            isbn=mock_isbn(2) if conflict == "isbn" else None,
        )
        session.add(book)
        await session.flush()
        if conflict == "barcode":
            session.add(BookCopy(book_id=book.id, barcode="MOCK-002-01"))
        await session.commit()
    with pytest.raises(ValueError if conflict == "isbn" else IntegrityError):
        await seed(settings, make_url(settings.database_url.get_secret_value()).database)
    async with client.test_app.state.session_factory() as session:
        assert await session.scalar(select(func.count(Category.id))) == 1
        assert await session.scalar(select(func.count(Book.id))) == 1
        assert await session.scalar(select(func.count(Author.id))) == 0
        assert await session.scalar(select(func.count(BookCopy.id))) == (conflict == "barcode")
        assert await session.scalar(select(Book.title)) == "保留已有图书"


async def test_empty_migration_guard_preserves_existing_database(client: AsyncClient) -> None:
    with pytest.raises(ValueError, match="已有表或视图"):
        await assert_empty_database(os.environ["TEST_DATABASE_URL"])
    async with client.test_app.state.session_factory() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0003_loans"


async def test_migrated_schema_uses_seven_innodb_tables(client: AsyncClient) -> None:
    async with client.test_app.state.session_factory() as session:
        result = await session.execute(
            text(
                "SELECT table_name AS name, engine AS engine_name, table_collation AS collation "
                "FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name <> 'alembic_version'"
            )
        )
        rows = result.all()
        assert {r.name for r in rows} == {
            "users",
            "categories",
            "authors",
            "books",
            "book_authors",
            "book_copies",
            "loans",
        }
        assert all(r.engine_name == "InnoDB" and r.collation == "utf8mb4_0900_as_ci" for r in rows)
