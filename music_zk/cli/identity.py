"""创作者身份初始化(Phase 3,SPEC §10.1 / §12.1 / AGENTS.md §1 红线 1)。

生成 Ed25519 keypair,私钥只落本地 `creator-secret/` 目录:
  creator-private-key      32 字节 Ed25519 种子(raw 二进制;签名时 hex 编码)
  creator-public-key.txt   32 字节公钥的 lowercase hex(SPEC §10.1)
  README-PRIVATE.txt       私密目录提醒(SPEC §12.1 三条 MUST + Windows ACL 说明)

目录创建是原子的:`os.makedirs(exist_ok=False)`——目标已存在即抛错、不覆盖任何文件
(SPEC §12.1:"CLI MUST 原子地创建该目录,若目标已存在则停止,不覆盖")。

Windows 无 POSIX 权限位:尽力用 `icacls` 把目录收紧到当前用户(PLAN §6.3),失败
不致命,在 README 中说明差异。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import nacl.signing

# 私密目录文件名(SPEC §12.1)
PRIVATE_KEY_FILE = "creator-private-key"
PUBLIC_KEY_FILE = "creator-public-key.txt"
README_FILE = "README-PRIVATE.txt"

README_TEMPLATE = """\
Music-ZK 创作者私密目录 —— 严禁公开!

本目录(creator-secret/)只存在于你的本地机器,永不入库、永不发送给服务端。

警告(SPEC §12.1):
1. original.mid、salt.bin、creator-private-key 三个文件不得公开;
   任何一项泄露都会破坏承诺的零知识属性(他人可重放/冒充)。
2. MIDI 或盐丢失后,将无法为旧承诺(C_M)生成新的证明——请备份。
3. 私钥丢失后,身份无法延续(公钥与日志中的签名不可迁移)。
4. 服务端与公开证据包 MUST NOT 包含本目录中的私密项。

平台说明:Windows 无 POSIX 权限位,此处尽力用 icacls 收紧到当前用户
(仅继承移除 + 当前用户读写),非完整防护;请勿把本目录放进网盘/云同步。
"""


class IdentityError(ValueError):
    """身份目录已存在或写入失败。"""


@dataclass(frozen=True)
class IdentityInfo:
    """身份初始化结果。"""

    out_dir: Path
    public_key_hex: str
    private_key_path: Path
    public_key_path: Path
    readme_path: Path


def _restrict_acl_windows(out_dir: Path) -> str | None:
    """尽力把目录 ACL 收紧到当前用户;返回失败原因,成功返回 None。"""
    user = os.environ.get("USERNAME")
    if not user:
        return "无法确定当前用户名(USERNAME 为空)"
    try:
        subprocess.run(
            [
                "icacls", str(out_dir),
                "/inheritance:r",
                f"/grant:r", f"{user}:(OI)(CI)(RX,W)",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return None
    except (OSError, subprocess.SubprocessError) as e:
        return f"icacls 收紧 ACL 失败(非致命): {e}"


def init_identity(out_dir: Path) -> IdentityInfo:
    """初始化创作者身份;目录已存在时抛 IdentityError 且不改动任何文件。

    私钥生成后立刻写盘(排他创建),任何一步失败都会回滚已建目录。
    """
    out_dir = Path(out_dir)
    try:
        os.makedirs(out_dir, exist_ok=False)  # 已存在 → FileExistsError → IdentityError
    except FileExistsError:
        raise IdentityError(
            f"目录已存在,停止且不覆盖: {out_dir} (SPEC §12.1 原子创建)"
        ) from None

    try:
        sk = nacl.signing.SigningKey.generate()
        pk_hex = sk.verify_key.encode().hex()

        # 排他写:目录刚建,文件必不存在;O_EXCL 防并发
        priv_path = out_dir / PRIVATE_KEY_FILE
        with open(priv_path, "xb") as f:
            f.write(bytes(sk))

        (out_dir / PUBLIC_KEY_FILE).write_text(pk_hex + "\n", encoding="ascii")

        acl_note = ""
        if sys.platform == "win32":
            reason = _restrict_acl_windows(out_dir)
            if reason:
                acl_note = "\n" + reason
        (out_dir / README_FILE).write_text(
            README_TEMPLATE + acl_note, encoding="utf-8"
        )
    except Exception:
        # 回滚:只删本次创建的空目录(不递归,避免误删用户文件)
        try:
            out_dir.rmdir()
        except OSError:
            pass
        raise

    return IdentityInfo(
        out_dir=out_dir,
        public_key_hex=pk_hex,
        private_key_path=priv_path,
        public_key_path=out_dir / PUBLIC_KEY_FILE,
        readme_path=out_dir / README_FILE,
    )


def load_private_key_hex(out_dir: Path) -> str:
    """读取 creator-private-key 并返回 lowercase hex(供 signing.sign_event_body 用)。"""
    p = Path(out_dir) / PRIVATE_KEY_FILE
    raw = p.read_bytes()
    if len(raw) != 32:
        raise IdentityError(f"{p} 不是 32 字节 Ed25519 种子(实际 {len(raw)} 字节)")
    return raw.hex()


def load_public_key_hex(out_dir: Path) -> str:
    """读取 creator-public-key.txt(lowercase hex)。"""
    p = Path(out_dir) / PUBLIC_KEY_FILE
    pk = p.read_text(encoding="ascii").strip()
    if len(pk) != 64:
        raise IdentityError(f"{p} 不是 32 字节公钥 hex(实际 {len(pk)} 字符)")
    return pk
