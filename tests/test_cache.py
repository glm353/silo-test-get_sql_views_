import pytest

from viewpull.cache import CachedSource, JsonStore
from tests.conftest import FIXTURES


def test_write_then_read_round_trip(tmp_path):
    store = JsonStore(cache_dir=tmp_path)
    obj = [{"Name": "domain_foundation_role_dev"}]
    store.write("glue_databases", obj)
    assert store.read("glue_databases") == obj
    assert store.exists("glue_databases")


def test_read_falls_back_to_fixtures(tmp_path):
    # empty cache dir, but fixtures supplied → reads resolve from fixtures
    store = JsonStore(cache_dir=tmp_path, fixtures_dir=FIXTURES)
    dbs = store.read("glue_databases")
    assert any(d["Name"] == "domain_foundation_role_dev" for d in dbs)


def test_missing_raises_filenotfound(tmp_path):
    store = JsonStore(cache_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read("does_not_exist")


def test_cached_source_reads_fixture_shapes():
    source = CachedSource(JsonStore(cache_dir=FIXTURES))
    assert len(source.get_databases()) == 5
    assert source.has_tables("domain_foundation_role_dev")
    items = source.get_process_configs("dev")
    assert any(i["ProcessId"] == "domain-foundation-role-staff" for i in items)
