"""OpenRouter settings.

The token is encrypted by vault before it is stored. This page never puts the
token back into HTML. Chat and other components call CompleteWorkflow; they do
not receive the token.
"""

from __future__ import annotations

import html
import os
import urllib.parse

from fastapi import Request
from fastapi.responses import RedirectResponse
from proteus import Component

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
    "Save a token",
)

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
    padding: 2.5rem 1.25rem 4rem;
    background:
      radial-gradient(820px 420px at 0% -12%, rgba(143, 208, 200, 0.14), transparent 56%),
      radial-gradient(560px 340px at 100% 0%, rgba(240, 215, 164, 0.1), transparent 50%),
      var(--ink);
    color: var(--text);
    font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: rgba(240, 215, 164, 0.32); }
  .shell { max-width: 42rem; margin: 0 auto; }
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
    font-size: clamp(2.5rem, 6vw, 3.6rem);
    font-weight: 500;
    letter-spacing: -0.03em;
    line-height: 0.95;
  }
  .links { display: flex; gap: 1rem; font-size: 0.88rem; }
  .shell a { color: var(--gold); text-underline-offset: 0.18em; }
  .shell a:hover { color: #f7e7c4; }
  .lede {
    margin: 0 0 1.15rem;
    color: var(--muted);
    max-width: 38rem;
  }
  #panel {
    border: 1px solid var(--line);
    border-radius: 18px;
    background:
      linear-gradient(180deg, rgba(240, 215, 164, 0.05), transparent 28%),
      var(--panel);
    padding: 1.15rem 1.15rem 1rem;
  }
  .status {
    margin: 0 0 1rem;
    color: var(--sea);
    font-size: 0.92rem;
  }
  .status.warn { color: var(--gold); }
  label {
    display: block;
    margin: 0.85rem 0 0.4rem;
    color: var(--muted);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
  input, textarea {
    display: block;
    width: 100%;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #0c0e14;
    color: var(--text);
    font: 15px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  input { padding: 0.7rem 0.85rem; }
  textarea {
    min-height: 7.5rem;
    padding: 0.8rem 0.85rem;
    resize: vertical;
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    font-size: 1.05rem;
  }
  input:focus, textarea:focus {
    outline: none;
    border-color: var(--sea);
    box-shadow: 0 0 0 3px rgba(143, 208, 200, 0.16);
  }
  .hint, .field-note {
    margin: 0.4rem 0 0;
    color: var(--faint);
    font-size: 0.8rem;
  }
  .token-row { display: flex; gap: 0.5rem; align-items: center; }
  .token-row input { flex: 1; }
  .actions, .secondary {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    align-items: center;
  }
  .actions { margin-top: 1.1rem; }
  .secondary { margin-top: 0.75rem; }
  button {
    appearance: none;
    border: 0;
    border-radius: 999px;
    padding: 0.62rem 1.05rem;
    background: var(--gold);
    color: #1c1508;
    font: inherit;
    font-size: 0.9rem;
    font-weight: 650;
    cursor: pointer;
  }
  button:hover { background: #f7e7c4; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.ghost, button.danger {
    background: transparent;
    color: var(--gold);
    border: 1px solid var(--gold-deep);
  }
  button.danger { color: #f0c2b8; border-color: #8a4038; }
  button.ghost:hover, button.danger:hover { background: rgba(244, 241, 234, 0.04); }
  .notice, .alert {
    margin: 0 0 0.9rem;
    padding: 0.65rem 0.8rem;
    border-radius: 10px;
  }
  .notice { border: 1px solid #2f6f68; background: #12211f; color: var(--sea); }
  .alert { border: 1px solid #8a4038; background: #2a1614; color: #f0c2b8; }
  .htmx-indicator { display: none; }
  .htmx-request .htmx-indicator, .htmx-request.htmx-indicator { display: inline; }
  @media (max-width: 640px) {
    .mast, .actions, .secondary { align-items: stretch; flex-direction: column; }
  }
</style>
"""

_SCRIPT = """
<script>
(function () {
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-token-toggle]");
    if (toggle) {
      var input = document.getElementById("llm-token");
      if (!input) return;
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      toggle.textContent = show ? "Hide" : "Show";
      return;
    }
    var skip = event.target.closest("[data-skip-token]");
    if (!skip) return;
    var field = document.getElementById("llm-token");
    if (!field) return;
    field.disabled = true;
    setTimeout(function () { field.disabled = false; }, 0);
  }, true);
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || (!event.metaKey && !event.ctrlKey)) return;
    var form = document.getElementById("settings-form");
    if (!form) return;
    event.preventDefault();
    form.requestSubmit();
  });
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (!target || target.id !== "panel") return;
    if (target.getAttribute("data-clear-token") === "1") {
      var input = document.getElementById("llm-token");
      if (input) input.value = "";
    }
  });
})();
</script>
"""


def setup():
    component = Component()

    @component.get("/")
    def index():
        return component.page("OpenRouter", _render(_view()))

    @component.post("/save")
    async def save(request: Request):
        try:
            form = await _form(request)
        except ValueError as exc:
            return _finish(request, component, _view(error=str(exc)), ok=False)
        token = form.pop("token", "")
        submitted = _safe_submitted(_public_fields(form), token)
        try:
            vault.save(
                base_url=form.get("base_url", ""),
                model=form.get("model", ""),
                system=form.get("system", ""),
                token=token,
            )
            replaced = bool(token.strip())
        except vault.SettingsError as exc:
            return _finish(
                request,
                component,
                _view(error=str(exc), submitted=submitted),
                ok=False,
            )
        except Exception:
            return _finish(
                request,
                component,
                _view(error="Settings could not be saved.", submitted=submitted),
                ok=False,
            )
        finally:
            token = ""
        notice = (
            "Saved. The token is encrypted and is not shown again."
            if replaced
            else "Saved."
        )
        return _finish(request, component, _view(notice=notice, clear_token=True), ok=True)

    @component.post("/models")
    async def models(request: Request):
        try:
            form = await _form(request)
        except ValueError as exc:
            return _finish(request, component, _view(error=str(exc)), ok=False)
        typed = bool(form.pop("token", "").strip())
        submitted = _safe_submitted(_public_fields(form))
        if submitted is None:
            return _finish(
                request,
                component,
                _view(error="The token must stay in the token field."),
                ok=False,
            )
        try:
            found, used, used_saved_url = _load_models(submitted["base_url"])
        except vault.SettingsError as exc:
            return _finish(
                request,
                component,
                _view(error=str(exc), submitted=submitted),
                ok=False,
            )
        except RuntimeError as exc:
            text = str(exc)
            message = text if text.startswith(_SAFE_PREFIXES) else "Models could not be loaded."
            return _finish(
                request,
                component,
                _view(error=message, submitted=submitted),
                ok=False,
            )
        except Exception:
            return _finish(
                request,
                component,
                _view(error="Models could not be loaded.", submitted=submitted),
                ok=False,
            )
        count = len(found)
        noun = "model" if count == 1 else "models"
        notice = f"Loaded {count} {noun}."
        if not used_saved_url:
            notice += " Save to use this base URL for chat."
        if typed:
            notice += " The token field was ignored; save it before it is stored."
        shown = dict(submitted)
        shown["base_url"] = used
        return _finish(
            request,
            component,
            _view(notice=notice, submitted=shown),
            ok=True,
        )

    @component.post("/clear")
    async def clear(request: Request):
        try:
            form = await _form(request)
        except ValueError as exc:
            return _finish(request, component, _view(error=str(exc)), ok=False)
        form.pop("token", None)
        submitted = _safe_submitted(_public_fields(form))
        try:
            vault.clear_token()
        except Exception:
            return _finish(
                request,
                component,
                _view(error="The token could not be removed.", submitted=submitted),
                ok=False,
            )
        return _finish(
            request,
            component,
            _view(
                notice="Token removed. Chat cannot call the model until a new one is saved.",
                submitted=submitted,
                clear_token=True,
            ),
            ok=True,
        )

    return component


def _finish(request: Request, component: Component, view: dict, *, ok: bool):
    if request.headers.get("hx-request") == "true" or not ok:
        response = component.page("OpenRouter", _render(view))
        if request.headers.get("hx-request") == "true":
            response.headers["HX-Push-Url"] = _url()
        return response
    return RedirectResponse(_url(), status_code=303)


def _prefix() -> str:
    raw = os.environ.get("PROTEUS_ROUTE_PREFIX", "/llm").rstrip("/")
    return "" if raw == "/" else raw


def _url() -> str:
    return _prefix() or "/"


async def _form(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > 32_000:
        raise ValueError("That form is too large.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("That form could not be read.") from exc
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _public_fields(form: dict[str, str]) -> dict[str, str]:
    return {
        "base_url": _clip(form.get("base_url", ""), 300),
        "model": _clip(form.get("model", ""), 200),
        "system": _clip(form.get("system", ""), 4000),
    }


def _clip(value: str, limit: int) -> str:
    text = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return text[:limit]


def _safe_submitted(fields: dict[str, str], provided: str = "") -> dict[str, str] | None:
    secret = provided.strip()
    if not secret:
        try:
            view = vault.public_view()
        except Exception:
            return None
        if view.get("token_set"):
            try:
                secret = vault.unlock()
            except Exception:
                return None
    if secret and any(secret in value for value in fields.values()):
        return None
    return fields


def _load_models(base_url: str) -> tuple[list[dict], str, bool]:
    cfg = vault.public_view()
    if not cfg["token_set"]:
        raise vault.SettingsError("Save a token before loading models.")
    token = vault.unlock()
    try:
        saved = cfg["base_url"]
        used = vault.normalize_base_url(base_url or saved)
        found = client.list_models(used, token)
        clean = [
            item
            for item in found
            if token not in item.get("id", "") and token not in item.get("name", "")
        ]
        vault.save_model_catalog(clean)
        return clean, used, used == saved
    finally:
        token = ""


def _view(
    *,
    error: str = "",
    notice: str = "",
    submitted: dict | None = None,
    clear_token: bool = False,
) -> dict:
    try:
        public = vault.public_view()
        models = vault.load_model_catalog()
    except Exception:
        public = {
            "base_url": vault.DEFAULT_BASE_URL,
            "model": vault.DEFAULT_MODEL,
            "system": "",
            "token_set": False,
            "token_hint": "",
        }
        models = []
        if not error:
            error = "Settings could not be read."
    if submitted:
        for key in ("base_url", "model", "system"):
            if key in submitted:
                public[key] = submitted[key]
    public["models"] = models
    public["error"] = error
    public["notice"] = notice
    public["clear_token"] = clear_token
    return public


def _esc(value: str, quote: bool = False) -> str:
    return html.escape(value or "", quote=quote)


def _options(models: list[dict]) -> str:
    parts = []
    for item in models:
        ident = item.get("id") if isinstance(item.get("id"), str) else ""
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        if not ident:
            continue
        label = name if name and name != ident else ""
        parts.append(
            f'<option value="{_esc(ident, True)}">{_esc(label)}</option>'
        )
    return "".join(parts)


def _render(view: dict) -> str:
    prefix = _prefix()
    save = _esc(prefix + "/save", True)
    models_url = _esc(prefix + "/models", True)
    clear = _esc(prefix + "/clear", True)
    token_set = bool(view.get("token_set"))
    hint = view.get("token_hint") if isinstance(view.get("token_hint"), str) else ""
    if token_set:
        status = "Token encrypted" + (f" · ••••{hint}" if hint else "") + "."
        status_class = "status"
    else:
        status = "No token saved."
        status_class = "status warn"
    error = (
        f'<p class="alert" role="alert">{_esc(view["error"])}</p>'
        if view.get("error")
        else ""
    )
    notice = (
        f'<p class="notice" role="status">{_esc(view["notice"])}</p>'
        if view.get("notice")
        else ""
    )
    count = len(view.get("models") or [])
    if count:
        catalog = f"{count} model" + ("" if count == 1 else "s") + " available as suggestions."
    else:
        catalog = "Type a model id, or load the list after a token is saved."
    disabled = "" if token_set else " disabled"
    clear_flag = "1" if view.get("clear_token") else "0"
    token_placeholder = "Leave blank to keep the saved token" if token_set else "API key"
    return _CSS + f"""
