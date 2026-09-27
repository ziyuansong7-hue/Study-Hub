from __future__ import annotations

import pytest

from scripts.seed_demo import seed_demo
from services.database import configure_database, init_database


@pytest.fixture()
def db_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    configure_database(url)
    init_database()
    seed_demo(url)
    return url
