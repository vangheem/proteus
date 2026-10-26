"""Chat desk.

Completions go through the llm component's CompleteWorkflow. This component
never reads llm.token. Public settings are read from llm.config for display.
"""

from __future__ import annotations

import asyncio
import html
import os
import urllib.parse
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import RedirectResponse
from proteus import Component, Store, bus

CONFIG_KEY = "llm.config"
TAG = "chat"
MAX_MESSAGES = 40
MAX_CONTENT = 12_000
CALL_TIMEOUT = 170

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
    "Settings could not be read",
    "The model needs",
)


class ChatError(Exception):
    """A message that is safe to show. Never includes a token."""


_CSS = """
<style>
  :root {
    --ink: #101218;
    --text: #f4f1ea;
    --muted: #a39888;
    --faint: #7c7468;
    --line: #2c3140;
    --gold: #f0d7a4;
    --gold-deep: #8a6a32;
    --sea: #8fd0c8;
    --panel: rgba(244, 241, 234, 0.035);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    padding: 2.4rem 1.25rem 4rem;
    background:
      radial-gradient(880px 460px at 0% -10%, rgba(143, 208, 200, 0.14), transparent 56%),
      radial-gradient(640px 380px at 100% 0%, rgba(240, 215, 164, 0.1), transparent 50%),
      var(--ink);
    color: var(--text);
    font: 16px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: rgba(240, 215, 164, 0.32); }
  .shell { max-width: 68rem; margin: 0 auto; }
  .mast {
    display: flex;
    justify-content: space-between;
    gap: 1.5rem;
    align-items: end;
    margin-bottom: 1.15rem;
  }
  .kicker {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 0;
    color: var(--gold);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }
  .mark {
    width: 0.8rem;
    height: 0.8rem;
    border-radius: 42% 58% 48% 52%;
    background: linear-gradient(135deg, var(--sea), var(--gold));
    box-shadow: 0 0 16px rgba(143, 208, 200, 0.4);
  }
  h1 {
    margin: 0.45rem 0 0;
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    font-size: clamp(2.4rem, 5vw, 3.4rem);
    font-weight: 500;
    letter-spacing: -0.03em;
    line-height: 0.95;
  }
  .links { display: flex; gap: 1rem; font-size: 0.88rem; }
  .shell a { color: var(--gold); text-underline-offset: 0.18em; }
  .shell a:hover { color: #f7e7c4; }
  .tools {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    align-items: center;
    margin-bottom: 0.85rem;
  }
  .new {
    display: inline-block;
    padding: 0.55rem 0.95rem;
    border-radius: 999px;
    background: var(--gold);
    color: #1c1508;
    font-size: 0.88rem;
    font-weight: 650;
    text-decoration: none;
  }
  .new:hover { background: #f7e7c4; color: #1c1508; }
  .filter {
    width: min(16rem, 100%);
    padding: 0.55rem 0.85rem;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: #0c0e14;
    color: var(--text);
    font: inherit;
  }
  .filter:focus { outline: none; border-color: var(--sea); }
  #desk {
    display: grid;
    grid-template-columns: minmax(14rem, 18rem) minmax(0, 1fr);
    gap: 1rem;
    align-items: start;
  }
  .list, .column {
    border: 1px solid var(--line);
    border-radius: 18px;
    background: var(--panel);
  }
  .list {
    display: flex;
    flex-direction: column;
    max-height: calc(100vh - 14rem);
    overflow: auto;
    padding: 0.4rem;
  }
  .item {
    display: grid;
    gap: 0.15rem;
    padding: 0.7rem 0.75rem;
    border-radius: 12px;
    color: var(--text);
    text-decoration: none;
  }
  .item strong {
    overflow: hidden;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .item span, .item time, .empty-list, .hint, .meta {
    color: var(--faint);
    font-size: 0.78rem;
  }
  .item span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .item:hover { background: rgba(244, 241, 234, 0.04); }
  .item[aria-current="page"] {
    background: rgba(240, 215, 164, 0.08);
    box-shadow: inset 2px 0 0 var(--gold);
  }
  .empty-list { margin: 0.8rem; }
  .column { min-height: 28rem; display: flex; flex-direction: column; }
  .meta {
    margin: 0;
    padding: 0.85rem 1.15rem 0;
  }
  .thread {
    flex: 1;
    max-height: calc(100vh - 24rem);
    overflow: auto;
    padding: 0.35rem 1.15rem 0.5rem;
  }
  .msg {
    padding: 0.85rem 0 0.85rem 0.85rem;
    border-bottom: 1px solid var(--line);
    border-left: 2px solid var(--sea);
  }
  .msg.user { border-left-color: var(--gold); }
  .who {
    display: block;
    margin-bottom: 0.28rem;
    color: var(--muted);
    font-size: 0.68rem;
    font-weight: 650;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .msg p {
    margin: 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    font-size: 1.06rem;
    line-height: 1.6;
  }
  .blank {
    display: grid;
    min-height: 12rem;
    place-items: center;
    text-align: center;
    color: var(--muted);
  }
  .blank p { max-width: 22rem; margin: 0; }
  .composer {
    border-top: 1px solid var(--line);
    padding: 0.9rem 1.05rem 1rem;
  }
  textarea {
    display: block;
    width: 100%;
    min-height: 6.5rem;
    margin: 0;
    padding: 0.8rem 0.9rem;
    resize: vertical;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #0c0e14;
    color: var(--text);
    font: 15px/1.55 ui-sans-serif, system-ui, sans-serif;
  }
  textarea:focus {
    outline: none;
    border-color: var(--sea);
    box-shadow: 0 0 0 3px rgba(143, 208, 200, 0.16);
  }
  .bar {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    align-items: center;
    margin-top: 0.75rem;
  }
  .status { margin: 0; color: var(--sea); font-size: 0.82rem; }
  button {
    appearance: none;
    border: 0;
    border-radius: 999px;
    padding: 0.58rem 1rem;
    background: var(--gold);
    color: #1c1508;
    font: inherit;
    font-size: 0.88rem;
    font-weight: 650;
    cursor: pointer;
  }
  button:hover { background: #f7e7c4; }
  button:disabled { opacity: 0.55; cursor: progress; }
  .bar-actions { display: flex; gap: 0.5rem; align-items: center; }
  button.ghost {
    background: transparent;
    color: var(--gold);
    border: 1px solid var(--gold-deep);
  }
  button.ghost:hover { background: rgba(244, 241, 234, 0.04); }
  .alert {
    margin: 0 0 0.75rem;
    padding: 0.65rem 0.8rem;
    border: 1px solid #8a4038;
    border-radius: 10px;
    background: #2a1614;
    color: #f0c2b8;
  }
  .warn {
    margin: 0.7rem 1.15rem 0;
    padding: 0.6rem 0.75rem;
    border: 1px solid var(--gold-deep);
    border-radius: 10px;
    color: var(--gold);
    font-size: 0.88rem;
  }
  .htmx-indicator { display: none; }
  .htmx-request .htmx-indicator, .htmx-request.htmx-indicator { display: inline; }
  @media (max-width: 800px) {
    #desk { grid-template-columns: 1fr; }
    .list { max-height: 14rem; }
    .mast, .tools, .bar { align-items: stretch; flex-direction: column; }
  }
</style>
"""

