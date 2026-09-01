"""Merkle 日志测试(SPEC §11.3):RFC 6962 域分离 + Google CT 官方向量 + inclusion proof 自洽。

官方向量抄自 transparency-dev/merkle testonly/constants.go(RFC 6962 哈希策略的
参考实现):8 个递增长度的叶输入、各级节点哈希、size 0..8 的树根——锁死叶/节点
域分离(0x00/0x01)与树结构的字节级正确性。
"""

from __future__ import annotations

import pytest

from music_zk.protocol.merkle import (
    EMPTY_TREE_ROOT,
    MerkleError,
    MerkleTree,
    leaf_hash,
    node_hash,
    verify_inclusion,
)

# Google CT 官方向量(transparency-dev/merkle testonly/constants.go)
OFFICIAL_LEAVES = [
    bytes.fromhex(h)
    for h in (
        "",
        "00",
        "10",
        "2021",
        "3031",
        "40414243",
        "5051525354555657",
        "606162636465666768696a6b6c6d6e6f",
    )
]
# 叶哈希(level 0)
OFFICIAL_LEAF_HASHES = [
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
    "0298d122906dcfc10892cb53a73992fc5b9f493ea4c9badb27b791b4127a7fe7",
    "07506a85fd9dd2f120eb694f86011e5bb4662e5c415a62917033d4a9624487e7",
    "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
    "4271a26be0d8a84f0bd54c8c302e7cb3a3b5d1fa6780a40bcce2873477dab658",
    "b08693ec2e721597130641e8211e7eedccb4c26413963eee6c1e2ed16ffb1a5f",
    "46f6ffadd3d06a09ff3c5860d2755c8b9819db7df44251788c7d8e3180de8eb1",
]
# size 0..8 的树根(RootHashes)
OFFICIAL_ROOTS = [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # 空树
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]


def test_official_leaf_hashes() -> None:
    for leaf, expect in zip(OFFICIAL_LEAVES, OFFICIAL_LEAF_HASHES):
        assert leaf_hash(leaf).hex() == expect


def test_official_roots_by_size() -> None:
    tree = MerkleTree()
    assert tree.root() == EMPTY_TREE_ROOT == bytes.fromhex(OFFICIAL_ROOTS[0])
    for i, leaf in enumerate(OFFICIAL_LEAVES):
        tree.append(leaf)
        assert tree.root().hex() == OFFICIAL_ROOTS[i + 1], f"size={i + 1} 树根不符"


def test_domain_separation() -> None:
    import hashlib

    # 叶哈希必须带 0x00 前缀:与直接 SHA256(记录)不同
    record = b'{"a":1}'
    assert leaf_hash(record) != hashlib.sha256(record).digest()
    # 节点哈希 0x01 前缀 + 顺序敏感
    l, r = b"\x11" * 32, b"\x22" * 32
    assert node_hash(l, r) != leaf_hash(l + r)
    assert node_hash(l, r) != node_hash(r, l)


def test_inclusion_proof_verifies_for_all_sizes() -> None:
    # 1..32 个叶的每个叶:证明必须通过;篡改叶内容/序号必须失败
    for n in range(1, 33):
        tree = MerkleTree()
        for i in range(n):
            tree.append(f"record-{i}".encode())
        root = tree.root()
        for idx in range(n):
            record = f"record-{idx}".encode()
            proof = tree.inclusion_proof(idx)
            assert verify_inclusion(record, idx, proof, root, n)
            # 负向:改一个字节
            bad = b"X" + record[1:]
            assert not verify_inclusion(bad, idx, proof, root, n)
            # 负向:改序号(除 n=1 外,单叶证明为空、换序号仍指向同一叶)
            if n > 1:
                assert not verify_inclusion(record, (idx + 1) % n, proof, root, n)


def test_official_leaves_audit_path() -> None:
    # 用官方 8 叶输入:每个叶的证明都能重建官方根
    tree = MerkleTree()
    for leaf in OFFICIAL_LEAVES:
        tree.append(leaf)
    root = tree.root()
    for idx, leaf in enumerate(OFFICIAL_LEAVES):
        proof = tree.inclusion_proof(idx)
        assert verify_inclusion(leaf, idx, proof, root, len(OFFICIAL_LEAVES))


def test_single_leaf_empty_path() -> None:
    tree = MerkleTree()
    tree.append(b"record-1")
    assert tree.root() == leaf_hash(b"record-1")
    assert tree.inclusion_proof(0) == []


def test_index_out_of_range_rejected() -> None:
    tree = MerkleTree()
    tree.append(b"a")
    with pytest.raises(MerkleError):
        tree.inclusion_proof(1)
    with pytest.raises(MerkleError):
        verify_inclusion(b"a", 2, [], tree.root(), 1)


def test_history_roots_monotonic() -> None:
    # previous_tree_size/root:STH 依赖历史根;追加后旧根不变(官方向量锁定)
    tree = MerkleTree()
    roots = []
    for leaf in OFFICIAL_LEAVES:
        tree.append(leaf)
        roots.append(tree.root())
    for i in range(8):
        assert tree.root_at(i + 1) == roots[i]
        assert tree.root_at(i + 1).hex() == OFFICIAL_ROOTS[i + 1]
    with pytest.raises(MerkleError):
        tree.root_at(9)
