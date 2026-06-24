from pathlib import Path

import pytest

from viewpull.cache import CachedSource, JsonStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def store():
    # Point the store's cache_dir at fixtures/ so reads resolve the committed sample.
    return JsonStore(cache_dir=FIXTURES, fixtures_dir=None)


@pytest.fixture
def source(store):
    return CachedSource(store)
