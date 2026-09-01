"""身份初始化测试(SPEC §10.1 / §12.1):原子创建、已存在即停、私钥只落本地。"""

from __future__ import annotations

import nacl.signing
import pytest

from music_zk.cli.identity import (
    PUBLIC_KEY_FILE,
    README_FILE,
    IdentityError,
    init_identity,
    load_private_key_hex,
    load_public_key_hex,
)


def test_init_creates_all_files(tmp_path: Path) -> None:
    out = tmp_path / "creator-secret"
    info = init_identity(out)
    assert info.out_dir == out
    assert (out / "creator-private-key").read_bytes() != b""
    assert len((out / "creator-private-key").read_bytes()) == 32
    pk = (out / PUBLIC_KEY_FILE).read_text(encoding="ascii").strip()
    assert len(pk) == 64 and pk == pk.lower()
    assert info.public_key_hex == pk
    readme = (out / README_FILE).read_text(encoding="utf-8")
    assert "严禁公开" in readme


def test_init_existing_dir_stops_without_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "creator-secret"
    init_identity(out)
    original_pk = (out / PUBLIC_KEY_FILE).read_text(encoding="ascii")
    # 再 init 必须失败且不动任何文件
    with pytest.raises(IdentityError):
        init_identity(out)
    assert (out / PUBLIC_KEY_FILE).read_text(encoding="ascii") == original_pk


def test_pubkey_derives_from_seed(tmp_path: Path) -> None:
    out = tmp_path / "creator-secret"
    info = init_identity(out)
    seed = (out / "creator-private-key").read_bytes()
    derived = nacl.signing.SigningKey(seed).verify_key.encode().hex()
    assert derived == info.public_key_hex


def test_readme_contains_three_warnings(tmp_path: Path) -> None:
    out = tmp_path / "creator-secret"
    init_identity(out)
    readme = (out / README_FILE).read_text(encoding="utf-8")
    assert "不得公开" in readme            # 警告 1
    assert "无法为旧承诺" in readme or "无法生成" in readme  # 警告 2
    assert "身份无法延续" in readme or "无法延续身份" in readme  # 警告 3


def test_load_helpers_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "creator-secret"
    info = init_identity(out)
    assert load_private_key_hex(out) == (out / "creator-private-key").read_bytes().hex()
    assert load_public_key_hex(out) == info.public_key_hex


def test_private_key_never_leaks_via_info(tmp_path: Path) -> None:
    # IdentityInfo 只暴露公钥与路径,不携带私钥字节(红线 1 精神)
    info = init_identity(tmp_path / "creator-secret")
    assert not hasattr(info, "private_key_hex")
    assert not hasattr(info, "private_key_seed")