_SCRIPT = """
<script>
(function () {
  function scrollThread() {
    var thread = document.getElementById("thread");
    if (thread) thread.scrollTop = thread.scrollHeight;
  }
  function applyFilter() {
    var filter = document.getElementById("chat-filter");
    if (!filter) return;
    var query = filter.value.trim().toLowerCase();
    document.querySelectorAll("[data-chat-item]").forEach(function (item) {
      var title = (item.getAttribute("data-title") || "").toLowerCase();
      item.hidden = query !== "" && title.indexOf(query) === -1;
    });
  }
  function focusComposer() {
    var field = document.querySelector("[data-focus]");
    if (field) field.focus();
  }
  document.addEventListener("input", function (event) {
    if (event.target && event.target.id === "chat-filter") applyFilter();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      var form = document.getElementById("chat-form");
      if (!form) return;
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (!target || target.id !== "desk") return;
    scrollThread();
    applyFilter();
    focusComposer();
  });
  scrollThread();
  focusComposer();
})();
</script>
"""


def setup():
    component = Component()

    @component.get("/")
    def index(request: Request):
        blank = request.query_params.get("new") == "1"
        selected = "" if blank else request.query_params.get("id", "")
        return component.page("Chat", _page(_load(selected, blank=blank, fresh=blank)))

    @component.post("/send")
    async def send(request: Request):
        try:
            form = await _form(request)
        except ValueError as exc:
            return _respond(request, component, _page(_failed(str(exc))), _url(), ok=False)
        ident = form.get("id", "")
        draft = form.get("message", "")
        try:
            saved = await _send(ident, draft)
        except ChatError as exc:
            return _respond(
                request,
                component,
                _page(_failed(str(exc), ident, draft)),
                _url(ident),
                ok=False,
            )
        except Exception:
            return _respond(
                request,
                component,
                _page(_failed("The message could not be sent.", ident, draft)),
                _url(ident),
                ok=False,
            )
        return _respond(request, component, _page(_load(saved["id"], sent=True)), _url(saved["id"]), ok=True)

    @component.post("/delete")
    async def remove(request: Request):
        try:
            form = await _form(request)
        except ValueError as exc:
            return _respond(request, component, _page(_failed(str(exc))), _url(), ok=False)
        ident = form.get("id", "")
        try:
            _delete(ident)
        except ChatError as exc:
            return _respond(
                request,
                component,
                _page(_failed(str(exc), ident)),
                _url(ident),
                ok=False,
            )
        except Exception:
            return _respond(
                request,
                component,
                _page(_failed("The conversation could not be deleted.", ident)),
                _url(ident),
                ok=False,
            )
        view = _load("")
        nxt = view["convo"]["id"] if view.get("convo") else ""
        return _respond(request, component, _page(view), _url(nxt), ok=True)

    return component


