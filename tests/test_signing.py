"""事件签名测试(SPEC §10.2):roundtrip + 篡改任一字节/字段失败。"""

from __future__ import annotations

import pytest

from music_zk.protocol.jcs import canonicalize
from music_zk.protocol.signing import (
    CREATOR_EVENT_PREFIX,
    SignatureError,
    sign_event_body,
    verify_event_signature,
)

# 测试专用密钥(非真实机密;仅验证 framing 语义)
SK = "a0" * 32
PK = "b533d8ad9fcfbdde0b481c1b334ddc3c53412fd614564e7e5afd020368d382c3"


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "client_nonce": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "creator_pubkey": PK,
        "event_type": "COMMIT",
        "protocol_id": "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2",
        "commit": {"c_m": "0717cc993bef93ce97480167625612992f230690779944c9ab69f650cbb97c68"},
    }
    body.update(overrides)
    return body


def test_sign_verify_roundtrip() -> None:
    body = _body()
    sig = sign_event_body(SK, body)
    assert len(sig) == 128
    verify_event_signature(PK, body, sig)  # 不抛即通过


def test_signature_is_deterministic() -> None:
    # Ed25519 确定性:同 (sk, msg) 两次签名相同
    body = _body()
    assert sign_event_body(SK, body) == sign_event_body(SK, body)


def test_signature_prefix_contains_real_null_bytes() -> None:
    # framing 前缀必须含真实 0x00(SPEC §10.2,与 framing.py 同一约定)
    assert b"\x00" in CREATOR_EVENT_PREFIX
    assert CREATOR_EVENT_PREFIX == b"MUSIC-ZK\x00CREATOR-EVENT\x00V1\x00"


def test_tamper_body_byte_fails() -> None:
    body = _body()
    sig = sign_event_body(SK, body)
    body["commit"] = {"c_m": "0717cc993bef93ce97480167625612992f230690779944c9ab69f650cbb97c69"}
    with pytest.raises(SignatureError):
        verify_event_signature(PK, body, sig)


def test_tamper_signature_fails() -> None:
    body = _body()
    sig = sign_event_body(SK, body)
    bad = ("ff" if sig[:2] != "ff" else "00") + sig[2:]
    with pytest.raises(SignatureError):
        verify_event_signature(PK, body, bad)


def test_wrong_pubkey_fails() -> None:
    body = _body()
    sig = sign_event_body(SK, body)
    other_pk = "3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29"
    with pytest.raises(SignatureError):
        verify_event_signature(other_pk, body, sig)


def test_missing_required_field_rejected() -> None:
    with pytest.raises(SignatureError):
        sign_event_body(SK, {"client_nonce": "a1b2c3d4e5f60718293a4b5c6d7e8f90"})


def test_key_order_does_not_affect_signature() -> None:
    # JCS 键排序:插入顺序不同,签名一致
    body1 = _body()
    body2 = {
        "commit": body1["commit"],
        "client_nonce": body1["client_nonce"],
        "event_type": "COMMIT",
        "protocol_id": body1["protocol_id"],
        "creator_pubkey": PK,
    }
    assert canonicalize(body1) == canonicalize(body2)
    assert sign_event_body(SK, body1) == sign_event_body(SK, body2)
