"""Presto/Athena view-SQL extraction — the SQL-decode core.

Copied verbatim from lineage-trace-analysis ``sources/aws.py`` (decode_presto_view +
view_sql_from_table) so the spike decodes view SQL **identically** to lta. This is the part we are
keeping; what ASP-1586 changes is *which tables we feed into it* (all catalog views, not name
guesses).
"""
from __future__ import annotations

import base64
import json

_PRESTO_PREFIX = "/* Presto View:"
_PRESTO_SUFFIX = "*/"


def decode_presto_view(view_original_text: str | None) -> str | None:
    """Recover ``originalSql`` from a Presto/Athena ``ViewOriginalText`` envelope.

    The envelope is ``/* Presto View: <base64(JSON)> */`` where the JSON has an ``originalSql``
    key. Returns None if ``view_original_text`` isn't in that form.
    """
    if not view_original_text:
        return None
    text = view_original_text.strip()
    if not text.startswith(_PRESTO_PREFIX) or not text.endswith(_PRESTO_SUFFIX):
        return None
    encoded = text[len(_PRESTO_PREFIX): -len(_PRESTO_SUFFIX)].strip()
    try:
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload.get("originalSql")


def view_sql_from_table(table_def: dict) -> str | None:
    """Extract a process's view SQL from a Glue ``get_table`` ``Table`` definition, or None."""
    if table_def.get("TableType") != "VIRTUAL_VIEW":
        return None
    return decode_presto_view(table_def.get("ViewOriginalText")) or (
        table_def.get("ViewExpandedText") or None
    )
