import asyncio
import json
import urllib.parse
import weakref
from collections import defaultdict

rooms = defaultdict(list)
nicks = weakref.WeakKeyDictionary()


async def broadcast(room, msg):
    await asyncio.gather(
        *[
            send({"type": "websocket.send", "text": json.dumps(msg)})
            for send in rooms[room]
        ]
    )


async def broadcast_presence(room):
    await broadcast(
        room,
        {
            "action": "presence",
            "presence": [nicks[user] for user in rooms[room]],
        },
    )


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
            nicks[send] = nick

            await broadcast_presence(room)
        elif not inited:
            continue

        if action == "send":
            msg = data.get("message")
            if not isinstance(msg, str):
                continue
            await broadcast(
                room,
                {
                    "action": "message",
                    "sender": nick,
                    "message": msg,
                },
            )

    if room is not None:
        rooms[room].remove(send)
    await broadcast_presence(room)


def sanitize_string(string):
    return string.replace('"', '\\"')


async def handle_http(scope, recv, send):
    event = await recv()

    assert event["type"] == "http.request"
    path = scope["path"]
    method = scope["method"]

    async def respond(status=200, content_type=b"text/plain", body=b""):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )

    if path == "/" and method == "GET":
        with open("templates/home.html", "rb") as f:
            await respond(content_type=b"text/html", body=f.read())
        return

    elif path == "/join" and method == "POST":
        data = dict(urllib.parse.parse_qsl(event["body"].decode("utf-8")))
        nick = data.get("nick")
        room = data.get("room")

        if not isinstance(nick, str) or not isinstance(room, str):
            await respond(status=400, body=b"Bad request")
            return

        with open("templates/room.html", "r") as f:
            template = f.read()

        await respond(
            content_type=b"text/html",
            body=template.replace("{ROOM}", sanitize_string(room))
            .replace("{NICK}", sanitize_string(nick))
            .encode("utf-8"),
        )
        return

    await respond(status=404, body=b"Not found")
