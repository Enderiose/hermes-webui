"""Unit test for the wecom_callback inbound-webhook patch (POST/GET /hook).

Run after applying /opt/data/patch/wecom_hook_patch.py (and after any redeploy):

    /opt/hermes/.venv/bin/python /opt/data/patch/test_wecom_hook_patch.py

Exit 0 + ALL_PASS means: 503 without a configured token, 403 on a bad token, 400 on empty
text, notify mode delivers exactly the target chat + text, bare userid gets the corp prefix,
agent mode queues a MessageEvent instead of sending, rate limit kicks in at the cap.
No network calls, no real 企微 message.
"""
import asyncio
import json
import sys
import types

sys.path.insert(0, "/opt/hermes")

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from plugins.platforms.wecom.callback_adapter import WecomCallbackAdapter

TOKEN = "test-token-123"
CORP = "wwcorp"
SENT = []
QUEUED = []


class Req:
    def __init__(self, query=None, headers=None, body=b"", method="POST"):
        self.query = dict(query or {})
        self.headers = dict(headers or {})
        self.method = method
        self.remote = "127.0.0.1"
        self._body = body

    async def read(self):
        return self._body


def make(env=None, home_chat=None, rate_max=None):
    a = WecomCallbackAdapter.__new__(WecomCallbackAdapter)
    a.platform = Platform.WECOM_CALLBACK
    a.config = PlatformConfig(
        enabled=True, extra={},
        home_channel=types.SimpleNamespace(chat_id=home_chat) if home_chat else None,
    )
    a._apps = [{"name": "default", "corp_id": CORP, "agent_id": "1", "token": "t", "encoding_aes_key": "k"}]
    a._hook_path = "/hook"
    a._hook_hits = []
    a._hook_env_cache = {"mtime": None, "vals": {}}
    a._message_queue = asyncio.Queue()
    if rate_max:
        a.HOOK_RATE_MAX = rate_max

    vals = dict(env or {})

    def _env(key):
        return vals.get(key, "")

    a._hook_env = _env

    async def _fake_send(chat_id, content, reply_to=None, metadata=None):
        SENT.append((chat_id, content))
        return SendResult(success=True, message_id="m1")

    a.send = _fake_send
    return a


def body(resp):
    return json.loads(resp.body.decode("utf-8"))


