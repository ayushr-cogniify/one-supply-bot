import pytest
from osb_tools import adapter


def _load(fn):
    try:
        return fn()
    except FileNotFoundError as e:
        pytest.skip(f"Data not available: {e}")


@pytest.fixture(scope="session")
def plan():
    return _load(adapter.kinaxis_plan)


@pytest.fixture(scope="session")
def att():
    return _load(adapter.attainment)


@pytest.fixture(scope="session")
def inv():
    return _load(adapter.inventory)


def pytest_collection_modifyitems(config, items):
    """No Reckitt data (e.g. GitHub Actions): skip the data-backed golden suites, keep logic tests."""
    from osb_tools import config as cfg
    if any(cfg.DATA_DIR.glob("*.xlsx")):
        return
    skip = pytest.mark.skip(reason="Reckitt data not present (expected in CI)")
    for item in items:
        if "golden" in item.nodeid or "storyline" in item.nodeid:
            item.add_marker(skip)
