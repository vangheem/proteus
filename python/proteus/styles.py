"""Global stylesheet registration.

Components write CSS into the shared Postgres table. The coordinator injects
the combined stylesheet into every component HTML page.
"""

from __future__ import annotations

import os

import psycopg


class Styles:
    def __init__(self, database_url: str | None = None, component: str | None = None) -> None:
        self.database_url = database_url
        self.component = component

    def put(self, css: str) -> dict:
        text = css if isinstance(css, str) else ""
        if "\x00" in text:
            raise ValueError("css must not contain NUL")
        if len(text) > 64_000:
            raise ValueError("css is too large")
        name = self._name()
        with self._connect() as conn:
            with conn.cursor() as cur:
                if text.strip() == "":
                    cur.execute("DELETE FROM global_styles WHERE component = %s", (name,))
                else:
                    cur.execute(
                        """
                        INSERT INTO global_styles (component, css, updated_at)
                        VALUES (%s, %s, now())
                        ON CONFLICT (component) DO UPDATE
                        SET css = EXCLUDED.css, updated_at = now()
                        """,
                        (name, text),
                    )
            conn.commit()
        return {"component": name, "css": text}

    def get(self) -> dict | None:
        name = self._name()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT css FROM global_styles WHERE component = %s", (name,))
                row = cur.fetchone()
        if row is None:
            return None
        return {"component": name, "css": row[0]}

    def clear(self) -> None:
        self.put("")

    def _name(self) -> str:
        name = self.component or os.environ.get("PROTEUS_COMPONENT_NAME", "")
        if not name:
            raise RuntimeError("PROTEUS_COMPONENT_NAME is not set")
        return name

    def _connect(self) -> psycopg.Connection:
        url = self.database_url or os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return psycopg.connect(url, connect_timeout=10)


styles = Styles()
