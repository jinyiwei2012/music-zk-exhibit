"""创作者事件签名(Phase 3,SPEC §10.2 / AGENTS.md §3.6)。

    creator_signature = Ed25519.Sign(
        creator_private_key,
        ASCII("MUSIC-ZK\\x00CREATOR-EVENT\\x00V1\\x00") || JCS(event_body)
    )

`\\x00` 是真实 0x00 字节(与 framing.py 的域分离前缀同一约定);event_body 是
不含 signature 字段的 JSON 对象(键为 str、值 JSON 可表示)。验证时用同一 framing
重建并 `Ed25519.Verify`,任一字节不同即失败。
"""

from __future__ import annotations

from typing import Any

import nacl.exceptions
import nacl.signing
from nacl.encoding import HexEncoder

from .jcs import canonicalize

# 域分离前缀(ASCII 字面量中的 \x00 是真实 0x00 字节;SPEC §10.2)
CREATOR_EVENT_PREFIX = b"MUSIC-ZK\x00CREATOR-EVENT\x00V1\x00"

# 事件 body 必须包含的字段(SPEC §10.2 / §11.1)
REQUIRED_BODY_FIELDS = ("client_nonce", "creator_pubkey", "event_type", "protocol_id")


class SignatureError(ValueError):
    """签名 framing 参数非法或验证失败。"""


def _check_body(body: dict[str, Any]) -> None:
    if not isinstance(body, dict):
        raise SignatureError("event_body 必须是 JSON 对象(dict)")
    missing = [f for f in REQUIRED_BODY_FIELDS if f not in body]
    if missing:
        raise SignatureError(f"event_body 缺少必需字段: {missing}")
    nonce = body["client_nonce"]
    # 16 字节随机 nonce 以 32 字符 lowercase hex 表示(SPEC §10.2 / §3.6)
    if not (isinstance(nonce, str) and len(nonce) == 32 and _is_hex(nonce)):
        raise SignatureError("client_nonce 必须是 16 字节 nonce 的 lowercase hex(32 字符)")


def _is_hex(s: str) -> bool:
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


def sign_event_body(sk_hex: str, body: dict[str, Any]) -> str:
    """对 event_body 签名,返回 lowercase hex 的 Ed25519 签名(64 字节)。

    sk_hex 是 32 字节 Ed25519 种子/私钥的 lowercase hex(与 identity init 写入
    `creator-secret/creator-private-key` 的格式一致)。
    """
    _check_body(body)
    signing_key = nacl.signing.SigningKey(bytes.fromhex(sk_hex))
    msg = CREATOR_EVENT_PREFIX + canonicalize(body)
    sig = signing_key.sign(msg).signature
    return sig.hex()


def verify_event_signature(
    pk_hex: str, body: dict[str, Any], signature_hex: str
) -> None:
    """验证 creator 签名;失败抛 SignatureError。

    pk_hex 是 32 字节公钥的 lowercase hex;signature_hex 是 64 字节签名的 lowercase hex。
    """
    _check_body(body)
    if len(signature_hex) != 128:
        raise SignatureError("signature 必须是 64 字节的 lowercase hex")
    try:
        pubkey = nacl.signing.VerifyKey(bytes.fromhex(pk_hex))
    except Exception as e:  # noqa: BLE001 - nacl 解码异常类型不定
        raise SignatureError(f"公钥 hex 非法: {e}") from e
    msg = CREATOR_EVENT_PREFIX + canonicalize(body)
    try:
        pubkey.verify(msg, bytes.fromhex(signature_hex))
    except nacl.exceptions.BadSignatureError as e:
        raise SignatureError("签名验证失败(消息或签名不一致)") from e
