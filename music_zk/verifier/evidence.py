"""公开证据包验证(Phase 4,SPEC §15 十一项)。

输入:`public-evidence/` 目录(SPEC §12.2 清单)+ 用户提供的信任根(server public key)。
每步独立布尔;总体有效要求步骤 2..10 全部成功;步骤 1 失败 = 包损坏(即使部分
文件单独通过,也不得显示总体有效)。

证据包 receipts 格式(commit/release/proof-receipt.json):
    {"event": 被接受事件(含 signature), "record": 服务端事件记录,
     "sth": 签署树头, "inclusion_proof": [hex 节点哈希, ...]}
"""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from music_zk.protocol.jcs import canonicalize
from music_zk.protocol.log import verify_sth
from music_zk.protocol.merkle import verify_inclusion
from music_zk.protocol.signing import verify_event_signature
from music_zk.web.copy import SIMILARITY
from music_zk.verifier.framing import (
    PROTOCOL_ID,
    commit_reference_wav,
    commit_song,
    protocol_hash,
)
from music_zk.verifier.journal import Journal, JournalError

# 冻结的 guest Image ID(protocol/v1.json guest.image_id;协议冻结后不得改)
FROZEN_IMAGE_ID = "5e06801b5e97e4c3d7bcbc99bf5432ff3fc4056a9cf71b4175038a7e895c7d8a"

# SPEC §12.2 公开证据包清单
REQUIRED_FILES = (
    "claim.json",
    "protocol-manifest.json",
    "creator-public-key.txt",
    "commit-receipt.json",
    "release-receipt.json",
    "proof-receipt.json",
    "journal.bin",
    "zkvm-receipt.bin",
    "song-S.bin",
    "reference-V.wav",
    "checksums.sha256",
)

# 三个回执文件名与事件类型
RECEIPTS = (
    ("commit-receipt.json", "COMMIT"),
    ("release-receipt.json", "RELEASE"),
    ("proof-receipt.json", "PROOF"),
)


class EvidenceError(ValueError):
    """证据包缺失/不可读。"""


@dataclass
class EvidenceResult:
    """SPEC §15 逐项结果(每步独立布尔)。"""

    step1_checksums: bool = False  # 传输完整性(失败 = 包损坏)
    step2_server_key: bool = False  # 信任根 = 用户提供值
    step3_receipts: bool = False  # 三回执:event_id + STH 签名 + inclusion
    step4_creator_sigs: bool = False  # 三类 creator 签名,公钥一致
    step5_ordering: bool = False  # COMMIT.seq < RELEASE.seq < PROOF.seq
    step6_c_s: bool = False  # 重算 C_S == release 事件
    step7_c_v: bool = False  # 重算 C_V == journal == proof 事件
    step8_manifest: bool = False  # manifest hash + Image ID == 冻结值
    step9_crypto: bool | None = None  # RISC Zero 复验(未执行 = None → 总体无效)
    step10_journal: bool = False  # journal 严格解析 + 上下文/C_M/C_V 一致
    notes: list[str] = field(default_factory=list)

    def step2_10_all_ok(self) -> bool:
        steps = [
            self.step2_server_key,
            self.step3_receipts,
            self.step4_creator_sigs,
            self.step5_ordering,
            self.step6_c_s,
            self.step7_c_v,
            self.step8_manifest,
        ]
        if self.step9_crypto is not None:
            steps.append(self.step9_crypto)
        else:
            return False  # 密码学复验未执行,不得宣称有效
        steps.append(self.step10_journal)
        return all(steps)

    @property
    def overall(self) -> bool:
        """总体有效 = 步骤 2..10 全过 且 步骤 1(包完整性)通过(SPEC §15:
        步骤 1 失败表示包损坏,即使部分文件单独通过也不得显示总体有效)。"""
        return self.step1_checksums and self.step2_10_all_ok()

    def render(self) -> str:
        def ok(b: bool | None) -> str:
            return "有效" if b else ("未执行" if b is None else "无效")

        lines = [
            "[1] 传输完整性(checksums.sha256):        " + ("有效" if self.step1_checksums else "无效/缺失"),
            "[2] 服务端公钥为指定信任根:              " + ok(self.step2_server_key),
            "[3] 三回执 + STH 签名 + inclusion proof: " + ok(self.step3_receipts),
            "[4] 三类 creator 签名(公钥一致):        " + ok(self.step4_creator_sigs),
            "[5] COMMIT.seq < RELEASE.seq < PROOF.seq: " + ok(self.step5_ordering),
            "[6] 公开 S 对应 release 事件(C_S):       " + ok(self.step6_c_s),
            "[7] 公开 V 对应 proof journal(C_V):      " + ok(self.step7_c_v),
            "[8] manifest hash + guest Image ID:       " + ok(self.step8_manifest),
            "[9] RISC Zero receipt 密码学复验:        " + ok(self.step9_crypto),
            "[10] journal 严格解析与上下文一致:       " + ok(self.step10_journal),
        ]
        for n in self.notes:
            lines.append(f"   注:{n}")
        lines.append(f"总体:密码学证明{'有效' if self.overall else '无效'}")
        lines.append(SIMILARITY)
        return "\n".join(lines)


