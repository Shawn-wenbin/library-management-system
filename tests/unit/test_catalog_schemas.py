import pytest
from pydantic import ValidationError

from app.schemas.catalog import BookCreate, BookPatch, CategoryPatch, CopyCreate, CopyPatch


def test_isbn_normalization_and_optional_fields() -> None:
    data = BookCreate(title="  示例  ", category_id=1, isbn="978-0-306-40615-7")
    assert data.isbn == "9780306406157" and data.title == "示例"
    assert BookCreate(title="无 ISBN", category_id=1).isbn is None
    assert BookPatch(isbn=None).model_dump(exclude_unset=True) == {"isbn": None}
    assert CopyCreate(barcode=" ABC ").barcode == "ABC"


@pytest.mark.parametrize(
    "isbn", ["", "9780306406158", "1234567890128", "０７８０３０６４０６１５７", "0306406152"]
)
def test_invalid_isbn(isbn: str) -> None:
    with pytest.raises(ValidationError):
        BookCreate(title="示例", category_id=1, isbn=isbn)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": None},
        {"title": " "},
        {"category_id": None},
        {"category_id": 0},
        {"author_ids": None},
        {"author_ids": [1, 1]},
        {"is_active": None},
        {"is_active": "true"},
        {"cover_url": "file:///tmp/a"},
        {"unknown": 1},
    ],
)
def test_invalid_book_patch(payload: dict) -> None:
    with pytest.raises(ValidationError):
        BookPatch(**payload)


def test_copy_and_category_patch_constraints() -> None:
    for model, payload in [
        (CopyPatch, {}),
        (CopyPatch, {"barcode": "new"}),
        (CopyPatch, {"status": "AVAILABLE"}),
        (CopyCreate, {"barcode": "x", "status": "BORROWED"}),
        (CategoryPatch, {"name": None}),
    ]:
        with pytest.raises(ValidationError):
            model(**payload)
    assert CopyPatch(location=None).model_fields_set == {"location"}
