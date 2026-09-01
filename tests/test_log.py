"""透明日志事件层测试(SPEC §11.2-11.3):event_id 确定性 + STH 签名 + 篡改负向。"""

from __future__ import annotations

import pytest

from music_zk.protocol.log import (
    LOG_EVENT_PREFIX,
    LogError,
    event_id,
    server_event_record,
    sign_sth,
    sth_body,
    verify_sth,
)
from music_zk.protocol.merkle import MerkleTree, verify_inclusion
from music_zk.protocol.signing import sign_event_body

# 测试专用密钥
CREATOR_SK = "a0" * 32
CREATOR_PK = "b533d8ad9fcfbdde0b481c1b334ddc3c53412fd614564e7e5afd020368d382c3"
SERVER_SK = "1b" * 32
SERVER_PK = "1e2a137c7fe2279f9d7f0644030a0e9c0b45f781dce71ae4519c0f4384031654"


def _accepted_event() -> dict[str, object]:
    """一个已被服务端接受(含签名)的 COMMIT 事件。"""
    body = {
        "client_nonce": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "creator_pubkey": CREATOR_PK,
        "event_type": "COMMIT",
        "protocol_id": "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2",
        "commit": {"c_m": "0717cc993bef93ce97480167625612992f230690779944c9ab69f650cbb97c68"},
    }
    return {"signature": sign_event_body(CREATOR_SK, body), **body}


def test_event_id_deterministic_and_sorted() -> None:
    ev = _accepted_event()
    id1 = event_id(ev)
    # 键序无关:乱序构造同一事件 → 同 ID
    scrambled = {k: ev[k] for k in reversed(list(ev.keys()))}
    assert event_id(scrambled) == id1
    assert len(id1) == 64


def test_event_id_prefix_contains_real_null_bytes() -> None:
    assert LOG_EVENT_PREFIX == b"MUSIC-ZK\x00LOG-EVENT\x00V1\x00"
    assert b"\x00" in LOG_EVENT_PREFIX


def test_event_id_changes_when_event_changes() -> None:
    ev = _accepted_event()
    id1 = event_id(ev)
    ev2 = dict(ev)
    ev2["commit"] = {"c_m": "0" * 64}
    assert event_id(ev2) != id1


def test_event_id_excludes_server_fields() -> None:
    # event_id 只覆盖 accepted_event 本体:附加服务端字段不改变它
    ev = _accepted_event()
    base_id = event_id(ev)
    record = server_event_record(ev, sequence=0, received_at_utc="2026-09-01T00:00:00Z")
    # 注意 server_event_record 里 event_id 是附加字段,不参与自身计算
    assert record["event_id"] == base_id


def test_server_event_record_shape() -> None:
    ev = _accepted_event()
    record = server_event_record(ev, sequence=3, received_at_utc="2026-09-01T00:00:00Z")
    assert record["sequence"] == 3
    assert record["received_at_utc"] == "2026-09-01T00:00:00Z"
    # 叶不携带 tree_size/tree_root(循环依赖,见 OPEN-QUESTIONS;它们归 STH)
    assert "tree_size" not in record
    assert "tree_root" not in record
    # 原事件字段保留
    assert record["event_type"] == "COMMIT"
    assert record["signature"] == ev["signature"]


def test_full_log_flow_commit_release_proof() -> None:
    """COMMIT → RELEASE → PROOF 三事件入日志,每个叶 inclusion proof 可验。"""
    tree = MerkleTree()
    events: list[dict[str, object]] = []
    for i, (etype, payload) in enumerate(
        [
            ("COMMIT", {"c_m": "11" * 32}),
            ("RELEASE", {"c_s": "22" * 32, "commit_event_id": "33" * 32}),
            ("PROOF", {"c_v": "44" * 32, "commit_event_id": "33" * 32, "release_event_id": "55" * 32}),
        ]
    ):
        body = {
            "client_nonce": f"{i:032x}",
            "creator_pubkey": CREATOR_PK,
            "event_type": etype,
            "protocol_id": "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2",
            **payload,
        }
        ev = {"signature": sign_event_body(CREATOR_SK, body), **body}
        events.append(ev)

    from music_zk.protocol.jcs import canonicalize

    roots: list[bytes] = []
    prev_size, prev_root = 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    for i, ev in enumerate(events):
        # 事件记录冻结(状态无关字段)→ append → 根确定
        record = server_event_record(ev, sequence=i, received_at_utc="2026-09-01T00:00:00Z")
        tree.append(canonicalize(record))
        root = tree.root()
        roots.append(root)
        # 每个事件独立验 inclusion(用冻结的记录)
        proof = tree.inclusion_proof(i)
        from music_zk.protocol.merkle import verify_inclusion

        assert verify_inclusion(canonicalize(record), i, proof, root, i + 1)
        # 服务端每次 append 后签署 STH(绑定 tree_size ↔ tree_root)
        sth = sth_body(
            tree_size=i + 1, tree_root=root.hex(), issued_at_utc="2026-09-01T00:00:00Z",
            previous_tree_size=prev_size, previous_tree_root=prev_root,
        )
        sig = sign_sth(SERVER_SK, sth)
        verify_sth(SERVER_PK, sth, sig)
        prev_size, prev_root = i + 1, root.hex()


def test_sth_tamper_fails() -> None:
    sth = sth_body(1, "ab" * 32, "2026-09-01T00:00:00Z", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    sig = sign_sth(SERVER_SK, sth)
    sth2 = dict(sth)
    sth2["tree_size"] = 2
    with pytest.raises(LogError):
        verify_sth(SERVER_PK, sth2, sig)


def test_sth_wrong_server_key_fails() -> None:
    sth = sth_body(1, "ab" * 32, "2026-09-01T00:00:00Z", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    sig = sign_sth(SERVER_SK, sth)
    other_pk = "8b4a9b2e5f3c1d7e9a0b2c4d6e8f0a1b3c5d7e9f0a1b2c3d4e5f6a7b8c9d0e1e"
    with pytest.raises(LogError):
        verify_sth(other_pk, sth, sig)


def test_sth_previous_size_validation() -> None:
    with pytest.raises(LogError):
        sth_body(1, "ab" * 32, "2026-09-01T00:00:00Z", 5, "cd" * 32)