def _read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise EvidenceError(f"读取 {what} 失败: {path}: {e}") from None


def _verify_checksums(evidence: Path, res: EvidenceResult) -> None:
    """步骤 1:逐行校验 checksums.sha256。"""
    cs = evidence / "checksums.sha256"
    if not cs.exists():
        res.notes.append("缺 checksums.sha256")
        return
    ok = True
    for line in cs.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            ok = False
            res.notes.append(f"checksums.sha256 行格式非法: {line!r}")
            continue
        digest, name = parts
        f = evidence / name
        if not f.exists():
            ok = False
            res.notes.append(f"checksums 列出的文件缺失: {name}")
            continue
        if hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            ok = False
            res.notes.append(f"checksum 不符: {name}")
    res.step1_checksums = ok


def _verify_receipt(receipt: dict, server_pk: str, notes: list[str]) -> bool:
    """单回执:event_id 重算 + STH 签名 + inclusion proof(SPEC §11.2/11.3)。"""
    from music_zk.protocol.log import event_id

    ok = True
    record = receipt.get("record")
    event = receipt.get("event")
    sth = receipt.get("sth")
    proof = receipt.get("inclusion_proof")
    if not (isinstance(record, dict) and isinstance(event, dict) and isinstance(sth, dict)):
        notes.append("回执缺 record/event/sth")
        return False
    # event_id:接受事件去掉服务端字段后重算
    server_fields = ("sequence", "received_at_utc", "event_id", "tree_size", "tree_root")
    accepted = {k: v for k, v in event.items() if k not in server_fields}
    if event_id(accepted) != record.get("event_id"):
        notes.append(f"event_id 重算不符({record.get('event_id', '?')[:16]}…)")
        ok = False
    # STH 签名(服务端信任根)
    try:
        sth_body = {k: sth[k] for k in (
            "tree_size", "tree_root", "issued_at_utc", "previous_tree_size", "previous_tree_root")}
        verify_sth(server_pk, sth_body, sth["signature"])
    except Exception as e:  # noqa: BLE001
        notes.append(f"STH 签名验证失败: {e}")
        ok = False
    # inclusion proof
    seq = record.get("sequence")
    if not isinstance(seq, int):
        notes.append("record 缺 sequence")
        return False
    if not isinstance(proof, list):
        notes.append("缺 inclusion_proof")
        return False
    try:
        root = bytes.fromhex(sth["tree_root"])
        proved = verify_inclusion(
            canonicalize(record), seq - 1, [bytes.fromhex(h) for h in proof],
            root, int(sth["tree_size"]),
        )
        if not proved:
            notes.append(f"inclusion proof 不成立(seq={seq})")
            ok = False
    except (KeyError, ValueError) as e:
        notes.append(f"inclusion proof 解析失败: {e}")
        ok = False
    return ok


