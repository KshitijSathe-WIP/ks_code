"""Teams Bot aiohttp app entry point."""
from __future__ import annotations

import os

from aiohttp import web
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity

from .activity_handler import OIRActivityHandler

SETTINGS = BotFrameworkAdapterSettings(
    app_id=os.environ.get("TEAMS_BOT_APP_ID", ""),
    app_password=os.environ.get("TEAMS_BOT_APP_PASSWORD", ""),
)
ADAPTER = BotFrameworkAdapter(SETTINGS)
BOT = OIRActivityHandler()

routes = web.RouteTableDef()


@routes.post("/api/messages")
async def messages(request: web.Request) -> web.Response:
    if "application/json" not in request.content_type:
        return web.Response(status=415)

    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    async def call_bot(turn_context):
        await BOT.on_turn(turn_context)

    await ADAPTER.process_activity(activity, auth_header, call_bot)
    return web.Response(status=200)


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "3978")))
