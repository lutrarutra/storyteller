import os

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")


@pytest.fixture
def cells():
    from storyteller.demo import simulated_cells

    return simulated_cells(n=800)


@pytest.fixture(autouse=True)
def _workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path
