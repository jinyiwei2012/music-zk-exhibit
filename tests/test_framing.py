"""framing 与 reference-core 权威向量对拍(向量抄自 rust/reference-core/src/lib.rs 测试)。

权威实现在 Rust;Python 实现必须逐字节一致。
"""

from __future__ import annotations

import pytest

from music_zk.verifier.framing import (
    PROTOCOL_ID,
    SALT_LEN,
    FramingError,
    commit_midi,
    commit_reference_wav,
    commit_song,
    protocol_hash,
)


def test_commit_midi_known_vector() -> None:
    # b"midi-bytes\x01\x02", r=[0xAB;32](与 Rust 测试同一向量)
    m = b"midi-bytes\x01\x02"
    r = bytes([0xAB]) * SALT_LEN
    expect = bytes.fromhex("201cab1270165ec9578590c6d1342dccf6a0203a792f08de45112f651dbe4b83")
    assert commit_midi(m, r) == expect


def test_commit_midi_empty_known_vector() -> None:
    r = bytes([0xAB]) * SALT_LEN
    expect = bytes.fromhex("f8408866541352f6ee7740b9c2f459726c7b9d77bac90547a7e05ef3d22ce1c4")
    assert commit_midi(b"", r) == expect


def test_commit_reference_wav_known_vector() -> None:
    expect = bytes.fromhex("cf8747a4eb32214c65437841b5335d7a92fb5270defeeefd1072f9ca0ae2ad76")
    assert commit_reference_wav(b"\x00\x01\x02\x03") == expect


def test_commit_song_known_vector() -> None:
    expect = bytes.fromhex("0a808823a6eeb007261732dd468b052e4a99af2694f00b20dfcfebee169fd481")
    assert commit_song(b"song-bytes") == expect


def test_protocol_hash_known_vector() -> None:
    expect = bytes.fromhex("ecbd2763a2307149207dc579579458956dc6ecad8237f9d73301bab7ac0c6da5")
    assert protocol_hash(PROTOCOL_ID) == expect


def test_framing_is_not_plain_hash() -> None:
    # 域分离检查:CommitMidi 不等于 SHA256(M || r)
    import hashlib

    m = b"plain"
    r = bytes([1]) * SALT_LEN
    plain = hashlib.sha256(m + r).digest()
    assert commit_midi(m, r) != plain


def test_framing_length_prefix_distinguishes() -> None:
    # 长度前缀使不同切分的拼接产生不同承诺
    r = bytes([2]) * SALT_LEN
    assert commit_midi(b"ab", r) != commit_midi(b"a", r)


def test_salt_length_must_be_32() -> None:
    with pytest.raises(FramingError):
        commit_midi(b"m", b"short-salt")
