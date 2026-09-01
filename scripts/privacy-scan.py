#!/usr/bin/env python3
"""scripts/privacy-scan.py — SPEC §17.4 隐私扫描。

断言服务端数据库、公开目录、访问日志与证据包(证据目录/zip)中
**不出现**私有 MIDI 字节(M)、盐字节(r)或创作者私钥字节。

扫描目标(公开面):
  - server-data/           服务端数据库 + 已发布文件
  - public-evidence/       公开证据包(目录与其中 zip)
  - **/*.log               访问日志等
  - **/*.zip               证据 zip

私密字节来源(needles):
  - creator-secret/original.mid       (M)
  - creator-secret/salt.bin           (r,32 B)
  - creator-secret/creator-private-key(私钥,32 B raw Ed25519)
  可用 --needle <文件> 显式追加(CI 等无 creator-secret 的场景)。

用法(仓库根,music-zk 环境):
  conda run -n music-zk python scripts/privacy-scan.py [--repo ROOT] [--needle F]...
退出码:0 = 零命中(通过);1 = 命中 / 配置缺失(失败)。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

SECRET_FILES = {
    "original.mid": "M (私有 MIDI 字节)",
    "salt.bin": "r (32 B 盐字节)",
    "creator-private-key": "创作者私钥(32 B raw Ed25519)",
}
EXCLUDE_DIRS = {".git", "creator-secret", "proof-work", "__pycache__", ".pytest_cache",
                "target", ".omo", ".mypy_cache", ".ruff_cache"}


def _walk_targets(root: Path) -> list[Path]:
    targets: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        name = p.name.lower()
        if name.endswith((".log", ".zip")) or name in ("log.sqlite",) or "server-data" in rel.parts or "public-evidence" in rel.parts:
            targets.append(p)
    return targets


def _load_needles(root: Path, extra: list[str]) -> list[tuple[bytes, str]]:
    needles: list[tuple[bytes, str]] = []
    for fname, label in SECRET_FILES.items():
        p = root / "creator-secret" / fname
        if p.exists():
            needles.append((p.read_bytes(), f"{label} ({fname})"))
    for arg in extra:
        p = Path(arg).resolve()
        if not p.exists():
            print(f"[配置错误] --needle 文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        needles.append((p.read_bytes(), f"显式 needle ({p.name})"))
    if not needles:
        print("[配置错误] 无私密字节来源:缺少 creator-secret/{original.mid,salt.bin,creator-private-key},"
              "或未传 --needle。请先跑 `identity init` + `commit create`,或用 --needle 显式指定。",
              file=sys.stderr)
        sys.exit(1)
    return needles


def _byte_search(hay: bytes, needle: bytes) -> list[int]:
    """返回 needle 在 hay 中的全部出现偏移(空 needle 返回 [])。"""
    if not needle:
        return []
    hits: list[int] = []
    pos = hay.find(needle)
    while pos != -1:
        hits.append(pos)
        pos = hay.find(needle, pos + 1)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="SPEC §17.4 隐私扫描")
    ap.add_argument("--repo", default=".", help="仓库根(默认当前目录)")
    ap.add_argument("--needle", action="append", default=[], help="显式私密字节文件(可多次)")
    args = ap.parse_args()

    root = Path(args.repo).resolve()
    if not (root / "server-data").exists() and not (root / "public-evidence").exists():
        print(f"[提示] 仓库根 {root} 下无 server-data/ 或 public-evidence/(首次扫描前请先跑过服务端与演示)",
              file=sys.stderr)

    needles = _load_needles(root, args.needle)
    targets = _walk_targets(root)
    print(f"私密字节来源: {len(needles)} 个 needle")
    for b, label in needles:
        print(f"  - {label}: {len(b)} B, SHA256={hashlib.sha256(b).hexdigest()[:16]}…")
    print(f"扫描目标: {len(targets)} 个文件")
    if len(targets) > 200:
        print(f"[提示] 目标文件较多({len(targets)}),包含 wav/zip 大文件时扫描稍慢。", file=sys.stderr)

    violations: list[str] = []
    checked_bytes = 0
    for t in sorted(targets):
        data = t.read_bytes()
        checked_bytes += len(data)
        for needle, label in needles:
            for off in _byte_search(data, needle):
                violations.append(f"  {t.relative_to(root)} @ 偏移 {off}: 命中 {label}")

    print(f"扫描字节数: {checked_bytes:,}")
    if violations:
        print("\n[FAIL] 隐私扫描发现私密字节泄漏:")
        for v in violations:
            print(v)
        return 1
    print("\n[PASS] 服务端数据库/公开目录/访问日志/证据包中未发现 M、r 或私钥字节(SPEC §17.4 第 1 条)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
