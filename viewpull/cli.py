"""``python -m viewpull`` — refresh | enumerate | compare.

* ``refresh``   — the **one** live AWS pull: dump Glue databases, per-conformant-DB tables, and the
  DynamoDB process-config scan into ``cache/`` (needs creds; honours ``--okta-login``).
* ``enumerate`` — offline: walk the cached catalog, decode every conformant view's SQL, report
  coverage, write ``out/enumerate-<env>.json``.
* ``compare``   — offline: catalog-driven vs lta's name-guess; report what the name-guess misses,
  write ``out/compare-<env>.json``.

``enumerate`` / ``compare`` never touch AWS — they read ``cache/`` (falling back to committed
``fixtures/``), so they run anywhere with no creds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import auth, catalog, compare
from .cache import CachedSource, JsonStore, LiveSource

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CACHE = _ROOT / "cache"
_DEFAULT_FIXTURES = _ROOT / "fixtures"
_DEFAULT_OUT = _ROOT / "out"


def _store(args) -> JsonStore:
    return JsonStore(Path(args.cache_dir), Path(args.fixtures_dir))


def _write_out(name: str, payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _cmd_refresh(args) -> int:
    session = auth.resolve_session(
        profile=args.profile, region=args.region, okta_login=args.okta_login
    )
    ident = session.client("sts").get_caller_identity()
    print(f"authenticated: account {ident.get('Account')} ({session.region_name})")

    store = JsonStore(Path(args.cache_dir), Path(args.fixtures_dir))
    live = LiveSource(session, store, log=print)

    conformant, nonconformant = catalog.classify_databases(live, args.env_code)
    print(f"databases: {len(conformant)} conformant, {len(nonconformant)} non-conformant "
          f"(env '{args.env_code}')")

    view_count = 0
    for i, database in enumerate(conformant, 1):
        tables = live.get_tables(database)
        views = sum(1 for t in tables if t.get("TableType") == "VIRTUAL_VIEW")
        view_count += views
        print(f"  [{i}/{len(conformant)}] {database}: {len(tables)} tables, {views} views")

    items = live.get_process_configs(args.env_code, table_name=args.process_config_table)
    print(f"process-config items: {len(items)}")
    print(f"cached {view_count} views across {len(conformant)} DBs -> {store.cache_dir}")
    return 0


def _cmd_enumerate(args) -> int:
    source = CachedSource(_store(args))
    result = catalog.enumerate_views(source, args.env_code)
    summary = result.summary()
    print(json.dumps(summary, indent=2))

    payload = {
        "summary": summary,
        "conformant_dbs": result.conformant_dbs,
        "nonconformant_dbs": result.nonconformant_dbs,
        "views": [
            {"qualified": v.qualified, "silver_base": v.silver_base, "has_sql": v.has_sql}
            for v in result.views
        ],
    }
    path = _write_out(f"enumerate-{args.env_code}.json", payload, Path(args.out_dir))
    undecoded = [v.qualified for v in result.views if not v.has_sql]
    if undecoded:
        print(f"\n{len(undecoded)} view(s) did not decode to SQL (first 10):")
        for q in undecoded[:10]:
            print(f"  - {q}")
    print(f"\nwrote {path}")
    return 0


def _cmd_compare(args) -> int:
    source = CachedSource(_store(args))
    result = catalog.enumerate_views(source, args.env_code)
    items = source.get_process_configs(args.env_code)
    report = compare.build_comparison(result, items)

    print(json.dumps(report["summary"], indent=2))
    missed = report["catalog_only_views"]
    if missed:
        print(f"\nviews the name-guess MISSED ({len(missed)}; first 15):")
        for q in missed[:15]:
            print(f"  - {q}")
    path = _write_out(f"compare-{args.env_code}.json", report, Path(args.out_dir))
    print(f"\nwrote {path}")
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--env-code", default="dev", help="deploy env (db suffix); default dev")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE))
    p.add_argument("--fixtures-dir", default=str(_DEFAULT_FIXTURES))
    p.add_argument("--out-dir", default=str(_DEFAULT_OUT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="viewpull", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("refresh", help="one-time live AWS pull into cache/")
    _add_common(pr)
    pr.add_argument("--profile", default=None, help="AWS profile (else AWS_PROFILE)")
    pr.add_argument("--region", default=None, help="AWS region (else env / ap-southeast-2)")
    pr.add_argument("--okta-login", action="store_true", help="refresh creds via okta-aws-cli")
    pr.add_argument("--process-config-table", default=None,
                    help="override the DynamoDB process-config table name")
    pr.set_defaults(func=_cmd_refresh)

    pe = sub.add_parser("enumerate", help="offline: decode all conformant view SQL")
    _add_common(pe)
    pe.set_defaults(func=_cmd_enumerate)

    pc = sub.add_parser("compare", help="offline: catalog-driven vs name-guess delta")
    _add_common(pc)
    pc.set_defaults(func=_cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (auth.AuthError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
