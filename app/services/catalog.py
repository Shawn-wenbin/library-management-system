from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.base import utc_now
from app.models.catalog import Author, Book, BookCopy, Category, CopyStatus
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import (
    AuthorCreate,
    AuthorPatch,
    BookCreate,
    BookOutput,
    BookPatch,
    CategoryCreate,
    CategoryPatch,
    CopyCreate,
    CopyPatch,
    SortBy,
    SortOrder,
)


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = CatalogRepository(session)

    @asynccontextmanager
    async def write_transaction(self, duplicate_code: str) -> AsyncIterator[None]:
        # 接续权限查询开启的事务；所有写入及关系更新由顶层业务一起提交。
        try:
            yield
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            number = exc.orig.args[0] if exc.orig.args else None
            if number == 1062:
                raise AppError(409, duplicate_code, "唯一字段已被使用") from None
            if number in (1451, 1452):
                raise AppError(409, "REFERENCE_CONFLICT", "关联资源仍在使用或已发生变化") from None
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def categories(self) -> list[Category]:
        return await self.repository.categories()

    async def create_category(self, data: CategoryCreate) -> Category:
        async with self.write_transaction("CATEGORY_EXISTS"):
            category = Category(**data.model_dump())
            await self.repository.add(category)
        return category

    async def update_category(self, entity_id: int, data: CategoryPatch) -> Category:
        async with self.write_transaction("CATEGORY_EXISTS"):
            category = await self.repository.category(entity_id, lock=True)
            if category is None:
                raise AppError(404, "CATEGORY_NOT_FOUND", "分类不存在")
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(category, field, value)
            await self.repository.flush()
        return category

    async def delete_category(self, entity_id: int) -> None:
        async with self.write_transaction("CATEGORY_EXISTS"):
            category = await self.repository.category(entity_id, lock=True)
            if category is None:
                raise AppError(404, "CATEGORY_NOT_FOUND", "分类不存在")
            if await self.repository.category_in_use(entity_id):
                raise AppError(409, "CATEGORY_IN_USE", "分类仍关联图书")
            await self.repository.remove(category)

    async def authors(self, page: int, page_size: int) -> tuple[list[Author], int]:
        return await self.repository.authors(page, page_size)

    async def author(self, entity_id: int) -> Author:
        author = await self.repository.author(entity_id)
        if author is None:
            raise AppError(404, "AUTHOR_NOT_FOUND", "作者不存在")
        return author

    async def create_author(self, data: AuthorCreate) -> Author:
        async with self.write_transaction("AUTHOR_EXISTS"):
            author = Author(**data.model_dump())
            await self.repository.add(author)
        return author

    async def update_author(self, entity_id: int, data: AuthorPatch) -> Author:
        async with self.write_transaction("AUTHOR_EXISTS"):
            author = await self.repository.author(entity_id, lock=True)
            if author is None:
                raise AppError(404, "AUTHOR_NOT_FOUND", "作者不存在")
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(author, field, value)
            await self.repository.flush()
        return author

    async def delete_author(self, entity_id: int) -> None:
        async with self.write_transaction("AUTHOR_EXISTS"):
            author = await self.repository.author(entity_id, lock=True)
            if author is None:
                raise AppError(404, "AUTHOR_NOT_FOUND", "作者不存在")
            if await self.repository.author_in_use(entity_id):
                raise AppError(409, "AUTHOR_IN_USE", "作者仍关联图书")
            await self.repository.remove(author)

    async def _category(self, entity_id: int) -> Category:
        category = await self.repository.category(entity_id)
        if category is None:
            raise AppError(404, "CATEGORY_NOT_FOUND", "分类不存在")
        return category

    async def _authors(self, ids: list[int]) -> list[Author]:
        authors = await self.repository.authors_by_ids(ids)
        if len(authors) != len(ids):
            raise AppError(404, "AUTHOR_NOT_FOUND", "部分作者不存在")
        return authors

    async def _book(self, entity_id: int, *, lock: bool = False) -> Book:
        book = await self.repository.book(entity_id, lock=lock)
        if book is None:
            raise AppError(404, "BOOK_NOT_FOUND", "图书不存在")
        return book

    async def _outputs(self, books: list[Book]) -> list[BookOutput]:
        inventory = await self.repository.inventory([book.id for book in books])
        outputs = []
        for book in books:
            total, available = inventory.get(book.id, (0, 0))
            # 显式挑选响应字段，不加载或输出实体条码及位置。
            values = {
                field: getattr(book, field)
                for field in BookOutput.model_fields
                if field not in {"total_copies", "available_copies"}
            }
            outputs.append(
                BookOutput(
                    **values,
                    total_copies=total,
                    available_copies=available if book.is_active else 0,
                )
            )
        return outputs

    async def books(
        self,
        page: int,
        page_size: int,
        keyword: str | None,
        category_id: int | None,
        author_id: int | None,
        available_only: bool,
        is_active: bool,
        sort_by: SortBy,
        sort_order: SortOrder,
    ) -> tuple[list[BookOutput], int]:
        books, total = await self.repository.books(
            page,
            page_size,
            keyword,
            category_id,
            author_id,
            available_only,
            is_active,
            sort_by,
            sort_order,
        )
        return await self._outputs(books), total

    async def book(self, entity_id: int, is_active: bool = True) -> BookOutput:
        book = await self._book(entity_id)
        if book.is_active != is_active:
            raise AppError(404, "BOOK_NOT_FOUND", "图书不存在")
        return (await self._outputs([book]))[0]

    async def create_book(self, data: BookCreate) -> BookOutput:
        async with self.write_transaction("ISBN_EXISTS"):
            category = await self._category(data.category_id)
            authors = await self._authors(data.author_ids)
            book = Book(
                **data.model_dump(exclude={"author_ids", "category_id", "cover_url"}),
                cover_url=str(data.cover_url) if data.cover_url else None,
                category=category,
                authors=authors,
            )
            await self.repository.add(book)
            output = (await self._outputs([book]))[0]
        return output

    async def update_book(self, entity_id: int, data: BookPatch) -> BookOutput:
        async with self.write_transaction("ISBN_EXISTS"):
            book = await self._book(entity_id, lock=True)
            for field, value in data.model_dump(exclude_unset=True).items():
                if field == "category_id":
                    book.category = await self._category(value)
                elif field == "author_ids":
                    book.authors = await self._authors(value)
                elif field == "cover_url":
                    book.cover_url = str(value) if value is not None else None
                else:
                    setattr(book, field, value)
            # 仅变更作者关系也更新书目的修改时间。
            if "author_ids" in data.model_fields_set:
                book.updated_at = utc_now()
            await self.repository.flush()
            output = (await self._outputs([book]))[0]
        return output

    async def deactivate_book(self, entity_id: int) -> None:
        async with self.write_transaction("ISBN_EXISTS"):
            book = await self._book(entity_id, lock=True)
            book.is_active = False
            await self.repository.flush()

    async def copies(
        self, book_id: int, page: int, page_size: int, status: CopyStatus | None
    ) -> tuple[list[BookCopy], int]:
        await self._book(book_id)
        return await self.repository.copies(book_id, page, page_size, status)

    async def create_copy(self, book_id: int, data: CopyCreate) -> BookCopy:
        async with self.write_transaction("BARCODE_EXISTS"):
            await self._book(book_id)
            copy = BookCopy(book_id=book_id, status=CopyStatus.AVAILABLE, **data.model_dump())
            await self.repository.add(copy)
        return copy

    async def copy(self, entity_id: int, *, lock: bool = False) -> BookCopy:
        copy = await self.repository.copy(entity_id, lock=lock)
        if copy is None:
            raise AppError(404, "COPY_NOT_FOUND", "实体副本不存在")
        return copy

    async def update_copy(self, entity_id: int, data: CopyPatch) -> BookCopy:
        async with self.write_transaction("BARCODE_EXISTS"):
            copy = await self.copy(entity_id, lock=True)
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(copy, field, value)
            await self.repository.flush()
        return copy

    async def update_copy_status(self, entity_id: int, status: CopyStatus) -> BookCopy:
        async with self.write_transaction("BARCODE_EXISTS"):
            copy = await self.copy(entity_id, lock=True)
            if copy.status == CopyStatus.BORROWED or status == CopyStatus.BORROWED:
                raise AppError(409, "COPY_STATUS_CONFLICT", "借出状态只能经借阅或归还流程改变")
            copy.status = status
            await self.repository.flush()
        return copy
