"""Redis cache. Values are JSON-encoded so strings and objects round-trip."""

from __future__ import annotations

import json
import os
from typing import Any

import redis


class Cache:
    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url
        self._client: redis.Redis | None = None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value)
        client = self._client_or_create()
        if ttl is None:
            client.set(key, payload)
        else:
            client.set(key, payload, ex=int(ttl))

    def get(self, key: str) -> Any:
        raw = self._client_or_create().get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)

    def delete(self, key: str) -> None:
        self._client_or_create().delete(key)

    def _client_or_create(self) -> redis.Redis:
        if self._client is None:
            url = self.redis_url or os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
            self._client = redis.Redis.from_url(
                url,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        return self._client
