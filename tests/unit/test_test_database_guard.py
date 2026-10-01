import pytest

from scripts.test_mysql import assert_test_database


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///test.db",
        "mysql+asyncmy://u:p@localhost/library",
        "mysql+asyncmy://u:p@localhost/",
        "not-a-url",
    ],
)
def test_reject_non_test_database(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        assert_test_database(url)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "database-proxy"])
def test_reject_development_database_alias(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@localhost/library_test")
    host = f"[{host}]" if ":" in host else host
    with pytest.raises(ValueError, match="名称必须与开发库不同"):
        assert_test_database(f"mysql+asyncmy://u:p@{host}/library_test")


def test_reject_same_development_database(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "mysql+asyncmy://u:p@localhost/library_test"
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ValueError):
        assert_test_database(url)
