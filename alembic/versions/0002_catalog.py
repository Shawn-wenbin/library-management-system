"""建立图书目录与实体馆藏，保留全部外键关联。"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0002_catalog"
down_revision = "0001_users"
branch_labels = None
depends_on = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_as_ci",
}


def _id() -> sa.Column:
    return sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            name,
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        )
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "categories",
        _id(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500)),
        *_timestamps(),
        sa.UniqueConstraint("name", name="uq_categories_name"),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "authors",
        _id(),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("biography", sa.Text()),
        *_timestamps(),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "books",
        _id(),
        sa.Column("isbn", sa.String(13)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("subtitle", sa.String(255)),
        sa.Column(
            "category_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("publisher", sa.String(150)),
        sa.Column("publication_date", sa.Date()),
        sa.Column("description", sa.Text()),
        sa.Column("cover_url", sa.String(500)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.UniqueConstraint("isbn", name="uq_books_isbn"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_books_category_active", "books", ["category_id", "is_active"])
    op.create_table(
        "book_authors",
        sa.Column(
            "book_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("books.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "author_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("authors.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_book_authors_author_book", "book_authors", ["author_id", "book_id"])
    op.create_table(
        "book_copies",
        _id(),
        sa.Column(
            "book_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("books.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="AVAILABLE"),
        sa.Column("location", sa.String(100)),
        sa.Column("acquired_at", sa.Date()),
        *_timestamps(),
        sa.UniqueConstraint("barcode", name="uq_book_copies_barcode"),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'BORROWED', 'MAINTENANCE', 'LOST', 'RETIRED')",
            name="ck_book_copies_status",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_book_copies_book_status", "book_copies", ["book_id", "status"])


def downgrade() -> None:
    op.drop_table("book_copies")
    op.drop_table("book_authors")
    op.drop_table("books")
    op.drop_table("authors")
    op.drop_table("categories")
