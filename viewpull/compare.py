"""The evidence for ASP-1586 — catalog-driven vs name-guess, as a delta report.

Answers the review comment ("this won't work across the board") with numbers: how many deployed
conformant views does the catalog approach pull SQL for, and how many of those does lta's name-guess
``_fetch_view_sql`` **miss** — split by *why* (off-pattern name vs no reachable DynamoDB item).
"""
from __future__ import annotations

from .catalog import CatalogResult, _VIEW_SUFFIXES
from .legacy import legacy_resolve


def _on_pattern(view_name: str) -> bool:
    """Does the view name end in a suffix the name-guess could ever produce?"""
    return any(view_name.endswith(s) for s in _VIEW_SUFFIXES)


def build_comparison(catalog: CatalogResult, process_items: list[dict]) -> dict:
    """Compare the two approaches over the same cached state; return a JSON-able report."""
    decoded = catalog.decoded_views
    available = {v.qualified for v in decoded}
    by_qualified = {v.qualified: v for v in decoded}

    legacy = legacy_resolve(process_items, available)

    catalog_found = available
    legacy_found = legacy.found_views
    catalog_only = sorted(catalog_found - legacy_found)   # views the name-guess missed
    legacy_only = sorted(legacy_found - catalog_found)    # (expected empty: legacy ⊆ catalog here)

    # Classify *why* each missed view was invisible to the name-guess.
    off_pattern = [q for q in catalog_only if not _on_pattern(by_qualified[q].name)]
    on_pattern_unreached = [q for q in catalog_only if _on_pattern(by_qualified[q].name)]

    return {
        "summary": {
            **catalog.summary(),
            "process_items_total": len(process_items),
            "legacy_resolved": len(legacy.resolved),
            "legacy_unresolved": len(legacy.unresolved),
            "legacy_skipped_no_db_or_silver": len(legacy.skipped),
            "catalog_found_views": len(catalog_found),
            "legacy_found_views": len(legacy_found),
            "catalog_only_views": len(catalog_only),
            "legacy_only_views": len(legacy_only),
            "missed_off_pattern_name": len(off_pattern),
            "missed_no_reachable_process": len(on_pattern_unreached),
        },
        # The headline lists (capped in the CLI print, full in the JSON artifact).
        "catalog_only_views": catalog_only,
        "missed_off_pattern_name": off_pattern,
        "missed_no_reachable_process": on_pattern_unreached,
        "legacy_only_views": legacy_only,
        "legacy_unresolved_processes": sorted(legacy.unresolved),
        "nonconformant_dbs": catalog.nonconformant_dbs,
    }
