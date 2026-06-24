"""The baseline being challenged — lta's name-reconstructing ``_fetch_view_sql``.

Ported from lineage-trace-analysis ``sources/aws.py`` (``_VIEW_SUFFIXES`` + ``_fetch_view_sql``).
The original does ``glue.get_table(db, silver + suffix)`` per DynamoDB process; here we resolve the
guess against the **already-enumerated catalog views** (an offline, apples-to-apples oracle — a
guessed name "resolves" iff that exact view exists in the catalog and decodes to SQL). This lets the
comparison run with no extra AWS calls while reproducing exactly what the old code would have found.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Same order lta tries: `<silver>_source_vw` then `<silver>_vw`.
_VIEW_SUFFIXES = ("_source_vw", "_vw")


def guess_view_names(silver_table: str) -> list[str]:
    """The candidate view names lta would probe, in order."""
    return [f"{silver_table}{suffix}" for suffix in _VIEW_SUFFIXES]


@dataclass
class LegacyResult:
    resolved: dict[str, str] = field(default_factory=dict)      # process_id -> qualified view found
    unresolved: list[str] = field(default_factory=list)         # process_ids with no name-guess hit
    skipped: list[str] = field(default_factory=list)            # items lacking db/silver_table

    @property
    def found_views(self) -> set[str]:
        return set(self.resolved.values())


def legacy_resolve(process_items: list[dict], available_views: set[str]) -> LegacyResult:
    """Run the name-guess over DynamoDB items against the set of existing ``db.view`` names.

    ``available_views`` is the set of qualified names (``db.name``) of catalog views that decode to
    SQL — the stand-in for "``glue.get_table`` returns a usable view".
    """
    result = LegacyResult()
    for item in process_items:
        process_id = item.get("ProcessId")
        database = item.get("CatalogDatabase")
        silver = item.get("SilverTable")
        if not process_id:
            continue
        if not (database and silver):
            result.skipped.append(process_id or "<no-id>")
            continue
        hit = None
        for name in guess_view_names(silver):
            qualified = f"{database}.{name}"
            if qualified in available_views:
                hit = qualified
                break
        if hit:
            result.resolved[process_id] = hit
        else:
            result.unresolved.append(process_id)
    return result