<main class="shell">
  <header class="mast">
    <div>
      <p class="kicker"><span class="mark" aria-hidden="true"></span> llm settings</p>
      <h1>OpenRouter</h1>
    </div>
    <nav class="links">
      <a href="/chat">Chat</a>
      <a href="/">Home</a>
    </nav>
  </header>
  <p class="lede">Chat calls this component for replies. The token is encrypted before it is stored, is never shown again, and is not included in those calls. Keys come from <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer noopener">OpenRouter</a>.</p>
  <div id="panel" data-clear-token="{clear_flag}">
    {error}
    {notice}
    <p class="{status_class}">{_esc(status)}</p>
    <form id="settings-form" method="post" action="{save}" hx-post="{save}" hx-target="#panel" hx-select="#panel" hx-swap="outerHTML" hx-disabled-elt="find button" autocomplete="off">
      <label for="llm-base-url">Base URL</label>
      <input id="llm-base-url" name="base_url" value="{_esc(view.get("base_url") or "", True)}" maxlength="300" spellcheck="false" autocapitalize="off" inputmode="url" placeholder="https://openrouter.ai/api/v1">
      <p class="field-note">Any OpenAI-compatible /v1 endpoint. The token does not go in the URL.</p>
      <label for="llm-model">Model</label>
      <input id="llm-model" name="model" value="{_esc(view.get("model") or "", True)}" list="llm-models" maxlength="200" spellcheck="false" autocapitalize="off" placeholder="openrouter/auto">
      <datalist id="llm-models">{_options(view.get("models") or [])}</datalist>
      <p class="field-note">{_esc(catalog)}</p>
      <label for="llm-system">System prompt</label>
      <textarea id="llm-system" name="system" maxlength="4000" placeholder="Optional. Included with every chat completion.">{_esc(view.get("system") or "")}</textarea>
      <label for="llm-token">Token</label>
      <div class="token-row">
        <input id="llm-token" name="token" type="password" maxlength="512" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="{_esc(token_placeholder, True)}" hx-preserve>
        <button type="button" class="ghost" data-token-toggle>Show</button>
      </div>
      <p class="hint" id="llm-token-hint">Encrypted at rest. Leave blank to keep the current token.</p>
      <div class="actions">
        <button type="submit">Save</button>
        <button type="submit" class="ghost" data-skip-token formaction="{models_url}" hx-post="{models_url}" hx-target="#panel" hx-select="#panel" hx-swap="outerHTML" hx-disabled-elt="find button"{disabled}>Load models<span class="htmx-indicator"> …</span></button>
      </div>
      <div class="secondary">
        <button type="submit" class="danger" data-skip-token formaction="{clear}" hx-post="{clear}" hx-target="#panel" hx-select="#panel" hx-swap="outerHTML" hx-confirm="Remove the saved token?" hx-disabled-elt="find button"{disabled}>Remove token</button>
      </div>
    </form>
  </div>
</main>
""" + _SCRIPT
