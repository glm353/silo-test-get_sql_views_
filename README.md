# asp-1586-view-pull

A throwaway spike for **[ASP-1586](https://uon.atlassian.net/browse/ASP-1586)**: prove we can pull
the SQL for **every** conformant Glue view by scanning the catalog directly, instead of
lineage-trace-analysis's name-reconstructing `_fetch_view_sql` (which guesses `<silver>_vw` /
`_source_vw` per DynamoDB process and misses off-pattern / unreachable views).

Live AWS state is **cached** to JSON once (`viewpull refresh`); everything else runs offline.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# one-time live pull (needs AWS creds; --okta-login to refresh via okta-aws-cli)
python -m viewpull refresh --env-code dev --profile <profile>

# offline analysis (reads cache/, falls back to committed fixtures/)
python -m viewpull enumerate --env-code dev   # decode all conformant view SQL + coverage
python -m viewpull compare   --env-code dev   # what the old name-guess misses, and why

pytest                                        # 12 offline tests over fixtures/
```

See [CLAUDE.md](CLAUDE.md) for the full design, the caching model, and the session log.
