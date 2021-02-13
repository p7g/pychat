import asyncio
import json
import urllib.parse
from collections import defaultdict

rooms = defaultdict(list)


async def application(scope, recv, send):
    if scope["type"] == "http":
        return await handle_http(scope, recv, send)

    if scope["type"] != "websocket":
        return

    event = await recv()
    assert event["type"] == "websocket.connect"
    if scope["path"] != "/ws":
        await send({"type": "websocket.close"})
        return
    await send({"type": "websocket.accept"})
    nick = room = None
    inited = False

    while True:
        event = await recv()
        if event["type"] == "websocket.disconnect":
            break
        assert event["type"] == "websocket.receive"

        text = event.get("text")
        if text is None:
            continue

        data = json.loads(text)
        action = data.get("action")
        if action == "init":
            nick, room = data.get("nick"), data.get("room")
            if not isinstance(nick, str) or not isinstance(room, str):
                await send(
                    {
                        "type": "websocket.close",
                        "code": 1008,
                    }
                )
                break
            inited = True
            rooms[room].append(send)
        elif not inited:
            continue

        if action == "send":
            msg = data.get("message")
            if not isinstance(msg, str):
                continue
            await asyncio.gather(
                *[
                    client(
                        {
                            "type": "websocket.send",
                            "text": json.dumps(
                                {
                                    "action": "message",
                                    "sender": nick,
                                    "message": msg,
                                }
                            ),
                        }
                    )
                    for client in rooms[room]
                ]
            )

    if room is not None:
        rooms[room].remove(send)


def sanitize_string(string):
    return string.replace('"', '\\"')


async def handle_http(scope, recv, send):
    event = await recv()

    assert event["type"] == "http.request"
    path = scope["path"]
    method = scope["method"]

    if path == "/" and method == "GET":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html"),
                ],
            }
        )
        with open("templates/home.html", "rb") as f:
            await send(
                {
                    "type": "http.response.body",
                    "body": f.read(),
                }
            )
        return

    elif path == "/join" and method == "POST":
        data = dict(urllib.parse.parse_qsl(event["body"].decode("utf-8")))
        nick = data.get("nick")
        room = data.get("room")

        if not isinstance(nick, str) or not isinstance(room, str):
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"text/plain"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"Bad request",
                }
            )
            return

        with open("templates/room.html", "r") as f:
            template = f.read()

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": template.replace("{ROOM}", sanitize_string(room))
                .replace("{NICK}", sanitize_string(nick))
                .encode("utf-8"),
            }
        )
        return

    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"text/html"),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"Not Found",
        }
    )
