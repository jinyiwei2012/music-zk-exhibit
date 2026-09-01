#!/usr/bin/env python3
"""生成 SPEC §18 性能基准负载 B1(15s/4v)、B2(30s/4v)、B3(60s/4v)。

4 个 voice 长音(同时活动 = 4,测合成器真实负载):
  - B1: 4 音 On@tick0,Off@7200(15 s,480 PPQ)
  - B2: 4 音 On@tick0,Off@14400(30 s)
  - B3: 4 音 On@tick0,Off@28800(60 s)
全部符合 MIDI Profile 1(fail-closed):Format 0、单 MTrk、division 480、
Set Tempo 500000 @ tick 0、Note On/Off 匹配、tick 单调、EOT 最后。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "protocol" / "bench-midis"
OUT.mkdir(parents=True, exist_ok=True)

DIVISION = 480
TEMPO = b"\x00\xff\x51\x03\x07\xa1\x20"  # Set Tempo 500000 @ delta 0


def vlq(v: int) -> bytes:
    out = bytearray([v & 0x7F])
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    out.reverse()
    return bytes(out)


def on(delta: int, note: int, vel: int = 96) -> bytes:
    return vlq(delta) + bytes([0x90, note, vel])


def off(delta: int, note: int) -> bytes:
    return vlq(delta) + bytes([0x80, note, 0x40])


def eot(delta: int) -> bytes:
    return vlq(delta) + bytes([0xFF, 0x2F, 0x00])


def build(events: bytes) -> bytes:
    # Format 0, ntrks=1, division=480
    header = b"MThd" + (6).to_bytes(4, "big") + b"\x00\x00" + b"\x00\x01" + DIVISION.to_bytes(2, "big")
    track = b"MTrk" + len(events).to_bytes(4, "big") + events
    return header + track


def four_voice_drone(duration_tick: int) -> bytes:
    # 4 个 voice:低音区 45/57/64/69(C3/A3/E4/A4 邻接,note range 21..108 内,
    # 不重叠音高避免 "same pitch 未 Off 不得再 On" 冲突)
    notes = [45, 57, 64, 69]
    ev = TEMPO
    for n in notes:
        ev += on(0, n, 96)
    # 第一个 Off 用 delta=duration_tick,其余同 tick 用 delta=0(同 sample 事件
    # 按文件顺序先生效,SPEC §9.2)
    for i, n in enumerate(notes):
        ev += off(duration_tick if i == 0 else 0, n)
    ev += eot(0)
    return build(ev)


def main() -> None:
    # tempo 500000 us/quarter → 1 quarter = 0.5 s;480 PPQ → 1 s = 960 tick
    # B1=15s→14400 tick,B2=30s→28800 tick,B3=60s→57600 tick(=Profile 1 上限)
    cases = {
        "b1-15s-4v": 15 * 960,      # 14400 tick = 15 s
        "b2-30s-4v": 30 * 960,      # 28800 tick = 30 s
        "b3-60s-4v": 60 * 960,      # 57600 tick = 60 s(Profile 1 上限)
    }
    for name, dur in cases.items():
        data = four_voice_drone(dur)
        p = OUT / f"{name}.mid"
        p.write_bytes(data)
        print(f"{p.name:16s} {len(data):5d} B  dur={dur//960:3d}s  sha256={hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
