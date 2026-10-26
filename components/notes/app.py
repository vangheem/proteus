from __future__ import annotations

import html
import os
import urllib.parse
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import RedirectResponse
from proteus import Component, Store

TAG = "note"


def setup():
    component = Component()

    @component.get("/")
    def index(request: Request):
        selected = request.query_params.get("id", "")
        return component.page("Notes", _page(_load(selected)))

    @component.post("/new")
    async def create(request: Request):
        try:
            note = _create()
        except Exception:
            return _respond(request, component, _page(_failed("A note could not be created.")), _url())
        return _respond(request, component, _page(_load(note["id"], fresh=True)), _url(note["id"]))

    @component.post("/save")
    async def save(request: Request):
        form = await _form(request)
        ident = form.get("id", "")
        try:
            note = _save(ident, form.get("title", ""), form.get("body", ""))
        except ValueError as exc:
            return _respond(request, component, _page(_failed(str(exc), ident)), _url(ident))
        except Exception:
            return _respond(request, component, _page(_failed("The note could not be saved.", ident)), _url(ident))
        return _respond(request, component, _page(_load(note["id"], saved=True)), _url(note["id"]))

    @component.post("/delete")
    async def remove(request: Request):
        form = await _form(request)
        ident = form.get("id", "")
        try:
            _delete(ident)
        except ValueError as exc:
            return _respond(request, component, _page(_failed(str(exc), ident)), _url(ident))
        except Exception:
            return _respond(request, component, _page(_failed("The note could not be deleted.", ident)), _url(ident))
        view = _load("")
        nxt = view["note"]["id"] if view.get("note") else ""
        return _respond(request, component, _page(view), _url(nxt))

    return component


def _respond(request: Request, component: Component, body: str, url: str):
    if request.headers.get("hx-request") == "true":
        response = component.page("Notes", body)
        response.headers["HX-Push-Url"] = url
        return response
    return RedirectResponse(url, status_code=303)


def _prefix() -> str:
    raw = os.environ.get("PROTEUS_ROUTE_PREFIX", "/notes").rstrip("/")
    return "" if raw == "/" else raw


def _url(ident: str = "") -> str:
    base = _prefix() or "/"
    if not ident:
        return base
    return f"{base}?id={urllib.parse.quote(ident)}"


def _key(ident: str) -> str:
    return f"note:{ident}"


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


def _create() -> dict:
    note = {"id": uuid.uuid4().hex[:12], "title": "", "body": "", "updated": _now()}
    Store().put(_key(note["id"]), note, [TAG])
    return note


def _save(ident: str, title: str, body: str) -> dict:
    if not _valid_id(ident):
        raise ValueError("That note could not be found.")
    existing = Store().get(_key(ident))
    if existing is None:
        raise ValueError("That note could not be found.")
    clean_title = title.replace("\r\n", "\n").replace("\r", "\n").strip()
    clean_body = body.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in clean_title or "\x00" in clean_body:
        raise ValueError("Notes cannot contain NUL.")
    if len(clean_title) > 200:
        raise ValueError("The title is too long.")
    if len(clean_body) > 100_000:
        raise ValueError("The note is too long.")
    if "\n" in clean_title:
        clean_title = " ".join(clean_title.splitlines()).strip()
    note = {
        "id": ident,
        "title": clean_title,
        "body": clean_body,
        "updated": _now(),
    }
    Store().put(_key(ident), note, [TAG])
    return note


def _delete(ident: str) -> None:
    if not _valid_id(ident):
        raise ValueError("That note could not be found.")
    Store().delete(_key(ident))


def _list() -> list[dict]:
    notes = []
    for row in Store().find([TAG]):
        value = row.get("value")
        if not isinstance(value, dict):
            continue
        ident = value.get("id")
        if not isinstance(ident, str) or not _valid_id(ident) or row.get("key") != _key(ident):
            continue
        notes.append(
            {
                "id": ident,
                "title": value.get("title") if isinstance(value.get("title"), str) else "",
                "body": value.get("body") if isinstance(value.get("body"), str) else "",
                "updated": value.get("updated") if isinstance(value.get("updated"), str) else "",
            }
        )
    notes.sort(key=lambda item: item["updated"], reverse=True)
    return notes


