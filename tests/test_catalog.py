from viewpull import catalog


def test_is_conformant_prefix_and_env():
    assert catalog.is_conformant("domain_foundation_role_dev", "dev")
    assert catalog.is_conformant("molecular_hr_sapsf_dev", "dev")
    assert catalog.is_conformant("business_fm_safezone_dev", "dev")
    # non-governed prefix is rejected regardless of env
    assert not catalog.is_conformant("random_scratch_dev", "dev")
    # right prefix, wrong env suffix
    assert not catalog.is_conformant("domain_foundation_role_prod", "dev")
    assert catalog.is_conformant("domain_foundation_role_prod", "prod")
    # env=None falls back to prefix-only
    assert catalog.is_conformant("domain_foundation_role_prod", None)


def test_classify_databases(source):
    conformant, nonconformant = catalog.classify_databases(source, "dev")
    assert conformant == [
        "business_fm_safezone_dev",
        "domain_foundation_role_dev",
        "molecular_hr_sapsf_dev",
    ]
    assert "random_scratch_dev" in nonconformant
    assert "domain_foundation_role_prod" in nonconformant


def test_silver_base():
    assert catalog.silver_base("staff_vw") == "staff"
    assert catalog.silver_base("cert_source_vw") == "cert"
    # off-pattern name is returned unchanged
    assert catalog.silver_base("staff_curated") == "staff_curated"


def test_enumerate_views_decodes_all_conformant_views(source):
    result = catalog.enumerate_views(source, "dev")
    summary = result.summary()
    assert summary["conformant_dbs"] == 3
    assert summary["views_total"] == 5      # staff_vw, staff_curated, cert_source_vw, user_vw, broken_vw
    assert summary["views_decoded"] == 4    # broken_vw fails to decode
    assert summary["views_undecoded"] == 1

    decoded = {v.qualified for v in result.decoded_views}
    assert "domain_foundation_role_dev.staff_curated" in decoded   # off-pattern, still found
    assert "business_fm_safezone_dev.user_vw" in decoded
    # the base table and the wrong-env / non-conformant DBs never appear
    assert not any(v.name == "staff" for v in result.views)


def test_enumerate_decoded_sql_is_real(source):
    result = catalog.enumerate_views(source, "dev")
    staff = next(v for v in result.views if v.name == "staff_vw")
    assert staff.has_sql and "select" in staff.sql.lower()
    broken = next(v for v in result.views if v.name == "broken_vw")
    assert not broken.has_sql and broken.sql is None
