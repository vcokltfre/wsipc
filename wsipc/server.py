from asyncio import Event, TimeoutError, create_task, sleep, wait_for
from json import JSONDecodeError
from logging import getLogger
from typing import Any, Dict, Optional, Set
from weakref import WeakKeyDictionary

from aiohttp import WSCloseCode, WSMessage, WSMsgType, web

from .enums import PayloadType

logger = getLogger(__name__)


class WSIPCServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        heartbeat: float = None,
        heartbeat_timeout: float = None,
        *,
        site_options: Dict[str, Any] = None,
    ) -> None:
        """A WSIPC server connector.

        Args:
            host (str, optional): The address to bind to. Defaults to "127.0.0.1".
            port (int, optional): The port to bind to. Defaults to 8080.
            heartbeat (float, optional): How often to send heartbeats. Defaults to None.
            heartbeat_timeout (float, optional): The timeout before kicking a client that fails to heartbeat. Defaults to None.
            site_options (Dict[str, Any], optional): Options passed to `TCPSite`. Defaults to None.
        """

        self.host = host
        self.port = port
        self.heartbeat = heartbeat
        self.heartbeat_timeout = heartbeat_timeout or 5.0

        self._options = site_options or {}

        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._stopped = Event()
        self._stopped.clear()

        self._app.add_routes(
            [
                web.get("/ipc", self._handle_ws),
            ]
        )

        self._sockets: Set[web.WebSocketResponse] = set()
        self._heartbeats: Dict[web.WebSocketResponse, Event] = {}
        self._remotes: WeakKeyDictionary[
            web.WebSocketResponse, str
        ] = WeakKeyDictionary()

    def _dispatch(
        self, message: Any, channel: int, exclude: web.WebSocketResponse = None
    ) -> None:
        for socket in self._sockets:
            if socket is exclude:
                continue

            create_task(
                socket.send_json(
                    {
                        "t": PayloadType.DATA,
                        "d": message,
                        "c": channel,
                    }
                )
            )

    async def _heartbeat(self, ws: web.WebSocketResponse) -> None:
        if not self.heartbeat:
            self._heartbeats.pop(ws, None)
            return

        while True:
            self._heartbeats[ws].clear()

            await ws.send_json(
                {
                    "t": PayloadType.HEARTBEAT,
                }
            )

            try:
                await wait_for(
                    self._heartbeats[ws].wait(), timeout=self.heartbeat_timeout
                )
            except TimeoutError:
                logger.debug(f"Timed out waiting for heartbeat from {ws}")
                await ws.close()
                del self._heartbeats[ws]

            await sleep(self.heartbeat)

    async def _handle_response(
        self, ws: web.WebSocketResponse, message: WSMessage
    ) -> None:
        try:
            data = message.json()
            logger.debug(f"Received message from {self._remotes[ws]}: {data}")
        except JSONDecodeError:
            await ws.close(code=WSCloseCode.INVALID_TEXT)
            return

        if "t" not in data or (t := data["t"]) in [PayloadType.HEARTBEAT]:
            await ws.close(code=WSCloseCode.POLICY_VIOLATION)
            return

        if t == PayloadType.HEARTBEAT_ACK:
            try:
                self._heartbeats[ws].set()
            except KeyError:
                pass
            return

        if t == PayloadType.DATA:
            if "d" not in data:
                await ws.close(code=WSCloseCode.POLICY_VIOLATION)
                return

            exclude = ws if data.get("s", False) else None
            channel = data.get("c", 0)

            self._dispatch(data["d"], channel, exclude)

            return

        await ws.close(code=WSCloseCode.POLICY_VIOLATION)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        self._sockets.add(ws)

        self._remotes[ws] = str(request.remote)

        logger.debug(f"Received connection from {self._remotes[ws]}.")

        await ws.prepare(request)

        self._heartbeats[ws] = Event()
        heartbeat_task = create_task(self._heartbeat(ws))

        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self._handle_response(ws, message)
                elif message.type == WSMsgType.ERROR:
                    logger.error(
                        f"Websocket from {self._remotes[ws]} closed with exception: {ws.exception()}"
                    )

            if not heartbeat_task.done():
                heartbeat_task.cancel()
        except ConnectionResetError:
            self._sockets.remove(ws)
        finally:
            logger.debug(f"Connection from {self._remotes[ws]} was terminated.")

            if not heartbeat_task.done():
                heartbeat_task.cancel()
                logger.debug(f"Cancelled heartbeat task for {self._remotes[ws]}.")

            self._sockets.remove(ws)
            self._remotes.pop(ws, None)

        return ws

    async def start(self, *, block: bool = False) -> None:
        """Start the WSIPC server.

        Args:
            block (bool, optional): Whether to wait until the server is stopped before returning. Defaults to False.
        """

        logger.debug("Starting WSIPC server...")

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port, **self._options)
        await self._site.start()

        logger.debug("Successfully started WSIPC server.")

        if block:
            await self._stopped.wait()

    async def stop(self) -> None:
        """Stop the WSIPC server."""

        logger.debug(f"Stopping WSIPC server on {self.host}:{self.port}...")

        if self._stopped.is_set():
            raise RuntimeError("Cannot restart stopped WSIPC server.")

        if not (self._site and self._runner):
            raise RuntimeError("Cannot stop non-running WSIPC server.")

        await self._site.stop()
        await self._runner.cleanup()
        await self._app.cleanup()

        self._stopped.set()

        logger.debug("Successfully stopped WSIPC server.")
