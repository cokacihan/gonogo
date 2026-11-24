import json

import pytest

from tracegraph.db import DATA_DIR, DB_PATH, build, connect
from tracegraph.generate import build_dataset
from tracegraph.truth import ground_truth


@pytest.fixture(scope="session")
def dataset():
    return build_dataset()


@pytest.fixture(scope="session")
def truth(dataset):
    return ground_truth(dataset)


@pytest.fixture(scope="session")
def db_path(tmp_path_factory, dataset):
    """Fresh store built from the generator, so tests never depend on a stale committed file."""
    path = tmp_path_factory.mktemp("store") / "test.rl"
    build(dataset, path)
    return path


@pytest.fixture()
def db(db_path):
    conn = connect(db_path, read_only=True)
    yield conn
    conn.close()
