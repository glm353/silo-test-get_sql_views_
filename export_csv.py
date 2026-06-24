"""One-off CSV export for the ASP-1586 evidence — offline, over the cached catalog.

Reuses the same offline path as ``viewpull enumerate`` / ``compare`` (``CachedSource`` over
``cache/`` with a ``fixtures/`` fallback), so it needs no AWS creds. Writes one CSV to ``out/``:

* ``views-<env>.csv`` — one row per catalog view (the unit of the ASP-1586 question), with the
  name-guess outcome folded in side by side: did decoding succeed, is the name on-pattern, did
  lta's name-guess reach it, *which DynamoDB process* reached it (if any), and *why* it's missed.

(The name-guess's own per-process view — including the 1043 process items that resolve to no view
at all — is summarised in ``out/compare-<env>.json``; it can't be a per-view row, so it stays there.)

Run:  python export_csv.py --env-code dev
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from viewpull import catalog
from viewpull.cache import CachedSource, JsonStore
from viewpull.compare import _on_pattern
from viewpull.legacy import legacy_resolve

_ROOT = Path(__file__).resolve().parent


def _miss_reason(row: catalog.ViewRow, found: set[str]) -> str:
    """Why the name-guess fails to pull this view's SQL (empty string = it succeeds)."""
    if not row.has_sql:
        return "undecoded"
    if row.qualified in found:
        return ""
    return "no_reachable_process" if _on_pattern(row.name) else "off_pattern_name"


def _write_views_csv(
    result: catalog.CatalogResult, by_view_process: dict[str, list[str]], path: Path
) -> int:
    found = set(by_view_process)
    cols = ["database", "view_name", "qualified", "silver_base", "decoded",
            "on_pattern", "found_in_legacy", "legacy_process_id",
            "miss_reason", "sql_char_count"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for v in sorted(result.views, key=lambda r: r.qualified):
            w.writerow([
                v.database, v.name, v.qualified, v.silver_base,
                v.has_sql, _on_pattern(v.name), v.qualified in found,
                ";".join(sorted(by_view_process.get(v.qualified, []))),
                _miss_reason(v, found), len(v.sql) if v.sql else 0,
            ])
    return len(result.views)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-code", default="dev", help="deploy env (db suffix); default dev")
    parser.add_argument("--cache-dir", default=str(_ROOT / "cache"))
    parser.add_argument("--fixtures-dir", default=str(_ROOT / "fixtures"))
    parser.add_argument("--out-dir", default=str(_ROOT / "out"))
    args = parser.parse_args(argv)

    source = CachedSource(JsonStore(Path(args.cache_dir), Path(args.fixtures_dir)))
    result = catalog.enumerate_views(source, args.env_code)
    items = source.get_process_configs(args.env_code)
    available = {v.qualified for v in result.decoded_views}

    # Invert the name-guess resolution (process_id -> view) into view -> [process_ids], so each
    # view row can name the DynamoDB process(es) the name-guess used to reach it.
    by_view_process: dict[str, list[str]] = {}
    for process_id, qualified in legacy_resolve(items, available).resolved.items():
        by_view_process.setdefault(qualified, []).append(process_id)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    views_path = out_dir / f"views-{args.env_code}.csv"
    n_views = _write_views_csv(result, by_view_process, views_path)

    print(f"wrote {n_views} view rows -> {views_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
