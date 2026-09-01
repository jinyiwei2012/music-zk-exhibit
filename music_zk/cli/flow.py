"""CLI 流程编排(Phase 3,SPEC §13 六步流程)。

  commit create  → t0:本地承诺 C_M + 签名 → 服务端 COMMIT 事件
  song publish   → t1:公开 S 的 C_S + 文件 → 服务端 RELEASE 事件
  prove          → 本地:ReferenceSynth 渲染 V → 真实 zkVM 证明 → 独立验证
  proof publish  → t2:V/receipt/journal/manifest → 服务端 PROOF 事件(服务端复验)

所有函数可注入 httpx 传输(测试用 ASGITransport 指向内嵌 app;生产走真实 HTTP)。
签名 framing 见 music_zk/protocol/signing.py;事件结构见 SPEC §11.1。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from music_zk.protocol.signing import sign_event_body
from music_zk.verifier.framing import PROTOCOL_ID, SALT_LEN, commit_midi, commit_reference_wav, commit_song

# creator-secret/ 与 proof-work/ 的文件名(SPEC §12.1 / §13)
ORIGINAL_MIDI = "original.mid"
SALT_FILE = "salt.bin"
PRIVATE_KEY_FILE = "creator-private-key"
PUBLIC_KEY_FILE = "creator-public-key.txt"
COMMIT_RECEIPT = "commit-receipt.json"
RELEASE_RECEIPT = "release-receipt.json"
PROOF_RECEIPT = "proof-receipt.json"


class FlowError(ValueError):
    """流程步骤失败(本地文件/服务端响应/验证失败)。"""


class _ExternalTransport(httpx.BaseTransport):
    """外部注入的 transport(如测试用 ASGI):所有权在调用方,close 为 no-op。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._inner.handle_request(request)

    def close(self) -> None:
        pass


