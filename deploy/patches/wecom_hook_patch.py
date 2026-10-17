#!/usr/bin/env python3
"""Re-apply the wecom_callback "inbound webhook" patch (POST/GET /hook → 企微通知).

WHY THIS SCRIPT EXISTS
----------------------
`/opt/hermes` is the IMAGE layer (only `/opt/data` and `/home/agent/.hermes` are volumes).
Every container recreate / image upgrade restores the pristine adapter and the endpoint
silently disappears. Re-run after every redeploy (apply-all.sh already calls it):

    /opt/hermes/.venv/bin/python /opt/data/patch/wecom_hook_patch.py

Idempotent: prints "already patched" and exits 0 when the marker is present.
Then restart the gateway (SIGTERM; PID 1 revives it in ~10 s) — the module is imported at
startup, so the route is not live until then.

VERSION NOTE (0.22.x)
---------------------
Targets the 0.22.x adapter (`nousresearch/hermes-agent:latest`, rewritten to a ~315-line
`WecomCallbackAdapter`). Only the `__init__` anchor changed vs. the 0.21.x patch: the class now
uses PEP-585 lowercase generics (`dict[str, str]`, not `Dict[str, str]`). The imports / route /
`_handle_health` anchors and the whole handler body are unchanged -- `Dict`/`List`/`Any` are still
imported from `typing` upstream, and `_MAX_BODY` / `_message_queue` / `build_source` / `send`
all still exist with the same shapes.

WHAT IT ADDS (all inside class WecomCallbackAdapter)
----------------------------------------------------
* `POST|GET /hook` (path from `platforms.wecom_callback.extra.hook_path`, default `/hook`)
  on the SAME listener/port as the WeCom callback (8645) — so it inherits the existing
  public URL (https://hp.286115.xyz:88/hook) with no compose / reverse-proxy change.
* Auth: `?token=` or `X-Hook-Token:` compared (constant-time) against `WECOM_HOOK_TOKEN`
  (process env, else the profile .env). Unset token ⇒ 503 (never an open write endpoint).
* Body — accepts BOTH shapes, so a third-party "企业微信群机器人" push channel can be pointed
  straight at this URL:
    - this endpoint's own: JSON `{"text": "..."}` | raw text | `?text=`
    - WeCom group-robot:   JSON `{"msgtype": "text", "text": {"content": "..."}}`
                           (also `markdown.content`)
* Reply is WeCom-shaped (`{"errcode": 0, "errmsg": "ok", ...}`) with the extra keys kept for
  direct curl callers — a group-robot client that checks `errcode` sees success.
* `mode=notify` (default) — pushes the text to the target chat as a bot message (a real
  notification; no agent run, no LLM cost).
* `mode=agent` — injects the text as an inbound user message so the bot answers it.
* Target chat: `to` (bare userid gets the app's corp_id prefixed) → `WECOM_HOOK_CHAT` →
  `extra.hook_chat` → platform `home_channel`.
* Rate limit: 30 requests / 60 s (in-process), then 429.
"""

from __future__ import annotations

import pathlib
import py_compile
import shutil
import sys

TARGET = pathlib.Path("/opt/hermes/plugins/platforms/wecom/callback_adapter.py")
PATCHED_COPY = pathlib.Path("/opt/data/patch/callback_adapter.py.patched")
MARK = "[patch] inbound webhook"

IMPORTS_ANCHOR = "import asyncio\nimport logging\nimport socket as _socket\nimport time\n"

IMPORTS_ADD = "import asyncio\nimport hmac\nimport json\nimport logging\nimport os\nimport socket as _socket\nimport time\n"

INIT_ANCHOR = "        self._user_app_map: dict[str, str] = {}\n"

INIT_ADD = """        # [patch] inbound webhook state (see /opt/data/patch/wecom_hook_patch.py)
        self._hook_path = str(extra.get("hook_path") or "/hook")
        self._hook_env_cache: Dict[str, Any] = {"mtime": None, "vals": {}}
        self._hook_hits: List[float] = []
"""

ROUTE_ANCHOR = "            self._app.router.add_post(self._path, self._handle_callback)\n"

ROUTE_ADD = """            # [patch] inbound webhook — POST/GET /hook (token-authenticated)
            self._app.router.add_post(self._hook_path, self._handle_hook)
            self._app.router.add_get(self._hook_path, self._handle_hook)
"""

