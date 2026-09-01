"""公开证据包构建(Phase 4,SPEC §12.2)。

    public-evidence/
      claim.json  protocol-manifest.json  creator-public-key.txt
      commit-receipt.json  release-receipt.json  proof-receipt.json
      journal.bin  zkvm-receipt.bin  song-S.<ext>  reference-V.wav
      checksums.sha256  VERIFYING.md

回执从服务端 /log/inclusion/{seq} 拉取(含 record + STH + inclusion proof),
与本地 creator-secret 里的签名事件(含 signature)合并。包内绝不含 MIDI/盐/私钥
(红线 1);唯一外部信任材料 = 用户选择的 server public key + 冻结 guest Image ID。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

from music_zk.cli.flow import (
    COMMIT_RECEIPT,
    FlowError,
    PROOF_RECEIPT,
    RELEASE_RECEIPT,
    PUBLIC_KEY_FILE,
    _load_json,
)

VERIFYING_MD = """\
# Music-ZK 公开证据包验证说明

本目录(public-evidence/)可完全离线验证,唯一外部信任材料:

1. 服务端公钥(trust root,你主动选择)——例如 `server-data/server-public-key.txt`;
2. 冻结的 guest Image ID(`protocol-manifest.json` 的 `guest.image_id`)。

验证命令:

    music-zk verify public-evidence/ --server-key <服务端公钥 hex>

输出 SPEC §15 的十一项逐项结果;总体有效要求步骤 2..10 全部通过。

注意:
- 本包不含 MIDI、盐或私钥(创作者私密材料永不公开)。
- checksums.sha256 只验证传输完整性,不替代签名或零知识证明。
- 本系统不判断 S 与 V 的音乐相似性;不证明原创、来源或版权。
"""


def _fetch_json(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=60) as resp:
        return json.loads(resp.read())


def export_evidence(
    secret_dir: str | Path,
    work_dir: str | Path,
    server_url: str,
    song_path: str | Path,
    out_dir: str | Path,
    *,
    server_url_label: str | None = None,
) -> Path:
    """构建公开证据包并返回输出目录。

    secret_dir:creator-secret/(须含三个回执);work_dir:proof-work/(prove 产物);
    song_path:公开歌曲 S 的本地文件(用于 C_S 复算与打包);server_url:demo 服务端。
    """
    secret = Path(secret_dir)
    work = Path(work_dir)
    out = Path(out_dir)
    if out.exists():
        raise FlowError(f"输出目录已存在,不覆盖: {out}")
    out.mkdir(parents=True)

    try:
        # 1) 三个回执:签名事件(本地)+ record/STH/inclusion(服务端)
        base = server_url
        for local_name, server_name, receipt_name in (
            (COMMIT_RECEIPT, "commit", "commit-receipt.json"),
            (RELEASE_RECEIPT, "release", "release-receipt.json"),
            (PROOF_RECEIPT, "proof", "proof-receipt.json"),
        ):
            local = _load_json(secret / local_name, local_name)
            seq = local["server"]["event"]["sequence"]
            inc = _fetch_json(base, f"/api/v1/log/inclusion/{seq}")
            merged = {
                "event": local["event"],  # 含 signature 的 accepted event
                "record": inc["event"],
                "sth": inc["sth"],
                "inclusion_proof": inc["inclusion_proof"],
            }
            (out / receipt_name).write_text(
                json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        # 2) 元数据与文件
        commit = _load_json(out / "commit-receipt.json", "commit-receipt.json")
        claim = {
            "claim_id": commit["record"]["event_id"],
            "protocol_id": commit["event"]["protocol_id"],
            "creator_pubkey": commit["event"]["creator_pubkey"],
            "server_url": server_url_label or base,
            "created_at_utc": commit["record"]["received_at_utc"],
        }
        (out / "claim.json").write_text(
            json.dumps(claim, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        shutil.copy2(work / "manifest.json", out / "protocol-manifest.json")
        shutil.copy2(secret / PUBLIC_KEY_FILE, out / "creator-public-key.txt")
        shutil.copy2(work / "journal.bin", out / "journal.bin")
        shutil.copy2(work / "receipt.bin", out / "zkvm-receipt.bin")
        shutil.copy2(work / "v.wav", out / "reference-V.wav")
        song = Path(song_path)
        if not song.exists():
            raise FlowError(f"歌曲文件不存在: {song_path}")
        song_name = f"song-S{song.suffix or '.bin'}"
        shutil.copy2(song, out / song_name)

        # 3) checksums(不含自身)
        checksums = []
        for f in sorted(out.iterdir()):
            if f.name in ("checksums.sha256", "VERIFYING.md"):
                continue
            checksums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
        (out / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")

        (out / "VERIFYING.md").write_text(VERIFYING_MD, encoding="utf-8")
        return out
    except Exception:
        import shutil as _sh

        _sh.rmtree(out, ignore_errors=True)
        raise
