"""Encrypted OpenRouter token and public LLM settings.

The token is encrypted before it touches Postgres. The key is derived from
DATABASE_URL, which is present in the component environment and is not returned
by the store HTTP API. Chat must call CompleteWorkflow instead of reading
llm.token.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from proteus import Store

CONFIG_KEY = "llm.config"
TOKEN_KEY = "llm.token"
MODELS_KEY = "llm.models"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openrouter/auto"

_KEYS: dict[tuple[bytes, bytes], bytes] = {}


class SettingsError(ValueError):
    """A safe, user-facing settings failure. Never includes the token."""


def public_view() -> dict:
    config = _config_value()
    token_set = _token_record() is not None
    hint = config.get("token_hint") if token_set else ""
    if not isinstance(hint, str):
        hint = ""
    return {
        "base_url": _text(config.get("base_url")) or DEFAULT_BASE_URL,
        "model": _text(config.get("model")) or DEFAULT_MODEL,
        "system": _text(config.get("system")),
        "token_set": token_set,
        "token_hint": hint[-4:] if token_set else "",
        "saved": bool(config),
    }


def save(*, base_url: str, model: str, system: str, token: str | None) -> dict:
    clean_url = normalize_base_url(base_url)
    clean_model = _clean_model(model)
    clean_system = _clean_system(system)
    provided = None if token is None else token.strip()
    if provided == "":
        provided = None
    if provided is None:
        if _token_record() is None:
            raise SettingsError("A token is required.")
        secret = unlock()
        blob = None
    else:
        _validate_token(provided)
        secret = provided
        blob = encrypt(provided)
    _reject_secret_in_public(clean_url, clean_model, clean_system, secret)
    if blob is not None:
        Store().put(TOKEN_KEY, blob, ["llm-secret"])
    Store().put(
        CONFIG_KEY,
        {
            "base_url": clean_url,
            "model": clean_model,
            "system": clean_system,
            "token_set": True,
            "token_hint": _hint(secret),
        },
        ["llm"],
    )
    return public_view()


def clear_token() -> dict:
    Store().delete(TOKEN_KEY)
    config = _config_value()
    if config:
        config["token_set"] = False
        config["token_hint"] = ""
        Store().put(CONFIG_KEY, config, ["llm"])
    return public_view()


def unlock() -> str:
    record = _token_record()
    if record is None:
        raise SettingsError("No token is saved.")
    return decrypt(record.get("value"))


def save_model_catalog(models: list[dict]) -> None:
    clean = []
    for item in models[:400]:
        if not isinstance(item, dict):
            continue
        ident = _text(item.get("id"))[:200]
        if not ident:
            continue
        clean.append({"id": ident, "name": _text(item.get("name"))[:200]})
    Store().put(MODELS_KEY, {"models": clean}, ["llm"])


def load_model_catalog() -> list[dict]:
    found = Store().get(MODELS_KEY)
    if not found or not isinstance(found.get("value"), dict):
        return []
    raw = found["value"].get("models")
    if not isinstance(raw, list):
        return []
    models = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            name = item.get("name") if isinstance(item.get("name"), str) else ""
            models.append({"id": item["id"], "name": name})
    return models


def redact(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    cleaned = text.replace(secret, "••••")
    encoded = secret.replace(" ", "%20")
    if encoded and encoded != secret:
        cleaned = cleaned.replace(encoded, "••••")
    return cleaned


def encrypt(plaintext: str) -> dict:
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    key = _derive(salt)
    data = plaintext.encode("utf-8")
    ciphertext = _xor(data, _keystream(key, nonce, len(data)))
    mac = hmac.new(key, b"proteus-llm-mac-v1" + nonce + ciphertext, hashlib.sha256).digest()
    return {
        "v": 1,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "mac": mac.hex(),
    }


def decrypt(blob: object) -> str:
    if not isinstance(blob, dict) or blob.get("v") != 1:
        raise SettingsError("The saved token could not be unlocked. Save it again.")
    try:
        salt = _unhex(blob.get("salt"), 16)
        nonce = _unhex(blob.get("nonce"), 16)
        ciphertext = _unhex(blob.get("ciphertext"))
        mac = _unhex(blob.get("mac"), 32)
    except SettingsError:
        raise
    except Exception as exc:
        raise SettingsError("The saved token could not be unlocked. Save it again.") from exc
    if not ciphertext or len(ciphertext) > 512:
        raise SettingsError("The saved token could not be unlocked. Save it again.")
    key = _derive(salt)
    expected = hmac.new(key, b"proteus-llm-mac-v1" + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise SettingsError("The saved token could not be unlocked. Save it again.")
    try:
        return _xor(ciphertext, _keystream(key, nonce, len(ciphertext))).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SettingsError("The saved token could not be unlocked. Save it again.") from exc


def normalize_base_url(value: str) -> str:
    import ipaddress
    import urllib.parse

    text = (value or "").strip() or DEFAULT_BASE_URL
    if len(text) > 300:
        raise SettingsError("Base URL is too long.")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("https", "http"):
        raise SettingsError("Base URL must start with https:// or http://")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise SettingsError("Put the token in the token field, not the URL.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise SettingsError("Base URL needs a host.")
    if host in {"metadata.google.internal", "metadata.google"} or host.endswith(".metadata.google.internal"):
        raise SettingsError("That base URL is not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and ip in {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }:
        raise SettingsError("That base URL is not allowed.")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _secret() -> bytes:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SettingsError("The token could not be saved securely.")
    return b"proteus-llm-token-v1\0" + url.encode("utf-8")


def _derive(salt: bytes) -> bytes:
    password = _secret()
    marker = hashlib.sha256(password).digest()
    cached = _KEYS.get((marker, salt))
    if cached is not None:
        return cached
    try:
        key = hashlib.scrypt(
            password,
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
    except ValueError as exc:
        raise SettingsError("The token could not be saved securely.") from exc
    _KEYS[(marker, salt)] = key
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            key,
            b"proteus-llm-stream-v1" + nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _unhex(value: object, size: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise SettingsError("The saved token could not be unlocked. Save it again.")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise SettingsError("The saved token could not be unlocked. Save it again.") from exc
    if size is not None and len(raw) != size:
        raise SettingsError("The saved token could not be unlocked. Save it again.")
    return raw


def _validate_token(token: str) -> None:
    if any(ch in token for ch in "\r\n\x00"):
        raise SettingsError("The token cannot contain line breaks.")
    if len(token) > 512:
        raise SettingsError("The token is too long.")


def _hint(token: str) -> str:
    if len(token) < 8:
        return ""
    return token[-4:]


def _clean_model(value: str) -> str:
    text = (value or "").strip() or DEFAULT_MODEL
    if any(ch in text for ch in "\r\n\x00"):
        raise SettingsError("Model name cannot contain line breaks.")
    if len(text) > 200:
        raise SettingsError("Model name is too long.")
    return text


def _clean_system(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\x00" in text:
        raise SettingsError("System prompt cannot contain NUL.")
    if len(text) > 4000:
        raise SettingsError("System prompt is too long.")
    return text


def _reject_secret_in_public(base_url: str, model: str, system: str, secret: str) -> None:
    if not secret:
        return
    fields = (("base URL", base_url), ("model", model), ("system prompt", system))
    for label, field in fields:
        if secret in field:
            raise SettingsError(f"The token must not be stored in the {label}.")


def _config_value() -> dict:
    found = Store().get(CONFIG_KEY)
    if not found or not isinstance(found.get("value"), dict):
        return {}
    return dict(found["value"])


def _token_record() -> dict | None:
    return Store().get(TOKEN_KEY)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
