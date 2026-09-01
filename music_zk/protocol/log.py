"""透明日志事件层(Phase 3,SPEC §11.2-11.3 / AGENTS.md §3.6)。

    event_id = SHA256( ASCII("MUSIC-ZK\\x00LOG-EVENT\\x00V1\\x00") || JCS(accepted_event_without_server_fields) )
    leaf     = SHA256(0x00 || JCS(server_event_record))        # merkle.py 的叶
    STH 签名 = Ed25519.Sign(server_sk, JCS(sth_body))          # SPEC §11.3,最小化选择(见 OPEN-QUESTIONS)

`\\x00` 是真实 0x00 字节。`server_event_record` = 被接受的事件 + 服务端附加的
**状态无关**字段(sequence / received_at_utc / event_id,SPEC §11.2)。

**tree_size/tree_root 不进叶**(OPEN-QUESTIONS 2026-09-01):SPEC §11.2 把二者列入
"服务端附加字段",但叶含自身根会造成循环依赖(根哈希依赖叶,叶又依赖根哈希)。
采用 CT 标准设计(RFC 6962):叶只含与树状态无关的数据,size↔root 绑定由每次
append 后独立签名的 STH 承担;回执 = 事件记录 + STH + inclusion proof 三件套。
"""

from __future__ import annotations

import hashlib
from typing import Any

import nacl.exceptions
import nacl.signing

from .jcs import canonicalize

# 域分离前缀(ASCII 字面量中的 \x00 是真实 0x00 字节;SPEC §11.2)
LOG_EVENT_PREFIX = b"MUSIC-ZK\x00LOG-EVENT\x00V1\x00"


class LogError(ValueError):
    """事件记录/STH 参数非法或签名验证失败。"""


def event_id(accepted_event_without_server_fields: dict[str, Any]) -> str:
    """计算事件 ID(lowercase hex 64 字符)。

    入参是"去掉服务端附加字段后的被接受事件"(含 creator signature)。
    """
    if not isinstance(accepted_event_without_server_fields, dict):
        raise LogError("accepted_event 必须是 JSON 对象")
    h = hashlib.sha256()
    h.update(LOG_EVENT_PREFIX)
    h.update(canonicalize(accepted_event_without_server_fields))
    return h.hexdigest()


def server_event_record(
    accepted_event: dict[str, Any],
    sequence: int,
    received_at_utc: str,
) -> dict[str, Any]:
    """构造叶内容/事件记录:accepted_event + 状态无关的服务端字段。

    不含 tree_size/tree_root(见模块 docstring 的循环依赖说明);事件记录经
    `jcs.canonicalize` 后喂给 merkle.append,或作为回执的事件部分。
    """
    if sequence < 0:
        raise LogError(f"sequence 不能为负: {sequence}")
    record = dict(accepted_event)
    record["sequence"] = sequence
    record["received_at_utc"] = received_at_utc
    record["event_id"] = event_id(accepted_event)  # 基于无服务端字段的版本
    return record


def sth_body(
    tree_size: int,
    tree_root: str,
    issued_at_utc: str,
    previous_tree_size: int,
    previous_tree_root: str,
) -> dict[str, Any]:
    """构造 Signed Tree Head 的可签名体(SPEC §11.3 五个字段)。"""
    if previous_tree_size > tree_size:
        raise LogError(f"previous_tree_size({previous_tree_size}) 不能大于 tree_size({tree_size})")
    return {
        "tree_size": tree_size,
        "tree_root": tree_root,
        "issued_at_utc": issued_at_utc,
        "previous_tree_size": previous_tree_size,
        "previous_tree_root": previous_tree_root,
    }


def sign_sth(server_sk_hex: str, sth: dict[str, Any]) -> str:
    """用服务端 Ed25519 密钥签署 STH;返回 lowercase hex 签名(64 字节)。

    签名对象 = JCS(sth_body)(SPEC 未定义 STH framing,采用最小化选择:直接签
    JCS,服务端密钥与 creator 密钥分离即天然区分;记录于 OPEN-QUESTIONS)。
    """
    sk = nacl.signing.SigningKey(bytes.fromhex(server_sk_hex))
    msg = canonicalize(sth)
    return sk.sign(msg).signature.hex()


def verify_sth(server_pk_hex: str, sth: dict[str, Any], signature_hex: str) -> None:
    """验证 STH 签名;失败抛 LogError。"""
    if len(signature_hex) != 128:
        raise LogError("STH 签名必须是 64 字节 lowercase hex")
    try:
        pk = nacl.signing.VerifyKey(bytes.fromhex(server_pk_hex))
    except Exception as e:  # noqa: BLE001
        raise LogError(f"服务端公钥 hex 非法: {e}") from e
    msg = canonicalize(sth)
    try:
        pk.verify(msg, bytes.fromhex(signature_hex))
    except nacl.exceptions.BadSignatureError as e:
        raise LogError("STH 签名验证失败") from e
