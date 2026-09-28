"""向已迁移的本机开发库追加虚构目录数据，不覆盖已有记录。"""

import argparse
import asyncio
import sys
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory
from app.models.catalog import Author, Book, BookCopy, Category, CopyStatus
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import BookCreate, CopyCreate

CATEGORIES = ("计算机", "文学", "历史", "科普", "艺术")
AUTHORS = (
    "林知远",
    "陈星禾",
    "周听澜",
    "沈书宁",
    "许明川",
    "叶清和",
    "苏望舒",
    "陆景行",
    "顾云舟",
    "宋南溪",
)
TITLES = (
    "Python 异步编程入门",
    "FastAPI 项目实践",
    "MySQL 事务与索引",
    "算法图解练习册",
    "街角的旧书店",
    "山海之间的信",
    "雨夜故事集",
    "向北出发",
    "城市记忆手札",
    "丝路上的旅人",
    "古代生活小史",
    "博物馆里的时间",
    "星空观察笔记",
    "身边的物理实验",
    "植物的一年",
    "海洋探索日记",
    "色彩与构图",
    "摄影入门练习",
    "音乐欣赏手册",
    "建筑之美",
    "SQL 查询进阶",
    "协作开发指南",
    "待入馆的新书",
    "旧版编程参考手册",
)


def mock_isbn(number: int) -> str:
    # 仅为虚构测试书目生成校验位合法的号码，不代表正式出版物。
    prefix = f"97900000{number:04d}"
    check = -sum(int(char) * (1 if i % 2 == 0 else 3) for i, char in enumerate(prefix)) % 10
    return f"{prefix}{check}"


async def seed(settings: Settings, database: str) -> dict[str, int]:
    target = make_url(settings.database_url.get_secret_value())
    if target.host not in {"localhost", "127.0.0.1", "::1"} or target.database != database:
        raise ValueError("仅允许本机开发库，--database 必须与 DATABASE_URL 的数据库名一致")
    print(f"开发库目标：{target.host}:{target.port or 3306}/{target.database}", flush=True)
    engine = create_engine(settings)
    counts = dict.fromkeys(("categories", "authors", "books", "book_copies"), 0)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            if await session.scalar(text("SELECT DATABASE()")) != database:
                raise ValueError("实际连接数据库与指定开发库不一致")
            revisions = set(await session.scalars(text("SELECT version_num FROM alembic_version")))
            if revisions != {"0002_catalog"}:
                raise ValueError("此脚本适用于 0002_catalog 迁移，请先核对迁移状态")
            repository = CatalogRepository(session)
            categories = []
            for name in CATEGORIES:
                name = f"{name}（示例）"
                category = await session.scalar(select(Category).where(Category.name == name))
                if category is None:
                    category = Category(name=name, description="MOCK：开发演示分类")
                    await repository.add(category)
                    counts["categories"] += 1
                categories.append(category)
            authors = []
            for number, name in enumerate(AUTHORS, 1):
                marker = f"MOCK-AUTHOR-{number:03d}：虚构作者，仅用于开发演示。"
                author = (
                    await session.scalars(select(Author).where(Author.biography == marker))
                ).one_or_none()
                if author is None:
                    author = Author(name=name, biography=marker)
                    await repository.add(author)
                    counts["authors"] += 1
                authors.append(author)
            for number, title in enumerate(TITLES, 1):
                isbn = mock_isbn(number)
                marker = f"开发样例 MOCK-BOOK-{number:03d}"
                existing = await session.scalar(select(Book).where(Book.isbn == isbn))
                if existing is not None:
                    if existing.subtitle != marker:
                        raise ValueError("示例 ISBN 与已有书目冲突；本次新增已回滚")
                    continue
                group = (number - 1) // 4 if number <= 20 else 0
                selected_authors = authors[group * 2 : group * 2 + (2 if number % 3 == 0 else 1)]
                data = BookCreate(
                    isbn=isbn,
                    title=title,
                    subtitle=marker,
                    category_id=categories[group].id,
                    author_ids=[author.id for author in selected_authors],
                    publisher="示例出版社",
                    publication_date=date(2020 + number % 5, 1, 1),
                    description=f"虚构开发数据：{title}。用于搜索、分页和馆藏状态演示。",
                    is_active=number != 24,
                )
                book = Book(**data.model_dump(exclude={"author_ids"}), authors=selected_authors)
                await repository.add(book)
                counts["books"] += 1
                # 第 23 本暂无馆藏，第 22 本无可借副本，第 24 本已下架。
                if number == 23:
                    continue
                other_states = (CopyStatus.MAINTENANCE, CopyStatus.LOST, CopyStatus.RETIRED)
                statuses = (
                    other_states
                    if number == 22
                    else (CopyStatus.AVAILABLE, CopyStatus.AVAILABLE, other_states[number % 3])
                )
                for copy_number, status in enumerate(statuses, 1):
                    copy = CopyCreate(
                        barcode=f"MOCK-{number:03d}-{copy_number:02d}",
                        location=f"示例区-{group + 1}架-{number:02d}层",
                        acquired_at=date(2025, 1, 1),
                    )
                    await repository.add(
                        BookCopy(book_id=book.id, status=status, **copy.model_dump())
                    )
                    counts["book_copies"] += 1
        return counts
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="明确指定 DATABASE_URL 对应的开发库名")
    args = parser.parse_args()
    try:
        counts = asyncio.run(seed(Settings(), args.database))
    except ValidationError:
        print("配置或样例数据校验失败，请检查环境配置", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError):
        print(
            "数据库连接或写入失败，本次新增未提交；请检查连接、迁移及唯一字段冲突", file=sys.stderr
        )
        return 1
    print("新增记录：" + "，".join(f"{name}={count}" for name, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
