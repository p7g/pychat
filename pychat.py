import asyncio
import json
import urllib.parse
import weakref
from collections import defaultdict
from http.cookies import SimpleCookie

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


def get_cookie(scope):
    for key, value in scope["headers"]:
        if key == b"cookie":
            cookie = SimpleCookie()
            cookie.load(value.decode("utf-8"))
            return cookie
    return None


def cookie_valid(cookie):
    return "nick" in cookie and "room" in cookie


async def application(scope, recv, send):
    if scope["type"] == "http":
        return await handle_http(scope, recv, send)

    if scope["type"] != "websocket":
        return

    cookie = get_cookie(scope)
    event = await recv()
    assert event["type"] == "websocket.connect"
    if cookie is None or not cookie_valid(cookie) or scope["path"] != "/ws":
        await send({"type": "websocket.close"})
        return
    await send({"type": "websocket.accept"})

    room = cookie["room"].value
    nick = cookie["nick"].value
    rooms[room].append(send)
    nicks[send] = nick

    await broadcast_presence(room)

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

    async def respond(
        status=200, content_type=b"text/plain", body=b"", cookie=None, location=None
    ):
        headers = [
            (b"content-type", content_type),
        ]

        if cookie is not None:
            cookie_headers = cookie.output().split("\r\n")
            headers.extend(
                (
                    b"set-cookie",
                    h[len("set-cookie: ") :].encode("utf-8"),
                )
                for h in cookie_headers
            )
        if location is not None:
            headers.append((b"location", location))

        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
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

    elif path == "/room" and method == "GET":
        cookie = get_cookie(scope)
        if not cookie or not cookie_valid(cookie):
            await respond(content_type=b"text/plain", status=303, location=b"/")
            return

        with open("templates/room.html", "r") as f:
            template = f.read()

        await respond(
            content_type=b"text/html",
            body=(
                template
                % {
                    "room": sanitize_string(cookie["room"].value),
                    "nick": sanitize_string(cookie["nick"].value),
                }
            ).encode("utf-8"),
            cookie=cookie,
        )
        return

    elif path == "/join" and method == "POST":
        data = dict(urllib.parse.parse_qsl(event["body"].decode("utf-8")))
        nick = data.get("nick")
        room = data.get("room")

        if not isinstance(nick, str) or not isinstance(room, str):
            await respond(status=400, body=b"Bad request")
            return

        cookie = SimpleCookie()
        cookie["nick"] = nick
        cookie["room"] = room

        await respond(status=303, location=b"/room", cookie=cookie)
        return

    await respond(status=404, body=b"Not found")