def _client(base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> httpx.Client:
    wrapped = _ExternalTransport(transport) if transport is not None else None
    return httpx.Client(base_url=base_url.rstrip("/"), transport=wrapped, timeout=120)


def _check_response(resp: httpx.Response, what: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:  # noqa: BLE001
            detail = resp.text[:400]
        raise FlowError(f"{what} 失败(HTTP {resp.status_code}): {detail}")
    return resp.json()


def _load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise FlowError(f"读取 {what} 失败: {path}: {e}") from None


def _save_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_secret(secret_dir: Path) -> dict[str, Any]:
    """读取 creator-secret/ 的必需私密材料。"""
    secret_dir = Path(secret_dir)
    midi = secret_dir / ORIGINAL_MIDI
    salt = secret_dir / SALT_FILE
    sk = secret_dir / PRIVATE_KEY_FILE
    pk = secret_dir / PUBLIC_KEY_FILE
    for p in (midi, salt, sk, pk):
        if not p.exists():
            raise FlowError(f"creator-secret/ 缺少 {p.name}(先跑 identity init + commit create)")
    salt_bytes = salt.read_bytes()
    if len(salt_bytes) != SALT_LEN:
        raise FlowError(f"salt.bin 必须恰 {SALT_LEN} 字节")
    return {
        "midi": midi.read_bytes(),
        "salt": salt_bytes,
        "sk_hex": sk.read_bytes().hex(),
        "pk_hex": pk.read_text(encoding="ascii").strip(),
        "commit_receipt": _load_json(secret_dir / COMMIT_RECEIPT, COMMIT_RECEIPT),
    }


def _event_body(
    sk_hex: str, pk_hex: str, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """构造签名事件:16 字节随机 nonce(hex)+ 必需字段 + payload。"""
    body: dict[str, Any] = {
        "client_nonce": secrets.token_bytes(16).hex(),
        "creator_pubkey": pk_hex,
        "event_type": event_type,
        "protocol_id": PROTOCOL_ID,
        **payload,
    }
    body["signature"] = sign_event_body(sk_hex, body)
    return body


# ---------- t0 ----------

def commit_create(
    midi_path: str | Path,
    secret_dir: str | Path,
    server_url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """t0:读 MIDI → 生盐 → 本地 C_M → 签名 → 服务端 COMMIT;落 commit-receipt.json。

    私密材料(original.mid/salt.bin)只写本地 creator-secret/,不上传(红线 1)。
    """
    secret_dir = Path(secret_dir)
    if not secret_dir.is_dir():
        raise FlowError(f"creator-secret/ 不存在: {secret_dir}(先 identity init)")
    midi = Path(midi_path).read_bytes()

    # 私密材料原子落盘(已存在即停,不覆盖;红线 1:只存本地)
    salt_bytes = os.urandom(SALT_LEN)
    midi_path_saved = secret_dir / ORIGINAL_MIDI
    salt_path = secret_dir / SALT_FILE
    for target in (midi_path_saved, salt_path):
        if target.exists():
            raise FlowError(f"{target.name} 已存在,不覆盖(需先 identity init 建新目录)")
    midi_path_saved.write_bytes(midi)
    salt_path.write_bytes(salt_bytes)

    c_m = commit_midi(midi, salt_bytes)
    sk_hex = (secret_dir / PRIVATE_KEY_FILE).read_bytes().hex()
    pk_hex = (secret_dir / PUBLIC_KEY_FILE).read_text(encoding="ascii").strip()
    body = _event_body(sk_hex, pk_hex, "COMMIT", {"commit": {"c_m": c_m.hex()}})

    with _client(server_url, transport) as c:
        resp = _check_response(c.post("/api/v1/commit-events", json=body), "COMMIT 提交")
    receipt = {"event": body, "server": resp, "c_m_hex": c_m.hex()}
    _save_json(secret_dir / COMMIT_RECEIPT, receipt)
    return receipt


# ---------- t1 ----------

def song_publish(
    song_path: str | Path,
    secret_dir: str | Path,
    server_url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """t1:计算 C_S = CommitSong(S) → 引用 COMMIT event_id → 服务端 RELEASE。"""
    secret_dir = Path(secret_dir)
    secret = _read_secret(secret_dir)
    song = Path(song_path).read_bytes()
    commit_event_id = secret["commit_receipt"]["server"]["event"]["event_id"]

    c_s = commit_song(song)
    body = _event_body(secret["sk_hex"], secret["pk_hex"], "RELEASE", {
        "commit_event_id": commit_event_id,
        "release": {
            "c_s": c_s.hex(),
            "song_file": {
                "name": Path(song_path).name,
                "size": len(song),
                "mime": "audio/wav",
            },
        },
    })
    with _client(server_url, transport) as c:
        resp = _check_response(
            c.post(
                "/api/v1/release-events",
                data={"json_body": json.dumps(body, ensure_ascii=False)},
                files={"song": (Path(song_path).name, song, "audio/wav")},
            ),
            "RELEASE 提交",
        )
    receipt = {"event": body, "server": resp, "c_s_hex": c_s.hex(), "commit_event_id": commit_event_id}
    _save_json(secret_dir / RELEASE_RECEIPT, receipt)
    return receipt


# ---------- t2 ----------

def proof_publish(
    work_dir: str | Path,
    secret_dir: str | Path,
    server_url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """t2:把 prove 产物(V/receipt/journal/manifest)提交为服务端 PROOF 事件。

    服务端会本地跑标准 verifier 复验(SPEC §11.1)后才接受。
    """
    work_dir = Path(work_dir)
    secret = _read_secret(secret_dir)
    release = _load_json(secret_dir / RELEASE_RECEIPT, RELEASE_RECEIPT)
    v = (work_dir / "v.wav").read_bytes()
    receipt_bytes = (work_dir / "receipt.bin").read_bytes()
    journal_bytes = (work_dir / "journal.bin").read_bytes()
    manifest = (work_dir / "manifest.json").read_bytes()

    c_v = commit_reference_wav(v)
    body = _event_body(secret["sk_hex"], secret["pk_hex"], "PROOF", {
        "commit_event_id": secret["commit_receipt"]["server"]["event"]["event_id"],
        "release_event_id": release["server"]["event"]["event_id"],
        "proof": {
            "c_v": c_v.hex(),
            "journal_hash": hashlib.sha256(journal_bytes).hexdigest(),
            "receipt_hash": hashlib.sha256(receipt_bytes).hexdigest(),
            "v_hash": hashlib.sha256(v).hexdigest(),
            "manifest_hash": hashlib.sha256(manifest).hexdigest(),
        },
    })
    with _client(server_url, transport) as c:
        resp = _check_response(
            c.post(
                "/api/v1/proof-events",
                data={"json_body": json.dumps(body, ensure_ascii=False)},
                files={
                    "v": ("reference-V.wav", v, "audio/wav"),
                    "receipt": ("receipt.bin", receipt_bytes, "application/octet-stream"),
                    "journal": ("journal.bin", journal_bytes, "application/octet-stream"),
                    "manifest": ("manifest.json", manifest, "application/json"),
                },
            ),
            "PROOF 提交",
        )
    receipt = {"event": body, "server": resp, "c_v_hex": c_v.hex()}
    _save_json(secret_dir / PROOF_RECEIPT, receipt)
    return receipt


# ---------- 工具 ----------

def preflight_midi(midi_path: str | Path, reference_native: str) -> str:
    """midi preflight:用 reference-native 解析校验 MIDI Profile 1(返回 C_V 需要 render)。"""
    import subprocess

    midi_path = Path(midi_path)
    proc = subprocess.run(
        [reference_native, "render", str(midi_path), os.devnull],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        raise FlowError(f"MIDI 未通过 Profile 1 解析(fail-closed): {proc.stderr[-300:]}")
    return proc.stdout
