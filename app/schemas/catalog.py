import re
from datetime import date, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.catalog import CopyStatus

EntityId = Annotated[int, Field(strict=True, gt=0, le=2**64 - 1)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
AuthorName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Barcode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
AuthorIds = Annotated[list[EntityId], Field(max_length=100)]
SortBy = Literal["id", "title", "publication_date", "created_at"]
SortOrder = Literal["asc", "desc"]


class CatalogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchInput(CatalogInput):
    required_fields: ClassVar[set[str]] = set()

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少提供一个修改字段")
        if any(
            getattr(self, field) is None for field in self.model_fields_set & self.required_fields
        ):
            raise ValueError("必填字段不能置空")
        return self


class CategoryCreate(CatalogInput):
    name: Name
    description: str | None = Field(default=None, max_length=500)


class CategoryPatch(PatchInput):
    required_fields = {"name"}
    name: Name | None = None
    description: str | None = Field(default=None, max_length=500)


class AuthorCreate(CatalogInput):
    name: AuthorName
    biography: str | None = Field(default=None, max_length=16000)


class AuthorPatch(PatchInput):
    required_fields = {"name"}
    name: AuthorName | None = None
    biography: str | None = Field(default=None, max_length=16000)


class BookFields(CatalogInput):
    isbn: str | None = Field(default=None, max_length=64)
    subtitle: str | None = Field(default=None, max_length=255)
    publisher: str | None = Field(default=None, max_length=150)
    publication_date: date | None = None
    description: str | None = Field(default=None, max_length=16000)
    cover_url: HttpUrl | None = Field(default=None, max_length=500)

    @field_validator("isbn")
    @classmethod
    def normalize_isbn(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = re.sub(r"[-\s]", "", value)
        if not re.fullmatch(r"97[89][0-9]{10}", value):
            raise ValueError("需要 ISBN-13")
        if sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(value)) % 10:
            raise ValueError("ISBN 校验位不正确")
        return value

    @field_validator("cover_url")
    @classmethod
    def validate_url_length(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and len(str(value)) > 500:
            raise ValueError("封面地址过长")
        return value


class BookCreate(BookFields):
    title: Title
    category_id: EntityId
    author_ids: AuthorIds = Field(default_factory=list)
    is_active: bool = Field(default=True, strict=True)

    @field_validator("author_ids")
    @classmethod
    def unique_authors(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("作者 ID 不能重复")
        return value


class BookPatch(BookFields, PatchInput):
    required_fields = {"title", "category_id", "author_ids", "is_active"}
    title: Title | None = None
    category_id: EntityId | None = None
    author_ids: AuthorIds | None = None
    is_active: bool | None = Field(default=None, strict=True)

    @field_validator("author_ids")
    @classmethod
    def unique_authors(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("作者 ID 不能重复")
        return value


class CopyCreate(CatalogInput):
    barcode: Barcode
    location: str | None = Field(default=None, max_length=100)
    acquired_at: date | None = None


class CopyPatch(PatchInput):
    location: str | None = Field(default=None, max_length=100)
    acquired_at: date | None = None


class CopyStatusInput(CatalogInput):
    status: CopyStatus


class CatalogOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class CategoryOutput(CatalogOutput):
    name: str
    description: str | None


class AuthorOutput(CatalogOutput):
    name: str
    biography: str | None


class BookOutput(CatalogOutput):
    isbn: str | None
    title: str
    subtitle: str | None
    category_id: int
    category: CategoryOutput
    authors: list[AuthorOutput]
    publisher: str | None
    publication_date: date | None
    description: str | None
    cover_url: str | None
    is_active: bool
    total_copies: int
    available_copies: int


class CopyOutput(CatalogOutput):
    book_id: int
    barcode: str
    status: CopyStatus
    location: str | None
    acquired_at: date | None


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
