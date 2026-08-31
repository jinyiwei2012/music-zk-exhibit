"""M0 证据验证骨架(SPEC §15 步骤 6/8/9/10 的 M0 子集)。

面向 evidence_dir,输出逐项结果。密码学复验委托 RISC Zero Rust 二进制(zkvm-verify),
本模块只做 journal 语义层(结构、protocol_hash、C_M 重算、t0 承诺绑定)。

红线:结果措辞使用"密码学证明有效/无效";绝不输出"原创""非AI""非 AI"或等价措辞。
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .framing import PROTOCOL_ID, SALT_LEN, commit_midi, protocol_hash
from .journal import Journal, JournalError


class M0VerifyError(ValueError):
    """证据缺失或不可读。"""


@dataclass
class M0Result:
    """逐项验证结果(SPEC §15 风格)。"""

    journal_structure_ok: bool = False
    protocol_hash_ok: bool = False
    c_m_recompute_ok: bool = False
    t0_bind_ok: bool | None = None  # 未提供 t0 承诺时为 None
    crypto_ok: bool | None = None  # 未提供 verify_bin 时为 None(未执行)
    notes: list[str] = field(default_factory=list)

    @property
    def overall(self) -> bool:
        """总体有效:所有实际执行的项目全部通过;密码学复验未执行时不算有效。"""
        checks = [self.journal_structure_ok, self.protocol_hash_ok, self.c_m_recompute_ok]
        if self.t0_bind_ok is not None:
            checks.append(self.t0_bind_ok)
        if self.crypto_ok is not None:
            checks.append(self.crypto_ok)
        else:
            return False  # 密码学复验必须执行并成功,才能宣称有效
        return all(checks)

    def render(self) -> str:
        """逐项输出(中性措辞)。"""
        lines = [
            f"journal 结构(202B+magic+version): {'有效' if self.journal_structure_ok else '无效'}",
            f"protocol_hash 与 protocol_id 一致:      {'是' if self.protocol_hash_ok else '否'}",
            f"C_M 与本地 (M, r) 重算一致:            {'是' if self.c_m_recompute_ok else '否'}",
        ]
        if self.t0_bind_ok is not None:
            lines.append(f"绑定 t0 已提交承诺:                    {'是' if self.t0_bind_ok else '否'}")
        if self.crypto_ok is None:
            lines.append("zkVM receipt 密码学复验:             未执行(M0 骨架,需提供 verify_bin)")
        else:
            lines.append(f"zkVM receipt 密码学复验:              {'通过' if self.crypto_ok else '失败'}")
        for n in self.notes:
            lines.append(f"  注:{n}")
        lines.append(f"总体:密码学证明{'有效' if self.overall else '无效'}")
        return "\n".join(lines)


class M0Verify:
    """M0 验证器:journal 语义层(纯 Python)+ 委托 zkvm-verify 做密码学复验。"""

    def __init__(
        self,
        evidence_dir: str | Path,
        t0_commit_hex: str | None = None,
        verify_bin: str | None = None,
    ) -> None:
        """
        evidence_dir:含 journal.bin / midi.bin / salt.bin(/ receipt.bin)的目录。
        t0_commit_hex:可选的 t0 已提交承诺(hex 32 字节),用于绑定检查。
        verify_bin:可选的 zkvm-verify 调用(支持带参数的命令字符串,如
                   "wsl -e bash -lc ..." 或绝对路径);提供时在 evidence_dir 内以子进程执行。
        """
        self.evidence_dir = Path(evidence_dir)
        self.t0_commit_hex = t0_commit_hex
        self.verify_bin = verify_bin

    def verify(self) -> M0Result:
        """执行逐项验证。"""
        r = M0Result()

        # 1) journal 结构 + protocol_hash
        try:
            journal_bytes = (self.evidence_dir / "journal.bin").read_bytes()
            journal = Journal.decode(journal_bytes)
            r.journal_structure_ok = True
        except (OSError, JournalError) as e:
            r.notes.append(f"journal 读取/解析失败:{e}")
            return r
        r.protocol_hash_ok = journal.protocol_hash == protocol_hash(PROTOCOL_ID)

        # 2) C_M 重算对拍(本地 (M, r) 独立重算)
        try:
            midi = (self.evidence_dir / "midi.bin").read_bytes()
            salt = (self.evidence_dir / "salt.bin").read_bytes()
            if len(salt) != SALT_LEN:
                raise M0VerifyError(f"salt 必须恰 {SALT_LEN} 字节,收到 {len(salt)}")
            r.c_m_recompute_ok = journal.c_m == commit_midi(midi, salt)
        except (OSError, ValueError) as e:
            r.notes.append(f"C_M 重算失败:{e}")
            return r

        # 3) t0 承诺绑定(可选)
        if self.t0_commit_hex is not None:
            committed = bytes.fromhex(self.t0_commit_hex)
            r.t0_bind_ok = journal.c_m == committed

        # 4) 密码学复验(委托 zkvm-verify,可选)
        if self.verify_bin is not None:
            args = shlex.split(self.verify_bin)
            if self.t0_commit_hex is not None:
                args += ["--expect-c-m", self.t0_commit_hex]
            try:
                proc = subprocess.run(
                    args,
                    cwd=self.evidence_dir,
                    capture_output=True,
                    timeout=600,
                    check=False,
                )
                r.crypto_ok = proc.returncode == 0
                if not r.crypto_ok:
                    r.notes.append(f"zkvm-verify 退出码 {proc.returncode}:"
                                   f"{proc.stderr.decode(errors='replace')[-400:]}")
            except (OSError, subprocess.TimeoutExpired) as e:
                r.crypto_ok = False
                r.notes.append(f"zkvm-verify 执行失败:{e}")

        return r
