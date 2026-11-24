import json
import random

import pytest

from gonogo.db import DATASET, build, connect


@pytest.fixture(scope="session")
def fp_data():
    if not DATASET.exists():
        pytest.skip("data/fprime_dataset.json missing (run: python -m gonogo import-fprime --repo <fprime checkout>)")
    return json.loads(DATASET.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fp_results(fp_data):
    """A deterministic, synthetic unit-test run: mostly PASSED, some FAILED/SKIPPED, some cases not run.
    Used only to exercise the verdict logic; real results come from the CI workflow."""
    rng = random.Random(7)
    cases = []
    for c in fp_data["nodes"]["TestCase"]:
        x = rng.random()
        if x < 0.05:
            continue  # not run
        status = "FAILED" if x < 0.08 else ("SKIPPED" if x < 0.10 else "PASSED")
        cases.append({"id": c["id"], "component": c["id"].split("::")[0], "suite": c["suite"], "name": c["name"],
                      "status": status, "time_s": 0.0})
    return {"fprime_commit": fp_data["source"]["commit"], "run": {"id": "synthetic-7", "url": ""}, "cases": cases}


@pytest.fixture(scope="session")
def store_norun(fp_data, tmp_path_factory):
    path = tmp_path_factory.mktemp("norun") / "fprime"
    build(fp_data, path)
    return path


@pytest.fixture(scope="session")
def store_run(fp_data, fp_results, tmp_path_factory):
    path = tmp_path_factory.mktemp("run") / "fprime"
    build(fp_data, path, fp_results)
    return path


@pytest.fixture()
def db(store_run):
    conn = connect(store_run)
    yield conn
    conn.close()


@pytest.fixture()
def db_norun(store_norun):
    conn = connect(store_norun)
    yield conn
    conn.close()
