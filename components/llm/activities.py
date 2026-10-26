from __future__ import annotations

import asyncio

from temporalio.exceptions import ApplicationError

from proteus import activity

import client
import vault

_SAFE_PREFIXES = (
    "No token is saved",
    "The saved token could not be unlocked",
    "Set a model",
    "The model",
    "The endpoint",
    "Base URL",
    "That base URL",
    "Put the token",
    "The token",
    "A token is required",
    "Model name",
    "System prompt",
)


@activity
async def complete(arg: dict) -> dict:
    try:
        return await asyncio.to_thread(_complete, arg if isinstance(arg, dict) else {})
    except vault.SettingsError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from None
    except RuntimeError as exc:
        text = str(exc)
        if text.startswith(_SAFE_PREFIXES):
            raise ApplicationError(text, non_retryable=True) from None
        raise ApplicationError("The endpoint could not be reached.", non_retryable=True) from None
    except Exception:
        raise ApplicationError("The endpoint could not be reached.", non_retryable=True) from None


@activity
async def public_config(arg: dict) -> dict:
    del arg
    try:
        return vault.public_view()
    except vault.SettingsError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from None
    except Exception:
        raise ApplicationError("Settings could not be read.", non_retryable=True) from None


def _complete(arg: dict) -> dict:
    messages = _clean_messages(arg.get("messages"))
    if not any(item["role"] == "user" for item in messages):
        raise RuntimeError("The model needs at least one user message.")
    return client.complete(messages)


def _clean_messages(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw[-40:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        cleaned.append({"role": role, "content": text[:12000]})
    return cleaned
