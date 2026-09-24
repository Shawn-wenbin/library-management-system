import pytest

from scripts.test_mysql import assert_test_database


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///test.db",
        "mysql+asyncmy://u:p@localhost/library",
        "mysql+asyncmy://u:p@localhost/",
    ],
)
def test_reject_non_test_database(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        assert_test_database(url)


def test_reject_same_development_database(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "mysql+asyncmy://u:p@localhost/library_test"
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ValueError):
        assert_test_database(url)
