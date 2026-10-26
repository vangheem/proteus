"""Server-side OpenRouter calls. The token never leaves this process."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import vault

_OPENER = urllib.request.build_opener(_NoRedirect := type(
    "_NoRedirect",
    (urllib.request.HTTPRedirectHandler,),
    {
        "redirect_request": staticmethod(
            lambda req, fp, code, msg, headers, newurl: (_ for _ in ()).throw(
                urllib.error.HTTPError(
                    getattr(req, "full_url", ""),
                    code,
                    "redirects are not followed",
                    headers,
                    fp,
                )
            )
        )
    },
))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "redirects are not followed",
            headers,
            fp,
        )


_OPENER = urllib.request.build_opener(_NoRedirect)


def complete(messages: list[dict]) -> dict:
    cfg = vault.public_view()
    if not cfg["token_set"]:
        raise vault.SettingsError("No token is saved.")
    model = (cfg.get("model") or "").strip()
    if not model:
        raise vault.SettingsError("Set a model in LLM settings.")
    token = vault.unlock()
    redacted = [
        {"role": item["role"], "content": vault.redact(item["content"], token)}
        for item in messages
        if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str)
    ]
    outbound: list[dict] = []
    system = (cfg.get("system") or "").strip()
    if system:
        outbound.append({"role": "system", "content": system})
    outbound.extend(redacted)
    payload = _request_json(
        "POST",
        _endpoint(cfg["base_url"], "chat/completions"),
        token,
        {"model": model, "messages": outbound},
        timeout=120,
        limit=1_000_000,
    )
    text = vault.redact(_choice_text(payload), token)
    if not text.strip():
        raise RuntimeError("The model returned an empty reply.")
    if len(text) > 100_000:
        text = text[:100_000] + "\n\n[truncated]"
    return {"content": text, "model": model, "messages": redacted}


def list_models(base_url: str, token: str) -> list[dict]:
    secret = (token or "").strip()
    if not secret:
        raise vault.SettingsError("No token is saved.")
    if len(secret) > 512 or any(ch in secret for ch in "\r\n\x00"):
        raise vault.SettingsError("The token cannot contain line breaks.")
    base = vault.normalize_base_url(base_url)
    payload = _request_json(
        "GET",
        _endpoint(base, "models"),
        secret,
        None,
        timeout=20,
        limit=2_000_000,
    )
    return _models(payload)


def _endpoint(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "proteus-chat",
        "HTTP-Referer": "http://127.0.0.1:8080/chat",
        "X-Title": "Proteus",
    }


def _request_json(
    method: str,
    url: str,
    token: str,
    payload: dict | None,
    *,
    timeout: int,
    limit: int,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=_headers(token))
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = _read_limited(response, limit)
    except urllib.error.HTTPError as exc:
        detail = _read_limited(exc, 8000).decode("utf-8", errors="replace")
        raise RuntimeError(vault.redact(_http_error(exc.code, detail), token)) from None
    except TimeoutError:
        raise RuntimeError("The endpoint did not respond in time.") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise RuntimeError("The endpoint did not respond in time.") from None
        raise RuntimeError("The endpoint could not be reached.") from None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("The endpoint could not be reached.") from None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The endpoint returned a response that was not JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("The endpoint returned an unexpected response.")
    return parsed


def _read_limited(source, limit: int) -> bytes:
    data = source.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("The endpoint returned too much data.")
    return data


def _http_error(status: int, raw: str) -> str:
    message = ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
        elif isinstance(err, str):
            message = err
    message = " ".join(message.split())
    if not message:
        return f"The model rejected the request ({status})."
    return f"The model rejected the request ({status}): {message[:300]}"


def _choice_text(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("The model returned no choices.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("The model returned no choices.")
    return _content_text(message.get("content"))


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _models(payload: dict) -> list[dict]:
    raw = payload.get("data")
    if not isinstance(raw, list):
        raise RuntimeError("The endpoint returned an unexpected response.")
    models = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            continue
        seen.add(ident)
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        models.append({"id": ident[:200], "name": name[:200]})
        if len(models) >= 400:
            break
    models.sort(key=lambda item: item["id"].lower())
    return models