HANDLER_ANCHOR = (
    "    async def _handle_health(self, request: web.Request) -> web.Response:\n"
    '        return web.json_response({"status": "ok", "platform": "wecom_callback"})\n'
)

HANDLER_ADD = '''

    # ── [patch] inbound webhook: generic POST/GET /hook → 企微通知 ─────────────────────
    HOOK_ENV_FILES = ("/home/agent/.hermes/.env", "/home/agent/.hermes/profiles/enderiose/.env")
    HOOK_RATE_MAX = 30        # requests per window, per process
    HOOK_RATE_WINDOW = 60.0   # seconds

    def _hook_env(self, key: str) -> str:
        """WECOM_HOOK_* lookup: process env first, else the profile .env files (mtime-cached)."""
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
        cache = getattr(self, "_hook_env_cache", None)
        if cache is None:
            cache = self._hook_env_cache = {"mtime": None, "vals": {}}
        files = list(self.HOOK_ENV_FILES)
        home = os.environ.get("HERMES_HOME") or ""
        if home:
            files.insert(0, os.path.join(home, ".env"))
            files.insert(1, os.path.join(home, "profiles", "enderiose", ".env"))
        newest = 0.0
        for path in files:
            try:
                newest = max(newest, os.path.getmtime(path))
            except OSError:
                continue
        if cache["mtime"] != newest:
            vals: Dict[str, str] = {}
            for path in files:
                try:
                    with open(path, encoding="utf-8") as fh:
                        for line in fh:
                            s = line.strip()
                            if s and not s.startswith("#") and "=" in s:
                                k, v = s.split("=", 1)
                                vals.setdefault(k.strip(), v.strip().strip('"').strip("'"))
                except OSError:
                    continue
            cache["mtime"] = newest
            cache["vals"] = vals
        return str(cache["vals"].get(key) or "")

    def _hook_rate_ok(self) -> bool:
        now = time.time()
        hits = [t for t in getattr(self, "_hook_hits", []) if now - t < self.HOOK_RATE_WINDOW]
        if len(hits) >= self.HOOK_RATE_MAX:
            self._hook_hits = hits
            return False
        hits.append(now)
        self._hook_hits = hits
        return True

    def _hook_default_chat(self) -> str:
        chat = self._hook_env("WECOM_HOOK_CHAT") or str((getattr(self.config, "extra", None) or {}).get("hook_chat") or "")
        if chat:
            return chat
        home = getattr(self.config, "home_channel", None)
        if isinstance(home, dict):
            chat = str(home.get("chat_id") or "")
        else:
            chat = str(getattr(home, "chat_id", "") or "")
        return chat

    @staticmethod
    def _hook_text(data: Dict[str, Any]) -> str:
        """Pull the message out of either payload shape (own shape, or WeCom group-robot)."""

        def _s(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""

        text = _s(data.get("text"))
        if not text and isinstance(data.get("text"), dict):
            text = _s(data["text"].get("content"))
        if not text and isinstance(data.get("markdown"), dict):
            text = _s(data["markdown"].get("content"))
        if not text:
            text = _s(data.get("markdown")) or _s(data.get("content"))
        if not text:
            text = _s(data.get("msg")) or _s(data.get("message"))
        return text

    @staticmethod
    def _hook_reply(errcode: int, errmsg: str, status: int, **extra) -> web.Response:
        """WeCom-shaped reply (`errcode`/`errmsg`) so a group-robot push channel reads success;
        the extra keys keep direct curl callers informed."""
        payload: Dict[str, Any] = {"errcode": errcode, "errmsg": errmsg}
        payload.update(extra)
        return web.json_response(payload, status=status)

    async def _handle_hook(self, request: web.Request) -> web.Response:
        """Token-authenticated generic inbound webhook (PC-side plugins, scripts, CI)."""
        expected = self._hook_env("WECOM_HOOK_TOKEN")
        if not expected:
            return self._hook_reply(50001, "hook disabled: WECOM_HOOK_TOKEN unset", 503, ok=False)
        supplied = request.query.get("token") or request.headers.get("X-Hook-Token") or ""
        if not supplied or not hmac.compare_digest(str(supplied), expected):
            logger.warning("[WecomCallback] /hook rejected: bad token from %s", request.remote)
            return self._hook_reply(40001, "invalid token", 403, ok=False)
        if not self._hook_rate_ok():
            return self._hook_reply(45009, "rate limited", 429, ok=False)

        data: Dict[str, Any] = {}
        if request.method == "POST":
            body = await request.read()
            if len(body) > _MAX_BODY:
                return self._hook_reply(40006, "payload too large", 413, ok=False)
            raw = body.decode("utf-8", errors="replace").strip()
            if raw.startswith("{"):
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                data = parsed if isinstance(parsed, dict) else {"text": raw}
            elif raw:
                data = {"text": raw}
        for key in ("text", "msg", "content", "message"):
            if request.query.get(key):
                data.setdefault(key, request.query[key])
        if request.query.get("to"):
            data.setdefault("to", request.query["to"])

        text = self._hook_text(data).strip()
        if not text:
            if request.method == "GET":
                # A bare GET is a reachability probe (third-party UIs' "test" buttons do this).
                return self._hook_reply(0, "ok", 200, ok=True, probe=True,
                                        hint="GET without ?text= is a reachability probe; "
                                             "add ?text=... or POST JSON to actually send")
            return self._hook_reply(
                40058, 'empty text — send JSON {"text": "..."}, the group-robot '
                       '{"msgtype":"text","text":{"content":"..."}}, raw text, or ?text=', 400, ok=False)
        chat = str(data.get("to") or "").strip() or self._hook_default_chat()
        if not chat:
            return self._hook_reply(50002, "no target chat (set WECOM_HOOK_CHAT)", 500, ok=False)
        corp = str((self._apps[0] or {}).get("corp_id") or "") if self._apps else ""
        if ":" not in chat and corp:
            chat = f"{corp}:{chat}"
        mode = str(data.get("mode") or request.query.get("mode") or "notify").strip().lower()

        if mode in {"agent", "run", "turn"}:
            user_id = chat.split(":", 1)[-1]
            source = self.build_source(chat_id=chat, chat_name=user_id, chat_type="dm", user_id=user_id, user_name=user_id)
            event = MessageEvent(text=text, message_type=MessageType.TEXT, source=source,
                                 raw_message=text, message_id=f"hook:{time.time()}")
            await self._message_queue.put(event)
            logger.info("[WecomCallback] /hook queued an agent turn for %s", chat)
            return self._hook_reply(0, "ok", 200, ok=True, mode="agent", chat=chat)

        result = await self.send(chat, text)
        ok = bool(getattr(result, "success", False))
        logger.info("[WecomCallback] /hook notify -> %s (%s)", chat, "ok" if ok else "failed")
        if not ok:
            return self._hook_reply(50003, str(getattr(result, "error", None) or "send failed"), 502, ok=False, chat=chat)
        return self._hook_reply(0, "ok", 200, ok=True, mode="notify", chat=chat,
                                message_id=getattr(result, "message_id", ""))
'''