async def main():
    failures = []

    def check(name, cond, extra=""):
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  <- {extra}"))
        if not cond:
            failures.append(name)

    env = {"WECOM_HOOK_TOKEN": TOKEN, "WECOM_HOOK_CHAT": f"{CORP}:MaGuoDong"}

    # 1) no token configured ⇒ endpoint refuses to work (never an open write path)
    a = make(env={})
    r = await a._handle_hook(Req())
    check("503 when WECOM_HOOK_TOKEN unset", r.status == 503, r.status)

    # 2) bad / missing token
    a = make(env=env)
    r = await a._handle_hook(Req(query={"token": "nope"}))
    check("403 on bad token", r.status == 403, r.status)
    check("403 body is WeCom-shaped (errcode)", body(r).get("errcode") == 40001, body(r))
    r = await a._handle_hook(Req())
    check("403 on missing token", r.status == 403, r.status)

    # 3) good token, header form, empty text
    r = await a._handle_hook(Req(headers={"X-Hook-Token": TOKEN}))
    check("400 on empty text (POST)", r.status == 400, r.status)
    r = await a._handle_hook(Req(query={"token": TOKEN}, method="GET"))
    check("bare GET = probe → 200", r.status == 200 and body(r).get("probe") is True, body(r))

    # 4) JSON body → notify to the default chat, no corp prefixing needed
    SENT.clear()
    r = await a._handle_hook(Req(query={"token": TOKEN}, body=json.dumps({"text": "构建完成 ✅"}).encode()))
    check("200 notify", r.status == 200, body(r))
    check("notify payload", SENT == [(f"{CORP}:MaGuoDong", "构建完成 ✅")], SENT)
    check("notify response ok", body(r).get("ok") is True and body(r).get("mode") == "notify", body(r))
    check("WeCom-shaped reply (errcode 0)", body(r).get("errcode") == 0 and body(r).get("errmsg") == "ok", body(r))

    # 4b) 企业微信群机器人 payload shapes — a third-party push channel points at this URL
    SENT.clear()
    r = await a._handle_hook(Req(query={"token": TOKEN},
                                 body=json.dumps({"msgtype": "text", "text": {"content": "群机器人格式"}}).encode()))
    check("group-robot text.content", SENT == [(f"{CORP}:MaGuoDong", "群机器人格式")] and body(r).get("errcode") == 0, (SENT, body(r)))

    SENT.clear()
    await a._handle_hook(Req(query={"token": TOKEN},
                             body=json.dumps({"msgtype": "markdown", "markdown": {"content": "**md**"}}).encode()))
    check("group-robot markdown.content", SENT == [(f"{CORP}:MaGuoDong", "**md**")], SENT)

    # 4c) an unknown shape is a clean 400, not a silent dict-repr message
    SENT.clear()
    r = await a._handle_hook(Req(query={"token": TOKEN}, body=json.dumps({"msgtype": "image", "image": {"base64": "x"}}).encode()))
    check("unsupported group-robot type → 400", r.status == 400 and SENT == [], (r.status, SENT))

    # 5) raw-text body + GET ?text= + bare userid gets the corp prefix
    SENT.clear()
    r = await a._handle_hook(Req(query={"token": TOKEN}, body=b"raw text"))
    check("raw text body", SENT == [(f"{CORP}:MaGuoDong", "raw text")], SENT)

    SENT.clear()
    r = await a._handle_hook(Req(query={"token": TOKEN, "text": "GET 通知", "to": "MaGuoDong"}, method="GET"))
    check("GET ?text= + bare to → corp-prefixed", SENT == [(f"{CORP}:MaGuoDong", "GET 通知")], SENT)

    # 6) explicit full chat id is passed through untouched
    SENT.clear()
    await a._handle_hook(Req(query={"token": TOKEN, "text": "x", "to": f"{CORP}:Other"}))
    check("full chat id passthrough", SENT == [(f"{CORP}:Other", "x")], SENT)

    # 7) home_channel fallback when WECOM_HOOK_CHAT is unset
    SENT.clear()
    b = make(env={"WECOM_HOOK_TOKEN": TOKEN}, home_chat=f"{CORP}:Home")
    await b._handle_hook(Req(query={"token": TOKEN, "text": "hi"}))
    check("home_channel fallback", SENT == [(f"{CORP}:Home", "hi")], SENT)

    # 8) no chat anywhere → 500, not a silent no-op
    c = make(env={"WECOM_HOOK_TOKEN": TOKEN})
    r = await c._handle_hook(Req(query={"token": TOKEN, "text": "hi"}))
    check("500 when no target chat", r.status == 500, r.status)

    # 9) mode=agent queues an inbound event instead of sending a notification
    SENT.clear()
    a._message_queue = asyncio.Queue()
    r = await a._handle_hook(Req(query={"token": TOKEN, "mode": "agent"}, body=json.dumps({"text": "跑一轮"}).encode()))
    ev = a._message_queue.get_nowait()
    check("agent mode does not send", SENT == [], SENT)
    check("agent mode queues the text", ev.text == "跑一轮" and ev.source.chat_id == f"{CORP}:MaGuoDong", ev)

    # 10) rate limit
    d = make(env=env, rate_max=3)
    codes = [(await d._handle_hook(Req(query={"token": TOKEN, "text": "n"}))).status for _ in range(4)]
    check("rate limit → 429 at the cap", codes == [200, 200, 200, 429], codes)

    print("\n" + ("ALL_PASS" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
