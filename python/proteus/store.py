"""Schemaless JSON object store backed by Postgres."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


class Store:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def put(self, key: str, value: dict, tags: list[str] | None = None) -> dict:
        if not isinstance(value, dict):
            raise ValueError("value must be a JSON object")
        clean_tags = _clean_tags(tags)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kv_entries (key, value, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = now()
                    """,
                    (key, Jsonb(value)),
                )
                cur.execute("DELETE FROM kv_tags WHERE key = %s", (key,))
                if clean_tags:
                    cur.executemany(
                        "INSERT INTO kv_tags (key, tag) VALUES (%s, %s)",
                        [(key, tag) for tag in clean_tags],
                    )
            conn.commit()
        return {"key": key, "value": value, "tags": clean_tags}

    def get(self, key: str) -> dict | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_entries WHERE key = %s", (key,))
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    "SELECT tag FROM kv_tags WHERE key = %s ORDER BY tag",
                    (key,),
                )
                tags = [item[0] for item in cur.fetchall()]
        return {"key": key, "value": _decode_json(row[0]), "tags": tags}

    def find(self, tags: list[str] | None = None) -> list[dict]:
        """Return entries that have every requested tag. Empty tags means all entries."""
        wanted = _clean_tags(tags)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not wanted:
                    cur.execute(
                        """
                        SELECT e.key,
                               e.value,
                               COALESCE(
                                   array_agg(t.tag ORDER BY t.tag)
                                   FILTER (WHERE t.tag IS NOT NULL),
                                   ARRAY[]::text[]
                               )
                        FROM kv_entries e
                        LEFT JOIN kv_tags t ON t.key = e.key
                        GROUP BY e.key, e.value
                        ORDER BY e.key
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT e.key,
                               e.value,
                               COALESCE(
                                   array_agg(t.tag ORDER BY t.tag)
                                   FILTER (WHERE t.tag IS NOT NULL),
                                   ARRAY[]::text[]
                               )
                        FROM kv_entries e
                        LEFT JOIN kv_tags t ON t.key = e.key
                        WHERE (
                            SELECT COUNT(DISTINCT t2.tag)
                            FROM kv_tags t2
                            WHERE t2.key = e.key AND t2.tag = ANY(%s)
                        ) = %s
                        GROUP BY e.key, e.value
                        ORDER BY e.key
                        """,
                        (wanted, len(wanted)),
                    )
                rows = cur.fetchall()
        return [
            {"key": key, "value": _decode_json(value), "tags": list(tags or [])}
            for key, value, tags in rows
        ]

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kv_entries WHERE key = %s", (key,))
            conn.commit()

    def _connect(self) -> psycopg.Connection:
        url = self.database_url or os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return psycopg.connect(url, connect_timeout=10)


def _clean_tags(tags: list[str] | None) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        if not isinstance(tag, str):
            raise ValueError("tags must be a list of strings")
        if tag in seen:
            continue
        seen.add(tag)
        clean.append(tag)
    return clean


def _decode_json(value: Any) -> Any:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        return json.loads(value)
    return value
