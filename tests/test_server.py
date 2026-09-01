"""服务端测试(SPEC §11.1/§14):happy path t0→t1→t2 + 负向(黑名单/签名/去重/顺序/
大小/verifier/C_V 绑定)+ checkpoint/inclusion 独立验算。

PROOF 的密码学复验用 stub verifier(真实 verifier 集成由 phase1 门禁覆盖);
C_V↔V 绑定与 journal 解析是真实逻辑。
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import nacl.signing
import pytest
from fastapi.testclient import TestClient

from music_zk.protocol.merkle import MerkleTree, verify_inclusion
from music_zk.protocol.signing import sign_event_body
from music_zk.server.app import create_app
from music_zk.server.store import Store
from music_zk.verifier.framing import commit_reference_wav, protocol_hash
from music_zk.verifier.journal import Journal

CREATOR_SK = "a0" * 32
CREATOR_PK = "b533d8ad9fcfbdde0b481c1b334ddc3c53412fd614564e7e5afd020368d382c3"
SERVER_SK = "1b" * 32
SERVER_PK = "1e2a137c7fe2279f9d7f0644030a0e9c0b45f781dce71ae4519c0f4384031654"
PROTOCOL = "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "log.sqlite", tmp_path / "data", SERVER_SK)
    yield s
    s.close()


@pytest.fixture()
def client(store: Store, tmp_path: Path):
    """stub verifier:任何调用返回 0(密码学复验由 phase1 门禁覆盖)。"""
    ok_script = tmp_path / "verify_ok.py"
    ok_script.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    app = create_app(store, verify_bin=f"{sys.executable} {ok_script.as_posix()}")
    return TestClient(app)


def _body(event_type: str, nonce: str, **payload: object) -> dict[str, object]:
    body: dict[str, object] = {
        "client_nonce": nonce,
        "creator_pubkey": CREATOR_PK,
        "event_type": event_type,
        "protocol_id": PROTOCOL,
        **payload,
    }
    return {"signature": sign_event_body(CREATOR_SK, body), **body}


def _fake_journal(c_v: bytes) -> bytes:
    return Journal(
        protocol_hash=protocol_hash(),
        creator_pubkey=bytes.fromhex(CREATOR_PK),
        commit_event_id=b"\x00" * 32,
        release_event_id=b"\x00" * 32,
        c_m=b"\x11" * 32,
        c_v=c_v,
    ).encode()


def _full_flow(client: TestClient) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """t0 COMMIT → t1 RELEASE → t2 PROOF,返回三个响应。"""
    r1 = client.post("/api/v1/commit-events", json=_body("COMMIT", "0" * 32, commit={"c_m": "11" * 32}))
    assert r1.status_code == 200, r1.text
    commit_resp = r1.json()

    r2 = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(_body(
            "RELEASE", "1" * 32,
            commit_event_id=commit_resp["event"]["event_id"],
            release={"c_s": "22" * 32, "song_file": {"name": "s.wav", "size": 4, "mime": "audio/wav"}},
        ))},
        files={"song": ("s.wav", b"abcd", "audio/wav")},
    )
    assert r2.status_code == 200, r2.text
    release_resp = r2.json()

    v = b"fake-wav-bytes-0000"
    journal = _fake_journal(commit_reference_wav(v))
    r3 = client.post(
        "/api/v1/proof-events",
        data={"json_body": json.dumps(_body(
            "PROOF", "2" * 32,
            commit_event_id=commit_resp["event"]["event_id"],
            release_event_id=release_resp["event"]["event_id"],
            proof={"c_v": commit_reference_wav(v).hex(), "v_hash": "33" * 32},
        ))},
        files={
            "v": ("ref.wav", v, "audio/wav"),
            "receipt": ("receipt.bin", b"fake-receipt", "application/octet-stream"),
            "journal": ("journal.bin", journal, "application/octet-stream"),
            "manifest": ("v1.json", b'{"guest":{"image_id":"00"}}', "application/json"),
        },
    )
    assert r3.status_code == 200, r3.text
    return commit_resp, release_resp, r3.json()


def test_full_flow_and_ordering(client: TestClient) -> None:
    c, rel, p = _full_flow(client)
    assert c["event"]["event_type"] == "COMMIT"
    assert rel["event"]["event_type"] == "RELEASE"
    assert p["event"]["event_type"] == "PROOF"
    # 顺序严格 COMMIT.seq < RELEASE.seq < PROOF.seq(SPEC §11.1)
    assert c["event"]["sequence"] < rel["event"]["sequence"] < p["event"]["sequence"]
    # STH 单调:tree_size = sequence
    assert c["sth"]["tree_size"] == 1
    assert rel["sth"]["tree_size"] == 2
    assert p["sth"]["tree_size"] == 3
    # STH 签名可验(服务端公钥)
    from music_zk.protocol.log import verify_sth

    sth = p["sth"]
    verify_sth(SERVER_PK, {k: sth[k] for k in (
        "tree_size", "tree_root", "issued_at_utc", "previous_tree_size", "previous_tree_root")},
        sth["signature"])


def test_checkpoint_entries_inclusion_independent_verify(client: TestClient) -> None:
    c, rel, p = _full_flow(client)
    cp = client.get("/api/v1/log/checkpoint")
    assert cp.status_code == 200
    sth = cp.json()
    assert sth["tree_size"] == 3

    # 独立验算:从 /log/entries 取全部记录重建树,根必须等于 checkpoint
    tree = MerkleTree()
    records: list[dict[str, object]] = []
    for seq in (1, 2, 3):
        e = client.get(f"/api/v1/log/entries/{seq}")
        assert e.status_code == 200, e.text
        records.append(e.json()["event"])
        tree.append(json.dumps(records[-1], ensure_ascii=False, sort_keys=True).encode())
    # 注意:叶内容是 record 的 JCS;entries 返回的 dict 需 JCS 化后比对
    from music_zk.protocol.jcs import canonicalize

    tree2 = MerkleTree()
    for rec in records:
        tree2.append(canonicalize(rec))
    assert tree2.root().hex() == sth["tree_root"]

    # 每个事件的 inclusion proof 用独立 verify_inclusion 验算
    for seq in (1, 2, 3):
        inc = client.get(f"/api/v1/log/inclusion/{seq}")
        assert inc.status_code == 200, inc.text
        proof = [bytes.fromhex(h) for h in inc.json()["inclusion_proof"]]
        assert verify_inclusion(
            canonicalize(inc.json()["event"]), seq - 1, proof,
            bytes.fromhex(sth["tree_root"]), 3,
        )
    # 负向:越界 sequence
    assert client.get("/api/v1/log/inclusion/99").status_code == 404
    assert client.get("/api/v1/log/entries/99").status_code == 404


def test_field_blacklist_rejected(client: TestClient) -> None:
    body = _body("COMMIT", "3" * 32, commit={"c_m": "11" * 32}, midi="secret")
    r = client.post("/api/v1/commit-events", json=body)
    assert r.status_code == 400
    assert "禁止上传" in r.json()["detail"]
    # 嵌套黑名单
    body2 = _body("COMMIT", "4" * 32, commit={"c_m": "11" * 32, "inner": {"salt": "x"}})
    assert client.post("/api/v1/commit-events", json=body2).status_code == 400


def test_bad_signature_rejected(client: TestClient) -> None:
    body = _body("COMMIT", "5" * 32, commit={"c_m": "11" * 32})
    body["signature"] = sign_event_body("bb" * 32, {k: v for k, v in body.items() if k != "signature"})
    r = client.post("/api/v1/commit-events", json=body)
    assert r.status_code == 401


def test_duplicate_nonce_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/commit-events", json=_body("COMMIT", "6" * 32, commit={"c_m": "11" * 32})).status_code == 200
    r = client.post("/api/v1/commit-events", json=_body("COMMIT", "6" * 32, commit={"c_m": "22" * 32}))
    assert r.status_code == 409


def test_event_type_mismatch_and_protocol(client: TestClient) -> None:
    body = _body("COMMIT", "7" * 32, commit={"c_m": "11" * 32})
    body["event_type"] = "RELEASE"
    assert client.post("/api/v1/commit-events", json=body).status_code == 400
    body2 = _body("COMMIT", "8" * 32, commit={"c_m": "11" * 32}, protocol_id="other/0")
    assert client.post("/api/v1/commit-events", json=body2).status_code == 400


def test_release_requires_existing_commit(client: TestClient) -> None:
    body = _body("RELEASE", "9" * 32, commit_event_id="ff" * 32, release={"c_s": "22" * 32})
    r = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(body)},
        files={"song": ("s.wav", b"abcd", "audio/wav")},
    )
    assert r.status_code == 404


def test_release_foreign_creator_rejected(client: TestClient) -> None:
    # 先提交自己的 COMMIT,再用他人公钥提交引用它的 RELEASE
    r1 = client.post("/api/v1/commit-events", json=_body("COMMIT", "a" * 32, commit={"c_m": "11" * 32}))
    cid = r1.json()["event"]["event_id"]
    foreign = {
        "client_nonce": "b" * 32,
        "creator_pubkey": "ca57eed30e4a7274ef4c648f56f58f880b20d2ca25725d9e5c13c83c08c09aeb",
        "event_type": "RELEASE",
        "protocol_id": PROTOCOL,
        "commit_event_id": cid,
        "release": {"c_s": "22" * 32},
    }
    foreign["signature"] = sign_event_body("cc" * 32, {k: v for k, v in foreign.items() if k != "signature"})
    r = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(foreign)},
        files={"song": ("s.wav", b"abcd", "audio/wav")},
    )
    assert r.status_code == 400


def test_proof_cv_mismatch_rejected(client: TestClient) -> None:
    c, rel, _ = _full_flow(client)
    v = b"another-v"
    journal = _fake_journal(commit_reference_wav(b"different-v"))  # 与上传 V 不一致
    r = client.post(
        "/api/v1/proof-events",
        data={"json_body": json.dumps(_body(
            "PROOF", "c" * 32,
            commit_event_id=c["event"]["event_id"],
            release_event_id=rel["event"]["event_id"],
            proof={"c_v": commit_reference_wav(v).hex()},
        ))},
        files={
            "v": ("ref.wav", v, "audio/wav"),
            "receipt": ("receipt.bin", b"fake", "application/octet-stream"),
            "journal": ("journal.bin", journal, "application/octet-stream"),
            "manifest": ("v1.json", b"{}", "application/json"),
        },
    )
    assert r.status_code == 400
    assert "C_V" in r.json()["detail"]


def test_proof_rejected_when_verifier_fails(store: Store, tmp_path: Path) -> None:
    fail_script = tmp_path / "verify_fail.py"
    fail_script.write_text("import sys\nsys.exit(1)\n", encoding="ascii")
    app = create_app(store, verify_bin=f"{sys.executable} {fail_script.as_posix()}")
    client = TestClient(app)
    # COMMIT + RELEASE 不需要 verifier,直接成功
    r1 = client.post("/api/v1/commit-events", json=_body("COMMIT", "0" * 32, commit={"c_m": "11" * 32}))
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(_body(
            "RELEASE", "1" * 32, commit_event_id=r1.json()["event"]["event_id"],
            release={"c_s": "22" * 32}))},
        files={"song": ("s.wav", b"abcd", "audio/wav")},
    )
    assert r2.status_code == 200, r2.text
    v = b"v-bytes"
    journal = _fake_journal(commit_reference_wav(v))
    r = client.post(
        "/api/v1/proof-events",
        data={"json_body": json.dumps(_body(
            "PROOF", "2" * 32,
            commit_event_id=r1.json()["event"]["event_id"],
            release_event_id=r2.json()["event"]["event_id"],
            proof={"c_v": commit_reference_wav(v).hex()},
        ))},
        files={
            "v": ("ref.wav", v, "audio/wav"),
            "receipt": ("receipt.bin", b"fake", "application/octet-stream"),
            "journal": ("journal.bin", journal, "application/octet-stream"),
            "manifest": ("v1.json", b"{}", "application/json"),
        },
    )
    assert r.status_code == 422
    assert "verifier" in r.json()["detail"]


def test_proof_requires_verifier(store: Store, tmp_path: Path) -> None:
    # 未配置 verifier → PROOF 必须被拒绝(服务端 MUST 本地验证才接受,SPEC §11.1)。
    # create_app(None) 会探测默认 C:/music-zk-target;存在 → 真实 verifier 拒绝假
    # receipt(422);不存在 → 500。两者都满足"不接受的 PROOF 不允许入库"。
    app = create_app(store, verify_bin=None)
    client = TestClient(app)
    r1 = client.post("/api/v1/commit-events", json=_body("COMMIT", "e" * 32, commit={"c_m": "11" * 32}))
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(_body(
            "RELEASE", "f" * 32, commit_event_id=r1.json()["event"]["event_id"],
            release={"c_s": "22" * 32}))},
        files={"song": ("s.wav", b"abcd", "audio/wav")},
    )
    assert r2.status_code == 200, r2.text
    v = b"vv"
    r3 = client.post(
        "/api/v1/proof-events",
        data={"json_body": json.dumps(_body(
            "PROOF", "0f" * 16,
            commit_event_id=r1.json()["event"]["event_id"],
            release_event_id=r2.json()["event"]["event_id"],
            proof={"c_v": commit_reference_wav(v).hex()}))},
        files={
            "v": ("v.wav", v, "audio/wav"),
            "receipt": ("r.bin", b"x", "application/octet-stream"),
            "journal": ("j.bin", _fake_journal(commit_reference_wav(v)), "application/octet-stream"),
            "manifest": ("m.json", b"{}", "application/json"),
        },
    )
    assert r3.status_code in (422, 500), r3.text
    assert r3.status_code != 200


def test_song_size_limit(store: Store, tmp_path: Path) -> None:
    from music_zk.server.app import MAX_SONG

    ok_script = tmp_path / "v_ok.py"
    ok_script.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    client = TestClient(create_app(store, verify_bin=f"{sys.executable} {ok_script.as_posix()}"))
    r = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(_body(
            "RELEASE", "00" * 16, commit_event_id="00" * 32, release={"c_s": "22" * 32}))},
        files={"song": ("s.wav", b"x" * (MAX_SONG + 1), "audio/wav")},
    )
    assert r.status_code == 413


def test_empty_checkpoint_404(store: Store, tmp_path: Path) -> None:
    ok_script = tmp_path / "v_ok2.py"
    ok_script.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    client = TestClient(create_app(store, verify_bin=f"{sys.executable} {ok_script.as_posix()}"))
    assert client.get("/api/v1/log/checkpoint").status_code == 404


def test_claims_endpoint(client: TestClient) -> None:
    c, rel, p = _full_flow(client)
    r = client.get(f"/api/v1/claims/{c['event']['event_id']}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["creator_pubkey"] == CREATOR_PK
    assert [e["event_type"] for e in data["events"]] == ["COMMIT", "RELEASE", "PROOF"]
    assert data["sth"]["tree_size"] == 3
    # 证据包 zip 可下载
    z = client.get(f"/api/v1/claims/{c['event']['event_id']}/evidence.zip")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"

