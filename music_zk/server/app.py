"""FastAPI 服务端(Phase 3,SPEC §11.1 / §14 / AGENTS.md §3.6)。

端点:
  POST /api/v1/commit-events            JSON body
  POST /api/v1/release-events           multipart:json + song
  POST /api/v1/proof-events             multipart:json + v + receipt + journal + manifest
  GET  /api/v1/claims/{claim_id}        COMMIT event_id → 三事件 + 回执
  GET  /api/v1/claims/{claim_id}/evidence.zip   已存文件 + 事件/回执(完整证据包属 Phase 4)
  GET  /api/v1/log/checkpoint           最新 STH
  GET  /api/v1/log/entries/{sequence}   事件记录
  GET  /api/v1/log/inclusion/{sequence} 事件 + inclusion proof + STH

服务端 MUST(SPEC §11.1):
  - 验 creator 签名(§10.2);字段黑名单拒绝 midi/salt/private_key(递归);
  - creator_pubkey+client_nonce 去重(409);引用存在且 creator/protocol 一致;
  - 顺序 COMMIT.seq < RELEASE.seq < PROOF.seq(引用保证,append 顺序单调);
  - PROOF 上传前本地跑标准 verifier 成功 + V 的 C_V 与 journal 一致;
  - 大小限制:JSON ≤256KiB、S ≤20MB、V ≤2MB、bundle ≤20MB;
  - 两阶段发布:临时目录校验通过后才落库/移动文件,失败即删。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from music_zk.protocol.log import verify_sth
from music_zk.protocol.signing import verify_event_signature
from music_zk.verifier.framing import commit_reference_wav
from music_zk.verifier.journal import Journal, JournalError

from .store import EventRow, SignedTreeHead, Store, StoreError

# 大小限制(SPEC §14)
MAX_JSON_BODY = 256 * 1024
MAX_SONG = 20 * 1024 * 1024
MAX_V = 2 * 1024 * 1024
MAX_BUNDLE = 20 * 1024 * 1024

# 字段黑名单(SPEC §11.1:拒绝 witness 相关字段,防正常 API 误传)
BLACKLIST_KEYS = ("midi", "salt", "private_key", "witness")

# 事件类型
EVENT_TYPES = ("COMMIT", "RELEASE", "PROOF")


class ServerError(Exception):
    """业务校验失败(映射为 HTTP 4xx)。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _contains_blacklisted_key(obj: Any) -> str | None:
    """递归扫描 dict 键名,命中黑名单返回键名,否则 None。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in BLACKLIST_KEYS:
                return k
            hit = _contains_blacklisted_key(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for item in obj:
            hit = _contains_blacklisted_key(item)
            if hit:
                return hit
    return None


def _parse_json_body(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BODY:
        raise ServerError(413, f"JSON body 超过 {MAX_JSON_BODY} 字节(SPEC §14)")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ServerError(400, f"JSON 解析失败: {e}") from None
    if not isinstance(obj, dict):
        raise ServerError(400, "body 必须是 JSON 对象")
    return obj


def _validate_common(
    body: dict[str, Any], expected_type: str, protocol_id: str | None = None
) -> None:
    """共同校验:黑名单、event_type、必需字段、creator 签名、nonce 格式。"""
    hit = _contains_blacklisted_key(body)
    if hit:
        raise ServerError(400, f"字段 '{hit}' 被禁止上传(SPEC §11.1 字段黑名单)")
    if body.get("event_type") != expected_type:
        raise ServerError(400, f"event_type 必须是 {expected_type}")
    sig = body.get("signature")
    event_body = {k: v for k, v in body.items() if k != "signature"}
    if not isinstance(sig, str) or len(sig) != 128:
        raise ServerError(400, "signature 必须是 64 字节 lowercase hex")
    pk = body.get("creator_pubkey")
    if not isinstance(pk, str):
        raise ServerError(400, "creator_pubkey 缺失")
    nonce = body.get("client_nonce")
    if not isinstance(nonce, str):
        raise ServerError(400, "client_nonce 缺失")
    try:
        verify_event_signature(pk, event_body, sig)
    except Exception as e:  # noqa: BLE001 - 统一映射 401
        raise ServerError(401, f"creator 签名无效: {e}") from None
    if protocol_id is not None and body.get("protocol_id") != protocol_id:
        raise ServerError(400, "protocol_id 与本展品不一致")


def _referenced_event(store: Store, ref_id: str, expected_type: str, body: dict[str, Any]):
    """引用校验:存在 + 类型 + creator/protocol 一致(SPEC §11.1)。"""
    ev = store.event_by_id(ref_id)
    if ev is None:
        raise ServerError(404, f"引用事件不存在: {ref_id}")
    if ev.event_type != expected_type:
        raise ServerError(400, f"引用 {ref_id} 不是 {expected_type} 事件")
    if ev.creator_pubkey != body["creator_pubkey"] or ev.protocol_id != body["protocol_id"]:
        raise ServerError(400, "引用事件的 creator/protocol 不一致")
    return ev


def _run_verifier(verify_bin: str, workdir: Path, expect_c_v: str) -> str:
    """在 workdir 内运行标准 verifier(zkvm-verify)复验 receipt/journal。

    workdir 需含 receipt.bin + journal.bin;expect_c_v 绑定 journal.C_V。
    返回错误说明(成功返回空串)。
    """
    args = shlex.split(verify_bin, posix=(os.name != "nt")) + ["--expect-c-v", expect_c_v]
    try:
        proc = subprocess.run(
            args, cwd=workdir, capture_output=True, timeout=1200, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"verifier 执行失败: {e}"
    if proc.returncode != 0:
        return f"verifier 拒绝(exit={proc.returncode}): {proc.stderr.decode(errors='replace')[-300:]}"
    return ""


def _proof_verification(
    verify_bin: str | None,
    tmp_dir: Path,
    v_bytes: bytes,
    receipt_bytes: bytes,
    journal_bytes: bytes,
) -> None:
    """PROOF 接受前校验(SPEC §11.1):本地 verifier + V 的 C_V 与 journal 一致。"""
    if verify_bin is None:
        raise ServerError(500, "服务端未配置 verifier(MZK_VERIFY_BIN),无法接受 PROOF")
    try:
        journal = Journal.decode(journal_bytes)
    except JournalError as e:
        raise ServerError(400, f"journal 结构无效: {e}") from None
    cv_from_journal = journal.c_v.hex()
    cv_from_v = commit_reference_wav(v_bytes).hex()
    if cv_from_journal != cv_from_v:
        raise ServerError(400, "V 的 C_V 与 journal 不一致(SPEC §11.1)")
    (tmp_dir / "receipt.bin").write_bytes(receipt_bytes)
    (tmp_dir / "journal.bin").write_bytes(journal_bytes)
    err = _run_verifier(verify_bin, tmp_dir, cv_from_journal)
    if err:
        raise ServerError(422, f"PROOF 未被标准 verifier 接受: {err}")


def _check_size(name: str, data: bytes | int, limit: int) -> None:
    size = len(data) if isinstance(data, (bytes, bytearray)) else data
    if size > limit:
        raise ServerError(413, f"{name} 超过 {limit} 字节限制(SPEC §14)")


async def _read_upload(upload: UploadFile | None, name: str, limit: int) -> bytes:
    if upload is None:
        raise ServerError(400, f"缺少上传文件: {name}")
    data = await upload.read(limit + 1)  # starlette ≥1.x:UploadFile.read 是 async
    _check_size(name, data, limit)
    return data


def create_app(
    store: Store,
    verify_bin: str | None = None,
    protocol_id: str = "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2",
) -> FastAPI:
    """构造 FastAPI 应用。

    verify_bin:标准 verifier 调用(默认 C:/music-zk-target/debug/zkvm-verify.exe,
    不存在则 PROOF 上传被 500 拒绝——服务端 MUST 本地验证才接受 PROOF)。
    """
    if verify_bin is None:
        candidate = Path("C:/music-zk-target/debug/zkvm-verify.exe")
        verify_bin = str(candidate) if candidate.exists() else None

    app = FastAPI(title="Music-ZK Exhibit Log Server", version="0.1.0")

    def _append(body: dict[str, Any], files: dict[str, bytes] | None = None) -> dict[str, Any]:
        """公共追加逻辑:签名/引用校验 + 两阶段发布。"""
        files = files or {}
        try:
            if body["event_type"] == "RELEASE":
                _referenced_event(store, body["commit_event_id"], "COMMIT", body)
            elif body["event_type"] == "PROOF":
                _referenced_event(store, body["commit_event_id"], "COMMIT", body)
                _referenced_event(store, body["release_event_id"], "RELEASE", body)
            event, sth = store.append(body)
            if files:
                store.publish_files(event.event_id, files)
        except StoreError as e:
            raise ServerError(409 if "重复" in str(e) else 400, str(e)) from None
        return {
            "event": {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "event_id": event.event_id,
                "received_at_utc": event.received_at_utc,
            },
            "sth": {
                "tree_size": sth.tree_size,
                "tree_root": sth.tree_root,
                "issued_at_utc": sth.issued_at_utc,
                "previous_tree_size": sth.previous_tree_size,
                "previous_tree_root": sth.previous_tree_root,
                "signature": sth.signature,
            },
        }

    def _sth_payload(sth: SignedTreeHead) -> dict[str, Any]:
        return {
            "tree_size": sth.tree_size,
            "tree_root": sth.tree_root,
            "issued_at_utc": sth.issued_at_utc,
            "previous_tree_size": sth.previous_tree_size,
            "previous_tree_root": sth.previous_tree_root,
            "signature": sth.signature,
        }

    # ---------- 三事件端点 ----------

    @app.post("/api/v1/commit-events")
    def commit_event(payload: dict[str, Any]) -> dict[str, Any]:
        _validate_common(payload, "COMMIT", protocol_id)
        return _append(payload)

    @app.post("/api/v1/release-events")
    async def release_event(json_body: str = Form(...), song: UploadFile | None = None) -> dict[str, Any]:
        body = _parse_json_body(json_body.encode())
        _validate_common(body, "RELEASE", protocol_id)
        if "commit_event_id" not in body:
            raise ServerError(400, "RELEASE 缺少 commit_event_id")
        song_data = await _read_upload(song, "song", MAX_SONG)
        _check_size("song", song_data, MAX_SONG)
        return _append(body, {"song": song_data})

    @app.post("/api/v1/proof-events")
    async def proof_event(
        json_body: str = Form(...),
        v: UploadFile | None = None,
        receipt: UploadFile | None = None,
        journal: UploadFile | None = None,
        manifest: UploadFile | None = None,
    ) -> dict[str, Any]:
        body = _parse_json_body(json_body.encode())
        _validate_common(body, "PROOF", protocol_id)
        for ref in ("commit_event_id", "release_event_id"):
            if ref not in body:
                raise ServerError(400, f"PROOF 缺少 {ref}")
        v_data = await _read_upload(v, "v", MAX_V)
        receipt_data = await _read_upload(receipt, "receipt", MAX_BUNDLE)
        journal_data = await _read_upload(journal, "journal", 4096)
        manifest_data = await _read_upload(manifest, "manifest", MAX_BUNDLE)
        bundle = len(receipt_data) + len(journal_data) + len(manifest_data) + len(v_data)
        _check_size("proof bundle", bundle, MAX_BUNDLE)
        # 两阶段:临时目录做 verifier 校验,通过后才落库
        with tempfile.TemporaryDirectory(prefix="mzk-proof-") as tmp:
            _proof_verification(verify_bin, Path(tmp), v_data, receipt_data, journal_data)
            return _append(body, {
                "v": v_data, "receipt": receipt_data, "journal": journal_data,
                "manifest": manifest_data,
            })

    # ---------- 查询端点 ----------

    @app.get("/api/v1/log/checkpoint")
    def checkpoint() -> dict[str, Any]:
        sth = store.latest_sth()
        if sth is None:
            raise HTTPException(404, "日志为空")
        return _sth_payload(sth)

    @app.get("/api/v1/log/entries/{sequence}")
    def entry(sequence: int) -> dict[str, Any]:
        ev = store.event_by_sequence(sequence)
        if ev is None:
            raise HTTPException(404, f"sequence {sequence} 不存在")
        return {"sequence": ev.sequence, "event": ev.record}

    @app.get("/api/v1/log/inclusion/{sequence}")
    def inclusion(sequence: int) -> dict[str, Any]:
        try:
            ev, proof, sth = store.inclusion_proof(sequence)
        except StoreError as e:
            raise HTTPException(404, str(e)) from None
        return {
            "event": ev.record,
            "inclusion_proof": [h.hex() for h in proof],
            "sth": _sth_payload(sth),
        }

    @app.get("/api/v1/claims/{claim_id}")
    def claim(claim_id: str) -> dict[str, Any]:
        commit = store.event_by_id(claim_id)
        if commit is None or commit.event_type != "COMMIT":
            raise HTTPException(404, f"claim 不存在: {claim_id}")
        events = store.events()
        chain = [ev for ev in events if ev.creator_pubkey == commit.creator_pubkey]
        return {
            "claim_id": claim_id,
            "creator_pubkey": commit.creator_pubkey,
            "events": [
                {
                    "sequence": ev.sequence,
                    "event_type": ev.event_type,
                    "event_id": ev.event_id,
                    "record": ev.record,
                }
                for ev in chain
            ],
            "sth": _sth_payload(store.latest_sth()) if store.latest_sth() else None,
        }

    @app.get("/api/v1/claims/{claim_id}/evidence.zip")
    def evidence_zip(claim_id: str) -> FileResponse:
        """打包该 claim 的全部已存文件 + 事件/回执(完整公开证据包属 Phase 4)。"""
        commit = store.event_by_id(claim_id)
        if commit is None:
            raise HTTPException(404, f"claim 不存在: {claim_id}")
        chain = [ev for ev in store.events() if ev.creator_pubkey == commit.creator_pubkey]
        zip_path = Path(tempfile.gettempdir()) / f"mzk-evidence-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ev in chain:
                zf.writestr(f"events/seq-{ev.sequence:04d}-{ev.event_type}.json",
                            json.dumps(ev.record, ensure_ascii=False, indent=2))
                for f in store.files_of(ev.event_id):
                    p = store.file_path(ev.event_id, f["kind"])
                    if p and p.exists():
                        zf.write(p, f"files/{ev.event_id}/{f['kind']}")
            sth = store.latest_sth()
            if sth:
                zf.writestr("log/checkpoint.json",
                            json.dumps(_sth_payload(sth), ensure_ascii=False, indent=2))
        return FileResponse(zip_path, media_type="application/zip",
                            filename=f"evidence-{claim_id[:8]}.zip")

    # ---------- 错误映射 ----------

    @app.exception_handler(ServerError)
    async def _server_error_handler(request, exc: ServerError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    return app
