"""Catalog-driven view enumeration — the ASP-1586 "pull all views" approach.

Instead of reconstructing a view name per DynamoDB process (lta's ``_fetch_view_sql``, which only
finds ``<silver>_vw`` / ``_source_vw`` and only for processes present in DynamoDB), we scan the Glue
catalog directly:

1. list **all** databases, keep only **conformant** ones (``molecular`` / ``domain`` / ``business``
   prefix, env-suffixed) — the ASP-1586 "caveat" that avoids the mess in random DBs;
2. list **every table** in each conformant DB and keep the ``VIRTUAL_VIEW`` rows;
3. decode each view's SQL straight from its ``ViewOriginalText`` (via :mod:`viewpull.presto`).

This finds every deployed view **regardless of its name**, and is independent of DynamoDB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .presto import view_sql_from_table

# A conformant database is one of the three governed namespaces, env-suffixed (e.g.
# ``domain_foundation_role_dev``, ``molecular_hr_sapsf_dev``, ``business_fm_safezone_dev``).
CONFORMANT_PREFIXES = ("molecular", "domain", "business")

# Same view suffixes lta strips, used only to recover the silver-table base name for cross-matching
# against the legacy/name-guess approach (longest first so ``_source_vw`` wins over ``_vw``).
_VIEW_SUFFIXES = ("_source_vw", "_vw")


def is_conformant(database: str, env_code: str | None) -> bool:
    """True if ``database`` is in a governed namespace and (when given) carries the env suffix."""
    if not database:
        return False
    name = database.lower()
    prefix_ok = any(name.startswith(f"{p}_") for p in CONFORMANT_PREFIXES)
    if not prefix_ok:
        return False
    if env_code:
        return bool(re.search(rf"_{re.escape(env_code.lower())}$", name))
    return True


def silver_base(view_name: str) -> str:
    """Strip a known view suffix to recover the silver-table base name (else the name unchanged)."""
    for suffix in _VIEW_SUFFIXES:
        if view_name.endswith(suffix):
            return view_name[: -len(suffix)]
    return view_name


@dataclass
class ViewRow:
    database: str
    name: str
    silver_base: str
    has_sql: bool          # did ViewOriginalText decode to real SQL?
    sql: str | None

    @property
    def qualified(self) -> str:
        return f"{self.database}.{self.name}"


@dataclass
class CatalogResult:
    env_code: str | None
    conformant_dbs: list[str] = field(default_factory=list)
    nonconformant_dbs: list[str] = field(default_factory=list)
    views: list[ViewRow] = field(default_factory=list)

    @property
    def decoded_views(self) -> list[ViewRow]:
        return [v for v in self.views if v.has_sql]

    def summary(self) -> dict:
        return {
            "env_code": self.env_code,
            "databases_total": len(self.conformant_dbs) + len(self.nonconformant_dbs),
            "conformant_dbs": len(self.conformant_dbs),
            "nonconformant_dbs": len(self.nonconformant_dbs),
            "views_total": len(self.views),
            "views_decoded": len(self.decoded_views),
            "views_undecoded": len(self.views) - len(self.decoded_views),
        }


def classify_databases(source, env_code: str | None) -> tuple[list[str], list[str]]:
    """Split all catalog databases into (conformant, non-conformant) name lists."""
    names = sorted(db.get("Name", "") for db in source.get_databases())
    conformant = [n for n in names if is_conformant(n, env_code)]
    nonconformant = [n for n in names if n and not is_conformant(n, env_code)]
    return conformant, nonconformant


def enumerate_views(source, env_code: str | None) -> CatalogResult:
    """Walk every conformant database, collect each ``VIRTUAL_VIEW`` and decode its SQL."""
    conformant, nonconformant = classify_databases(source, env_code)
    result = CatalogResult(
        env_code=env_code, conformant_dbs=conformant, nonconformant_dbs=nonconformant
    )
    for database in conformant:
        for table in source.get_tables(database):
            if table.get("TableType") != "VIRTUAL_VIEW":
                continue
            name = table.get("Name", "")
            sql = view_sql_from_table(table)
            result.views.append(
                ViewRow(
                    database=database,
                    name=name,
                    silver_base=silver_base(name),
                    has_sql=bool(sql),
                    sql=sql,
                )
            )
    return result
