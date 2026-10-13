"""The bridge's HTTP API, on the one port."""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def create_app() -> Starlette:
    return Starlette(routes=[Route("/healthz", healthz)])
