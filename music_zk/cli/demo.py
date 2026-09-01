"""reveal-check 与 demo tamper(Phase 4,SPEC §13 / §17.3 精神)。

reveal-check:original.mid + salt.bin + commit-receipt.json → 重算 C_M 与 t0 已提交
承诺对比(揭示私有材料确实"打开"承诺)。

demo tamper:对证据包/私密材料做一处篡改,运行对应检查——展示"任何篡改都被检出"。
  案例:
    midi-byte    翻转 original.mid 一字节 → reveal-check 失败(C_M 不符)
    salt         翻转 salt.bin 一字节   → reveal-check 失败(C_M 不符)
    wav-sample   翻转 reference-V.wav 一字节 → verify 步骤 7 失败(C_V 不符)
    log-receipt  篡改 commit-receipt.json 的 sth.tree_root → verify 步骤 3 失败
    event-order  交换 commit/release 的 sequence → verify 步骤 5 失败
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from music_zk.cli.flow import COMMIT_RECEIPT, FlowError, ORIGINAL_MIDI, SALT_FILE, _load_json
from music_zk.verifier.framing import commit_midi

TAMPER_CASES = ("midi-byte", "wav-sample", "salt", "log-receipt", "event-order")


def reveal_check(midi_path: str | Path, salt_path: str | Path, receipt_path: str | Path) -> str:
    """对比 (M, r) 重算的 C_M 与 t0 已提交承诺;返回人类可读报告。"""
    midi = Path(midi_path).read_bytes()
    salt = Path(salt_path).read_bytes()
    if len(salt) != 32:
        raise FlowError(f"salt 必须恰 32 字节,收到 {len(salt)}")
    receipt = _load_json(Path(receipt_path), COMMIT_RECEIPT)
    committed = receipt.get("c_m_hex")
    if committed is None:
        committed = receipt["event"]["commit"]["c_m"]
    recomputed = commit_midi(midi, salt).hex()
    match = recomputed == committed
    lines = [
        f"midi bytes : {len(midi)}",
        f"salt bytes : {len(salt)}",
        f"t0 已提交 C_M: {committed}",
        f"本地重算 C_M : {recomputed}",
        f"结果: {'打开' if match else '不打开'}",
    ]
    return "\n".join(lines)


def _flip_byte(data: bytes) -> bytes:
    return bytes([data[0] ^ 0xFF]) + data[1:]


def _tamper_evidence(dir_path: Path) -> Path:
    """复制证据包到临时目录返回副本路径。"""
    tmp = Path(tempfile.mkdtemp(prefix="mzk-tamper-"))
    shutil.copytree(dir_path, tmp / "evidence", dirs_exist_ok=True)
    return tmp / "evidence"


def run_tamper(case: str, evidence_dir: str | Path, secret_dir: str | Path | None = None) -> str:
    """对证据包/私密材料做一处篡改并运行对应检查;返回"篡改被检出"报告。

    正常演示:检查必须失败(篡改被检出);若检查意外通过则抛 FlowError。
    """
    if case not in TAMPER_CASES:
        raise FlowError(f"未知 tamper 案例: {case}(可选: {', '.join(TAMPER_CASES)})")
    evidence = Path(evidence_dir)
    secret = Path(secret_dir) if secret_dir else None

    if case == "midi-byte":
        if secret is None:
            raise FlowError("midi-byte 案例需要 --secret creator-secret(需 original.mid)")
        with tempfile.TemporaryDirectory(prefix="mzk-tamper-secret-") as tmp:
            t = Path(tmp)
            for name in (ORIGINAL_MIDI, SALT_FILE, COMMIT_RECEIPT):
                shutil.copy2(secret / name, t / name)
            original = (t / ORIGINAL_MIDI).read_bytes()
            (t / ORIGINAL_MIDI).write_bytes(_flip_byte(original))
            report = reveal_check(t / ORIGINAL_MIDI, t / SALT_FILE, t / COMMIT_RECEIPT)
            return _assert_detected(report, case)

    if case == "salt":
        if secret is None:
            raise FlowError("salt 案例需要 --secret creator-secret(需 salt.bin)")
        with tempfile.TemporaryDirectory(prefix="mzk-tamper-secret-") as tmp:
            t = Path(tmp)
            for name in (ORIGINAL_MIDI, SALT_FILE, COMMIT_RECEIPT):
                shutil.copy2(secret / name, t / name)
            salt = (t / SALT_FILE).read_bytes()
            (t / SALT_FILE).write_bytes(_flip_byte(salt))
            report = reveal_check(t / ORIGINAL_MIDI, t / SALT_FILE, t / COMMIT_RECEIPT)
            return _assert_detected(report, case)

    tampered = _tamper_evidence(evidence)

    if case == "wav-sample":
        v = tampered / "reference-V.wav"
        v.write_bytes(_flip_byte(v.read_bytes()))

    elif case == "log-receipt":
        p = tampered / "commit-receipt.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        root = rec["sth"]["tree_root"]
        rec["sth"]["tree_root"] = _flip_byte(bytes.fromhex(root)).hex()
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    elif case == "event-order":
        # 交换 commit/release 的 sequence(及 event_id 重新派生会随之失效,但顺序步骤已能检出)
        cp = tampered / "commit-receipt.json"
        rp = tampered / "release-receipt.json"
        c = json.loads(cp.read_text(encoding="utf-8"))
        r = json.loads(rp.read_text(encoding="utf-8"))
        c["record"]["sequence"], r["record"]["sequence"] = r["record"]["sequence"], c["record"]["sequence"]
        cp.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        rp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    # 篡改后跑 verify,报告每步(正常演示中总体必须无效)
    from music_zk.verifier.evidence import EvidenceVerifier

    server_pk = (Path(evidence_dir).parent / "server-key.txt").read_text().strip() \
        if (Path(evidence_dir).parent / "server-key.txt").exists() else "00" * 32
    res = EvidenceVerifier(tampered, server_pk).verify()
    lines = [f"篡改案例: {case}", res.render()]
    if res.overall:
        raise FlowError(f"篡改未被检出(总体仍有效)! case={case}")
    lines.insert(1, "检测:篡改被检出(总体无效)")
    return "\n".join(lines)


def _assert_detected(report: str, case: str) -> str:
    if "不打开" not in report:
        raise FlowError(f"篡改未被检出(commitment 仍被打开)! case={case}")
    return f"篡改案例: {case}\n检测:篡改被检出(承诺不打开)\n{report}"
