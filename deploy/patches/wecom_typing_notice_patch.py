#!/usr/bin/env python3
"""Re-apply the wecom_callback "thinking notice" patch (⏳ 正在思考处理，请稍候…).

WHY THIS SCRIPT EXISTS
----------------------
`/opt/hermes` is the IMAGE layer (only `/opt/data` is a volume). Callback mode has no
typing-status API, so the adapter is patched to push one real message per long turn — and
**every container recreate / image upgrade silently restores the pristine file**. Re-run this
after every redeploy:

    /opt/hermes/.venv/bin/python /opt/data/patch/wecom_typing_notice_patch.py

Idempotent: prints "already patched" and exits 0 when the marker is present.
Then restart the gateway (SIGTERM; PID 1 revives it in ~10 s) — the module is imported at
startup, so the patch is not live until then.

WHAT IT ADDS (all inside class WecomCallbackAdapter)
---------------------------------------------------
* `send_typing()` — base ticks this every 2 s per turn; fires ONE background send once the
  turn passes `platforms.wecom_callback.extra.typing_notice_after` seconds (default 20, <=0
  disables). Text override: platform `typing_status_text`.
* `send()` — thin wrapper that clears the per-turn state, then calls the original body
  (renamed `_send_text`). `stop_typing()` clears it too ⇒ at most one notice per turn.
* `_send_text()` — the untouched original `send()` body (the notice uses this, so the notice
  never re-enters the wrapper / never resets its own state).
"""

from __future__ import annotations

import pathlib
import py_compile
import shutil
import sys

TARGET = pathlib.Path("/opt/hermes/plugins/platforms/wecom/callback_adapter.py")
PATCHED_COPY = pathlib.Path("/opt/data/patch/callback_adapter.py.patched")
MARK = "[patch] thinking notice"

INIT_ANCHOR = "        self._access_tokens: Dict[str, Dict[str, Any]] = {}\n"

INIT_ADD = """        # [patch] thinking notice state (see /opt/data/patch/wecom_typing_notice_patch.py)
        self._typing_notes: Dict[str, Dict[str, Any]] = {}
        self._typing_notice_tasks: set = set()
"""

SEND_ANCHOR = (
    "    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,"
    " metadata: Optional[Dict[str, Any]] = None) -> SendResult:\n"
)

SEND_ADD = '''    # ── [patch] thinking notice: callback mode has no typing-status API ──────────────
    # BasePlatformAdapter ticks send_typing() every 2s per turn. Self-built apps have no typing
    # indicator, so push ONE real message once the turn passes extra.typing_notice_after seconds
    # (default 20; <=0 disables). Text override: platform typing_status_text. State is cleared by
    # send() and stop_typing() ⇒ the notice fires at most once per turn.
    TYPING_NOTICE_TEXT = "⏳ 正在思考处理，请稍候…"

    def _typing_notice_after(self) -> float:
        raw = (getattr(self.config, "extra", None) or {}).get("typing_notice_after", 20)
        try:
            after = float(raw)
        except (TypeError, ValueError):
            after = 20.0
        return after if after > 0 else 0.0

    def _typing_notice_state(self) -> Dict[str, Dict[str, Any]]:
        notes = getattr(self, "_typing_notes", None)  # __new__-built adapters (tests) lack it
        if notes is None:
            notes = self._typing_notes = {}
        return notes

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        del metadata
        key = str(chat_id)
        notes = self._typing_notice_state()
        state = notes.setdefault(key, {"start": time.time(), "sent": False})
        if state["sent"]:
            return
        after = self._typing_notice_after()
        if after <= 0 or time.time() - state["start"] < after:
            return
        state["sent"] = True
        text = str(getattr(self.config, "typing_status_text", None) or self.TYPING_NOTICE_TEXT)
        # Background task: base bounds each send_typing() call at ~1.5s; a real POST can exceed that.
        task = asyncio.create_task(self._send_text(key, text))
        tasks = getattr(self, "_typing_notice_tasks", None)
        if tasks is None:
            tasks = self._typing_notice_tasks = set()
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        logger.info("[WecomCallback] Thinking notice sent to %s (%.0fs into the turn)", key, after)

    async def stop_typing(self, chat_id: str) -> None:
        """Turn over — reset so the next turn can fire its own notice."""
        self._typing_notice_state().pop(str(chat_id), None)

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        """Real outbound reply: clear the per-turn notice state first, then deliver."""
        self._typing_notice_state().pop(str(chat_id), None)
        return await self._send_text(chat_id, content, reply_to=reply_to, metadata=metadata)

'''


def main() -> int:
    if not TARGET.exists():
        print(f"FAIL: {TARGET} not found")
        return 1
    text = TARGET.read_text(encoding="utf-8")

    if MARK in text:
        print("already patched — nothing to do")
        return 0

    for name, anchor in (("__init__", INIT_ANCHOR), ("send", SEND_ANCHOR)):
        count = text.count(anchor)
        if count != 1:
            print(f"FAIL: anchor '{name}' matched {count} times (need exactly 1) — file changed upstream, re-derive the patch")
            return 1

    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak-typingmsg"))  # in-tree backup of the pristine file
    patched = text.replace(INIT_ANCHOR, INIT_ANCHOR + INIT_ADD, 1).replace(
        SEND_ANCHOR, SEND_ADD + SEND_ANCHOR.replace("    async def send(", "    async def _send_text(", 1), 1
    )
    if MARK not in patched or patched.count("async def _send_text(") != 1:
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
