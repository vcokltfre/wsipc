# wsipc

Async Python IPC using WebSockets

## Server Example (Simple Broker)

```py
from asyncio import run

from wsipc import WSIPCServer

server = WSIPCServer(heartbeat=45)

run(server.start(block=True))
```

## Client Example

```py
from asyncio import create_task, run, sleep

from wsipc import WSIPCClient


client = WSIPCClient()

@client.listener()
async def on_message(message):
    print(message)

@client.listener()
def sync_listener(message):
    print(message)

async def main() -> None:
    create_task(client.connect())

    await client.connected.wait()

    await client.send("Hello World!")

    await sleep(1)

    await client.close()

run(main())
```
