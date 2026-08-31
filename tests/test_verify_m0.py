"""M0Verify 骨架测试:语义层逻辑 + 可选真实 receipt 集成。

集成测试要求 MZK_VERIFY_BIN 环境变量指向 zkvm-verify 可执行入口(可含参数,
如 "wsl -e bash -lc 'source ~/.zk-env.sh && exec $CARGO_TARGET_DIR/debug/zkvm-verify'"),
并使用 proof-work/gate/pos/ 下的真实产物;未配置时跳过。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from music_zk.verifier.framing import PROTOCOL_ID, commit_midi, protocol_hash
from music_zk.verifier.journal import Journal
from music_zk.verifier.verify import M0Verify

POS_DIR = Path(__file__).resolve().parents[1] / "proof-work" / "gate" / "pos"
HAS_POS = (POS_DIR / "receipt.bin").exists() and (POS_DIR / "journal.bin").exists()


def _write_evidence(tmp_path: Path, midi: bytes, salt: bytes, journal: Journal) -> Path:
    (tmp_path / "midi.bin").write_bytes(midi)
    (tmp_path / "salt.bin").write_bytes(salt)
    (tmp_path / "journal.bin").write_bytes(journal.encode())
    return tmp_path


def _sample_witness() -> tuple[bytes, bytes, bytes]:
    midi = b"midi-bytes\x01\x02\x03\x04"
    salt = bytes(range(32))
    cm = commit_midi(midi, salt)
    return midi, salt, cm


def _make_journal(cm: bytes) -> Journal:
    return Journal(
        protocol_hash=protocol_hash(PROTOCOL_ID),
        creator_pubkey=bytes(32),
        commit_event_id=bytes(32),
        release_event_id=bytes(32),
        c_m=cm,
        c_v=bytes(32),
    )


def test_verify_ok_but_crypto_not_run_is_not_valid(tmp_path: Path) -> None:
    midi, salt, cm = _sample_witness()
    d = _write_evidence(tmp_path, midi, salt, _make_journal(cm))
    r = M0Verify(d, t0_commit_hex=cm.hex()).verify()
    assert r.journal_structure_ok
    assert r.protocol_hash_ok
    assert r.c_m_recompute_ok
    assert r.t0_bind_ok is True
    assert r.crypto_ok is None  # 未提供 verify_bin
    assert r.overall is False  # 密码学复验未执行 → 不得宣称有效
    assert "密码学证明无效" in r.render()


def test_verify_rejects_tampered_midi(tmp_path: Path) -> None:
    midi, salt, cm = _sample_witness()
    d = _write_evidence(tmp_path, midi, salt, _make_journal(cm))
    # 篡改本地 midi 一字节(verifier 独立重算 C_M → 与 journal 不符)
    d.joinpath("midi.bin").write_bytes(b"X" + midi[1:])
    r = M0Verify(d).verify()
    assert r.c_m_recompute_ok is False
    assert r.overall is False


def test_verify_rejects_wrong_salt(tmp_path: Path) -> None:
    midi, salt, cm = _sample_witness()
    d = _write_evidence(tmp_path, midi, salt, _make_journal(cm))
    d.joinpath("salt.bin").write_bytes(bytes([salt[0] ^ 0xFF]) + salt[1:])
    r = M0Verify(d).verify()
    assert r.c_m_recompute_ok is False


def test_verify_rejects_t0_mismatch(tmp_path: Path) -> None:
    midi, salt, cm = _sample_witness()
    d = _write_evidence(tmp_path, midi, salt, _make_journal(cm))
    wrong_t0 = bytes([0xFF]) * 32
    r = M0Verify(d, t0_commit_hex=wrong_t0.hex()).verify()
    assert r.c_m_recompute_ok
    assert r.t0_bind_ok is False


def test_verify_rejects_bad_protocol_hash(tmp_path: Path) -> None:
    midi, salt, cm = _sample_witness()
    j = _make_journal(cm)
    j = Journal(
        protocol_hash=bytes(32),  # 非 PROTOCOL_ID 的哈希
        creator_pubkey=j.creator_pubkey,
        commit_event_id=j.commit_event_id,
        release_event_id=j.release_event_id,
        c_m=j.c_m,
        c_v=j.c_v,
    )
    d = _write_evidence(tmp_path, midi, salt, j)
    r = M0Verify(d).verify()
    assert r.protocol_hash_ok is False


@pytest.mark.skipif(
    not os.environ.get("MZK_VERIFY_BIN") or not HAS_POS,
    reason="需要 MZK_VERIFY_BIN 环境变量与 proof-work/gate/pos 真实产物",
)
def test_integration_with_real_receipt() -> None:
    """真实 prove 产物(proof-work/gate/pos)全链路验证:1 正 4 负已由 scripts/phase1-m0.sh 覆盖,
    这里验证 Python M0Verify 语义层 + 委托 zkvm-verify 通过。"""
    t0 = (POS_DIR / "cm_t0.txt").read_text().strip()
    r = M0Verify(POS_DIR, t0_commit_hex=t0, verify_bin=os.environ["MZK_VERIFY_BIN"]).verify()
    assert r.journal_structure_ok
    assert r.protocol_hash_ok
    assert r.c_m_recompute_ok
    assert r.t0_bind_ok is True
    assert r.crypto_ok is True
    assert r.overall is True
