from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.catalog import Author, Book, BookCopy, Category, CopyStatus, book_authors
from app.schemas.catalog import SortBy, SortOrder


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def category(self, entity_id: int, *, lock: bool = False) -> Category | None:
        query = select(Category).where(Category.id == entity_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def author(self, entity_id: int, *, lock: bool = False) -> Author | None:
        query = select(Author).where(Author.id == entity_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def categories(self) -> list[Category]:
        return list(await self.session.scalars(select(Category).order_by(Category.id)))

    async def authors(self, page: int, page_size: int) -> tuple[list[Author], int]:
        total = await self.session.scalar(select(func.count(Author.id)))
        items = await self.session.scalars(
            select(Author).order_by(Author.id).offset((page - 1) * page_size).limit(page_size)
        )
        return list(items), int(total or 0)

    async def authors_by_ids(self, ids: list[int]) -> list[Author]:
        return list(
            await self.session.scalars(select(Author).where(Author.id.in_(ids)).order_by(Author.id))
        )

    async def category_in_use(self, entity_id: int) -> bool:
        return bool(
            await self.session.scalar(select(Book.id).where(Book.category_id == entity_id).limit(1))
        )

    async def author_in_use(self, entity_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(book_authors.c.book_id).where(book_authors.c.author_id == entity_id).limit(1)
            )
        )

    async def add(self, entity: Category | Author | Book | BookCopy) -> None:
        self.session.add(entity)
        await self.session.flush()

    async def remove(self, entity: Category | Author) -> None:
        await self.session.delete(entity)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()

    async def book(self, entity_id: int, *, lock: bool = False) -> Book | None:
        query = (
            select(Book)
            .where(Book.id == entity_id)
            .options(selectinload(Book.category), selectinload(Book.authors))
            # 自动预加载`category` 和`authors` 关联
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def books(
        self,
        page: int,
        page_size: int,
        keyword: str | None,     # None = 不做关键词搜索
        category_id: int | None, # None = 不按分类过滤
        author_id: int | None,   # None = 不按作者过滤
        available_only: bool,    # True = 仅显示"至少有一本可借副本"的书
        is_active: bool,         # True = 只看已上架；False = 管理员看下架的
        sort_by: SortBy,
        sort_order: SortOrder,
    ) -> tuple[list[Book], int]:
        predicates = [Book.is_active == is_active]
        if keyword:
            predicates.append(
                or_(
                    Book.title.contains(keyword, autoescape=True),
                    Book.isbn.contains(keyword, autoescape=True),
                    Book.authors.any(Author.name.contains(keyword, autoescape=True)),
                )
            )
        if category_id is not None:
            predicates.append(Book.category_id == category_id)
        if author_id is not None:
            predicates.append(Book.authors.any(Author.id == author_id))
        if available_only:
            predicates.extend([
                Book.is_active.is_(True),
                Book.copies.any(BookCopy.status == CopyStatus.AVAILABLE)
            ])
        total = await self.session.scalar(select(func.count(Book.id)).where(*predicates))
        # 排序只从明确的列白名单中选择，并以主键稳定分页。
        column = {
            "id": Book.id,
            "title": Book.title,
            "publication_date": Book.publication_date,
            "created_at": Book.created_at,
        }[sort_by]
        order = (
            [column.asc(), Book.id.asc()]
            if sort_order == "asc"
            else [column.desc(), Book.id.desc()]
        )
        items = await self.session.scalars(
            select(Book)
            .where(*predicates)
            .options(selectinload(Book.category), selectinload(Book.authors))
            .order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(items), int(total or 0)

    async def inventory(self, book_ids: list[int]) -> dict[int, tuple[int, int]]:
        if not book_ids:
            return {}
        rows = await self.session.execute(
            select(
                BookCopy.book_id,
                func.count(BookCopy.id),
                func.sum(case((BookCopy.status == CopyStatus.AVAILABLE, 1), else_=0)),
            )
            .where(BookCopy.book_id.in_(book_ids))
            .group_by(BookCopy.book_id)
        )
        return {book_id: (int(total), int(available)) for book_id, total, available in rows}

    async def copy(self, entity_id: int, *, lock: bool = False) -> BookCopy | None:
        query = select(BookCopy).where(BookCopy.id == entity_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def copies(
        self, book_id: int, page: int, page_size: int, status: CopyStatus | None
    ) -> tuple[list[BookCopy], int]:
        predicates = [BookCopy.book_id == book_id]
        if status is not None:
            predicates.append(BookCopy.status == status)
        total = await self.session.scalar(select(func.count(BookCopy.id)).where(*predicates))
        items = await self.session.scalars(
            select(BookCopy)
            .where(*predicates)
            .order_by(BookCopy.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(items), int(total or 0)
