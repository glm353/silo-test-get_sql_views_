from viewpull import catalog, compare


def _report(source):
    result = catalog.enumerate_views(source, "dev")
    items = source.get_process_configs("dev")
    return compare.build_comparison(result, items)


def test_compare_counts(source):
    summary = _report(source)["summary"]
    # the name-guess resolves the two on-pattern, process-reachable views
    assert summary["legacy_resolved"] == 2
    assert summary["legacy_unresolved"] == 1            # business-fm-safezone-user (SilverTable mismatch)
    assert summary["legacy_skipped_no_db_or_silver"] == 1   # the item with no SilverTable
    # the catalog pulls SQL for all 4 decodable views; 2 of them the name-guess misses
    assert summary["catalog_found_views"] == 4
    assert summary["legacy_found_views"] == 2
    assert summary["catalog_only_views"] == 2
    assert summary["legacy_only_views"] == 0


def test_compare_missed_views_classified(source):
    report = _report(source)
    catalog_only = set(report["catalog_only_views"])
    assert catalog_only == {
        "domain_foundation_role_dev.staff_curated",   # off-pattern name
        "business_fm_safezone_dev.user_vw",           # on-pattern but no reachable process
    }
    assert report["missed_off_pattern_name"] == ["domain_foundation_role_dev.staff_curated"]
    assert report["missed_no_reachable_process"] == ["business_fm_safezone_dev.user_vw"]


def test_nonconformant_dbs_reported(source):
    report = _report(source)
    assert "random_scratch_dev" in report["nonconformant_dbs"]
    assert "domain_foundation_role_prod" in report["nonconformant_dbs"]
