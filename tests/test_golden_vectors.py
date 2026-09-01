"""golden vectors 三方一致对拍(native == Python;guest 经 prove 另行验证)。

SPEC §17.1:每个 vector 含 MIDI SHA-256、盐、C_M、事件列表、WAV sample 数、
前后样本片段、完整 C_V。Python 侧独立重算,必须与 reference-native 输出逐字节一致。
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from music_zk.verifier.framing import SALT_LEN, commit_midi, commit_reference_wav

GV = Path(__file__).resolve().parents[1] / "protocol" / "golden-vectors"
NAMES = [
    "minimal-onenote",
    "chord4",
    "twinkle4",
    "same-tick-off-on",
    "early-off-attack",
    "max60s",
]


@pytest.mark.parametrize("name", NAMES)
def test_golden_vector(name: str) -> None:
    midi = (GV / "midi" / f"{name}.mid").read_bytes()
    gv = json.loads((GV / f"{name}.json").read_text(encoding="utf-8"))
    wav = (GV / f"{name}.wav").read_bytes()

    # 1) MIDI SHA-256
    assert hashlib.sha256(midi).hexdigest() == gv["midi_sha256"]

    # 2) 盐 + C_M(native == Python)
    salt = bytes.fromhex(gv["salt"])
    assert len(salt) == SALT_LEN
    assert commit_midi(midi, salt).hex() == gv["c_m"]

    # 3) C_V(native == Python,完整 WAV 字节)
    assert commit_reference_wav(wav).hex() == gv["c_v"]

    # 4) Canonical WAV 1 头(SPEC §9.1):44 字节、RIFF/WAVE、PCM、mono、8000 Hz、16-bit
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[36:40] == b"data"
    assert len(wav) == 44 + gv["sample_count"] * 2
    assert struct.unpack("<H", wav[20:22])[0] == 1  # audio_format = PCM
    assert struct.unpack("<H", wav[22:24])[0] == 1  # channels = 1
    assert struct.unpack("<I", wav[24:28])[0] == 8000  # sample rate
    assert struct.unpack("<H", wav[34:36])[0] == 16  # bits per sample

    # 5) sample 数与头尾样本(SPEC §17.1 的"前后样本片段")
    assert gv["sample_count"] == (len(wav) - 44) // 2
    head = struct.unpack(f"<{len(gv['head_samples'])}h", wav[44:44 + 2 * len(gv["head_samples"])])
    assert list(head) == gv["head_samples"]
    tail = struct.unpack(f"<{len(gv['tail_samples'])}h", wav[-2 * len(gv["tail_samples"]):])
    assert list(tail) == gv["tail_samples"]

    # 6) 事件列表非空且 last_note_off_tick 与 sample_count 一致:
    #    sample_count = floor(last_off * 25 / 3) + 960
    assert len(gv["events"]) >= 1
    assert gv["sample_count"] == (gv["last_note_off_tick"] * 25 // 3) + 960


def test_all_vectors_share_salt_and_are_distinct() -> None:
    salts = set()
    midi_hashes = set()
    for name in NAMES:
        gv = json.loads((GV / f"{name}.json").read_text(encoding="utf-8"))
        salts.add(gv["salt"])
        midi_hashes.add(gv["midi_sha256"])
    assert len(salts) == 1  # 固定测试盐(公开素材)
    assert len(midi_hashes) == len(NAMES)  # 6 个不同 MIDI
