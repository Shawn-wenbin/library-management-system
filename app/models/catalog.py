from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utc_now

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_as_ci",
}


class CatalogTimestamps:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)"), onupdate=utc_now
    )


book_authors = Table(
    "book_authors",
    Base.metadata,
    Column(
        "book_id",
        BIGINT(unsigned=True),
        ForeignKey("books.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "author_id",
        BIGINT(unsigned=True),
        ForeignKey("authors.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Index("ix_book_authors_author_book", "author_id", "book_id"),
    **TABLE_OPTIONS,
)


class Category(CatalogTimestamps, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("name", name="uq_categories_name"), TABLE_OPTIONS)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    books: Mapped[list["Book"]] = relationship(
        back_populates="category", lazy="raise", passive_deletes="all"
    )


class Author(CatalogTimestamps, Base):
    __tablename__ = "authors"
    __table_args__ = TABLE_OPTIONS
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150))
    biography: Mapped[str | None] = mapped_column(Text)
    books: Mapped[list["Book"]] = relationship(
        secondary=book_authors, back_populates="authors", lazy="raise", passive_deletes="all"
    )


class Book(CatalogTimestamps, Base):
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("isbn", name="uq_books_isbn"),
        Index("ix_books_category_active", "category_id", "is_active"),
        TABLE_OPTIONS,
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    isbn: Mapped[str | None] = mapped_column(String(13))
    title: Mapped[str] = mapped_column(String(255))
    subtitle: Mapped[str | None] = mapped_column(String(255))
    category_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("categories.id", ondelete="RESTRICT")
    )
    publisher: Mapped[str | None] = mapped_column(String(150))
    publication_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    category: Mapped[Category] = relationship(back_populates="books", lazy="raise")
    authors: Mapped[list[Author]] = relationship(
        secondary=book_authors, back_populates="books", lazy="raise", order_by="Author.id"
    )
    copies: Mapped[list["BookCopy"]] = relationship(
        back_populates="book", lazy="raise", passive_deletes="all"
    )


class CopyStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BORROWED = "BORROWED"
    MAINTENANCE = "MAINTENANCE"
    LOST = "LOST"
    RETIRED = "RETIRED"


class BookCopy(CatalogTimestamps, Base):
    __tablename__ = "book_copies"
    __table_args__ = (
        UniqueConstraint("barcode", name="uq_book_copies_barcode"),
        CheckConstraint(
            "status IN ('AVAILABLE', 'BORROWED', 'MAINTENANCE', 'LOST', 'RETIRED')",
            name="ck_book_copies_status",
        ),
        Index("ix_book_copies_book_status", "book_id", "status"),
        TABLE_OPTIONS,
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("books.id", ondelete="RESTRICT")
    )
    barcode: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="AVAILABLE")
    location: Mapped[str | None] = mapped_column(String(100))
    acquired_at: Mapped[date | None] = mapped_column(Date)
    book: Mapped[Book] = relationship(back_populates="copies", lazy="raise")
