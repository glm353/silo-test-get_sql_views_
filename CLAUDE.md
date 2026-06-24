# asp-1586-view-pull — catalog-driven "pull all views" spike (ASP-1586)

> **Throwaway sandbox**, not a belt module. It lives outside `int-CDCv2-Tooling` (in
> `silo testing/`) on purpose: it proves one idea before any of it touches the real
> `lineage-trace-analysis` (lta) tool.

## Why this exists

**[ASP-1586](https://uon.atlassian.net/browse/ASP-1586)** — *"Implement pull all views in DB as the
first approach"* (sub-task of ASP-1575). Review comment on the ticket: **"This won't work across the
board."**

The thing that won't work across the board is lta's Tier-1 view read,
`_fetch_view_sql` in `lineage-trace-analysis/lineage_trace_analysis/sources/aws.py`. It is
**process-driven and name-reconstructing**: for every DynamoDB process-config item it *computes* a
view name as `silver_table + "_source_vw"` / `"_vw"` and does a targeted `glue.get_table`. So it
silently misses any view that:

1. **doesn't match the exact `_vw` / `_source_vw` suffix** — off-pattern / hand-named views, or
   Data-Loader silver suffixes (`_s`, …) that shift the base name; and
2. **isn't reachable from a well-formed DynamoDB item** — missing item, or wrong
   `CatalogDatabase` / `SilverTable`.

**ASP-1586's alternative (what this spike implements): scan the Glue catalog directly.** List all
databases, keep only **conformant** ones (`molecular` / `domain` / `business` prefix, env-suffixed —
the "caveat" that avoids the mess in random DBs), list **every `VIRTUAL_VIEW`** in them, and decode
its SQL straight from `ViewOriginalText`. This finds every deployed view **regardless of name** and
needs no DynamoDB at all. (lta's Tier-2 DynamoDB `SourceQuery` is still where non-view Glue-job SQL
comes from — out of scope here.)

**Goal of the spike:** prove we can pull SQL for *all* conformant views, and **quantify** how many
the old name-guess misses. **Sandbox-only** — wiring the proven approach back into lta is a separate
follow-up.

## Caching — the backbone (what & why)

**Caching = store the result of a slow/expensive operation so later runs reuse it instead of
recomputing.** Here the expensive op is the **live AWS round-trip**: Okta SSO login → a
`glue.get_databases` + per-DB `glue.get_tables` page-walk over hundreds of databases → a
`dynamodb scan`. Running that on every code change is slow, needs valid (expiring) creds, and is
rate-limited.

So `viewpull refresh` pulls the **raw API responses once** and writes them to JSON in `cache/`.
Every later command reads that JSON. Payoff:

- **Offline iteration** — develop/test the enumeration & decode with **zero AWS calls**, no Okta
  expiry mid-work. (Same spirit as the belt's `samples/` + botocore `Stubber` convention; here the
  cache *is* the sample data.)
- **Reproducible / deterministic** — same bytes every run, so the committed `fixtures/` make tests
  stable.
- **Fast & safe** — no network latency, read-only snapshot, no prod risk.

The enumeration code never sees boto3 — it talks to a **source** with three methods
(`get_databases` / `get_tables` / `get_process_configs`). Two implementations share that shape so
nothing downstream can tell where the bytes came from:

| Source | Used by | Behaviour |
|---|---|---|
| `LiveSource` | `refresh` | calls boto3 **and writes** each response to `cache/` |
| `CachedSource` | `enumerate`, `compare` | **reads** JSON only; falls back to committed `fixtures/` |

Cache files (raw; DynamoDB items already deserialised): `glue_databases.json`,
`glue_tables__<db>.json` (one per conformant DB), `dynamo_process_config_<env>.json`.

## Layout

```
viewpull/
  auth.py      # COPIED verbatim from lta/auth.py (boto3 session + sts preflight + okta-aws-cli)
  presto.py    # COPIED from lta/sources/aws.py: decode_presto_view + view_sql_from_table (decode core)
  cache.py     # the caching layer: JsonStore + LiveSource (write) / CachedSource (read)
  catalog.py   # NEW — is_conformant + classify_databases + enumerate_views (the ASP-1586 approach)
  legacy.py    # NEW — port of lta's name-guess _fetch_view_sql (the baseline being challenged)
  compare.py   # NEW — catalog-driven vs name-guess delta report (the evidence)
  cli.py       # python -m viewpull  refresh | enumerate | compare
fixtures/      # committed trimmed sample (3 conformant DBs, 1 off-pattern view, DynamoDB items)
cache/         # gitignored live dumps
tests/         # offline pytest over fixtures/
```

**Reuse = port, not import** (the house convention): `auth.py` and the Presto helpers are copied so
the spike decodes SQL identically to lta; `legacy.py` re-implements `_fetch_view_sql` so the
comparison runs the *real* old logic.

## Usage

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt

# one-time live pull (needs creds; add --okta-login to refresh via okta-aws-cli)
python -m viewpull refresh --env-code dev --profile <profile>

# then everything is offline:
python -m viewpull enumerate --env-code dev   # decode every conformant view's SQL + coverage
python -m viewpull compare   --env-code dev   # what the name-guess misses, and why

pytest                                        # offline, over fixtures/
```

Reports land in `out/` (gitignored): `enumerate-<env>.json`, `compare-<env>.json`.

The compare summary is the answer to the ticket comment — key fields: `catalog_only_views` (views
the name-guess missed), split into `missed_off_pattern_name` (reason 1) and
`missed_no_reachable_process` (reason 2).

## Verification

- **`pytest` — 12 tests, offline** over `fixtures/`: conformant-DB filter (rejects a non-governed DB
  *and* a right-prefix/wrong-env DB), `VIRTUAL_VIEW` enumeration + Presto decode (incl. an
  undecodable envelope), cache write→read round-trip + fixtures fallback, and the compare delta —
  the fixture seeds an **off-pattern** view (`staff_curated`) the name-guess can't reach and an
  on-pattern view with no matching process (`user_vw`), so the report classifies both miss reasons.
- **Offline CLI smoke (fixtures):** `enumerate` → 3 conformant DBs, 5 views, 4 decoded;
  `compare` → `catalog_only_views = 2` (1 off-pattern + 1 unreached), `legacy_only_views = 0`.

## Session log

- **2026-06-24** — built the sandbox. Ported `auth.py` + Presto decode from lta; new
  `cache.py` (JsonStore + Live/Cached sources), `catalog.py` (conformant filter + view enumeration),
  `legacy.py` (name-guess baseline), `compare.py` (delta report), `cli.py`. Committed `fixtures/` +
  12 offline tests, all green. Offline `enumerate`/`compare` verified against fixtures.

- **2026-06-24 (live)** — ran the one-time `refresh --env-code dev` against acct **484438948628**
  (`ap-southeast-2`, `default` profile, already SSO-authenticated so no `--okta-login`). Confirmed
  the DynamoDB table `uon-nonprod-process-configuration-dev` (ACTIVE, 1296 items) before the pull,
  so no `--process-config-table` override. Fixed a Windows cp1252 crash on the final summary line
  (`→` → `->` in `cli.py`; the crash was *after* all cache writes, so the dump was intact).
  **The live answer to the "won't work across the board" comment** (from `out/compare-dev.json`):

  | Metric | Value |
  |---|---|
  | Glue databases total | 323 |
  | …conformant (governed prefix + env suffix) | 101 |
  | …non-conformant (skipped) | 222 |
  | **Views found by catalog scan** | **476** |
  | …decoded to SQL | **476 (100%)** |
  | …undecoded | 0 |
  | Views found by lta name-guess | 248 |
  | **Catalog-only (name-guess MISSED)** | **228 (48% of all views)** |
  | …missed — off-pattern name (reason 1) | 42 |
  | …missed — no reachable DynamoDB process (reason 2) | 186 |
  | Legacy-only (catalog missed) | 0 |

  **Bottom line:** the name-guess finds barely **half** the deployed views (248/476); the
  catalog scan finds and decodes **all 476** and misses nothing the name-guess found. The comment
  is correct — the Tier-1 name-reconstruction does not work across the board. **Pending:**
  next-step #5 (trim a couple of real DBs/views/items from `cache/` into committed `fixtures/`).

## Next steps

The live spike is **done** — the `refresh` ran, and the comparison answer is recorded in the
*Session log* above (476 views, name-guess misses 228 / 48%). Steps 1–4 (confirm table → live pull
→ live compare → record numbers) are complete. Remaining:

1. **Trim a couple of real DBs / views / DynamoDB items** from `cache/` into committed `fixtures/`
   so the offline tests exercise real shapes (keep the sample small). Good real candidates from the
   live run: an off-pattern miss (e.g. `business_cs_microsoft_dev.crestron_room_event_vw_with_dup`)
   and a no-reachable-process miss, plus a clean on-pattern view the name-guess resolves.

## Out of scope (follow-ups)

- Wiring the catalog-driven enumeration back into lta `sources/aws.py` / `load_aws_processes`.
- Mapping each catalog view back to its `process_id` / `Schema` / `SourceType` (only needed for the
  lta integration, not for "can we pull the SQL").
- Spark-dialect column lineage, GitHub↔AWS drift (already deferred in lta).
```