def _load(selected: str, *, saved: bool = False, fresh: bool = False, error: str = "") -> dict:
    try:
        notes = _list()
    except Exception:
        return {"notes": [], "note": None, "error": "Notes could not be read.", "saved": False, "fresh": False}
    note = None
    if _valid_id(selected):
        note = next((item for item in notes if item["id"] == selected), None)
    if note is None and notes and not error:
        note = notes[0]
    return {"notes": notes, "note": note, "error": error, "saved": saved, "fresh": fresh}


def _failed(message: str, selected: str = "") -> dict:
    view = _load(selected)
    view["error"] = message
    view["saved"] = False
    view["fresh"] = False
    return view


async def _form(request: Request) -> dict[str, str]:
    raw = await request.body()
    parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _esc(value: str, quote: bool = False) -> str:
    return html.escape(value, quote=quote)


def _display_title(note: dict) -> str:
    title = note["title"].strip()
    if title:
        return title
    first = note["body"].strip().splitlines()
    if first and first[0].strip():
        return first[0].strip()[:48]
    return "Untitled"


def _excerpt(note: dict) -> str:
    text = " ".join(note["body"].split())
    if not text:
        return "Empty"
    return text[:72]


def _page(view: dict) -> str:
    prefix = _prefix()
    note = view.get("note")
    selected = note["id"] if note else ""
    items = []
    for item in view["notes"]:
        current = ' aria-current="page"' if item["id"] == selected else ""
        title = _display_title(item)
        items.append(
            f'<a class="item" href="{_esc(_url(item["id"]), True)}" '
            f'hx-get="{_esc(_url(item["id"]), True)}" hx-target="#desk" hx-select="#desk" '
            f'hx-swap="outerHTML" hx-push-url="true" data-note-item '
            f'data-title="{_esc(title, True)}"{current}>'
            f'<strong>{_esc(title)}</strong>'
            f'<span>{_esc(_excerpt(item))}</span>'
            f'<time>{_esc(_pretty(item["updated"]))}</time></a>'
        )
    if items:
        listing = "".join(items)
    else:
        listing = '<p class="empty-list">Nothing here yet.</p>'
    error = f'<p class="notice" role="alert">{_esc(view["error"])}</p>' if view.get("error") else ""
    return f"""
<style>
  :root {{
    --ink: #101218;
    --text: #f4f1ea;
    --muted: #a39888;
    --faint: #7c7468;
    --line: #2c3140;
    --gold: #f0d7a4;
    --gold-deep: #8a6a32;
    --sea: #8fd0c8;
    --panel: rgba(244, 241, 234, 0.035);
  }}
  * {{ box-sizing: border-box; }}
  body {{
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
  }}
  ::selection {{ background: rgba(240, 215, 164, 0.32); }}
  .shell {{ max-width: 68rem; margin: 0 auto; }}
  .mast {{
    display: flex;
    justify-content: space-between;
    gap: 1.5rem;
    align-items: end;
    margin-bottom: 1.25rem;
  }}
  .kicker {{
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 0;
    color: var(--gold);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }}
  .mark {{
    width: 0.8rem;
    height: 0.8rem;
    border-radius: 42% 58% 48% 52%;
    background: linear-gradient(135deg, var(--sea), var(--gold));
    box-shadow: 0 0 16px rgba(143, 208, 200, 0.4);
  }}
  h1 {{
    margin: 0.45rem 0 0;
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    font-size: clamp(2.4rem, 5vw, 3.4rem);
    font-weight: 500;
    letter-spacing: -0.03em;
    line-height: 0.95;
  }}
  .links {{ display: flex; gap: 1rem; font-size: 0.88rem; }}
  .shell a {{ color: var(--gold); text-underline-offset: 0.18em; }}
  .shell a:hover {{ color: #f7e7c4; }}
  .tools {{
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    align-items: center;
    margin-bottom: 0.85rem;
  }}
  .shell button, .filter {{
    appearance: none;
    border: 0;
    border-radius: 999px;
    font: inherit;
  }}
  .shell button {{
    padding: 0.55rem 0.95rem;
    background: var(--gold);
    color: #1c1508;
    font-size: 0.88rem;
    font-weight: 650;
    cursor: pointer;
  }}
  .shell button:hover {{ background: #f7e7c4; }}
  .shell button.ghost {{
    background: transparent;
    color: var(--gold);
    border: 1px solid var(--gold-deep);
  }}
  .shell button:disabled {{ opacity: 0.55; cursor: progress; }}
  .filter {{
    width: min(16rem, 100%);
    padding: 0.55rem 0.85rem;
    border: 1px solid var(--line);
    background: #0c0e14;
    color: var(--text);
  }}
  .filter:focus {{ outline: none; border-color: var(--sea); }}
  #desk {{
    display: grid;
    grid-template-columns: minmax(14rem, 18rem) minmax(0, 1fr);
    gap: 1rem;
    align-items: start;
  }}
  .list, .editor {{
    border: 1px solid var(--line);
    border-radius: 18px;
    background: var(--panel);
  }}
  .list {{
    display: flex;
    flex-direction: column;
    max-height: calc(100vh - 14rem);
    overflow: auto;
    padding: 0.4rem;
  }}
  .item {{
    display: grid;
    gap: 0.15rem;
    padding: 0.7rem 0.75rem;
    border-radius: 12px;
    color: var(--text);
    text-decoration: none;
  }}
  .item strong {{
    overflow: hidden;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .item span, .item time, .empty-list, .hint {{
    color: var(--faint);
    font-size: 0.78rem;
  }}
  .item span {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .item:hover {{ background: rgba(244, 241, 234, 0.04); }}
  .item[aria-current="page"] {{
    background: rgba(240, 215, 164, 0.08);
    box-shadow: inset 2px 0 0 var(--gold);
  }}
  .empty-list {{ margin: 0.8rem; }}
  .editor {{ padding: 1.15rem 1.25rem 1rem; min-height: 28rem; }}
  .editor input[name="title"], .editor textarea {{
    display: block;
    width: 100%;
    border: 0;
    background: transparent;
    color: var(--text);
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
  }}
  .editor input[name="title"] {{
    margin: 0 0 0.35rem;
    padding: 0 0 0.55rem;
    border-bottom: 1px solid var(--line);
    font-size: clamp(1.7rem, 3vw, 2.15rem);
    letter-spacing: -0.03em;
  }}
  .editor textarea {{
    min-height: 22rem;
    margin: 0.85rem 0 0;
    padding: 0;
    resize: vertical;
    font-size: 1.12rem;
    line-height: 1.65;
  }}
  .editor input:focus, .editor textarea:focus {{ outline: none; }}
  .editor input::placeholder, .editor textarea::placeholder {{ color: var(--faint); }}
  .editor-bar {{
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    align-items: center;
    margin-top: 0.9rem;
  }}
  .status {{ margin: 0; color: var(--sea); font-size: 0.82rem; }}
  .notice {{
    margin: 0 0 0.8rem;
    padding: 0.65rem 0.8rem;
    border: 1px solid #8a4038;
    border-radius: 10px;
    background: #2a1614;
    color: #f0c2b8;
  }}
  .blank {{
    display: grid;
    min-height: 24rem;
    place-items: center;
    text-align: center;
  }}
  .blank p {{ max-width: 18rem; margin: 0; color: var(--muted); }}
  @media (max-width: 800px) {{
    #desk {{ grid-template-columns: 1fr; }}
    .list {{ max-height: 16rem; }}
    .mast, .tools, .editor-bar {{ align-items: stretch; flex-direction: column; }}
  }}
</style>
<main class="shell">
  <header class="mast">
    <div>
      <p class="kicker"><span class="mark" aria-hidden="true"></span> notebook</p>
      <h1>Notes</h1>
    </div>
    <nav class="links">
      <a href="/">Home</a>
      <a href="/chat">Chat</a>
      <a href="/llm">LLM</a>
      <a href="/__proteus/introspect">Introspection</a>
    </nav>
  </header>
  <div class="tools">
    <form method="post" action="{_esc(prefix + '/new', True)}" hx-post="{_esc(prefix + '/new', True)}" hx-target="#desk" hx-select="#desk" hx-swap="outerHTML" hx-disabled-elt="find button">
      <button type="submit">New note</button>
    </form>
    <input id="note-filter" class="filter" type="search" placeholder="Filter" autocomplete="off" aria-label="Filter notes">
  </div>
  {_desk(view, listing, error, prefix)}
</main>
<script>
(function () {{
  function count() {{
    var body = document.querySelector("[data-note-body]");
    var out = document.getElementById("note-count");
    if (!body || !out) return;
    var words = body.value.trim() ? body.value.trim().split(/\\s+/).length : 0;
    out.textContent = " · " + (words === 1 ? "1 word" : words + " words");
  }}
  document.addEventListener("input", function (event) {{
    if (event.target && event.target.id === "note-filter") {{
      var query = event.target.value.trim().toLowerCase();
      document.querySelectorAll("[data-note-item]").forEach(function (item) {{
        var title = (item.getAttribute("data-title") || "").toLowerCase();
        item.hidden = query !== "" && title.indexOf(query) === -1;
      }});
      return;
    }}
    if (event.target && event.target.name === "body") count();
  }});
  document.addEventListener("keydown", function (event) {{
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {{
      var form = document.getElementById("note-form");
      if (!form) return;
      event.preventDefault();
      form.requestSubmit();
    }}
  }});
  document.body.addEventListener("htmx:afterSwap", function (event) {{
    var target = event.detail && event.detail.target;
    if (!target || target.id !== "desk") return;
    count();
    var filter = document.getElementById("note-filter");
    if (filter) filter.dispatchEvent(new Event("input", {{bubbles: true}}));
    var field = document.querySelector("[data-focus]");
    if (field) field.focus();
  }});
  count();
}})();
</script>
"""


