"""建立借阅表，保留用户与馆藏历史并约束归还状态。"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0003_loans"
down_revision = "0002_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loans",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "book_copy_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("book_copies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("borrowed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("due_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("returned_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        *[
            sa.Column(
                name,
                mysql.DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            )
            for name in ("created_at", "updated_at")
        ],
        sa.CheckConstraint(
            "(status = 'BORROWED' AND returned_at IS NULL) OR "
            "(status = 'RETURNED' AND returned_at IS NOT NULL)",
            name="ck_loans_status_returned",
        ),
        sa.CheckConstraint("due_at > borrowed_at", name="ck_loans_due_after_borrowed"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index("ix_loans_user_status", "loans", ["user_id", "status"])
    op.create_index("ix_loans_copy_status", "loans", ["book_copy_id", "status"])
    op.create_index("ix_loans_status_due", "loans", ["status", "due_at"])


def downgrade() -> None:
    op.drop_table("loans")
