"""demo 服务端 CLI(Phase 3):server init / server run。

server init:生成服务端 Ed25519 密钥到 <data>/server-private-key + server-public-key.txt
(私钥只存本机;公钥是验证者的信任根,见 SPEC §15 步骤 2)。
server run:以 uvicorn 起 FastAPI(数据目录含 SQLite + 已发布文件)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nacl.signing

from ..server.app import create_app
from ..server.store import Store

PRIVATE_KEY_FILE = "server-private-key"
PUBLIC_KEY_FILE = "server-public-key.txt"


def server_init(data_dir: str | Path) -> Path:
    """原子创建服务端密钥目录(已存在即停)。"""
    data = Path(data_dir)
    if data.exists():
        raise FileExistsError(f"{data} 已存在,不覆盖(server init 只建新目录)")
    data.mkdir(parents=True)
    sk = nacl.signing.SigningKey.generate()
    (data / PRIVATE_KEY_FILE).write_bytes(bytes(sk))
    (data / PUBLIC_KEY_FILE).write_text(sk.verify_key.encode().hex() + "\n", encoding="ascii")
    return data


def _server_sk(data: Path) -> str:
    raw = (data / PRIVATE_KEY_FILE).read_bytes()
    if len(raw) != 32:
        raise ValueError(f"{data / PRIVATE_KEY_FILE} 不是 32 字节 Ed25519 种子")
    return raw.hex()


def server_run(data_dir: str | Path, port: int = 8000) -> int:
    """以 uvicorn 起服务端(阻塞);Ctrl-C 退出。"""
    data = Path(data_dir)
    sk = _server_sk(data)
    store = Store(data / "log.sqlite", data / "public", sk)
    app = create_app(store)
    import uvicorn

    print(f"Music-ZK demo server: http://127.0.0.1:{port}  (data={data})")
    print(f"信任根公钥: {(data / PUBLIC_KEY_FILE).read_text().strip()}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    store.close()
    return 0
