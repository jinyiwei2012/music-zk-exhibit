"""journal 编解码测试(参考 Rust 同名测试,偏移按 AGENTS.md §3.3 表格)。"""

from __future__ import annotations

import pytest

from music_zk.verifier.journal import (
    JOURNAL_LEN,
    JOURNAL_MAGIC,
    STATEMENT_VERSION,
    BadLength,
    BadMagic,
    BadVersion,
    Journal,
    JournalError,
)


def _zero_journal() -> Journal:
    return Journal(
        protocol_hash=bytes(32),
        creator_pubkey=bytes(32),
        commit_event_id=bytes(32),
        release_event_id=bytes(32),
        c_m=bytes(32),
        c_v=bytes(32),
    )


def test_roundtrip() -> None:
    j = Journal(
        protocol_hash=bytes([1]) * 32,
        creator_pubkey=bytes([2]) * 32,
        commit_event_id=bytes([3]) * 32,
        release_event_id=bytes([4]) * 32,
        c_m=bytes([5]) * 32,
        c_v=bytes([6]) * 32,
    )
    enc = j.encode()
    assert len(enc) == JOURNAL_LEN
    assert Journal.decode(enc) == j


def test_rejects_trailing_bytes() -> None:
    with pytest.raises(BadLength):
        Journal.decode(_zero_journal().encode() + b"\x00")


def test_rejects_bad_version() -> None:
    enc = bytearray(_zero_journal().encode())
    enc[9] = 2  # version 1 -> 2
    with pytest.raises(BadVersion):
        Journal.decode(bytes(enc))


def test_rejects_bad_magic() -> None:
    enc = bytearray(_zero_journal().encode())
    enc[0] = ord("X")
    with pytest.raises(BadMagic):
        Journal.decode(bytes(enc))


def test_offsets() -> None:
    # 逐字段偏移检查(SPEC §6.4 表格)
    j = _zero_journal()
    j = Journal(
        protocol_hash=bytes([0xA0]) + bytes(31),
        creator_pubkey=bytes(32),
        commit_event_id=bytes(32),
        release_event_id=bytes(32),
        c_m=bytes([0xB1]) + bytes(31),
        c_v=bytes(32),
    )
    enc = j.encode()
    assert enc[0:8] == JOURNAL_MAGIC
    assert enc[8:10] == STATEMENT_VERSION.to_bytes(2, "big")
    assert enc[10:11] == bytes([0xA0])
    assert enc[138:139] == bytes([0xB1])


def test_field_length_checked() -> None:
    with pytest.raises(JournalError):
        Journal(
            protocol_hash=b"short",
            creator_pubkey=bytes(32),
            commit_event_id=bytes(32),
            release_event_id=bytes(32),
            c_m=bytes(32),
            c_v=bytes(32),
        )
