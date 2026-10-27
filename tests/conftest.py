"""
These tests run against the real docker-compose Postgres instance — no
mocks. Bring it up first with `docker compose up -d --wait`. If it isn't
reachable, every test here is skipped (not failed) with a clear message,
since that's an environment problem, not a code problem.
"""

import pytest

from app.db import DBConfigError, DBConnectionError, DBPermissionError, check_health


@pytest.fixture(scope="session", autouse=True)
def _require_live_database():
    try:
        check_health()
    except (DBConfigError, DBConnectionError, DBPermissionError) as exc:
        pytest.skip(
            f"docker-compose Postgres is not reachable/healthy: {exc}. "
            f"Run `docker compose up -d --wait` first."
        )
