"""Unit test for the wecom_callback thinking-notice patch.

Run after applying /opt/data/patch/wecom_typing_notice_patch.py (and after any redeploy):

    /opt/hermes/.venv/bin/python /opt/data/patch/test_wecom_typing_notice_patch.py

Exit 0 + ALL_PASS means: silent until the threshold, exactly one notice per turn, state reset
by both send() and stop_typing(), typing_status_text override honoured, `<=0` disables.
"""
import asyncio
import sys
import time
import types

sys.path.insert(0, "/opt/hermes")

from gateway.config import PlatformConfig
from plugins.platforms.wecom.callback_adapter import WecomCallbackAdapter

SENT = []


def make(extra=None, typing_status_text=None):
    a = WecomCallbackAdapter.__new__(WecomCallbackAdapter)
    a.config = PlatformConfig(enabled=True, extra=extra or {}, typing_status_text=typing_status_text)
    a._typing_notes = {}
    a._typing_notice_tasks = set()

    async def _fake_send_text(chat_id, content, reply_to=None, metadata=None):
        SENT.append((chat_id, content))
        return types.SimpleNamespace(success=True, message_id="fake")

    a._send_text = _fake_send_text
    return a


async def main():
    failures = []

    def check(name, cond):
        print(("PASS  " if cond else "FAIL  ") + name)
        if not cond:
            failures.append(name)

    # 1) silent before the threshold, then fires exactly once
    SENT.clear()
    a = make({"typing_notice_after": 20})
    for _ in range(10):                                # many ticks right after turn start
        await a.send_typing("corp:user")
    await asyncio.sleep(0)
    check("no notice before threshold", SENT == [])
    a._typing_notes["corp:user"]["start"] -= 21        # simulate 21s into the turn
    await a.send_typing("corp:user")
    await asyncio.sleep(0)
    check("fires once after threshold", len(SENT) == 1 and SENT[0][0] == "corp:user")
    check("default text", SENT and SENT[0][1] == "⏳ 正在思考处理，请稍候…")
    for _ in range(5):                                 # more ticks, same turn
        await a.send_typing("corp:user")
    await asyncio.sleep(0)
    check("at most one notice per turn", len(SENT) == 1)

    # 2) a real reply resets state → the next turn gets its own notice
    await a.send("corp:user", "real answer")
    await asyncio.sleep(0)
    check("send() delivers the real reply", SENT[-1] == ("corp:user", "real answer"))
    check("send() clears the per-turn state", a._typing_notes == {})
    await a.send_typing("corp:user")                   # next turn, first tick
    await asyncio.sleep(0)
    check("next turn silent on first tick", len(SENT) == 2)
    a._typing_notes["corp:user"]["start"] -= 25
    await a.send_typing("corp:user")
    await asyncio.sleep(0)
    check("next turn fires its own notice", len(SENT) == 3)

    # 3) stop_typing() resets too
    await a.stop_typing("corp:user")
    check("stop_typing() clears state", a._typing_notes == {})

    # 4) text override and the disable knob
    SENT.clear()
    b = make({"typing_notice_after": 5}, typing_status_text="⏳ 自定义提示")
    b._typing_notes["u"] = {"start": time.time() - 6, "sent": False}
    await b.send_typing("u")
    await asyncio.sleep(0)
    check("typing_status_text override wins", SENT == [("u", "⏳ 自定义提示")])

    SENT.clear()
    c = make({"typing_notice_after": 0})
    c._typing_notes["u"] = {"start": time.time() - 600, "sent": False}
    await c.send_typing("u")
    await asyncio.sleep(0)
    check("typing_notice_after=0 disables", SENT == [])

    print("\n" + ("ALL_PASS" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
