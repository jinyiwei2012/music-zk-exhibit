"""Phase 4 测试:文案常量、reveal-check、evidence 验证(SPEC §15)、tamper 五案例、展示页。

evidence 包用真实协议逻辑构造(stub zkvm-verify),验证器逐项断言;每个 tamper
案例断言对应步骤失败且总体无效。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from music_zk.cli import demo
from music_zk.cli.identity import init_identity
from music_zk.protocol.jcs import canonicalize
from music_zk.protocol.log import server_event_record, sign_sth, sth_body
from music_zk.protocol.merkle import MerkleTree
from music_zk.protocol.signing import sign_event_body
from music_zk.server.store import Store
from music_zk.verifier.evidence import EvidenceVerifier, FROZEN_IMAGE_ID
from music_zk.verifier.framing import (
    PROTOCOL_ID,
    commit_midi,
    commit_reference_wav,
    commit_song,
    protocol_hash,
)
from music_zk.verifier.journal import Journal
from music_zk.web import copy as C

CREATOR_SK = "a0" * 32
CREATOR_PK = "b533d8ad9fcfbdde0b481c1b334ddc3c53412fd614564e7e5afd020368d382c3"
SERVER_SK = "1b" * 32
SERVER_PK = "1e2a137c7fe2279f9d7f0644030a0e9c0b45f781dce71ae4519c0f4384031654"


# ---------- 文案常量(红线 4) ----------

def test_copy_constants_exact() -> None:
    assert C.RESULT_TITLE == "结构化音乐材料的预先持有证明有效"
    assert C.SIMILARITY == "S/V similarity: not evaluated by this system"
    assert C.DEV_WARNING == "不是密码学证明"
    assert C.NOT_PROVEN_HEADER == "本系统不能证明"
    # LIMITATION 是"否定声明"文案:含"不证明…原创性"是合法固定文案,
    # 但不得出现"原创已验证"等肯定式徽章(见 test_copy_forbidden_badges_absent)


def test_copy_forbidden_badges_absent() -> None:
    # 所有文案常量不得含"原创已验证""非AI(已认证)"或等价措辞
    all_copy = " ".join([
        C.RESULT_TITLE, C.LIMITATION, C.SIMILARITY, C.DEV_WARNING, C.NOT_PROVEN_HEADER,
    ])
    assert "原创已验证" not in all_copy
    assert "非AI" not in all_copy
    assert "已认证" not in all_copy


# ---------- reveal-check ----------

def _commit_receipt(midi: bytes, salt: bytes, seq: int = 1) -> dict:
    body = {
        "client_nonce": "0" * 32,
        "creator_pubkey": CREATOR_PK,
        "event_type": "COMMIT",
        "protocol_id": PROTOCOL_ID,
        "commit": {"c_m": commit_midi(midi, salt).hex()},
    }
    return {"event": {"signature": sign_event_body(CREATOR_SK, body), **body},
            "c_m_hex": commit_midi(midi, salt).hex(),
            "server": {"event": {"sequence": seq, "event_id": "11" * 32}}}


def test_reveal_check_match(tmp_path: Path) -> None:
    midi, salt = b"MIDI", bytes(range(32))
    rec = tmp_path / "commit-receipt.json"
    rec.write_text(json.dumps(_commit_receipt(midi, salt)), encoding="utf-8")
    m, s = tmp_path / "m.mid", tmp_path / "s.bin"
    m.write_bytes(midi)
    s.write_bytes(salt)
    report = demo.reveal_check(m, s, rec)
    assert "打开" in report


def test_reveal_check_mismatch(tmp_path: Path) -> None:
    midi, salt = b"MIDI", bytes(range(32))
    rec = tmp_path / "commit-receipt.json"
    rec.write_text(json.dumps(_commit_receipt(midi, salt)), encoding="utf-8")
    m, s = tmp_path / "m.mid", tmp_path / "s.bin"
    m.write_bytes(b"MIDJ")  # 篡改一字节
    s.write_bytes(salt)
    assert "不打开" in demo.reveal_check(m, s, rec)


# ---------- evidence 包构造(真实协议逻辑) ----------

def _build_evidence(tmp_path: Path, store: Store, verify_stub: str) -> Path:
    """构造完整公开证据包(COMMIT→RELEASE→PROOF,stub verifier),返回目录。"""
    midi, salt = b"MIDI-data", bytes(range(32))
    song, v = b"WAV-song-data", b"WAV-reference-v"

    def signed(etype: str, nonce: str, payload: dict) -> dict:
        body = {"client_nonce": nonce, "creator_pubkey": CREATOR_PK,
                "event_type": etype, "protocol_id": PROTOCOL_ID, **payload}
        return {"signature": sign_event_body(CREATOR_SK, body), **body}

    c_m = commit_midi(midi, salt).hex()
    c_s = commit_song(song).hex()
    c_v = commit_reference_wav(v).hex()

    ev1 = signed("COMMIT", "0" * 32, {"commit": {"c_m": c_m}})
    row1, _ = store.append(ev1)
    ev2 = signed("RELEASE", "1" * 32, {
        "commit_event_id": row1.record["event_id"],
        "release": {"c_s": c_s, "song_file": {"name": "s.wav", "size": len(song)}},
    })
    row2, _ = store.append(ev2)
    journal = Journal(
        protocol_hash=protocol_hash(),
        creator_pubkey=bytes.fromhex(CREATOR_PK),
        commit_event_id=bytes.fromhex(row1.record["event_id"]),
        release_event_id=bytes.fromhex(row2.record["event_id"]),
        c_m=bytes.fromhex(c_m),
        c_v=bytes.fromhex(c_v),
    ).encode()
    ev3 = signed("PROOF", "2" * 32, {
        "commit_event_id": row1.record["event_id"],
        "release_event_id": row2.record["event_id"],
        "proof": {
            "c_v": c_v,
            "journal_hash": hashlib.sha256(journal).hexdigest(),
            "receipt_hash": hashlib.sha256(b"receipt").hexdigest(),
            "v_hash": hashlib.sha256(v).hexdigest(),
            "manifest_hash": hashlib.sha256(json.dumps(
                {"guest": {"image_id": FROZEN_IMAGE_ID}}).encode()).hexdigest(),
        },
    })
    store.publish_files(row1.event_id, {})
    store.publish_files(row2.event_id, {"song": song})
    row3, _ = store.append(ev3)
    store.publish_files(row3.event_id, {"v": v})

    # 组装证据包
    ev = tmp_path / "public-evidence"
    ev.mkdir()
    for row in (row1, row2, row3):
        _, proof, sth = store.inclusion_proof(row.sequence)
        rec = {
            "event": row.body,
            "record": row.record,
            "sth": {"tree_size": sth.tree_size, "tree_root": sth.tree_root,
                    "issued_at_utc": sth.issued_at_utc,
                    "previous_tree_size": sth.previous_tree_size,
                    "previous_tree_root": sth.previous_tree_root,
                    "signature": sth.signature},
            "inclusion_proof": [h.hex() for h in proof],
        }
        name = {"COMMIT": "commit", "RELEASE": "release", "PROOF": "proof"}[row.event_type]
        (ev / f"{name}-receipt.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    (ev / "claim.json").write_text(json.dumps(
        {"claim_id": row1.record["event_id"], "protocol_id": PROTOCOL_ID,
         "creator_pubkey": CREATOR_PK}), encoding="utf-8")
    (ev / "protocol-manifest.json").write_text(
        json.dumps({"guest": {"image_id": FROZEN_IMAGE_ID}}), encoding="utf-8")
    (ev / "creator-public-key.txt").write_text(CREATOR_PK + "\n", encoding="ascii")
    (ev / "journal.bin").write_bytes(journal)
    (ev / "zkvm-receipt.bin").write_bytes(b"receipt")
    (ev / "song-S.wav").write_bytes(song)
    (ev / "reference-V.wav").write_bytes(v)
    (ev / "VERIFYING.md").write_text("见 SPEC §12.2\n", encoding="utf-8")
    cs = []
    for f in sorted(ev.iterdir()):
        if f.name in ("checksums.sha256", "VERIFYING.md"):
            continue
        cs.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (ev / "checksums.sha256").write_text("\n".join(cs) + "\n", encoding="utf-8")
    return ev


@pytest.fixture()
def evidence_pkg(tmp_path: Path) -> Path:
    store = Store(tmp_path / "log.sqlite", tmp_path / "data", SERVER_SK)
    stub = tmp_path / "verify_ok.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    try:
        return _build_evidence(tmp_path, store, f"{sys.executable} {stub}")
    finally:
        store.close()


def test_evidence_verify_all_steps_pass(evidence_pkg: Path, tmp_path: Path) -> None:
    stub = tmp_path / "v.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    res = EvidenceVerifier(evidence_pkg, SERVER_PK, verify_bin=f"{sys.executable} {stub}").verify()
    assert res.step1_checksums is True
    assert res.step2_server_key is True
    assert res.step3_receipts is True
    assert res.step4_creator_sigs is True
    assert res.step5_ordering is True
    assert res.step6_c_s is True
    assert res.step7_c_v is True
    assert res.step8_manifest is True
    assert res.step9_crypto is True
    assert res.step10_journal is True
    assert res.overall is True
    assert "S/V similarity" in res.render()


@pytest.mark.parametrize(
    "tamper,step",
    [
        ("wav-sample", "step7_c_v"),
        ("log-receipt", "step3_receipts"),
        ("event-order", "step5_ordering"),
    ],
)
def test_evidence_tamper_detected(evidence_pkg: Path, tmp_path: Path, tamper: str, step: str) -> None:
    stub = tmp_path / "v.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="ascii")

    import shutil

    tampered = tmp_path / f"tampered-{tamper}"
    shutil.copytree(evidence_pkg, tampered)
    if tamper == "wav-sample":
        p = tampered / "reference-V.wav"
        p.write_bytes(bytes([p.read_bytes()[0] ^ 0xFF]) + p.read_bytes()[1:])
    elif tamper == "log-receipt":
        p = tampered / "commit-receipt.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["sth"]["tree_root"] = "00" * 32
        p.write_text(json.dumps(rec), encoding="utf-8")
    elif tamper == "event-order":
        c = json.loads((tampered / "commit-receipt.json").read_text(encoding="utf-8"))
        r = json.loads((tampered / "release-receipt.json").read_text(encoding="utf-8"))
        c["record"]["sequence"], r["record"]["sequence"] = r["record"]["sequence"], c["record"]["sequence"]
        (tampered / "commit-receipt.json").write_text(json.dumps(c), encoding="utf-8")
        (tampered / "release-receipt.json").write_text(json.dumps(r), encoding="utf-8")

    res = EvidenceVerifier(tampered, SERVER_PK, verify_bin=f"{sys.executable} {stub}").verify()
    assert getattr(res, step) is False
    assert res.overall is False


def test_evidence_checksum_failure_blocks_overall(evidence_pkg: Path, tmp_path: Path) -> None:
    # 步骤 1 失败 = 包损坏,不得显示总体有效(SPEC §15)
    stub = tmp_path / "v.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    (evidence_pkg / "checksums.sha256").write_text("00" * 64 + "  reference-V.wav\n", encoding="utf-8")
    res = EvidenceVerifier(evidence_pkg, SERVER_PK, verify_bin=f"{sys.executable} {stub}").verify()
    assert res.step1_checksums is False
    assert res.overall is False


def test_evidence_wrong_image_id_detected(evidence_pkg: Path, tmp_path: Path) -> None:
    stub = tmp_path / "v.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="ascii")
    # 负向:期望 Image ID 换成错误值(等价"错 Image ID")
    res = EvidenceVerifier(evidence_pkg, SERVER_PK, verify_bin=f"{sys.executable} {stub}",
                           expect_image_id="00" * 32).verify()
    assert res.step8_manifest is False
    assert res.overall is False


# ---------- demo tamper CLI 层 ----------

def test_demo_tamper_wav_sample(evidence_pkg: Path, tmp_path: Path) -> None:
    report = demo.run_tamper("wav-sample", evidence_pkg)
    assert "篡改被检出" in report
    assert "无效" in report


def test_demo_tamper_midi_byte(evidence_pkg: Path, tmp_path: Path) -> None:
    secret = tmp_path / "creator-secret"
    init_identity(secret)
    midi, salt = b"MIDI-data", bytes(range(32))
    (secret / "original.mid").write_bytes(midi)
    (secret / "salt.bin").write_bytes(salt)
    (secret / "commit-receipt.json").write_text(
        json.dumps(_commit_receipt(midi, salt)), encoding="utf-8")
    report = demo.run_tamper("midi-byte", evidence_pkg, secret)
    assert "篡改被检出" in report
    assert "不打开" in report


def test_demo_tamper_salt(evidence_pkg: Path, tmp_path: Path) -> None:
    secret = tmp_path / "creator-secret"
    init_identity(secret)
    midi, salt = b"MIDI-data", bytes(range(32))
    (secret / "original.mid").write_bytes(midi)
    (secret / "salt.bin").write_bytes(salt)
    (secret / "commit-receipt.json").write_text(
        json.dumps(_commit_receipt(midi, salt)), encoding="utf-8")
    report = demo.run_tamper("salt", evidence_pkg, secret)
    assert "篡改被检出" in report


# ---------- 展示页 ----------

def test_result_page_sections_and_order(evidence_pkg: Path, tmp_path: Path) -> None:
    # 直接从证据包回执构造 claim(无需真实 store)
    def _load(name: str) -> dict:
        return json.loads((evidence_pkg / name).read_text(encoding="utf-8"))

    c, r, p = _load("commit-receipt.json"), _load("release-receipt.json"), _load("proof-receipt.json")
    claim = {
        "claim_id": c["record"]["event_id"],
        "creator_pubkey": c["event"]["creator_pubkey"],
        "events": [
            {"sequence": c["record"]["sequence"], "event_type": "COMMIT", "event_id": c["record"]["event_id"]},
            {"sequence": r["record"]["sequence"], "event_type": "RELEASE", "event_id": r["record"]["event_id"]},
            {"sequence": p["record"]["sequence"], "event_type": "PROOF", "event_id": p["record"]["event_id"]},
        ],
    }
    from music_zk.web.pages import result_page

    page = result_page(claim)
    # 首屏顺序:结论 → 密码学已证明 → 播放器 → 不能证明 → 时间线 → 技术细节
    assert C.RESULT_TITLE in page
    assert C.SECTION_PROVEN in page
    assert C.SECTION_LISTEN in page
    assert "audio controls" in page
    assert C.NOT_PROVEN_HEADER in page
    assert "<details" in page and "open" in page  # 不能证明默认展开
    assert C.SECTION_TIMELINE in page
    assert C.SECTION_TECH in page
    assert C.SIMILARITY in page
    assert page.index(C.RESULT_TITLE) < page.index(C.SECTION_PROVEN) \
        < page.index(C.SECTION_LISTEN) < page.index(C.NOT_PROVEN_HEADER)
    # 无禁止徽章
    assert "原创已验证" not in page and "非AI" not in page
