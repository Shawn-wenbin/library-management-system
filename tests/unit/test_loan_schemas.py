import pytest
from pydantic import ValidationError

from app.schemas.loan import LoanCreate


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"book_id": 0},
        {"book_id": -1},
        {"book_id": 2**64},
        {"book_id": True},
        {"book_id": "1"},
        {"book_id": 1, "user_id": None},
        {"book_id": 1, "user_id": False},
        {"book_id": 1, "user_id": 0},
        {"book_id": 1, "book_copy_id": 1},
        {"book_id": 1, "status": "BORROWED"},
        {"book_id": 1, "due_at": "2026-01-01T00:00:00Z"},
        {"book_id": 1, "borrowed_at": "2026-01-01T00:00:00Z"},
    ],
)
def test_reject_invalid_or_server_owned_fields(data: dict) -> None:
    with pytest.raises(ValidationError):
        LoanCreate.model_validate(data)


def test_loan_create_ids() -> None:
    assert LoanCreate(book_id=1).user_id is None
    assert LoanCreate(book_id=1, user_id=2).user_id == 2
