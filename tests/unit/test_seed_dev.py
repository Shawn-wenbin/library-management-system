import pytest

from app.core.config import Settings
from scripts.seed_dev import seed


@pytest.mark.parametrize(
    "url,database",
    [
        ("mysql+asyncmy://unused:unused@remote/library", "library"),
        ("mysql+asyncmy://unused:unused@127.0.0.1/library", "other"),
    ],
)
async def test_seed_rejects_wrong_target(settings: Settings, url: str, database: str) -> None:
    settings = Settings(_env_file=None, **{**settings.model_dump(), "database_url": url})
    with pytest.raises(ValueError, match="仅允许本机开发库"):
        await seed(settings, database)
