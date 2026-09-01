#!/usr/bin/env python3
"""生成 SPEC §17.1 的 6 个 golden vector 示例 MIDI(公开测试素材,非私密)。

输出到 protocol/golden-vectors/midi/<name>.mid。每个文件都是合法 MIDI Profile 1
(Format 0, division 480, Set Tempo 500000 @ tick 0, EOT 结尾)。

向量(SPEC §17.1):
1. minimal-onenote  —— 最短单音:On@0, Off@1
2. chord4           —— 四音和弦:60/64/67/71 On@0, Off@480
3. twinkle4         —— 四句《小星星》(C大调,每音 240 tick)
4. same-tick-off-on —— 同 tick Off/On 边界:Off60 与 On64 同在 tick 240
5. early-off-attack —— Attack(40 ms=320 samples)中提前 Off:On@0, Off@100
6. max60s           —— 最大 60 秒与 release tail:On@0, Off@57600

用法:python scripts/gen-golden-midis.py  (conda env music-zk)
"""

from __future__ import annotations

import struct
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "protocol" / "golden-vectors" / "midi"

TEMPO = bytes([0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20])  # tick 0, 500000 us/quarter


def vlq(v: int) -> bytes:
    out = bytearray([v & 0x7F])
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    out.reverse()
    return bytes(out)


def on(delta: int, note: int, vel: int = 100) -> bytes:
    return vlq(delta) + bytes([0x90, note, vel])


def off(delta: int, note: int) -> bytes:
    return vlq(delta) + bytes([0x80, note, 0x40])


def eot() -> bytes:
    return bytes([0x00, 0xFF, 0x2F, 0x00])


def build(events: bytes) -> bytes:
    mthd = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    mtrk = b"MTrk" + struct.pack(">I", len(events)) + events
    return mthd + mtrk


def minimal_onenote() -> bytes:
    return build(TEMPO + on(0, 60, 100) + off(1, 60) + eot())


def chord4() -> bytes:
    ev = TEMPO
    for n in (60, 64, 67, 71):
        ev += on(0, n, 90)
    for n in (60, 64, 67, 71):
        ev += off(480, n)
    return build(ev + eot())


def twinkle4() -> bytes:
    # 四句:每音 240 tick(0.25 s),句间休止 240 tick
    # 句1:C C G G A A G | 句2:F F E E D D C
    # 句3:G G F F E E D | 句4:G G F F E E D
    notes = [
        60, 60, 67, 67, 69, 69, 67,
        65, 65, 64, 64, 62, 62, 60,
        67, 67, 65, 65, 64, 64, 62,
        67, 67, 65, 65, 64, 64, 62,
    ]
    ev = TEMPO
    prev: int | None = None
    for n in notes:
        if prev is None:
            ev += on(0, n, 96)
        else:
            ev += off(240, prev) + on(0, n, 96)
        prev = n
    ev += off(240, notes[-1]) + eot()
    return build(ev)


def same_tick_off_on() -> bytes:
    # Off60 与 On64 同在 tick 240(同 sample 按文件顺序:先 Off 后 On)
    ev = TEMPO + on(0, 60, 100) + off(240, 60) + on(0, 64, 100) + off(240, 64)
    return build(ev + eot())


def early_off_attack() -> bytes:
    # Attack 40 ms = 320 samples;Off@100 tick = sample 833(attack 内)
    ev = TEMPO + on(0, 72, 100) + off(100, 72)
    return build(ev + eot())


def max60s() -> bytes:
    # 最后 Note Off tick = 57600(60 s),release tail 120 ms
    ev = TEMPO + on(0, 60, 100) + off(57600, 60)
    return build(ev + eot())


VECTORS = {
    "minimal-onenote": minimal_onenote,
    "chord4": chord4,
    "twinkle4": twinkle4,
    "same-tick-off-on": same_tick_off_on,
    "early-off-attack": early_off_attack,
    "max60s": max60s,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in VECTORS.items():
        data = fn()
        p = OUT / f"{name}.mid"
        p.write_bytes(data)
        print(f"{p.name:24s} {len(data):5d} B  sha256={__import__('hashlib').sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
