from asyncio import Event, create_task, sleep
from logging import getLogger
from typing import Any, Callable, Dict, Optional, Set

from aiohttp import (
    ClientConnectionError,
    ClientSession,
    ClientWebSocketResponse,
    WSCloseCode,
    WSMessage,
    WSMsgType,
)

from .enums import PayloadType
from .utils import Callback, maybe_async

logger = getLogger(__name__)


class WSIPCClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        connection_options: dict[str, Any] = None,
    ) -> None:
        """A WSIPC client connector.

        Args:
            host (str, optional): The host to connect to. Defaults to "127.0.0.1".
            port (int, optional): The port to connect on. Defaults to 8080.
            connection_options (dict[str, Any], optional): Options passed to `ws_connect`. Defaults to None.
        """

        self.host = host
        self.port = port

        self._options = connection_options or {}

        self.__session: Optional[ClientSession] = None
        self._ws: Optional[ClientWebSocketResponse] = None

        self.closed: bool = False

        self._callbacks: Dict[int, Set[Callback]] = {}

        self.connected = Event()
        self.connected.clear()

    @property
    def _session(self) -> ClientSession:
        if self.__session is None or self.__session.closed:
            self.__session = ClientSession()

        return self.__session

    def listen(self, func: Callback, *, channel: int = 0) -> None:
        """Add a listener for a given channel.

        Args:
            func (Callback): The listener to add.
            channel (int, optional): The channel to listen on. Defaults to 0.
        """

        self._callbacks.setdefault(channel, set()).add(func)

    def listener(self, channel: int = 0) -> Callable[[Callback], Callback]:
        """Decorator version of `listen`.

        Args:
            channel (int, optional): The channel to listen on. Defaults to 0.
        """

        def decorator(func: Callback) -> Callback:
            self.listen(func, channel=channel)

            return func

        return decorator

    async def _heartbeat(self) -> None:
        assert self._ws, "WSIPCClient is not connected"

        await self._ws.send_json(
            {
                "t": PayloadType.HEARTBEAT_ACK,
            }
        )

        logger.debug("Sent heartbeat.")

    async def _connect(self) -> None:
        logger.debug(f"Connecting to {self.host}:{self.port}...")

        self._ws = await self._session.ws_connect(
            f"http://{self.host}:{self.port}/ipc", **self._options
        )

        logger.debug(f"Connected to {self.host}:{self.port}.")

        self.connected.set()

        async for message in self._ws:
            message: WSMessage

            if message.type == WSMsgType.TEXT:
                data = message.json()

                logger.debug(f"Received message: {data}")

                if not isinstance(data, dict):
                    await self._ws.close(code=WSCloseCode.POLICY_VIOLATION)

                if data["t"] == PayloadType.HEARTBEAT:
                    await self._heartbeat()

                elif data["t"] == PayloadType.DATA:
                    for callback in self._callbacks.get(data.get("c", 0), set()):
                        create_task(maybe_async(callback, data["d"]))

    async def connect(self, *, reconnect: bool = True) -> None:
        """Connect to a WSIPC server.

        Args:
            reconnect (bool, optional): Whether to reconnect on disconnect. Defaults to True.
        """

        while True:
            try:
                await self._connect()
            except ClientConnectionError:
                pass

            self.connected.clear()

            if not reconnect:
                break

            await sleep(1)

        if not self.closed:
            await self.close()

    async def close(self) -> None:
        """Close the connection to the WSIPC server."""

        logger.debug(f"Closing connection to {self.host}:{self.port}...")

        if not self._ws:
            raise RuntimeError("WSIPCClient is not connected.")

        if not self._ws.closed:
            await self._ws.close()

        if not self._session.closed:
            await self._session.close()

        logger.debug(f"Successfully closed connection to {self.host}:{self.port}.")

    async def send(
        self, message: Any, include_self: bool = False, channel: int = 0
    ) -> None:
        """Send a message to the IPC network.

        Args:
            message (Any): The JSON data to send.
            include_self (bool, optional): Whether to dispatch this event to this client instance. Defaults to False.
            channel (int, optional): The channel to send the message to. Defaults to 0.

        Raises:
            RuntimeError: The client is not connected to the server.
        """

        if not self._ws:
            raise RuntimeError("WSIPCClient is not connected.")

        await self._ws.send_json(
            {
                "t": PayloadType.DATA,
                "d": message,
                "s": include_self,
                "c": channel,
            }
        )

        logger.debug(f"Sent message: {message}")
