#!/usr/bin/env python3
"""把真实 DAW 工程 MIDI(通常是 Format 1 多轨)转换为满足 Profile 1 的测试负载。

用途:用真实音符密度做大数据量证明测试。转换规则(确定性):
- 提取所有 track 的 NoteOn/NoteOff(channel 0 之外强制归 0),丢弃全部 meta/SysEx;
- 合并到单 track,按 (tick, 原顺序) 排序;同 tick 先 Off 后 On(避免重复 On 冲突);
- 若 division ≠ 480,按比例缩放 tick(round);
- 强制 Set Tempo 500000 @ tick 0,120 BPM;
- 施加 Profile 1 上限:NoteOn ≤ 256、最后 NoteOff tick ≤ 57600(60 s)、同音高不重叠、
  同时活动 ≤ 4(丢弃超出部分,从后往前);音符数不足时按文件顺序保留前 256 个 On。

输出到指定路径。转换产物是测试素材,禁止入库(红线 1)。

用法:python scripts/conv-midi-to-profile1.py <input.mid> <output.mid>
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

DIVISION = 480
TEMPO_US = 500_000


def vlq(v: int) -> bytes:
    out = bytearray([v & 0x7F])
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    out.reverse()
    return bytes(out)


def read_vlq(d: bytes, p: int) -> tuple[int, int]:
    v = 0
    while True:
        b = d[p]
        p += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, p


def parse_events(d: bytes) -> tuple[list[tuple[int, int, int, int]], int]:
    """返回 (事件列表 [(tick, kind, note, order)], division)。kind: 1=on, 0=off。"""
    p = 0
    assert d[p:p + 4] == b"MThd"
    hlen = struct.unpack(">I", d[p + 4:p + 8])[0]
    format_, ntrks, div = struct.unpack(">HHH", d[p + 8:p + 14])
    p += 8 + hlen
    tracks: list[list[tuple[int, int, int, int]]] = []
    order = 0
    for _ in range(ntrks):
        assert d[p:p + 4] == b"MTrk"
        tlen = struct.unpack(">I", d[p + 4:p + 8])[0]
        t = p + 8
        end = t + tlen
        tick = 0
        ev: list[tuple[int, int, int, int]] = []
        running: int | None = None
        while t < end:
            dt, t = read_vlq(d, t)
            tick += dt
            b = d[t]
            if b == 0xFF:
                meta_type = d[t + 1]
                ml, t = read_vlq(d, t + 2)
                t += ml
                running = None
                continue
            if b == 0xF0 or b == 0xF7:
                sl, t = read_vlq(d, t + 1)
                t += sl
                running = None
                continue
            if b >= 0x80:
                running = b
                t += 1
            else:
                assert running is not None, "running status 前置缺失"
            status = running
            if status & 0xF0 == 0x90:
                note, vel = d[t], d[t + 1]
                t += 2
                kind = 1 if vel > 0 else 0
                ev.append((tick, kind, note, order))
                order += 1
            elif status & 0xF0 == 0x80:
                note = d[t]
                t += 2
                ev.append((tick, 0, note, order))
                order += 1
            else:
                # CC/弯音/Program 等:跳过 1-2 个数据字节(粗放,测试用途)
                t += 1 if status & 0xF0 in (0xC0, 0xD0) else 2
        tracks.append(ev)
        p = end
    events = [e for tr in tracks for e in tr]
    return events, div


def build_profile1(events: list[tuple[int, int, int, int]], div: int) -> tuple[bytes, int]:
    """返回 (MIDI 字节, 输出 NoteOn 数)。"""
    scale = DIVISION / div
    scaled: list[tuple[int, int, int, int]] = []
    for tick, kind, note, order in events:
        st = round(tick * scale)
        if st > 57600:
            continue
        if not (21 <= note <= 108):
            continue
        scaled.append((st, kind, note, order))
    # 按 tick + 原顺序排序;同 tick Off(0) 先于 On(1)
    scaled.sort(key=lambda e: (e[0], e[1], e[3]))
    # Profile 1 约束:同音高不重叠、同时活动 ≤4、NoteOn ≤256
    active: dict[int, int] = {}
    cur_active = 0
    ons = 0
    out: list[tuple[int, int, int, int]] = []
    for st, kind, note, order in scaled:
        if kind == 1:
            if note in active:
                continue  # 未 Off 再 On → 丢弃(测试负载,不做 fail 测试)
            if cur_active >= 4:
                continue  # 超过 4 声部 → 丢弃
            if ons >= 256:
                continue
            active[note] = st
            cur_active += 1
            ons += 1
            out.append((st, 1, note, order))
        else:
            if note not in active:
                continue
            if st <= active[note]:
                continue
            active.pop(note)
            cur_active -= 1
            out.append((st, 0, note, order))
    assert not active, "转换后仍有悬挂音符(不应发生)"
    # 组装 Format 0
    ev_bytes = bytearray()
    prev = 0
    for st, kind, note, _ in out:
        ev_bytes += vlq(st - prev)
        prev = st
        if kind:
            ev_bytes += bytes([0x90, note, 100])
        else:
            ev_bytes += bytes([0x80, note, 0x40])
    ev_bytes = bytearray([0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20]) + ev_bytes
    ev_bytes += bytes([0x00, 0xFF, 0x2F, 0x00])
    mid = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, DIVISION)
           + b"MTrk" + struct.pack(">I", len(ev_bytes)) + bytes(ev_bytes))
    return mid, ons


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    data = src.read_bytes()
    events, div = parse_events(data)
    mid, ons = build_profile1(events, div)
    dst.write_bytes(mid)
    import hashlib
    src_ons = sum(1 for e in events if e[1] == 1)
    print(f"{src.name}: {len(data)}B (format-div={div}, note_ons={src_ons})")
    print(f"  -> {dst.name}: {len(mid)}B, profile1 note_ons={ons}, sha256={hashlib.sha256(mid).hexdigest()}")


if __name__ == "__main__":
    main()