class EvidenceVerifier:
    """SPEC §15 十一项验证器。"""

    def __init__(
        self,
        evidence_dir: str | Path,
        server_public_key: str,
        verify_bin: str | None = None,
        expect_image_id: str | None = None,
    ) -> None:
        """
        evidence_dir:公开证据包目录(SPEC §12.2)。
        server_public_key:用户主动选择的信任根(服务端公钥 hex)。
        verify_bin:标准 verifier 调用(默认 C:/music-zk-target/debug/zkvm-verify.exe)。
        expect_image_id:覆盖冻结 Image ID(负向测试用)。
        """
        self.evidence = Path(evidence_dir)
        self.server_pk = server_public_key
        self.verify_bin = verify_bin
        if verify_bin is None:
            candidate = Path("C:/music-zk-target/debug/zkvm-verify.exe")
            self.verify_bin = str(candidate) if candidate.exists() else None
        self.expect_image_id = expect_image_id or FROZEN_IMAGE_ID

    def verify(self) -> EvidenceResult:
        res = EvidenceResult()
        missing = [f for f in REQUIRED_FILES if not (self.evidence / f).exists()]
        if missing:
            res.notes.append(f"证据包缺文件: {missing}")
            # 仍继续执行能执行的步骤,但缺失项相关步骤会失败

        # 步骤 1:传输完整性(失败 = 包损坏,单独报告,不阻塞 2..10)
        _verify_checksums(self.evidence, res)

        # 步骤 2:信任根
        if len(self.server_pk) == 64:
            res.step2_server_key = True
        else:
            res.notes.append("server public key 不是 32 字节 hex")

        # 步骤 3:三回执
        res.step3_receipts = all(
            _verify_receipt(_read_json(self.evidence / f, f), self.server_pk, res.notes)
            for f, _ in RECEIPTS
        )

        # 步骤 4:creator 签名 + 公钥一致
        sig_ok = True
        pubkeys: set[str] = set()
        for f, _ in RECEIPTS:
            r = _read_json(self.evidence / f, f)
            event = r.get("event", {})
            pk = event.get("creator_pubkey")
            pubkeys.add(pk) if isinstance(pk, str) else None
            sig = event.get("signature")
            body = {k: v for k, v in event.items() if k != "signature"}
            try:
                verify_event_signature(pk, body, sig)
            except Exception as e:  # noqa: BLE001
                res.notes.append(f"{f} creator 签名失败: {e}")
                sig_ok = False
        res.step4_creator_sigs = sig_ok and len(pubkeys) == 1

        # 步骤 5:顺序
        seqs = []
        for f, _ in RECEIPTS:
            rec = _read_json(self.evidence / f, f).get("record", {})
            seqs.append(rec.get("sequence"))
        res.step5_ordering = (
            isinstance(seqs[0], int) and isinstance(seqs[1], int) and isinstance(seqs[2], int)
            and seqs[0] < seqs[1] < seqs[2]
        )
        if not res.step5_ordering:
            res.notes.append(f"事件顺序异常: {seqs}")

        # 步骤 6:C_S(文件名 = song-S.<原扩展名>,SPEC §12.2)
        try:
            song_files = sorted(self.evidence.glob("song-S.*"))
            if not song_files:
                res.notes.append("证据包缺 song-S.*")
            else:
                song = song_files[0].read_bytes()
                release = _read_json(self.evidence / "release-receipt.json", "release-receipt.json")
                c_s = release["event"]["release"]["c_s"]
                res.step6_c_s = commit_song(song).hex() == c_s
                if not res.step6_c_s:
                    res.notes.append("公开 S 的 C_S 与 release 事件不一致")
        except (OSError, KeyError) as e:
            res.notes.append(f"步骤 6 失败: {e}")

        # 步骤 7:C_V(与 journal 与 proof 事件)
        try:
            v = (self.evidence / "reference-V.wav").read_bytes()
            journal = Journal.decode((self.evidence / "journal.bin").read_bytes())
            proof = _read_json(self.evidence / "proof-receipt.json", "proof-receipt.json")
            c_v_event = proof["event"]["proof"]["c_v"]
            cv = commit_reference_wav(v).hex()
            res.step7_c_v = cv == journal.c_v.hex() == c_v_event
            if not res.step7_c_v:
                res.notes.append("公开 V 的 C_V 与 journal/proof 事件不一致")
        except (OSError, JournalError, KeyError) as e:
            res.notes.append(f"步骤 7 失败: {e}")

        # 步骤 8:manifest hash + Image ID
        try:
            manifest_bytes = (self.evidence / "protocol-manifest.json").read_bytes()
            proof = _read_json(self.evidence / "proof-receipt.json", "proof-receipt.json")
            manifest_hash = proof["event"]["proof"]["manifest_hash"]
            manifest = json.loads(manifest_bytes)
            image_id = manifest.get("guest", {}).get("image_id")
            res.step8_manifest = (
                hashlib.sha256(manifest_bytes).hexdigest() == manifest_hash
                and image_id == self.expect_image_id
            )
            if not res.step8_manifest:
                res.notes.append(
                    f"manifest hash 或 Image ID 不符(期望 {self.expect_image_id[:16]}…)"
                )
        except (OSError, KeyError, json.JSONDecodeError) as e:
            res.notes.append(f"步骤 8 失败: {e}")

        # 步骤 9:RISC Zero 复验(真实 receipt vs Image ID;无 midi/salt → 无 witness 模式)
        if self.verify_bin is None:
            res.notes.append("未配置 zkvm-verify,密码学复验未执行")
        else:
            try:
                commit = _read_json(self.evidence / "commit-receipt.json", "commit-receipt.json")
                c_m = commit["event"]["commit"]["c_m"]
                journal = Journal.decode((self.evidence / "journal.bin").read_bytes())
                args = shlex.split(self.verify_bin, posix=False) + [
                    "--expect-image-id", self.expect_image_id,
                    "--expect-c-m", c_m,
                    "--expect-c-v", journal.c_v.hex(),
                ]
                with tempfile.TemporaryDirectory(prefix="mzk-ev-") as tmp:
                    shutil.copy2(self.evidence / "zkvm-receipt.bin", Path(tmp) / "receipt.bin")
                    shutil.copy2(self.evidence / "journal.bin", Path(tmp) / "journal.bin")
                    try:
                        proc = subprocess.run(args, cwd=tmp, capture_output=True,
                                              timeout=600, check=False)
                        res.step9_crypto = proc.returncode == 0
                        if not res.step9_crypto:
                            res.notes.append(
                                f"zkvm-verify 拒绝(exit={proc.returncode}):"
                                f" {proc.stderr.decode(errors='replace')[-300:]}"
                            )
                    except (OSError, subprocess.TimeoutExpired) as e:
                        res.notes.append(f"zkvm-verify 执行失败: {e}")
            except (OSError, JournalError, KeyError) as e:
                res.notes.append(f"步骤 9 失败: {e}")

        # 步骤 10:journal 严格解析 + 上下文一致
        try:
            journal = Journal.decode((self.evidence / "journal.bin").read_bytes())
            commit = _read_json(self.evidence / "commit-receipt.json", "commit-receipt.json")
            release = _read_json(self.evidence / "release-receipt.json", "release-receipt.json")
            proof = _read_json(self.evidence / "proof-receipt.json", "proof-receipt.json")
            res.step10_journal = (
                journal.protocol_hash == protocol_hash(PROTOCOL_ID)
                and journal.creator_pubkey.hex() == commit["event"]["creator_pubkey"]
                and journal.commit_event_id.hex() == commit["record"]["event_id"]
                and journal.release_event_id.hex() == release["record"]["event_id"]
                and journal.c_m.hex() == commit["event"]["commit"]["c_m"]
                and journal.c_v.hex() == proof["event"]["proof"]["c_v"]
            )
            if not res.step10_journal:
                res.notes.append("journal 字段与事件上下文不一致")
        except (OSError, JournalError, KeyError) as e:
            res.notes.append(f"步骤 10 失败: {e}")

        return res
