#!/usr/bin/env python3
"""Re-apply the WeCom crypto "empty receive_id" patch.

WHY THIS SCRIPT EXISTS
----------------------
`/opt/hermes` is the IMAGE layer (only `/opt/data` and `/home/agent/.hermes` are volumes) —
every container recreate / image upgrade restores the pristine `wecom_crypto.py` and the
relaxation silently disappears. Re-run after every redeploy (`apply-all.sh` calls it).

    /opt/hermes/.venv/bin/python /opt/data/patch/wecom_crypto_receiveid_patch.py

Idempotent: prints "already patched" and exits 0 when the marker is present.
Restart the gateway afterwards (SIGTERM; PID 1 revives it in ~10 s).

WHAT IT CHANGES
---------------
`WXBizMsgCrypt.decrypt()` rejects a payload whose embedded receive_id differs from the
configured one. A WeCom **self-built app** always embeds the corp_id, so that check is
useful there — but a 群机器人 / 智能机器人 (and third-party robot panels) encrypt with an
**empty** receive_id, which made every URL-verification handshake fail with
`DecryptError: receive_id mismatch` → the vendor UI showed 403.

After the patch the check is skipped only when the payload's receive_id is EMPTY; a
non-empty but wrong receive_id still raises. The signature check (shared token) is
untouched, so authentication is unchanged.
"""

from __future__ import annotations

import pathlib
import py_compile
import shutil
import sys

TARGET = pathlib.Path("/opt/hermes/plugins/platforms/wecom/wecom_crypto.py")
PATCHED_COPY = pathlib.Path("/opt/data/patch/wecom_crypto.py.patched")
MARK = "[patch] accept an empty receive_id"

ANCHOR = "        if receive_id != self.receive_id:\n            raise DecryptError(\"receive_id mismatch\")\n"

REPLACEMENT = (
    "        # [patch] accept an empty receive_id (group-robot / third-party callbacks encrypt without one)\n"
    "        if receive_id and receive_id != self.receive_id:\n"
    "            raise DecryptError(\"receive_id mismatch\")\n"
)


def main() -> int:
    if not TARGET.exists():
        print(f"FAIL: {TARGET} not found")
        return 1
    text = TARGET.read_text(encoding="utf-8")
    if MARK in text:
        print("already patched — nothing to do")
        return 0
    if text.count(ANCHOR) != 1:
        print(f"FAIL: anchor matched {text.count(ANCHOR)} times (need exactly 1) — file changed upstream, re-derive the patch")
        return 1

    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak-receiveid"))
    patched = text.replace(ANCHOR, REPLACEMENT, 1)
    if MARK not in patched:
        print("FAIL: patch assembly did not produce the expected shape")
        return 1
    TARGET.write_text(patched, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
    PATCHED_COPY.write_text(patched, encoding="utf-8")
    print(f"patched {TARGET} (+{len(patched) - len(text)} bytes); durable copy: {PATCHED_COPY}")
    print("restart the gateway to load it (SIGTERM → PID 1 revives it in ~10s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
