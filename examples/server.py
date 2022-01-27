from asyncio import run

from wsipc import WSIPCServer

server = WSIPCServer(heartbeat=45)

run(server.start(block=True))
