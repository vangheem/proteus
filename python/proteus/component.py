"""Component router and page shell."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

_HTMX = "https://unpkg.com/htmx.org@2.0.4"


class Component:
    """HTTP surface a component author registers routes on."""

    def __init__(self) -> None:
        self.router = APIRouter()

    def get(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._route(path, "GET")

    def post(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._route(path, "POST")

    def page(self, title: str, body: str) -> HTMLResponse:
        """Full HTML document. Title is escaped; body is inserted raw.

        Loads HTMX 2.0.4 and reserves bottom padding for the coordinator status
        frame. Components must not render their own status bar.
        """
        safe_title = html.escape(title, quote=True)
        document = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            f"<title>{safe_title}</title>\n"
            f'<script src="{_HTMX}"></script>\n'
            "<style>body { padding-bottom: 4rem; }</style>\n"
            "</head>\n"
            "<body>\n"
            f"{body}\n"
            "</body>\n"
            "</html>\n"
        )
        return HTMLResponse(document)

    def _route(
        self, path: str, method: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.router.add_api_route(path, fn, methods=[method])
            return fn

        return decorator
