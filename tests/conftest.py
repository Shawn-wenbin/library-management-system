import os
import secrets

import pytest

from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=os.getenv(
            "TEST_DATABASE_URL", "mysql+asyncmy://unused:unused@127.0.0.1:1/library_test"
        ),
        jwt_secret=secrets.token_urlsafe(48),
    )