def main() -> int:
    if not TARGET.exists():
        print(f"FAIL: {TARGET} not found")
        return 1
    text = TARGET.read_text(encoding="utf-8")

    if MARK in text:
        print("already patched — nothing to do")
        return 0

    for name, anchor in (("imports", IMPORTS_ANCHOR), ("__init__", INIT_ANCHOR),
                         ("routes", ROUTE_ANCHOR), ("_handle_health", HANDLER_ANCHOR)):
        count = text.count(anchor)
        if count != 1:
            print(f"FAIL: anchor '{name}' matched {count} times (need exactly 1) — file changed upstream, re-derive the patch")
            return 1

    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak-hook"))  # in-tree backup of the pre-hook file
    patched = text.replace(IMPORTS_ANCHOR, IMPORTS_ADD, 1).replace(
        INIT_ANCHOR, INIT_ANCHOR + INIT_ADD, 1).replace(
        ROUTE_ANCHOR, ROUTE_ANCHOR + ROUTE_ADD, 1).replace(
        HANDLER_ANCHOR, HANDLER_ANCHOR + HANDLER_ADD, 1)
    if MARK not in patched or patched.count("async def _handle_hook(") != 1:
        print("FAIL: patch assembly did not produce the expected shape")
        return 1

    TARGET.write_text(patched, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)  # syntax gate before touching the live gateway
    PATCHED_COPY.write_text(patched, encoding="utf-8")
    print(f"patched {TARGET} (+{len(patched) - len(text)} bytes); durable copy: {PATCHED_COPY}")
    print("restart the gateway to load it (SIGTERM → PID 1 revives it in ~10s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
