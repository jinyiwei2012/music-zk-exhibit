//! reference-core —— MIDI 语义 + ReferenceSynth 合成 + hash framing 的唯一实现。
//!
//! native 与 zkVM guest 共用,协议行为以本 crate 为准(SPEC §4)。
//! Phase 0 仅为占位;Phase 1 起实现:
//!   - SPEC §3.2 三个 framing(CommitMidi / CommitReferenceWav / CommitSong)
//!   - SPEC §3.4 MIDI Profile 1 解析器(fail-closed)
//!   - SPEC §3.5 ReferenceSynth 1(纯整数合成)
