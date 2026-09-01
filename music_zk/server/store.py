"""透明日志 SQLite 存储(Phase 3,SPEC §11.1/§14)。

- 事件表:sequence(日志序号)、event_id、creator_pubkey、client_nonce、body、
  record(冻结的事件记录,即 Merkle 叶内容)、received_at_utc。
- STH 表:每次 append 后签署的树头(tree_size/tree_root/issued_at_utc/
  previous_tree_size/previous_tree_root/signature)。
- 文件表:event_id → 已发布文件(kind/path/size/sha256),按 event_id 存储路径
  (SPEC §14:文件名不可信,服务端按事件 ID 生成存储路径)。
- 两阶段发布:先写临时目录、全部校验通过后原子落库并移动文件;任何失败回滚。

Merkle 树不单独持久化:每次从 record 列重算(展品规模足够,且天然防状态分裂)。
叶内容不含 tree_size/tree_root(循环依赖,见 OPEN-QUESTIONS 2026-09-01)。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from music_zk.protocol.jcs import canonicalize
from music_zk.protocol.log import server_event_record, sign_sth, sth_body
from music_zk.protocol.merkle import MerkleTree, verify_inclusion

# 空树根(SPEC §11.3 STH 初始 previous_tree_root)
EMPTY_ROOT = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class StoreError(ValueError):
    """存储/日志操作失败(重复、引用缺失、回滚等)。"""


def utc_now() -> str:
    """RFC 3339 UTC 时间戳(秒级,如 2026-09-01T08:00:00Z)。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EventRow:
    """数据库事件行(sequence 为 1-based 日志序号)。"""

    sequence: int
    event_type: str
    event_id: str
    creator_pubkey: str
    client_nonce: str
    protocol_id: str
    body: dict[str, Any]  # 被接受事件(含 signature)
    record: dict[str, Any]  # 冻结的事件记录(叶内容,含 sequence/event_id)
    received_at_utc: str

    def record_bytes(self) -> bytes:
        return canonicalize(self.record)


@dataclass(frozen=True)
class SignedTreeHead:
    """已签署树头。"""

    tree_size: int
    tree_root: str
    issued_at_utc: str
    previous_tree_size: int
    previous_tree_root: str
    signature: str

    def body(self) -> dict[str, Any]:
        return sth_body(
            self.tree_size, self.tree_root, self.issued_at_utc,
            self.previous_tree_size, self.previous_tree_root,
        )


