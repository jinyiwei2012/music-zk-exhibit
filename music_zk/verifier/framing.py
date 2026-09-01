"""哈希 framing(SPEC §7 / AGENTS.md §3.2)。

与 `rust/reference-core` 逐字节一致;权威向量对拍见 tests/test_framing.py。
域分离前缀中的 `\\x00` 是真实 0x00 字节;长度前缀为 U64BE(8 字节大端)。
"""

from __future__ import annotations

import hashlib

# protocol_id(SPEC §5 / AGENTS.md §3.1,一个字符都不许改;2026-09-01 升为 statement-2)
PROTOCOL_ID = "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2"

# 域分离前缀(ASCII 字面量中的 \\x00 是真实 0x00 字节)
MIDI_COMMIT_PREFIX = b"MUSIC-ZK\x00MIDI-COMMIT\x00V1\x00"
REF_WAV_PREFIX = b"MUSIC-ZK\x00REF-WAV\x00V1\x00"
SONG_PREFIX = b"MUSIC-ZK\x00SONG\x00V1\x00"

# 盐长度(SPEC §6.1:恰好 32 字节)
SALT_LEN = 32


class FramingError(ValueError):
    """framing 参数非法(如盐长度不为 32)。"""


def _u64be(n: int) -> bytes:
    return n.to_bytes(8, "big")


def commit_midi(m: bytes, r: bytes) -> bytes:
    """CommitMidi(M, r) = SHA256("MUSIC-ZK\\0MIDI-COMMIT\\0V1\\0" || U64BE(len(M)) || M || r)。

    r 必须恰 32 字节,否则抛 FramingError。
    """
    if len(r) != SALT_LEN:
        raise FramingError(f"salt 必须恰 {SALT_LEN} 字节,收到 {len(r)}")
    h = hashlib.sha256()
    h.update(MIDI_COMMIT_PREFIX)
    h.update(_u64be(len(m)))
    h.update(m)
    h.update(r)
    return h.digest()


def commit_reference_wav(v: bytes) -> bytes:
    """CommitReferenceWav(V) = SHA256("MUSIC-ZK\\0REF-WAV\\0V1\\0" || U64BE(len(V)) || V)。"""
    h = hashlib.sha256()
    h.update(REF_WAV_PREFIX)
    h.update(_u64be(len(v)))
    h.update(v)
    return h.digest()


def commit_song(s: bytes) -> bytes:
    """CommitSong(S) = SHA256("MUSIC-ZK\\0SONG\\0V1\\0" || U64BE(len(S)) || S)。"""
    h = hashlib.sha256()
    h.update(SONG_PREFIX)
    h.update(_u64be(len(s)))
    h.update(s)
    return h.digest()


def protocol_hash(protocol_id: str = PROTOCOL_ID) -> bytes:
    """protocol_hash = SHA256(UTF8(protocol_id))(journal 字段)。"""
    return hashlib.sha256(protocol_id.encode("utf-8")).digest()
