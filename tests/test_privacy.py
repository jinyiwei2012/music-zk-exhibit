"""SPEC §17.4 隐私测试。

断言:
  1. 服务端数据库、已发布文件、证据 zip 中不出现私有 MIDI 字节(M)、盐字节(r)
     ——即使服务端完整走通 t0→t1→t2、存下全部上传文件。
  2. witness 字段(midi/salt/private_key/witness)运行时递归拒绝(§11.1 黑名单)。
  3. 上传校验失败后临时目录零残留(崩溃残留,§17.4 第 3 条)。
  4. 服务端运行不留访问日志文件(§17.4 第 1 条"访问日志"面)。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from music_zk.protocol.signing import sign_event_body
from music_zk.server.app import create_app
from music_zk.server.store import Store
from music_zk.verifier.framing import commit_midi, commit_reference_wav, protocol_hash
from music_zk.verifier.journal import Journal

CREATOR_SK = "a0" * 32
CREATOR_PK = "b533d8ad9fcfbdde0b481c1b334ddc3c53412fd614564e7e5afd020368d382c3"
SERVER_SK = "1b" * 32
PROTOCOL = "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2"

# 模拟真实私密输入:任意 41 B MIDI + 32 B 盐(测试夹具,不进仓库)
M = bytes(range(0x30, 0x59))  # 41 字节
R = bytes(range(0x80, 0xA0))  # 32 字节
PUBLIC_SONG = b"public-song-bytes-not-the-midi"


def _find_all(hay: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    pos = hay.find(needle)
    while pos != -1:
        hits.append(pos)
        pos = hay.find(needle, pos + 1)
    return hits


def _scan_files(root: Path, needles: dict[bytes, str]) -> list[str]:
    leaks: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        data = p.read_bytes()
        for needle, label in needles.items():
            for off in _find_all(data, needle):
                leaks.append(f"{p.relative_to(root)} @ {off}: {label}")
    return leaks


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "log.sqlite", tmp_path / "data", SERVER_SK)
    yield s
    s.close()


@pytest.fixture()
def client(store: Store, tmp_path: Path):
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
        c_m=commit_midi(M, R),
        c_v=c_v,
    ).encode()


def _full_flow_with_private_inputs(client: TestClient) -> tuple[dict[str, object], ...]:
    """t0→t1→t2,输入用真实 M/r(只上传统承诺,绝不上传 M/r)。"""
    c_m = commit_midi(M, R).hex()
    r1 = client.post("/api/v1/commit-events", json=_body("COMMIT", "0" * 32, commit={"c_m": c_m}))
    assert r1.status_code == 200, r1.text
    commit_resp = r1.json()

    r2 = client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(_body(
            "RELEASE", "1" * 32,
            commit_event_id=commit_resp["event"]["event_id"],
            release={"c_s": commit_midi(PUBLIC_SONG, R).hex(),
                     "song_file": {"name": "s.wav", "size": len(PUBLIC_SONG), "mime": "audio/wav"}},
        ))},
        files={"song": ("s.wav", PUBLIC_SONG, "audio/wav")},
    )
    assert r2.status_code == 200, r2.text
    release_resp = r2.json()

    v = b"fake-reference-wav-0000"
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


def test_witness_fields_rejected_runtime(client: TestClient) -> None:
    """黑名单字段在运行时被递归拒绝(§11.1):midi/salt/private_key/witness。"""
    for field in ("midi", "salt", "private_key"):
        body = _body("COMMIT", "3" * 32, commit={"c_m": "11" * 32}, **{field: "witness-value"})
        r = client.post("/api/v1/commit-events", json=body)
        assert r.status_code == 400, f"{field}: {r.text}"
    # 嵌套字段同样拒绝(witness 递归扫描)
    r = client.post("/api/v1/commit-events",
                    json=_body("COMMIT", "4" * 32, commit={"c_m": "11" * 32, "meta": {"witness": "x"}}))
    assert r.status_code == 400, r.text


def test_server_storage_contains_no_private_bytes(client: TestClient, store: Store, tmp_path: Path) -> None:
    """完整 t0→t1→t2 后,数据库 + 已发布文件 + 日志记录中无 M/r/私钥字节。"""
    _full_flow_with_private_inputs(client)

    needles = {
        M: "私有 MIDI 字节 (M)",
        R: "盐字节 (r)",
        bytes.fromhex(CREATOR_SK): "创作者私钥字节",
    }
    leaks = _scan_files(store.db_path.parent, needles)
    # 事件记录(JSON 文本)也要查——字符串形式序列化的 M/r 不可能出现,但查证零命中
    for ev in store.events():
        text = json.dumps(ev.record, ensure_ascii=False).encode()
        for needle, label in needles.items():
            assert _find_all(text, needle) == [], f"{label} 出现在事件记录: {ev.event_type}"
    assert leaks == [], f"服务端存储出现私密字节: {leaks}"
    # 服务端不留访问日志文件(§17.4"访问日志"面)
    assert list(tmp_path.rglob("*.log")) == []


def test_evidence_zip_contains_no_private_bytes(client: TestClient) -> None:
    """证据 zip(公开面)不含 M/r/私钥字节。"""
    _, _, p = _full_flow_with_private_inputs(client)
    claim_id = p["event"]["event_id"]  # PROOF 无 claim_id;用 COMMIT 的 event_id 下载
    commit_resp = client.get("/api/v1/log/entries/1").json()
    r = client.get(f"/api/v1/claims/{commit_resp['event']['event_id']}/evidence.zip")
    assert r.status_code == 200, r.text
    zip_bytes = r.content
    assert zip_bytes[:2] == b"PK"
    for needle, label in ((M, "M"), (R, "r"), (bytes.fromhex(CREATOR_SK), "私钥")):
        assert _find_all(zip_bytes, needle) == [], f"证据 zip 命中 {label}"


def test_proof_rejected_temp_dir_cleaned(client: TestClient, store: Store, tmp_path: Path) -> None:
    """上传校验失败(verifier 拒绝)后,OS 临时目录不残留 witness 临时目录(§17.4 第 3 条)。"""
    fail_script = tmp_path / "verify_fail.py"
    fail_script.write_text("import sys\nsys.exit(1)\n", encoding="ascii")
    app = create_app(store, verify_bin=f"{sys.executable} {fail_script.as_posix()}")
    bad_client = TestClient(app)

    c_m = commit_midi(M, R).hex()
    r1 = bad_client.post("/api/v1/commit-events", json=_body("COMMIT", "5" * 32, commit={"c_m": c_m}))
    assert r1.status_code == 200
    r2 = bad_client.post(
        "/api/v1/release-events",
        data={"json_body": json.dumps(_body(
            "RELEASE", "6" * 32,
            commit_event_id=r1.json()["event"]["event_id"],
            release={"c_s": "22" * 32, "song_file": {"name": "s.wav", "size": 4, "mime": "audio/wav"}},
        ))},
        files={"song": ("s.wav", b"abcd", "audio/wav")},
    )
    assert r2.status_code == 200

    before = {d.name for d in Path(tempfile.gettempdir()).glob("mzk-proof-*")}
    journal = _fake_journal(commit_reference_wav(b"fake-reference-wav-0000"))
    r3 = bad_client.post(
        "/api/v1/proof-events",
        data={"json_body": json.dumps(_body(
            "PROOF", "7" * 32,
            commit_event_id=r1.json()["event"]["event_id"],
            release_event_id=r2.json()["event"]["event_id"],
            proof={"c_v": "11" * 32, "v_hash": "33" * 32},
        ))},
        files={
            "v": ("ref.wav", b"fake-reference-wav-0000", "audio/wav"),
            "receipt": ("receipt.bin", b"bad-receipt", "application/octet-stream"),
            "journal": ("journal.bin", journal, "application/octet-stream"),
            "manifest": ("v1.json", b'{"guest":{"image_id":"00"}}', "application/json"),
        },
    )
    assert r3.status_code == 422, r3.text  # verifier 拒绝 = 422(SPEC §11.1)
    after = {d.name for d in Path(tempfile.gettempdir()).glob("mzk-proof-*")}
    assert after == before, f"临时目录残留: {after - before}"
    # 临时目录即使残留也不得含 witness(双保险:残留目录内容扫描)
    for d in after - before:
        for needle, label in ((M, "M"), (R, "r")):
            assert _find_all((d / "journal").read_bytes(), needle) == [], f"{d} 命中 {label}"
