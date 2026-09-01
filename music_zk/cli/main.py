"""music-zk CLI(Phase 3,SPEC §13)。

子命令:
  identity init [--out creator-secret]
  midi preflight <midi>
  commit create <midi> --server URL [--secret creator-secret]
  song publish <song> --secret creator-secret --server URL
  prove --secret creator-secret --release EVENT_ID [--out proof-work]
  proof publish --work proof-work --secret creator-secret --server URL
  server init [--data server-data]
  server run  [--data server-data] [--port 8000]
  verify <public-evidence/>        # Phase 4 实现,此处占位
  reveal-check <midi> <salt> <commit-receipt.json>   # Phase 4 实现,此处占位

红线:私密材料只落本地 creator-secret/;CLI 永不接收/上传 midi、salt、private_key 字段。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import demo, flow, prove as prove_mod


def _secret_arg(p: str | None) -> str:
    return p or "creator-secret"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="music-zk", description="Music-ZK Exhibit CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_identity = sub.add_parser("identity", help="创作者身份")
    p_identity.add_argument("action", choices=["init"])
    p_identity.add_argument("--out", default="creator-secret")

    p_preflight = sub.add_parser("midi", help="MIDI Profile 1 预检")
    p_preflight.add_argument("action", choices=["preflight"])
    p_preflight.add_argument("midi", type=Path)

    p_commit = sub.add_parser("commit", help="t0:提交 MIDI 承诺")
    p_commit.add_argument("action", choices=["create"])
    p_commit.add_argument("midi", type=Path)
    p_commit.add_argument("--server", required=True)
    p_commit.add_argument("--secret", default="creator-secret")

    p_song = sub.add_parser("song", help="t1:发布公开歌曲")
    p_song.add_argument("action", choices=["publish"])
    p_song.add_argument("song", type=Path)
    p_song.add_argument("--secret", default="creator-secret")
    p_song.add_argument("--server", required=True)

    p_prove = sub.add_parser("prove", help="本地真实证明")
    p_prove.add_argument("--secret", default="creator-secret")
    p_prove.add_argument("--release", required=True, help="RELEASE 事件 ID")
    p_prove.add_argument("--out", default="proof-work")
    p_prove.add_argument("--bin-dir", default=None, help="覆盖二进制目录(测试用)")

    p_proof = sub.add_parser("proof", help="t2:发布证明")
    p_proof.add_argument("action", choices=["publish"])
    p_proof.add_argument("--work", default="proof-work")
    p_proof.add_argument("--secret", default="creator-secret")
    p_proof.add_argument("--server", required=True)

    p_server = sub.add_parser("server", help="本地 demo 服务端")
    p_server.add_argument("action", choices=["init", "run"])
    p_server.add_argument("--data", default="server-data")
    p_server.add_argument("--port", type=int, default=8000)

    p_verify = sub.add_parser("verify", help="验证公开证据包(SPEC §15 十一项)")
    p_verify.add_argument("evidence_dir", type=Path)
    p_verify.add_argument("--server-key", required=True, help="服务端公钥(信任根,32 字节 hex)")
    p_verify.add_argument("--verify-bin", default=None, help="覆盖 zkvm-verify 路径(测试用)")
    p_verify.add_argument("--expect-image-id", default=None, help="覆盖冻结 Image ID(负向测试)")

    p_reveal = sub.add_parser("reveal-check", help="reveal:验证 (midi, salt) 打开 t0 承诺")
    p_reveal.add_argument("midi", type=Path)
    p_reveal.add_argument("salt", type=Path)
    p_reveal.add_argument("commit_receipt", type=Path)

    p_evidence = sub.add_parser("evidence", help="公开证据包导出(SPEC §12.2)")
    p_evidence.add_argument("action", choices=["export"])
    p_evidence.add_argument("--secret", default="creator-secret")
    p_evidence.add_argument("--work", default="proof-work")
    p_evidence.add_argument("--server", required=True)
    p_evidence.add_argument("--song", required=True, help="公开歌曲 S 的本地文件")
    p_evidence.add_argument("--out", default="public-evidence")

    p_demo = sub.add_parser("demo", help="篡改演示(SPEC §17.3 精神)")
    p_demo.add_argument("action", choices=["tamper"])
    p_demo.add_argument("--case", choices=demo.TAMPER_CASES, required=True)
    p_demo.add_argument("--evidence", type=Path, required=True)
    p_demo.add_argument("--secret", default=None, help="midi-byte/salt 案例需要 creator-secret")

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except flow.FlowError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "identity" and args.action == "init":
        from .identity import init_identity

        out = Path(args.out)
        info = init_identity(out)
        print(f"身份已初始化: {out}")
        print(f"  公钥: {info.public_key_hex}")
        print("  私钥只存于本目录(红线 1);不要公开/上传。")
        return 0

    if args.cmd == "midi" and args.action == "preflight":
        render = str(prove_mod.bin_dir() / prove_mod.RENDER_BIN)
        if not Path(render).exists():
            print(f"错误: 缺少 {render}(先构建 reference-native)", file=sys.stderr)
            return 1
        flow.preflight_midi(args.midi, render)
        print(f"MIDI 通过 Profile 1 预检: {args.midi}")
        return 0

    if args.cmd == "commit" and args.action == "create":
        receipt = flow.commit_create(args.midi, args.secret, args.server)
        e = receipt["server"]["event"]
        print(f"COMMIT 已接受: event_id={e['event_id']} sequence={e['sequence']}")
        print(f"  C_M={receipt['c_m_hex']}")
        print(f"  回执: {Path(args.secret) / flow.COMMIT_RECEIPT}")
        return 0

    if args.cmd == "song" and args.action == "publish":
        receipt = flow.song_publish(args.song, args.secret, args.server)
        e = receipt["server"]["event"]
        print(f"RELEASE 已接受: event_id={e['event_id']} sequence={e['sequence']}")
        print(f"  C_S={receipt['c_s_hex']}")
        return 0

    if args.cmd == "prove":
        if args.bin_dir:
            prove_mod.set_bin_dir(args.bin_dir)
        summary = prove_mod.prove(args.secret, args.release, args.out)
        print(f"真实证明完成(独立 verifier 通过): {summary['work_dir']}")
        print(f"  C_M={summary['c_m']}")
        print(f"  C_V={summary['c_v']}")
        return 0

    if args.cmd == "proof" and args.action == "publish":
        receipt = flow.proof_publish(args.work, args.secret, args.server)
        e = receipt["server"]["event"]
        print(f"PROOF 已接受(服务端本地 verifier 复验通过): event_id={e['event_id']} sequence={e['sequence']}")
        print(f"  C_V={receipt['c_v_hex']}")
        return 0

    if args.cmd == "server":
        return _server_dispatch(args)

    if args.cmd == "verify":
        from music_zk.verifier.evidence import EvidenceVerifier

        verifier = EvidenceVerifier(
            args.evidence_dir,
            args.server_key,
            verify_bin=args.verify_bin,
            expect_image_id=args.expect_image_id,
        )
        res = verifier.verify()
        print(res.render())
        return 0 if res.overall else 1

    if args.cmd == "reveal-check":
        print(demo.reveal_check(args.midi, args.salt, args.commit_receipt))
        return 0

    if args.cmd == "evidence" and args.action == "export":
        from .evidence import export_evidence

        out = export_evidence(
            args.secret, args.work, args.server, args.song, args.out
        )
        print(f"公开证据包已导出: {out}")
        print("验证: music-zk verify public-evidence/ --server-key <服务端公钥 hex>")
        return 0

    if args.cmd == "demo" and args.action == "tamper":
        print(demo.run_tamper(args.case, args.evidence, args.secret))
        return 0

    print(f"未知子命令: {args.cmd}", file=sys.stderr)
    return 2


def _server_dispatch(args: argparse.Namespace) -> int:
    from .server_cli import server_init, server_run

    data = Path(args.data)
    if args.action == "init":
        server_init(data)
        print(f"服务端密钥已生成: {data}(私钥只存本机;公钥即信任根)")
        return 0
    return server_run(data, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
