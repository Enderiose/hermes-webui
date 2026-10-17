"""Unit test for the WeCom crypto "empty receive_id" patch.

Run after applying /opt/data/patch/wecom_crypto_receiveid_patch.py:

    /opt/hermes/.venv/bin/python /opt/data/patch/test_wecom_crypto_receiveid_patch.py

Exit 0 + ALL_PASS means: a payload encrypted with an EMPTY receive_id is accepted, while a
non-empty-but-wrong receive_id is still rejected and a bad signature is still rejected.
"""
import sys

sys.path.insert(0, "/opt/hermes")

from plugins.platforms.wecom.wecom_crypto import SignatureError, WeComCryptoError, WXBizMsgCrypt

TOKEN = "test-token"
AES = "a" * 43  # 43 chars is all the constructor checks for
RECEIVE = "wwcorp123"

failures = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  <- {extra}"))
    if not cond:
        failures.append(name)


receiver = WXBizMsgCrypt(token=TOKEN, encoding_aes_key=AES, receive_id=RECEIVE)
sender = WXBizMsgCrypt(token=TOKEN, encoding_aes_key=AES, receive_id="whatever")


def envelope(encrypt: str, ts: str = "1700000000", nonce: str = "12345") -> str:
    from plugins.platforms.wecom.wecom_crypto import _sha1_signature
    return _sha1_signature(TOKEN, ts, nonce, encrypt)


# 1) empty receive_id in the payload → accepted (this is what a 群机器人 callback looks like)
sender.receive_id = ""
enc = sender._encrypt_bytes(b"hello-empty")
try:
    out = receiver.decrypt(envelope(enc), "1700000000", "12345", enc)
    check("empty receive_id accepted", out == b"hello-empty", out)
except Exception as exc:  # noqa: BLE001
    check("empty receive_id accepted", False, f"{type(exc).__name__}: {exc}")

# 2) matching non-empty receive_id → still accepted
sender.receive_id = RECEIVE
enc = sender._encrypt_bytes(b"hello-match")
try:
    out = receiver.decrypt(envelope(enc), "1700000000", "12345", enc)
    check("matching receive_id accepted", out == b"hello-match", out)
except Exception as exc:  # noqa: BLE001
    check("matching receive_id accepted", False, f"{type(exc).__name__}: {exc}")

# 3) wrong NON-empty receive_id → still rejected (the check must not be fully disabled)
sender.receive_id = "someone-else"
enc = sender._encrypt_bytes(b"hello-wrong")
try:
    receiver.decrypt(envelope(enc), "1700000000", "12345", enc)
    check("wrong non-empty receive_id rejected", False, "no exception")
except WeComCryptoError as exc:
    check("wrong non-empty receive_id rejected", "receive_id mismatch" in str(exc), exc)
except Exception as exc:  # noqa: BLE001
    check("wrong non-empty receive_id rejected", False, f"{type(exc).__name__}: {exc}")

# 4) bad signature → still rejected (authentication unchanged)
enc = sender._encrypt_bytes(b"hello-sig")
try:
    receiver.decrypt("deadbeef", "1700000000", "12345", enc)
    check("bad signature rejected", False, "no exception")
except SignatureError:
    check("bad signature rejected", True)
except Exception as exc:  # noqa: BLE001
    check("bad signature rejected", False, f"{type(exc).__name__}: {exc}")

print("\n" + ("ALL_PASS" if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)
