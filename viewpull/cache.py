"""The caching layer — the backbone of the spike.

**Caching** here means: pull the *raw* AWS API responses **once**, write them to JSON, and let every
later run read that JSON instead of calling AWS again. The expensive operation we are caching is the
live round-trip — Okta SSO login, a ``glue.get_databases`` + per-database ``glue.get_tables``
page-walk over hundreds of databases, and a ``dynamodb scan`` of the process-config table. Caching
buys us: offline iteration (no creds / no Okta expiry mid-work), reproducible/deterministic tests,
and speed.

The catalog/compare code never talks to boto3 directly — it talks to a **source** with three
methods (``get_databases`` / ``get_tables`` / ``get_process_configs``). Two implementations share
that shape so the rest of the tool can't tell where the data came from:

* :class:`LiveSource` — calls boto3 **and writes** each response into the cache (used by ``refresh``).
* :class:`CachedSource` — **reads** the JSON only (used by ``enumerate`` / ``compare``); falls back
  to the committed ``fixtures/`` when ``cache/`` is empty, so a fresh clone runs offline.

Cache files (raw, DynamoDB items already deserialised to plain dicts):
``glue_databases.json``, ``glue_tables__<database>.json``, ``dynamo_process_config_<env>.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

_DESER = TypeDeserializer()


def _deserialise(item: dict) -> dict:
    """DynamoDB low-level item ({'S': ...}) → plain Python dict (copied from lta sources/aws.py)."""
    return {k: _DESER.deserialize(v) for k, v in item.items()}


def _tables_key(database: str) -> str:
    return f"glue_tables__{database}"


def _process_key(env_code: str) -> str:
    return f"dynamo_process_config_{env_code}"


class JsonStore:
    """Read/write JSON under ``cache/`` with a read-only fall back to committed ``fixtures/``."""

    def __init__(self, cache_dir: Path, fixtures_dir: Path | None = None):
        self.cache_dir = Path(cache_dir)
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None

    def write(self, name: str, obj) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{name}.json"
        # default=str so Glue's datetime fields (CreateTime/UpdateTime/…) serialise cleanly.
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        return path

    def read(self, name: str):
        for base in (self.cache_dir, self.fixtures_dir):
            if base is None:
                continue
            path = base / f"{name}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(
            f"no cached '{name}.json' in {self.cache_dir}"
            + (f" or {self.fixtures_dir}" if self.fixtures_dir else "")
            + " — run `viewpull refresh` first (a one-time live AWS pull)."
        )

    def exists(self, name: str) -> bool:
        for base in (self.cache_dir, self.fixtures_dir):
            if base and (base / f"{name}.json").exists():
                return True
        return False


class CachedSource:
    """Offline source — reads dumped JSON only, never touches AWS."""

    def __init__(self, store: JsonStore):
        self.store = store

    def get_databases(self) -> list[dict]:
        return self.store.read("glue_databases")

    def get_tables(self, database: str) -> list[dict]:
        return self.store.read(_tables_key(database))

    def get_process_configs(self, env_code: str) -> list[dict]:
        return self.store.read(_process_key(env_code))

    def has_tables(self, database: str) -> bool:
        return self.store.exists(_tables_key(database))


class LiveSource:
    """Live source — calls boto3, writes every response into the cache, returns the Python object.

    A Lake-Formation / IAM ``AccessDeniedException`` on a single database's ``get_tables`` is
    **skipped, not fatal** (logged, cached as ``[]``) so one locked-down DB can't abort the dump —
    mirroring lta ``_fetch_view_sql``'s skip-not-fatal stance.
    """

    def __init__(self, session, store: JsonStore, log=print):
        self.glue = session.client("glue")
        self.dynamo = session.client("dynamodb")
        self.store = store
        self.log = log or (lambda *_: None)

    def get_databases(self) -> list[dict]:
        out: list[dict] = []
        for page in self.glue.get_paginator("get_databases").paginate():
            out.extend(page.get("DatabaseList", []))
        self.store.write("glue_databases", out)
        return out

    def get_tables(self, database: str) -> list[dict]:
        out: list[dict] = []
        try:
            for page in self.glue.get_paginator("get_tables").paginate(DatabaseName=database):
                out.extend(page.get("TableList", []))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AccessDeniedException", "AccessDenied"):
                self.log(f"skipping {database} — get_tables access denied (Lake Formation); "
                         "cached as empty.")
            elif code == "EntityNotFoundException":
                self.log(f"skipping {database} — database not found.")
            else:
                raise
        self.store.write(_tables_key(database), out)
        return out

    def get_process_configs(self, env_code: str, table_name: str | None = None) -> list[dict]:
        table_name = table_name or self._process_config_table(env_code)
        out: list[dict] = []
        for page in self.dynamo.get_paginator("scan").paginate(TableName=table_name):
            for raw in page.get("Items", []):
                out.append(_deserialise(raw))
        self.store.write(_process_key(env_code), out)
        return out

    @staticmethod
    def _process_config_table(env_code: str) -> str:
        # The deployed catalogue table. lta's live runs used the uon-nonprod account naming; the
        # exact name is overridable on the CLI (--process-config-table) if it differs per account.
        return f"uon-nonprod-process-configuration-{env_code}"
