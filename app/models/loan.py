from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utc_now
from app.models.catalog import TABLE_OPTIONS, BookCopy
from app.models.user import User


class LoanStatus(StrEnum):
    BORROWED = "BORROWED"
    RETURNED = "RETURNED"


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint(
            "(status = 'BORROWED' AND returned_at IS NULL) OR "
            "(status = 'RETURNED' AND returned_at IS NOT NULL)",
            name="ck_loans_status_returned",
        ),
        CheckConstraint("due_at > borrowed_at", name="ck_loans_due_after_borrowed"),
        Index("ix_loans_user_status", "user_id", "status"),
        Index("ix_loans_copy_status", "book_copy_id", "status"),
        Index("ix_loans_status_due", "status", "due_at"),
        TABLE_OPTIONS,
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    book_copy_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("book_copies.id", ondelete="RESTRICT")
    )
    borrowed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    due_at: Mapped[datetime] = mapped_column(UTCDateTime())
    returned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)"), onupdate=utc_now
    )
    user: Mapped[User] = relationship(lazy="raise")
    copy: Mapped[BookCopy] = relationship(lazy="raise")
