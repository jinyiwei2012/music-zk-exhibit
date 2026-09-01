"""CLI 流程测试(SPEC §13):t0→t1→t2 完整闭环 + prove 六步 + 负向。

服务端用 ASGI transport 内嵌(不启真实端口);prove 的 Rust 二进制用 stub 脚本
注入(真实 zkvm-prove 由 phase1/2 门禁覆盖)。C_M/C_V 绑定与签名是真实逻辑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from music_zk.cli import flow, prove as prove_mod
from music_zk.cli.identity import init_identity
from music_zk.cli.server_cli import server_init
from music_zk.server.app import create_app
from music_zk.server.store import Store

PROTOCOL = "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2"


def _make_stub(name: str, code: str, tmp: Path) -> str:
    p = tmp / name
    p.write_text(code, encoding="ascii")
    return str(p)


@pytest.fixture()
def server_and_client(tmp_path: Path):
    """内嵌服务端:临时端口起真实 uvicorn,返回 base_url(走真 HTTP,贴近门禁)。"""
    import socket
    import threading
    import time

    import uvicorn

    server_init(tmp_path / "server-data")
    sk_hex = (tmp_path / "server-data" / "server-private-key").read_bytes().hex()
    store = Store(tmp_path / "server-data" / "log.sqlite", tmp_path / "server-data" / "public", sk_hex)
    verify_stub = _make_stub("verify_ok.py", "import sys\nsys.exit(0)\n", tmp_path)
    app = create_app(store, verify_bin=f"{sys.executable} {verify_stub}")

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)
    store.close()


@pytest.fixture()
def creator(tmp_path: Path) -> Path:
    init_identity(tmp_path / "creator-secret")
    return tmp_path / "creator-secret"


def test_commit_create_flow(creator: Path, server_and_client, tmp_path: Path) -> None:
    base_url = server_and_client
    m = tmp_path / "demo.mid"
    m.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0\x00")  # 占位(commit 不解析 MIDI)
    receipt = flow.commit_create(m, creator, base_url)
    assert receipt["server"]["event"]["event_type"] == "COMMIT"
    assert len(receipt["c_m_hex"]) == 64
    # 私密材料只落本地
    assert (creator / flow.ORIGINAL_MIDI).exists()
    assert (creator / flow.SALT_FILE).exists()
    assert (creator / flow.COMMIT_RECEIPT).exists()


def test_commit_create_refuses_overwrite(creator: Path, server_and_client, tmp_path: Path) -> None:
    base_url = server_and_client
    m = tmp_path / "demo.mid"
    m.write_bytes(b"x")
    flow.commit_create(m, creator, base_url)
    with pytest.raises(flow.FlowError):
        flow.commit_create(m, creator, base_url)


def test_song_publish_requires_commit(creator: Path, server_and_client, tmp_path: Path) -> None:
    base_url = server_and_client
    with pytest.raises(flow.FlowError):
        flow.song_publish(tmp_path / "s.wav", creator, base_url)


def test_full_t0_t1_t2_flow(creator: Path, server_and_client, tmp_path: Path) -> None:
    """完整 t0→t1→t2:COMMIT → RELEASE → PROOF(stub verifier 通过)。"""
    base_url = server_and_client
    m = tmp_path / "demo.mid"
    m.write_bytes(b"MIDI-data")
    song = tmp_path / "song.wav"
    song.write_bytes(b"WAV-data")

    r1 = flow.commit_create(m, creator, base_url)
    r2 = flow.song_publish(song, creator, base_url)
    assert r2["server"]["event"]["event_type"] == "RELEASE"

    # prove(stub 二进制):render 写固定 v.wav;prove 写与 v.wav 匹配 C_V 的 journal。
    # stub 用纯 stdlib(运行 cwd=proof-work,无 music_zk 包路径)。
    bdir = tmp_path / "bin"
    bdir.mkdir(exist_ok=True)
    RENDERED = b"WAV-rendered"

    def cv_of(data: bytes) -> str:
        import hashlib

        h = hashlib.sha256()
        h.update(b"MUSIC-ZK\x00REF-WAV\x00V1\x00")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
        return h.digest().hex()

    _make_stub(
        "reference-native.exe",
        f"import pathlib, sys\npathlib.Path(r'{tmp_path}\\proof-work').mkdir(exist_ok=True)\n"
        f"pathlib.Path(sys.argv[3]).write_bytes({RENDERED!r})\n"  # argv: [stub,render,midi,v] → v 是 argv[3]
        f"print('C_V=' + '{cv_of(RENDERED)}')\nsys.exit(0)\n",
        bdir,
    )
    _make_stub(
        "zkvm-prove.exe",
        "import hashlib, pathlib, sys\n"
        "out = pathlib.Path.cwd()\n"
        "v = b'WAV-rendered'\n"
        "h = hashlib.sha256(); h.update(b'MUSIC-ZK\\x00REF-WAV\\x00V1\\x00')\n"
        "h.update(len(v).to_bytes(8,'big')); h.update(v)\n"
        "c_v = h.digest()\n"
        "pid = b'music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2'\n"
        "ph = hashlib.sha256(pid).digest()\n"
        "j = b'MZKJNL01' + (1).to_bytes(2,'big') + ph + b'\\x11'*32 + b'\\x00'*32 + b'\\x00'*32 + b'\\x11'*32 + c_v\n"
        "out.joinpath('receipt.bin').write_bytes(b'R'*10)\n"
        "out.joinpath('journal.bin').write_bytes(j)\n"
        "print('PROVE OK')\n"
        "sys.exit(0)\n",
        bdir,
    )
    _make_stub("zkvm-verify.exe", "import sys\nsys.exit(0)\n", bdir)
    prove_mod.set_bin_dir(bdir)

    summary = prove_mod.prove(creator, r2["server"]["event"]["event_id"], tmp_path / "proof-work")
    assert (tmp_path / "proof-work" / "receipt.bin").exists()
    assert (tmp_path / "proof-work" / "journal.bin").exists()
    assert (tmp_path / "proof-work" / "v.wav").read_bytes() == RENDERED
    assert (tmp_path / "proof-work" / "manifest.json").exists()

    r3 = flow.proof_publish(tmp_path / "proof-work", creator, base_url)
    assert r3["server"]["event"]["event_type"] == "PROOF"
    # 顺序
    seqs = [r1["server"]["event"]["sequence"], r2["server"]["event"]["sequence"], r3["server"]["event"]["sequence"]]
    assert seqs == [1, 2, 3]


def test_prove_binary_missing_gives_degraded_hint(creator: Path, tmp_path: Path) -> None:
    prove_mod.set_bin_dir(tmp_path / "no-such-bin")
    from music_zk.verifier.framing import commit_midi

    (creator / flow.ORIGINAL_MIDI).write_bytes(b"m")
    (creator / flow.SALT_FILE).write_bytes(b"\x01" * 32)
    (creator / flow.COMMIT_RECEIPT).write_text(json.dumps({
        "server": {"event": {"event_id": "00" * 32}}, "c_m_hex": commit_midi(b"m", b"\x01" * 32).hex()
    }), encoding="utf-8")
    with pytest.raises(flow.FlowError) as ei:
        prove_mod.prove(creator, "00" * 32, tmp_path / "pw")
    assert "降级路径" in str(ei.value)
