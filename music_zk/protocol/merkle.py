"""RFC 6962 风格 Merkle 树(Phase 3,SPEC §11.3 / AGENTS.md §3.6)。

域分离:
    leaf_hash = SHA256(0x00 || JCS(server_event_record))   # 叶 = 已 JCS 的完整事件记录
    node_hash = SHA256(0x01 || left_hash || right_hash)

本模块只处理"已序列化的事件记录字节"(leaf 内容由调用方 JCS 化);树结构/证明
按 RFC 6962 §2.1 精确实现,并用官方测试向量(RFC 6962 Appendix B,16 叶树根哈希)
锁定字节级正确性。

用途:透明日志检测普通数据库篡改(SPEC §11.3 限制说明:无外部 witness 时不能阻止
服务端同时重写数据库/根/历史签名,该限制 MUST 出现在技术页面)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

EMPTY_TREE_ROOT = hashlib.sha256(b"").digest()  # RFC 6962:空树根 = SHA256("")

HASH_LEN = 32


class MerkleError(ValueError):
    """树参数非法或证明验证失败。"""


def leaf_hash(record_bytes: bytes) -> bytes:
    """叶哈希:SHA256(0x00 || record_bytes)。record_bytes 应为 JCS(server_event_record)。"""
    h = hashlib.sha256()
    h.update(b"\x00")
    h.update(record_bytes)
    return h.digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """内部节点哈希:SHA256(0x01 || left || right)。"""
    if len(left) != HASH_LEN or len(right) != HASH_LEN:
        raise MerkleError(f"节点哈希必须 {HASH_LEN} 字节")
    h = hashlib.sha256()
    h.update(b"\x01")
    h.update(left)
    h.update(right)
    return h.digest()


def _largest_power_of_2_lt(n: int) -> int:
    """RFC 6962 k = 2^(floor(log2(n-1))),n ≥ 2 时使用。"""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def _mth(leaves: list[bytes]) -> bytes:
    """Merkle Tree Hash(RFC 6962 §2.1.1)。"""
    n = len(leaves)
    if n == 0:
        return EMPTY_TREE_ROOT
    if n == 1:
        return leaf_hash(leaves[0])
    k = _largest_power_of_2_lt(n)
    return node_hash(_mth(leaves[:k]), _mth(leaves[k:]))


def _audit_path(m: int, leaves: list[bytes]) -> list[bytes]:
    """RFC 6962 §2.1.2 PATH(m, D[n]):第 m 个叶的 inclusion proof(自下而上)。"""
    n = len(leaves)
    if m == n - 1 and n == 1:
        return []
    if n == 1:
        raise MerkleError("内部不一致:证明算法越界")
    k = _largest_power_of_2_lt(n)
    if m < k:
        return _audit_path(m, leaves[:k]) + [_mth(leaves[k:])]
    return _audit_path(m - k, leaves[k:]) + [_mth(leaves[:k])]


@dataclass
class MerkleTree:
    """追加式透明日志树。叶字节由调用方提供(已是 JCS 的事件记录)。"""

    _leaves: list[bytes] = field(default_factory=list)

    def append(self, record_bytes: bytes) -> int:
        """追加一条记录,返回其序号(0-based)。"""
        self._leaves.append(record_bytes)
        return len(self._leaves) - 1

    @property
    def size(self) -> int:
        return len(self._leaves)

    def root(self) -> bytes:
        """当前树根(空树 = EMPTY_TREE_ROOT)。"""
        return _mth(self._leaves)

    def inclusion_proof(self, index: int) -> list[bytes]:
        """第 index 个叶的 inclusion proof(节点哈希列表,自下而上)。"""
        if not 0 <= index < len(self._leaves):
            raise MerkleError(f"叶序号越界: {index} (size={len(self._leaves)})")
        return _audit_path(index, self._leaves)

    def root_at(self, size: int) -> bytes:
        """返回历史 size 处的树根(用于 STH 的 previous_tree_root)。"""
        if not 0 <= size <= len(self._leaves):
            raise MerkleError(f"历史 size 越界: {size}")
        return _mth(self._leaves[:size])


def verify_inclusion(
    record_bytes: bytes,
    index: int,
    proof: list[bytes],
    root: bytes,
    tree_size: int,
) -> bool:
    """独立验证 inclusion proof(RFC 6962 §2.1.3.2 VerifyAuditPath 精确实现)。

    record_bytes 必须是 JCS(server_event_record) 原始字节(叶内容);
    proof 为自下而上的节点哈希列表(与 inclusion_proof 输出一致);
    root/tree_size 为审计时的树头。
    """
    if not 0 <= index < tree_size:
        raise MerkleError(f"叶序号越界: {index} (tree_size={tree_size})")
    fn = index
    sn = tree_size - 1
    r = leaf_hash(record_bytes)
    for p in proof:
        if sn == 0:
            return False
        if fn % 2 == 1 or fn == sn:
            r = node_hash(p, r)  # p 是左兄弟
            while fn % 2 == 0 and fn != 0:
                fn //= 2
                sn //= 2
        else:
            r = node_hash(r, p)  # p 是右兄弟
        fn //= 2
        sn //= 2
    return sn == 0 and r == root
