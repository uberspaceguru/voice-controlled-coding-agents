"""The Director app's microphone and speakers, over WebRTC on this Mac.

`bot.py --local` normally opens the Mac's microphone and speakers itself
(LocalAudioTransport). That path has no echo canceller, so the bot mutes the
microphone whenever it speaks and nobody can talk over it.

With TB_AUDIO=webrtc (set only by the Director app, from its own settings) the
bot stays exactly the stdio child it was: the same launcher, the same event
lines on stdout, the same commands on stdin, the same tools run here. Only the
audio moves: the bot answers one WebRTC offer on 127.0.0.1:TB_WEBRTC_PORT, and
the app connects to it with the peer connection it already uses for a hosted
manager (ManagerPeer). The app's WebRTC engine plays the bot's voice and
captures the microphone, so it cancels the voice out of the microphone, which
is what lets the gate stay open and a word said over Director reach it.

Signalling is the subset of Pipecat's dev runner the app speaks: POST
/api/offer {sdp,type} -> {sdp,type,pc_id}; PATCH /api/offer {pc_id,candidates}.
A bearer token (TB_WEBRTC_TOKEN, minted by the app per start) keeps any other
local process from taking the microphone's place. One session per process:
when the peer goes, the pipeline ends and the child exits, and the app's child
watcher does what it always did.

Nothing here writes to stdout: stdout is the app's event pipe.
"""

import asyncio
import contextlib
import os
from typing import Awaitable, Callable

from loguru import logger


def requested() -> bool:
    return os.getenv("TB_AUDIO", "").strip().lower() == "webrtc"


def settings() -> tuple[int, str]:
    port = int(os.getenv("TB_WEBRTC_PORT") or "0")
    if not 0 < port < 65536:
        raise SystemExit("TB_AUDIO=webrtc needs TB_WEBRTC_PORT")
    return port, os.getenv("TB_WEBRTC_TOKEN", "")


def build_app(on_connection: Callable[[object], Awaitable[None]], token: str):
    """The two signalling routes. `on_connection` is started, not awaited: the
    offer's answer must go back before the session runs."""
    from fastapi import FastAPI, HTTPException, Request
    from pipecat.transports.smallwebrtc.request_handler import (
        ConnectionMode,
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )

    handler = SmallWebRTCRequestHandler(connection_mode=ConnectionMode.SINGLE)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.handler = handler
    app.state.tasks = set()

    def check(request: Request):
        if token and request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.post("/api/offer")
    async def offer(request: Request):
        check(request)
        data = await request.json()
        req = SmallWebRTCRequest(sdp=data["sdp"], type=data["type"], pc_id=data.get("pc_id"),
                                 restart_pc=data.get("restart_pc"))

        async def started(connection):
            task = asyncio.get_running_loop().create_task(on_connection(connection))
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)

        answer = await handler.handle_web_request(req, started)
        logger.info(f"local rtc: answered offer, pc_id {answer.get('pc_id')}")
        return answer

    @app.patch("/api/offer")
    async def candidates(request: Request):
        check(request)
        data = await request.json()
        await handler.handle_patch_request(SmallWebRTCPatchRequest(
            pc_id=data["pc_id"], candidates=[IceCandidate(**c) for c in data.get("candidates", [])]))
        return {"status": "success"}

    return app


async def serve(run_session: Callable[[object], Awaitable[None]], port: int, token: str) -> None:
    """Answer one offer on 127.0.0.1:`port`, run the session it brings, return
    when that session ends (or the server fails to bind)."""
    import uvicorn

    class Server(uvicorn.Server):
        # SIGTERM from the app ends the child as it always did: no graceful
        # HTTP shutdown standing between the app and its child's exit.
        def capture_signals(self):
            return contextlib.nullcontext()

    finished = asyncio.Event()

    async def on_connection(connection):
        try:
            await run_session(connection)
        except Exception as e:  # noqa: BLE001 - a failed session ends the child, loudly in the log
            logger.exception(f"local rtc: session failed: {e}")
        finally:
            finished.set()

    app = build_app(on_connection, token)
    server = Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                   access_log=False, lifespan="off"))
    serving = asyncio.get_running_loop().create_task(server.serve())
    logger.info(f"local rtc: waiting for the app's offer on 127.0.0.1:{port}")
    done = asyncio.get_running_loop().create_task(finished.wait())
    await asyncio.wait({serving, done}, return_when=asyncio.FIRST_COMPLETED)
    if serving.done() and not finished.is_set():
        logger.error("local rtc: the signalling server stopped before any session (port taken?)")
    server.should_exit = True
    with contextlib.suppress(Exception):
        await app.state.handler.close()
    done.cancel()
    with contextlib.suppress(Exception):
        await serving