class Store:
    """SQLite + 文件系统 + 内存重算 Merkle 树的日志存储。"""

    def __init__(
        self,
        db_path: str | Path,
        data_dir: str | Path,
        server_sk_hex: str,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.server_sk_hex = server_sk_hex
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "tmp").mkdir(exist_ok=True)
        (self.data_dir / "files").mkdir(exist_ok=True)
        # FastAPI 在线程池跑 handler:check_same_thread=False + 写锁串行化
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY,          -- 1-based 日志序号
                event_type TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                creator_pubkey TEXT NOT NULL,
                client_nonce TEXT NOT NULL,
                protocol_id TEXT NOT NULL,
                body_json TEXT NOT NULL,
                record_json TEXT NOT NULL,
                received_at_utc TEXT NOT NULL,
                UNIQUE(creator_pubkey, client_nonce)
            );
            CREATE TABLE IF NOT EXISTS sth (
                tree_size INTEGER PRIMARY KEY,
                tree_root TEXT NOT NULL,
                issued_at_utc TEXT NOT NULL,
                previous_tree_size INTEGER NOT NULL,
                previous_tree_root TEXT NOT NULL,
                signature TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                event_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY (event_id, kind)
            );
            """
        )
        self._conn.commit()

    # ---------- 查询 ----------

    def event_by_id(self, event_id: str) -> EventRow | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def event_by_nonce(self, creator_pubkey: str, client_nonce: str) -> EventRow | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE creator_pubkey = ? AND client_nonce = ?",
            (creator_pubkey, client_nonce),
        ).fetchone()
        return self._row_to_event(row) if row else None

    def event_by_sequence(self, sequence: int) -> EventRow | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE sequence = ?", (sequence,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def latest_sth(self) -> SignedTreeHead | None:
        row = self._conn.execute(
            "SELECT * FROM sth ORDER BY tree_size DESC LIMIT 1"
        ).fetchone()
        return self._row_to_sth(row) if row else None

    def events(self) -> list[EventRow]:
        rows = self._conn.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        return [self._row_to_event(r) for r in rows]

    def files_of(self, event_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM files WHERE event_id = ? ORDER BY kind", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def file_path(self, event_id: str, kind: str) -> Path | None:
        row = self._conn.execute(
            "SELECT rel_path FROM files WHERE event_id = ? AND kind = ?",
            (event_id, kind),
        ).fetchone()
        return self.data_dir / row["rel_path"] if row else None

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> EventRow:
        return EventRow(
            sequence=row["sequence"],
            event_type=row["event_type"],
            event_id=row["event_id"],
            creator_pubkey=row["creator_pubkey"],
            client_nonce=row["client_nonce"],
            protocol_id=row["protocol_id"],
            body=json.loads(row["body_json"]),
            record=json.loads(row["record_json"]),
            received_at_utc=row["received_at_utc"],
        )

    @staticmethod
    def _row_to_sth(row: sqlite3.Row) -> SignedTreeHead:
        return SignedTreeHead(
            tree_size=row["tree_size"],
            tree_root=row["tree_root"],
            issued_at_utc=row["issued_at_utc"],
            previous_tree_size=row["previous_tree_size"],
            previous_tree_root=row["previous_tree_root"],
            signature=row["signature"],
        )

    # ---------- 写入(两阶段:调用方先完成全部校验) ----------

    def append(
        self,
        body: dict[str, Any],
        received_at_utc: str | None = None,
    ) -> tuple[EventRow, SignedTreeHead]:
        """把已校验的 accepted_event(body) 追加进日志并签署新 STH。

        原子:单事务内插入事件行 + 重算树 + 写 STH。重复(creator+nonce)抛 StoreError。
        """
        if received_at_utc is None:
            received_at_utc = utc_now()
        with self._lock:
            return self._append_locked(body, received_at_utc)

    def _append_locked(
        self, body: dict[str, Any], received_at_utc: str
    ) -> tuple[EventRow, SignedTreeHead]:
        with self._conn:
            if self.event_by_nonce(body["creator_pubkey"], body["client_nonce"]) is not None:
                raise StoreError("重复事件(creator_pubkey + client_nonce 已存在)")
            # 树重算(在事务内读取当前记录)
            tree = MerkleTree()
            for r in self._conn.execute("SELECT record_json FROM events ORDER BY sequence"):
                tree.append(canonicalize(json.loads(r["record_json"])))
            prev_sth = self.latest_sth()
            sequence = tree.size + 1
            record = server_event_record(body, sequence, received_at_utc)
            tree.append(canonicalize(record))
            tree_root = tree.root().hex()
            prev_size = prev_sth.tree_size if prev_sth else 0
            prev_root = prev_sth.tree_root if prev_sth else EMPTY_ROOT
            sth = sth_body(sequence, tree_root, received_at_utc, prev_size, prev_root)
            signature = sign_sth(self.server_sk_hex, sth)
            self._conn.execute(
                "INSERT INTO events (sequence, event_type, event_id, creator_pubkey,"
                " client_nonce, protocol_id, body_json, record_json, received_at_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sequence,
                    body["event_type"],
                    record["event_id"],
                    body["creator_pubkey"],
                    body["client_nonce"],
                    body["protocol_id"],
                    json.dumps(body, ensure_ascii=False, sort_keys=True),
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                    received_at_utc,
                ),
            )
            self._conn.execute(
                "INSERT INTO sth (tree_size, tree_root, issued_at_utc,"
                " previous_tree_size, previous_tree_root, signature) VALUES (?,?,?,?,?,?)",
                (sequence, tree_root, received_at_utc, prev_size, prev_root, signature),
            )
        return EventRow(
            sequence=sequence,
            event_type=body["event_type"],
            event_id=record["event_id"],
            creator_pubkey=body["creator_pubkey"],
            client_nonce=body["client_nonce"],
            protocol_id=body["protocol_id"],
            body=body,
            record=record,
            received_at_utc=received_at_utc,
        ), SignedTreeHead(
            sequence, tree_root, received_at_utc, prev_size, prev_root, signature
        )

    # ---------- inclusion proof ----------

    def inclusion_proof(self, sequence: int) -> tuple[EventRow, list[bytes], SignedTreeHead]:
        """返回 (事件行, inclusion proof, 当前 STH);序列号越界抛 StoreError。"""
        with self._lock:
            return self._inclusion_locked(sequence)

    def _inclusion_locked(self, sequence: int) -> tuple[EventRow, list[bytes], SignedTreeHead]:
        event = self.event_by_sequence(sequence)
        if event is None:
            raise StoreError(f"sequence {sequence} 不存在")
        sth = self.latest_sth()
        if sth is None:
            raise StoreError("日志为空")
        tree = MerkleTree()
        for r in self.events():
            tree.append(r.record_bytes())
        proof = tree.inclusion_proof(sequence - 1)
        ok = verify_inclusion(
            event.record_bytes(), sequence - 1, proof, bytes.fromhex(sth.tree_root), tree.size
        )
        if not ok:
            raise StoreError("inclusion proof 与 STH 根不一致(日志被篡改?)")
        return event, proof, sth

    # ---------- 文件发布(两阶段) ----------

    def publish_files(self, event_id: str, files: dict[str, bytes]) -> None:
        """把校验通过的临时文件发布为按 event_id 存储的正式文件(两阶段之第二阶段)。

        调用方负责第一阶段(写临时目录 + 校验);本方法只做原子化落地:
        写完整 → rename 到正式路径,失败即清理并回滚 files 表。
        """
        event_dir = self.data_dir / "files" / event_id
        event_dir.mkdir(parents=True, exist_ok=False)
        try:
            for kind, data in files.items():
                tmp = self.data_dir / "tmp" / f"{uuid.uuid4().hex}.{kind}"
                tmp.write_bytes(data)
                final = event_dir / kind
                os.replace(tmp, final)  # 同卷原子 rename
                sha = hashlib.sha256(data).hexdigest()
                self._conn.execute(
                    "INSERT INTO files (event_id, kind, rel_path, size, sha256)"
                    " VALUES (?,?,?,?,?)",
                    (event_id, kind, f"files/{event_id}/{kind}", len(data), sha),
                )
            self._conn.commit()
        except Exception:
            shutil.rmtree(event_dir, ignore_errors=True)
            self._conn.rollback()
            raise

    # ---------- 清理 ----------

    def cleanup_tmp(self) -> None:
        """清空临时目录(失败请求残留)。"""
        tmp = self.data_dir / "tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
            tmp.mkdir()
