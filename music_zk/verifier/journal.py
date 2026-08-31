"""Journal 编解码(SPEC §6.4 / AGENTS.md §3.3,总长固定 202 字节)。

验证器 MUST 拒绝尾随字节、未知版本、字段长度不符(长度不符天然由定长拒绝覆盖)。
"""

from __future__ import annotations

from dataclasses import dataclass

JOURNAL_LEN = 202
JOURNAL_MAGIC = b"MZKJNL01"
STATEMENT_VERSION = 1


class JournalError(ValueError):
    """journal 结构非法。"""


class BadLength(JournalError):
    """长度不为 202。"""


class BadMagic(JournalError):
    """magic 不匹配。"""


class BadVersion(JournalError):
    """statement_version 未知。"""


@dataclass(frozen=True)
class Journal:
    """§6.4 journal 字段(全部 32 字节)。"""

    protocol_hash: bytes
    creator_pubkey: bytes
    commit_event_id: bytes
    release_event_id: bytes
    c_m: bytes
    c_v: bytes

    def __post_init__(self) -> None:
        for name in ("protocol_hash", "creator_pubkey", "commit_event_id",
                     "release_event_id", "c_m", "c_v"):
            if len(getattr(self, name)) != 32:
                raise JournalError(f"{name} 必须 32 字节")

    def encode(self) -> bytes:
        """编码为定长 202 字节。"""
        b = bytearray(JOURNAL_LEN)
        b[0:8] = JOURNAL_MAGIC
        b[8:10] = STATEMENT_VERSION.to_bytes(2, "big")
        b[10:42] = self.protocol_hash
        b[42:74] = self.creator_pubkey
        b[74:106] = self.commit_event_id
        b[106:138] = self.release_event_id
        b[138:170] = self.c_m
        b[170:202] = self.c_v
        return bytes(b)

    @classmethod
    def decode(cls, b: bytes) -> "Journal":
        """解码;拒绝尾随字节、未知版本、magic 不匹配。"""
        if len(b) != JOURNAL_LEN:
            raise BadLength(f"journal 必须 {JOURNAL_LEN} 字节,收到 {len(b)}")
        if b[0:8] != JOURNAL_MAGIC:
            raise BadMagic(f"magic 不匹配:{b[0:8]!r}")
        version = int.from_bytes(b[8:10], "big")
        if version != STATEMENT_VERSION:
            raise BadVersion(f"statement_version 未知:{version}")
        return cls(
            protocol_hash=b[10:42],
            creator_pubkey=b[42:74],
            commit_event_id=b[74:106],
            release_event_id=b[106:138],
            c_m=b[138:170],
            c_v=b[170:202],
        )
