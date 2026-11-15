from __future__ import annotations

from pathlib import Path

import pytest

from app.config import REPO_ROOT, Settings
from app.storage import Storage
from app.tools.registry import SampleToolRegistry

FIXTURES = REPO_ROOT / "evals" / "fixtures"
TEST_KEY = "sk-or-v1-TEST-SENTINEL-0123456789abcdef"


@pytest.fixture
def registry() -> SampleToolRegistry:
    return SampleToolRegistry(FIXTURES)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite3"


@pytest.fixture
def storage(tmp_db: Path) -> Storage:
    return Storage(tmp_db)


@pytest.fixture
def settings(tmp_db: Path) -> Settings:
    """Isolated settings: never reads the developer's real .env or key."""
    return Settings(
        _env_file=None,
        OPENROUTER_API_KEY=TEST_KEY,
        CHOTU_PROVIDER="openrouter",
        CHOTU_DB_PATH=tmp_db,
        CHOTU_MONTHLY_BUDGET_INR=300,
        CHOTU_USD_TO_INR=100.0,
    )
