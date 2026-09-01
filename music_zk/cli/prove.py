"""本地真实证明编排(Phase 3,SPEC §13 的 `prove` 命令)。

流程(SPEC §13 prove MUST):
  1. 再次校验 original.mid + salt 打开 C_M(与 t0 承诺一致);
  2. reference-native render 生成真实 V(C_V 与 guest 内流式 SHA-256 一致);
  3. zkvm-prove 生成真实 receipt(带内存限制 --segment-po2 18 --keccak-po2 18,
     蓝屏防护,见 docs/ENV.md);dev-mode 由二进制编译期硬禁(红线 2);
  4. 独立 zkvm-verify 复验 receipt/journal/Image ID 与 C_M/C_V 绑定;
  5. 全部成功后才写 proof-work/(可上传目录)。

二进制位置:MZK_BIN_DIR 环境变量,默认 C:/music-zk-target/debug(构建产物,
rust/.cargo/config.toml target-dir)。缺失时给出降级路径提示(PLAN §6.4)。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from music_zk.cli.flow import (
    FlowError,
    ORIGINAL_MIDI,
    SALT_FILE,
    _read_secret,
)
from music_zk.verifier.framing import commit_midi

DEFAULT_BIN_DIR = Path("C:/music-zk-target/debug")
PROVE_BIN = "zkvm-prove.exe"
VERIFY_BIN = "zkvm-verify.exe"
RENDER_BIN = "reference-native.exe"
# 内存限制(蓝屏防护,2026-09-01 起必带;见 docs/ENV.md)
SEGMENT_PO2 = 18
KECCAK_PO2 = 18


def bin_dir() -> Path:
    env = sys.modules[__name__].__dict__.get("_bin_dir_override")
    if env is not None:
        return env
    return DEFAULT_BIN_DIR


def set_bin_dir(p: str | Path) -> None:
    """测试注入二进制目录。"""
    sys.modules[__name__].__dict__["_bin_dir_override"] = Path(p)


def _require_binary(name: str) -> str:
    b = bin_dir() / name
    if not b.exists():
        raise FlowError(
            f"缺少本地证明二进制: {b}\n"
            "降级路径(PLAN §6.4):① 证明一次性生成、证据包搬运到老电脑只验证;"
            "② Linux Live USB 静态 prover;或在本机先构建(见 docs/ENV.md)。"
        )
    return str(b)


def _run_bin(
    cmd: list[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """执行二进制;非 PE 可执行文件(测试 stub 脚本)自动改用 Python 解释器跑。"""
    exe = Path(cmd[0])
    if exe.exists() and exe.read_bytes()[:2] != b"MZ":
        cmd = [sys.executable, cmd[0], *cmd[1:]]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False
    )


def prove(
    secret_dir: str | Path,
    release_event_id: str,
    out_dir: str | Path,
    *,
    force_release: bool = False,
) -> dict[str, str]:
    """执行 SPEC §13 的 prove 六步,成功后写 proof-work/。返回产物摘要。"""
    secret = _read_secret(Path(secret_dir))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # 子进程 cwd=out,所有输入路径必须绝对化
    secret_abs = Path(secret_dir).resolve()
    out = out.resolve()
    secret["midi"] = (secret_abs / ORIGINAL_MIDI).read_bytes()
    secret["salt"] = (secret_abs / SALT_FILE).read_bytes()

    prove_bin = _require_binary(PROVE_BIN)
    verify_bin = _require_binary(VERIFY_BIN)
    render_bin = _require_binary(RENDER_BIN)

    # 1) C_M 复算:original.mid + salt 必须打开 t0 已提交承诺
    c_m = commit_midi(secret["midi"], secret["salt"]).hex()
    committed = secret["commit_receipt"].get("c_m_hex")
    if committed is not None and committed != c_m:
        raise FlowError("C_M 与 t0 已提交承诺不一致(original.mid + salt 不匹配)")

    # 2) native ReferenceSynth 渲染真实 V(public 承诺 C_V)
    midi_file = secret_abs / ORIGINAL_MIDI
    v_file = out / "v.wav"
    proc = _run_bin([render_bin, "render", str(midi_file), str(v_file)], cwd=out, timeout=300)
    if proc.returncode != 0:
        raise FlowError(f"ReferenceSynth 渲染失败: {proc.stderr[-400:]}")
    m = re.search(r"C_V=([0-9a-f]{64})", proc.stdout)
    if not m:
        raise FlowError(f"渲染输出缺少 C_V: {proc.stdout[-400:]}")
    c_v = m.group(1)

    # 3) 真实证明(内存限制防蓝屏;dev-mode 编译期硬禁);cwd=out 使产物直接落 proof-work/
    #    journal 上下文:creator_pubkey/commit_event_id/release_event_id 必须填真实值
    #    (SPEC §6.4 布局),否则离线验证步骤 10(journal 上下文一致)失败。
    salt_file = secret_abs / SALT_FILE
    commit_id = secret["commit_receipt"]["server"]["event"]["event_id"]
    prov = _run_bin(
        [
            prove_bin, "--cv", c_v,
            "--creator-pubkey", secret["pk_hex"],
            "--commit-event-id", commit_id,
            "--release-event-id", release_event_id,
            "--segment-po2", str(SEGMENT_PO2),
            "--keccak-po2", str(KECCAK_PO2),
            str(midi_file), str(salt_file),
        ],
        cwd=out, timeout=3600,
    )
    if prov.returncode != 0:
        raise FlowError(f"真实证明失败(exit={prov.returncode}): {prov.stderr[-600:] or prov.stdout[-600:]}")

    # 4) 独立 verifier 复验(receipt/journal/Image ID + C_M/C_V 绑定)
    vrf = _run_bin(
        [verify_bin, "--expect-c-m", c_m, "--expect-c-v", c_v],
        cwd=out, timeout=600,
    )
    if vrf.returncode != 0:
        raise FlowError(f"独立 verifier 复验失败(exit={vrf.returncode}): {vrf.stderr[-600:]}")

    # 5) 复制辅助产物到 out(证明成功后才创建可上传目录,SPEC §13 step 6)
    shutil.copy2(str(midi_file), out / "midi.bin")
    shutil.copy2(str(salt_file), out / "salt.bin")
    _copy_manifest(out)

    summary = {
        "midi": str(midi_file),
        "c_m": c_m,
        "c_v": c_v,
        "v_wav": str(v_file),
        "release_event_id": release_event_id,
        "work_dir": str(out),
    }
    # 摘要落盘(供 proof publish 使用)
    (out / "prove-summary.json").write_text(
        __import__("json").dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _copy_manifest(out: Path) -> None:
    """protocol/v1.json → proof-work/manifest.json(PROOF 事件 manifest 字段)。"""
    import json

    repo_protocol = Path(__file__).resolve().parent.parent.parent / "protocol" / "v1.json"
    if repo_protocol.exists():
        manifest = json.loads(repo_protocol.read_text(encoding="utf-8"))
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