def _desk(view: dict, listing: str, error: str, prefix: str) -> str:
    note = view.get("note")
    if note is None:
        editor = f"""
<section class="editor">
  <div class="blank">
    {error}
    <p>Write something down. It stays with the rest of the app.</p>
  </div>
</section>
"""
    else:
        ident = note["id"]
        focus = " data-focus=\"1\"" if view.get("fresh") else ""
        status = "Saved" if view.get("saved") else "Ctrl or ⌘ Enter to save"
        editor = f"""
<section class="editor">
  {error}
  <form id="note-form" method="post" action="{_esc(prefix + '/save', True)}" hx-post="{_esc(prefix + '/save', True)}" hx-target="#desk" hx-select="#desk" hx-swap="outerHTML" hx-disabled-elt="find button">
    <input type="hidden" name="id" value="{_esc(ident, True)}">
    <input id="note-title-{_esc(ident, True)}" name="title" value="{_esc(note['title'], True)}" maxlength="200" placeholder="Title" autocomplete="off" hx-preserve{focus}>
    <textarea id="note-body-{_esc(ident, True)}" name="body" data-note-body maxlength="100000" placeholder="Write…" hx-preserve>{_esc(note['body'])}</textarea>
    <div class="editor-bar">
      <p class="status"><span id="note-status">{_esc(status)}</span><span id="note-count"></span></p>
      <button type="submit">Save</button>
    </div>
  </form>
  <form method="post" action="{_esc(prefix + '/delete', True)}" hx-post="{_esc(prefix + '/delete', True)}" hx-target="#desk" hx-select="#desk" hx-swap="outerHTML" hx-confirm="Delete this note?" hx-disabled-elt="find button">
    <input type="hidden" name="id" value="{_esc(ident, True)}">
    <button type="submit" class="ghost">Delete</button>
  </form>
</section>
"""
    return f'<div id="desk"><aside class="list">{listing}</aside>{editor}</div>'
