from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.loan import LoanStatus
from app.schemas.catalog import EntityId


class LoanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: EntityId
    user_id: EntityId | None = None

    @model_validator(mode="after")
    def reject_null_user(self) -> Self:
        if "user_id" in self.model_fields_set and self.user_id is None:
            raise ValueError("user_id 不可为空；为本人借阅请省略该字段")
        return self


class LoanOutput(BaseModel):
    id: int
    user_id: int
    book_id: int
    book_copy_id: int
    borrowed_at: datetime
    due_at: datetime
    returned_at: datetime | None
    status: LoanStatus
    created_at: datetime
    updated_at: datetime
    is_overdue: bool
