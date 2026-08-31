#!/usr/bin/env python3
"""冻结 Phase 2 的两个协议权威值(SPEC §9.3,用户已确认方案):

1. protocol/wavetable-v1.bin —— 2048 个 LE i16 波表:
   s[k] = A * Σ_{h=1..4} sin(2π·h·k/2048) / h,归一化到满幅(A 由峰值确定,无直流)。
   生成规则(确定性,冻结后只认字节与 SHA-256):
   - v[k] = Σ_{h=1..4} sin(2π·h·k/2048) / h,float64 计算(误差 ~1e-15,远小于舍入边界);
   - M = max(|v[k]|),s[k] = round(32767 * v[k] / M),round 采用 Python 的 banker's rounding(half-to-even);
   - 检查 s[k] ∈ [-32768, 32767],且 32767 与 -32767..-32768 至少各出现一次(确认满幅)。
2. rust/reference-core/src/phase_steps.rs —— note 21..108 的 u32 phase_step(索引 note-21):
   phase_step(n) = floor(freq(n) * 2^32 / 8000),freq(n) = 440 * 2^((n-69)/12)
   - 先用 Decimal(50 位)初算,再以纯整数 12 次方验证,确保 floor 无误差:
     令 lo = phase_step·8000/2^32,hi = (phase_step+1)·8000/2^32,
     要求 lo ≤ freq < hi ⇔ lo^12 ≤ 440^12·2^(n-69) < hi^12(用整数比较,无浮点)。
   输出:protocol/wavetable-v1.bin、rust/reference-core/src/phase_steps.rs,
   并打印 wavetable SHA-256 与 phase_step 表(供 protocol/v1.json 冻结)。

用法:python scripts/gen-wavetable.py  (在 conda env music-zk 内执行)
"""

from __future__ import annotations

import hashlib
import math
import struct
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAVETABLE_PATH = ROOT / "protocol" / "wavetable-v1.bin"
PHASE_STEPS_RS = ROOT / "rust" / "reference-core" / "src" / "phase_steps.rs"

TABLE_LEN = 2048
NOTE_MIN, NOTE_MAX = 21, 108
N_NOTES = NOTE_MAX - NOTE_MIN + 1  # 88
SAMPLE_RATE = 8000
A4 = 440
A4_MIDI = 69


def gen_wavetable() -> list[int]:
    """s[k] = round(32767 * v[k] / M),v[k] = Σ_{h=1..4} sin(2πhk/2048)/h。"""
    v: list[float] = [0.0] * TABLE_LEN
    for k in range(TABLE_LEN):
        acc = 0.0
        for h in range(1, 5):
            acc += math.sin(2.0 * math.pi * h * k / TABLE_LEN) / h
        v[k] = acc
    m = max(abs(x) for x in v)
    scale = 32767.0 / m
    table = [round(x * scale) for x in v]
    for s in table:
        assert -32768 <= s <= 32767, f"溢出:{s}"
    assert 32767 in table, "未达到正满幅"
    assert -32768 <= min(table) <= -32767, "未达到负满幅附近"
    return table


def phase_step_decimal(n: int) -> int:
    """phase_step = floor(440·2^((n-69)/12)·2^32/8000),Decimal 初算。"""
    freq = Decimal(A4) * (Decimal(2) ** (Decimal(n - A4_MIDI) / Decimal(12)))
    raw = freq * (Decimal(2) ** 32) / Decimal(SAMPLE_RATE)
    return int(raw.to_integral_value(rounding=ROUND_FLOOR))


def verify_phase_step_exact(n: int, step: int) -> None:
    """纯整数验证 floor:freq^12 = 440^12·2^(n-69) 落在 [lo^12, hi^12)。"""
    # lo = step·8000/2^32,hi = (step+1)·8000/2^32
    # 验证 lo ≤ freq < hi ⇔ lo^12 ≤ 440^12·2^(n-69) < hi^12
    f12 = 440**12 * (1 << (n - A4_MIDI)) if n >= A4_MIDI else 440**12 >> (A4_MIDI - n)
    two32 = 1 << 32
    lo_num = step * SAMPLE_RATE
    hi_num = (step + 1) * SAMPLE_RATE
    # lo^12 ≤ f12 ⇔ lo_num^12 ≤ f12 * two32^12
    if lo_num**12 > f12 * (two32**12):
        raise AssertionError(f"note {n}: step {step} 低于下界")
    # f12 < hi^12 ⇔ f12 * two32^12 < hi_num^12
    if f12 * (two32**12) >= hi_num**12:
        raise AssertionError(f"note {n}: step {step} 高于上界")


def gen_phase_steps() -> list[int]:
    steps: list[int] = []
    for n in range(NOTE_MIN, NOTE_MAX + 1):
        step = phase_step_decimal(n)
        verify_phase_step_exact(n, step)
        steps.append(step)
    return steps


def write_phase_steps_rs(steps: list[int]) -> None:
    lines = [
        "//! 冻结的 u32 phase_step 表(SPEC §9.3;生成:scripts/gen-wavetable.py)。",
        "//!",
        "//! phase_step(n) = floor(freq(n)·2^32/8000),freq(n) = 440·2^((n-69)/12)。",
        "//! 索引 = note - 21(21..=108 共 88 项)。值经纯整数 12 次方验证,无浮点误差。",
        "//! 冻结后不得改动:任何变化必须产生新 protocol_id(SPEC §5)。",
        "",
        "/// note 21..=108 的 u32 phase_step,索引为 note-21。",
        "pub const PHASE_STEP: [u32; 88] = [",
    ]
    for i in range(0, N_NOTES, 4):
        row = ", ".join(f"0x{steps[j]:08X}" for j in range(i, min(i + 4, N_NOTES)))
        lines.append(f"    {row},")
    lines.append("];")
    PHASE_STEPS_RS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    table = gen_wavetable()
    WAVETABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WAVETABLE_PATH.write_bytes(struct.pack(f"<{TABLE_LEN}h", *table))
    digest = hashlib.sha256(WAVETABLE_PATH.read_bytes()).hexdigest()

    steps = gen_phase_steps()
    write_phase_steps_rs(steps)

    print(f"wavetable-v1.bin = {WAVETABLE_PATH}({WAVETABLE_PATH.stat().st_size} B)")
    print(f"wavetable SHA-256 = {digest}")
    print(f"phase_steps.rs = {PHASE_STEPS_RS}({N_NOTES} 项)")
    print("phase_step(note: step):")
    for n, s in zip(range(NOTE_MIN, NOTE_MAX + 1), steps):
        print(f"  {n}: {s}")


if __name__ == "__main__":
    main()
