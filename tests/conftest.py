from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LEGACY = REPO / "data" / "sessions" / "legacy"


@pytest.fixture(scope="session")
def legacy_dir() -> Path:
    if not LEGACY.is_dir() or not any(LEGACY.glob("*.log")):
        pytest.skip("no legacy recordings available")
    return LEGACY


@pytest.fixture(scope="session")
def legacy_session(legacy_dir):
    from bikecan import session

    return session.load(legacy_dir)