def _respond(request: Request, component: Component, body: str, url: str, *, ok: bool):
    if request.headers.get("hx-request") == "true" or not ok:
        response = component.page("Chat", body)
        if request.headers.get("hx-request") == "true":
            response.headers["HX-Push-Url"] = url
        return response
    return RedirectResponse(url, status_code=303)


def _prefix() -> str:
    raw = os.environ.get("PROTEUS_ROUTE_PREFIX", "/chat").rstrip("/")
    return "" if raw == "/" else raw


def _url(ident: str = "", *, blank: bool = False) -> str:
    base = _prefix() or "/"
    if blank:
        return base + "?new=1"
    if not ident:
        return base
    return f"{base}?id={urllib.parse.quote(ident)}"


def _key(ident: str) -> str:
    return f"chat:{ident}"


def _valid_id(ident: str) -> bool:
    return len(ident) == 12 and all(char in "0123456789abcdef" for char in ident)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _pretty(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return ""
    return f"{parsed.day} {parsed.strftime('%b')} · {parsed.strftime('%H:%M')} UTC"


def _esc(value: str, quote: bool = False) -> str:
    return html.escape(value or "", quote=quote)


async def _form(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > 20_000:
        raise ValueError("That form is too large.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("That form could not be read.") from exc
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _settings() -> dict:
    empty = {"model": "", "token_set": False, "system_set": False, "ready": False}
    try:
        found = Store().get(CONFIG_KEY)
    except Exception:
        return empty
    if not found or not isinstance(found.get("value"), dict):
        return {"model": "", "token_set": False, "system_set": False, "ready": True}
    value = found["value"]
    model = value.get("model") if isinstance(value.get("model"), str) else ""
    system = value.get("system") if isinstance(value.get("system"), str) else ""
    return {
        "model": model.strip()[:200],
        "token_set": value.get("token_set") is True,
        "system_set": bool(system.strip()),
        "ready": True,
    }


def _list() -> list[dict]:
    rows = []
    for row in Store().find([TAG]):
        value = row.get("value")
        if not isinstance(value, dict):
            continue
        ident = value.get("id")
        if not isinstance(ident, str) or not _valid_id(ident) or row.get("key") != _key(ident):
            continue
        messages = value.get("messages") if isinstance(value.get("messages"), list) else []
        rows.append(
            {
                "id": ident,
                "title": value.get("title") if isinstance(value.get("title"), str) else "",
                "messages": [_message(item) for item in messages if _message(item)],
                "updated": value.get("updated") if isinstance(value.get("updated"), str) else "",
            }
        )
    rows.sort(key=lambda item: item["updated"], reverse=True)
    return rows


def _message(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    role = item.get("role")
    content = item.get("content")
    if role not in ("user", "assistant") or not isinstance(content, str) or not content:
        return None
    message = {"role": role, "content": content[:100_000]}
    model = item.get("model")
    if isinstance(model, str) and model.strip():
        message["model"] = model.strip()[:200]
    return message


def _load(
    selected: str,
    *,
    blank: bool = False,
    fresh: bool = False,
    sent: bool = False,
    error: str = "",
    draft: str = "",
) -> dict:
    settings = _settings()
    try:
        convos = _list()
    except Exception:
        return {
            "convos": [],
            "convo": None,
            "settings": settings,
            "error": error or "Conversations could not be read.",
            "draft": draft,
            "fresh": fresh,
            "sent": False,
        }
    convo = None
    if _valid_id(selected):
        convo = next((item for item in convos if item["id"] == selected), None)
        if convo is None and not error:
            error = "That conversation could not be found."
            blank = True
    elif selected and not error:
        error = "That conversation could not be found."
        blank = True
    if convo is None and convos and not blank and not draft and not error:
        convo = convos[0]
    return {
        "convos": convos,
        "convo": convo,
        "settings": settings,
        "error": error,
        "draft": draft,
        "fresh": fresh,
        "sent": sent,
    }


def _failed(message: str, selected: str = "", draft: str = "") -> dict:
    return _load(selected, error=message, draft=draft, blank=not _valid_id(selected))


def _delete(ident: str) -> None:
    if not _valid_id(ident):
        raise ChatError("That conversation could not be found.")
    if Store().get(_key(ident)) is None:
        raise ChatError("That conversation could not be found.")
    Store().delete(_key(ident))


def _new_id() -> str:
    for _ in range(5):
        ident = uuid.uuid4().hex[:12]
        if Store().get(_key(ident)) is None:
            return ident
    raise ChatError("A conversation could not be created.")


def _title_from(messages: list[dict]) -> str:
    for item in messages:
        if item.get("role") != "user":
            continue
        text = " ".join(item.get("content", "").split())
        if text:
            return text[:48]
    return "New chat"


def _outbound(messages: list[dict]) -> list[dict]:
    cleaned = []
    for item in messages[-MAX_MESSAGES:]:
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        cleaned.append({"role": role, "content": text[:MAX_CONTENT]})
    return cleaned


def _clean_draft(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\x00" in text:
        raise ChatError("Messages cannot contain NUL.")
    if not text:
        raise ChatError("Write a message first.")
    if len(text) > MAX_CONTENT:
        raise ChatError("That message is too long.")
    return text


async def _send(ident: str, draft: str) -> dict:
    text = _clean_draft(draft)
    existing = None
    if ident:
        if not _valid_id(ident):
            raise ChatError("That conversation could not be found.")
        found = Store().get(_key(ident))
        if not found or not isinstance(found.get("value"), dict):
            raise ChatError("That conversation could not be found.")
        value = found["value"]
        messages = value.get("messages") if isinstance(value.get("messages"), list) else []
        existing = {
            "id": ident,
            "title": value.get("title") if isinstance(value.get("title"), str) else "",
            "messages": [item for item in (_message(raw) for raw in messages) if item],
        }
    history = existing["messages"] if existing else []
    outbound = _outbound(history + [{"role": "user", "content": text}])
    if not any(item["role"] == "user" for item in outbound):
        raise ChatError("Write a message first.")
    result = await _complete(outbound)
    stored = _merge_redacted(outbound, result["messages"])
    assistant = {"role": "assistant", "content": result["content"][:100_000]}
    if result["model"]:
        assistant["model"] = result["model"]
    stored.append(assistant)
    return _persist(existing, stored)


def _persist(existing: dict | None, messages: list[dict]) -> dict:
    ident = existing["id"] if existing else _new_id()
    title = existing.get("title", "").strip() if existing else ""
    if not title:
        title = _title_from(messages)
    convo = {
        "id": ident,
        "title": title[:80],
        "messages": messages[-MAX_MESSAGES:],
        "updated": _now(),
    }
    Store().put(_key(ident), convo, [TAG])
    return convo


async def _complete(messages: list[dict]) -> dict:
    try:
        result = await asyncio.wait_for(
            bus.workflow("llm", "CompleteWorkflow", {"messages": messages}),
            timeout=CALL_TIMEOUT,
        )
    except TimeoutError:
        raise ChatError("The model did not respond in time.") from None
    except ChatError:
        raise
    except Exception as exc:
        raise ChatError(_failure_text(exc)) from None
    if not isinstance(result, dict):
        raise ChatError("The model returned an unexpected response.")
    payload = result.get("result")
    if not isinstance(payload, dict):
        raise ChatError("The model returned an unexpected response.")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ChatError("The model returned an empty reply.")
    model = payload.get("model")
    model = model.strip()[:200] if isinstance(model, str) else ""
    if any(ch in model for ch in "\r\n\x00"):
        model = ""
    return {"content": content, "model": model, "messages": payload.get("messages")}


def _merge_redacted(history: list[dict], redacted: object) -> list[dict]:
    cleaned = []
    if isinstance(redacted, list):
        for item in redacted:
            message = _message(item)
            if message is None:
                cleaned = []
                break
            cleaned.append(message)
    if cleaned and len(cleaned) <= len(history):
        head_len = len(history) - len(cleaned)
        tail = history[head_len:]
        if all(original.get("role") == safe["role"] for original, safe in zip(tail, cleaned)):
            merged = [dict(item) for item in history[:head_len]]
            for original, safe in zip(tail, cleaned):
                updated = dict(original)
                updated["content"] = safe["content"]
                merged.append(updated)
            return merged
    if cleaned:
        return cleaned
    return [dict(item) for item in history]


def _failure_text(exc: BaseException) -> str:
    current: BaseException | None = exc
    seen = 0
    timed_out = False
    unavailable = False
    while current is not None and seen < 8:
        name = type(current).__name__.lower()
        if "timeout" in name:
            timed_out = True
        if name in {"rpcerror", "rpcstatuscode"} or "connect" in name:
            unavailable = True
        message = getattr(current, "message", None)
        candidates = []
        if isinstance(message, str):
            candidates.append(message)
        candidates.append(str(current))
        for candidate in candidates:
            text = " ".join(candidate.split())
            if text.startswith(_SAFE_PREFIXES):
                return text[:400]
        nxt = getattr(current, "cause", None)
        if nxt is None or nxt is current:
            nxt = current.__cause__
        current = nxt if isinstance(nxt, BaseException) else None
        seen += 1
    if timed_out:
        return "The model did not respond in time."
    if unavailable:
        return "The model service is not available. Try again in a moment."
    return "The model could not be reached. Check LLM settings and try again."


def _display_title(convo: dict) -> str:
    title = convo.get("title", "").strip()
    return title or "New chat"


def _excerpt(convo: dict) -> str:
    messages = convo.get("messages") or []
    if not messages:
        return "Empty"
    text = " ".join(messages[-1].get("content", "").split())
    return text[:72] or "Empty"


def _who(message: dict, fallback: str) -> str:
    if message.get("role") == "user":
        return "You"
    model = message.get("model") if isinstance(message.get("model"), str) else ""
    return model or fallback or "Model"


def _page(view: dict) -> str:
    prefix = _prefix()
    settings = view.get("settings") or {}
    convo = view.get("convo")
    selected = convo["id"] if convo else ""
    items = []
    for item in view.get("convos") or []:
        current = ' aria-current="page"' if item["id"] == selected else ""
        title = _display_title(item)
        href = _esc(_url(item["id"]), True)
        items.append(
            f'<a class="item" href="{href}" hx-get="{href}" hx-target="#desk" hx-select="#desk" '
            f'hx-swap="outerHTML" hx-push-url="true" data-chat-item '
            f'data-title="{_esc(title, True)}"{current}>'
            f"<strong>{_esc(title)}</strong>"
            f"<span>{_esc(_excerpt(item))}</span>"
            f"<time>{_esc(_pretty(item.get('updated', '')))}</time></a>"
        )
    listing = "".join(items) if items else '<p class="empty-list">Nothing here yet.</p>'
    new_url = _esc(_url(blank=True), True)
    return _CSS + f"""
<main class="shell">
  <header class="mast">
    <div>
      <p class="kicker"><span class="mark" aria-hidden="true"></span> openrouter</p>
      <h1>Chat</h1>
    </div>
    <nav class="links">
      <a href="/llm">Settings</a>
      <a href="/">Home</a>
    </nav>
  </header>
  <div class="tools">
    <a class="new" href="{new_url}" hx-get="{new_url}" hx-target="#desk" hx-select="#desk" hx-swap="outerHTML" hx-push-url="true">New chat</a>
    <input id="chat-filter" class="filter" type="search" placeholder="Filter" autocomplete="off" aria-label="Filter conversations">
  </div>
  {_desk(view, listing, prefix, settings)}
</main>
""" + _SCRIPT


def _desk(view: dict, listing: str, prefix: str, settings: dict) -> str:
    convo = view.get("convo")
    model = settings.get("model") or ""
    bits = []
    if model:
        bits.append(model)
    else:
        bits.append("No model selected")
    if settings.get("system_set"):
        bits.append("system prompt on")
    if not settings.get("ready"):
        bits.append("settings unread")
    meta = " · ".join(bits)
    warning = ""
    if settings.get("ready") and not settings.get("token_set"):
        warning = '<p class="warn">No token is saved. <a href="/llm">Add one in LLM settings</a> before sending.</p>'
    error = f'<p class="alert" role="alert">{_esc(view.get("error") or "")}</p>' if view.get("error") else ""
    thread = _thread(convo, model)
    focus = " data-focus=\"1\"" if view.get("fresh") or view.get("sent") or view.get("draft") else ""
    ident = f'<input type="hidden" name="id" value="{_esc(convo["id"], True)}">' if convo else ""
    send = _esc(prefix + "/send", True)
    status = "Sent" if view.get("sent") else "Ctrl or ⌘ Enter to send"
    delete_form = ""
    delete_button = ""
    if convo:
        remove = _esc(prefix + "/delete", True)
        delete_form = (
            f'<form id="chat-delete" method="post" action="{remove}">'
            f'<input type="hidden" name="id" value="{_esc(convo["id"], True)}"></form>'
        )
        delete_button = (
            f'<button type="submit" class="ghost" form="chat-delete" hx-post="{remove}" '
            f'hx-target="#desk" hx-select="#desk" hx-swap="outerHTML" hx-include="#chat-delete" '
            f'hx-confirm="Delete this conversation?" hx-disabled-elt="closest form">Delete</button>'
        )
    return f"""
<div id="desk">
  <aside class="list">{listing}</aside>
  <section class="column">
    <p class="meta">{_esc(meta)}</p>
    {warning}
    <div class="thread" id="thread">{thread}</div>
    <form id="chat-form" class="composer" method="post" action="{send}" hx-post="{send}" hx-target="#desk" hx-select="#desk" hx-swap="outerHTML" hx-disabled-elt="find button, find textarea">
      {error}
      {ident}
      <label class="hint" for="chat-message">Message</label>
      <textarea id="chat-message" name="message" maxlength="{MAX_CONTENT}" placeholder="Write a message" required{focus}>{_esc(view.get("draft") or "")}</textarea>
      <div class="bar">
        <p class="status"><span>{_esc(status)}</span> <span class="htmx-indicator">Thinking…</span></p>
        <div class="bar-actions">
          {delete_button}
          <button type="submit">Send</button>
        </div>
      </div>
    </form>
    {delete_form}
  </section>
</div>
"""


def _thread(convo: dict | None, fallback: str) -> str:
    messages = convo.get("messages") if convo else None
    if not messages:
        return '<div class="blank"><p>Write something. Replies use the model saved in LLM settings and can take a minute.</p></div>'
    parts = []
    for message in messages:
        role = "user" if message.get("role") == "user" else "assistant"
        parts.append(
            f'<article class="msg {role}"><span class="who">{_esc(_who(message, fallback))}</span>'
            f"<p>{_esc(message.get('content') or '')}</p></article>"
        )
    return "".join(parts)
