"""viewpull — ASP-1586 catalog-driven "pull all views" spike.

A throwaway sandbox that proves we can pull the SQL for **every** conformant Glue view by scanning
the catalog directly, instead of lta's name-reconstructing ``_fetch_view_sql`` (which guesses
``<silver>_vw`` / ``_source_vw`` per DynamoDB item and misses off-pattern / unreachable views).

Live AWS state is dumped once to ``cache/`` (see :mod:`viewpull.cache`); all enumeration / decode /
compare logic then runs offline against that JSON.
"""
