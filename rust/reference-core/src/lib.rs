//! reference-core —— MIDI 语义 + ReferenceSynth 合成 + hash framing 的唯一实现。
//!
//! native 与 zkVM guest 共用,协议行为以本 crate 为准(SPEC §4)。
//! Phase 1:SPEC §7 三个 framing + §6.4 journal 编解码。
//! Phase 2:MIDI Profile 1 解析(§8)、ReferenceSynth 1(§9)。

pub mod midi;
mod phase_steps;
pub mod synth;

#[cfg(test)]
mod jcs_vectors;

pub use midi::{parse_midi, NoteEvent, ParsedMidi, ParseError};
pub use synth::{render, render_stream, sample_count, SynthParams, SAMPLE_RATE};

use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// 协议常量(根 AGENTS.md §3,直接抄,一个字符都不许改)
// ---------------------------------------------------------------------------

/// protocol_id(SPEC §5 / AGENTS.md §3.1)。
/// statement-1 = Phase 1 M0 guest(MIDI 重算 C_M);statement-2 = Phase 2 完整 guest
/// (MIDI Profile 1 + ReferenceSynth 1,Image ID bee80805...,见 protocol/v1.json)。
pub const PROTOCOL_ID: &str = "music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2";

/// 域分离前缀(SPEC §7;ASCII 中的 `\0` 是真实 0x00 字节)。
pub const MIDI_COMMIT_PREFIX: &[u8] = b"MUSIC-ZK\x00MIDI-COMMIT\x00V1\x00";
pub const REF_WAV_PREFIX: &[u8] = b"MUSIC-ZK\x00REF-WAV\x00V1\x00";
pub const SONG_PREFIX: &[u8] = b"MUSIC-ZK\x00SONG\x00V1\x00";

/// 盐长度(SPEC §6.1:恰好 32 字节)。
pub const SALT_LEN: usize = 32;

// ---------------------------------------------------------------------------
// 哈希 framing(SPEC §7)
// ---------------------------------------------------------------------------

/// `CommitMidi(M, r) = SHA256(prefix || U64BE(len(M)) || M || r)`;r 恰 32 字节。
pub fn commit_midi(m: &[u8], r: &[u8; SALT_LEN]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(MIDI_COMMIT_PREFIX);
    h.update((m.len() as u64).to_be_bytes());
    h.update(m);
    h.update(r);
    h.finalize().into()
}

/// `CommitReferenceWav(V) = SHA256(prefix || U64BE(len(V)) || V)`。
pub fn commit_reference_wav(v: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(REF_WAV_PREFIX);
    h.update((v.len() as u64).to_be_bytes());
    h.update(v);
    h.finalize().into()
}

/// `CommitSong(S) = SHA256(prefix || U64BE(len(S)) || S)`。
pub fn commit_song(s: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(SONG_PREFIX);
    h.update((s.len() as u64).to_be_bytes());
    h.update(s);
    h.finalize().into()
}

/// `protocol_hash = SHA256(UTF8(protocol_id))`(journal 字段)。
pub fn protocol_hash(protocol_id: &str) -> [u8; 32] {
    Sha256::digest(protocol_id.as_bytes()).into()
}

// ---------------------------------------------------------------------------
// Journal 编码(SPEC §6.4;总长固定 202 字节)
// ---------------------------------------------------------------------------

/// journal 总长(字节)。
pub const JOURNAL_LEN: usize = 202;
/// magic = "MZKJNL01"。
pub const JOURNAL_MAGIC: &[u8; 8] = b"MZKJNL01";
/// statement_version = 1(big-endian)。
pub const STATEMENT_VERSION: u16 = 1;

/// Journal 结构错误。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JournalError {
    /// 长度不为 202。
    BadLength(usize),
    /// magic 不匹配。
    BadMagic,
    /// statement_version 未知。
    BadVersion(u16),
}

/// §6.4 journal 字段。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Journal {
    pub protocol_hash: [u8; 32],
    pub creator_pubkey: [u8; 32],
    pub commit_event_id: [u8; 32],
    pub release_event_id: [u8; 32],
    pub c_m: [u8; 32],
    pub c_v: [u8; 32],
}

impl Journal {
    /// 编码为定长 202 字节(拒绝任何变体;M0 未覆盖字段以全零占位,结构不变)。
    pub fn encode(&self) -> [u8; JOURNAL_LEN] {
        let mut b = [0u8; JOURNAL_LEN];
        b[0..8].copy_from_slice(JOURNAL_MAGIC);
        b[8..10].copy_from_slice(&STATEMENT_VERSION.to_be_bytes());
        b[10..42].copy_from_slice(&self.protocol_hash);
        b[42..74].copy_from_slice(&self.creator_pubkey);
        b[74..106].copy_from_slice(&self.commit_event_id);
        b[106..138].copy_from_slice(&self.release_event_id);
        b[138..170].copy_from_slice(&self.c_m);
        b[170..202].copy_from_slice(&self.c_v);
        b
    }

    /// 解码;验证器 MUST 拒绝尾随字节、未知版本、字段长度不符。
    pub fn decode(b: &[u8]) -> Result<Journal, JournalError> {
        if b.len() != JOURNAL_LEN {
            return Err(JournalError::BadLength(b.len()));
        }
        if &b[0..8] != JOURNAL_MAGIC {
            return Err(JournalError::BadMagic);
        }
        let version = u16::from_be_bytes([b[8], b[9]]);
        if version != STATEMENT_VERSION {
            return Err(JournalError::BadVersion(version));
        }
        let f = |r: std::ops::Range<usize>| -> [u8; 32] { b[r].try_into().unwrap() };
        Ok(Journal {
            protocol_hash: f(10..42),
            creator_pubkey: f(42..74),
            commit_event_id: f(74..106),
            release_event_id: f(106..138),
            c_m: f(138..170),
            c_v: f(170..202),
        })
    }
}

// ---------------------------------------------------------------------------
// 测试:与 Python hashlib 对拍的已知向量(防实现自洽性误差)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn hex32(s: &str) -> [u8; 32] {
        let v = hex::decode(s).unwrap();
        v.try_into().unwrap()
    }

    #[test]
    fn commit_midi_known_vector() {
        // 由 Python hashlib 独立计算(b"midi-bytes\x01\x02", [0xAB;32])
        let m: &[u8] = b"midi-bytes\x01\x02";
        let r = [0xABu8; 32];
        let expect = hex32("201cab1270165ec9578590c6d1342dccf6a0203a792f08de45112f651dbe4b83");
        assert_eq!(commit_midi(m, &r), expect);
    }

    #[test]
    fn commit_midi_empty_known_vector() {
        // 空 MIDI + [0xAB;32]
        let r = [0xABu8; 32];
        let expect = hex32("f8408866541352f6ee7740b9c2f459726c7b9d77bac90547a7e05ef3d22ce1c4");
        assert_eq!(commit_midi(b"", &r), expect);
    }

    #[test]
    fn commit_reference_wav_known_vector() {
        // b"\x00\x01\x02\x03"
        let v: &[u8] = b"\x00\x01\x02\x03";
        let expect = hex32("cf8747a4eb32214c65437841b5335d7a92fb5270defeeefd1072f9ca0ae2ad76");
        assert_eq!(commit_reference_wav(v), expect);
    }

    #[test]
    fn commit_song_known_vector() {
        // b"song-bytes"
        let s: &[u8] = b"song-bytes";
        let expect = hex32("0a808823a6eeb007261732dd468b052e4a99af2694f00b20dfcfebee169fd481");
        assert_eq!(commit_song(s), expect);
    }

    #[test]
    fn protocol_hash_known_vector() {
        let expect = hex32("ecbd2763a2307149207dc579579458956dc6ecad8237f9d73301bab7ac0c6da5");
        assert_eq!(protocol_hash(PROTOCOL_ID), expect);
    }

    #[test]
    fn framing_is_not_plain_hash() {
        // 域分离检查:CommitMidi 不等于 SHA256(M||r)
        let m: &[u8] = b"plain";
        let r = [1u8; 32];
        let plain: [u8; 32] = Sha256::digest(&[m, &r].concat()).into();
        assert_ne!(commit_midi(m, &r), plain);
    }

    #[test]
    fn framing_length_prefix_distinguishes() {
        // 长度前缀使不同切分的拼接产生不同承诺
        let a = commit_midi(b"ab", &[2u8; 32]);
        let b = commit_midi(b"a", &[2u8; 32]);
        assert_ne!(a, b);
    }

    #[test]
    fn journal_roundtrip() {
        let j = Journal {
            protocol_hash: [1u8; 32],
            creator_pubkey: [2u8; 32],
            commit_event_id: [3u8; 32],
            release_event_id: [4u8; 32],
            c_m: [5u8; 32],
            c_v: [6u8; 32],
        };
        let enc = j.encode();
        assert_eq!(enc.len(), JOURNAL_LEN);
        assert_eq!(Journal::decode(&enc).unwrap(), j);
    }

    #[test]
    fn journal_rejects_trailing_bytes() {
        let j = Journal {
            protocol_hash: [0u8; 32],
            creator_pubkey: [0u8; 32],
            commit_event_id: [0u8; 32],
            release_event_id: [0u8; 32],
            c_m: [0u8; 32],
            c_v: [0u8; 32],
        };
        let mut enc = j.encode().to_vec();
        enc.push(0u8); // 尾随字节
        assert_eq!(Journal::decode(&enc), Err(JournalError::BadLength(203)));
    }

    #[test]
    fn journal_rejects_bad_version_and_magic() {
        let j = Journal {
            protocol_hash: [0u8; 32],
            creator_pubkey: [0u8; 32],
            commit_event_id: [0u8; 32],
            release_event_id: [0u8; 32],
            c_m: [0u8; 32],
            c_v: [0u8; 32],
        };
        let mut enc = j.encode();
        enc[9] = 2; // version 1 -> 2
        assert_eq!(Journal::decode(&enc), Err(JournalError::BadVersion(2)));

        let mut enc2 = j.encode();
        enc2[0] = b'X';
        assert_eq!(Journal::decode(&enc2), Err(JournalError::BadMagic));
    }

    #[test]
    fn journal_offsets() {
        // 逐字段偏移检查(SPEC §6.4 表格)
        let mut j = Journal {
            protocol_hash: [0u8; 32],
            creator_pubkey: [0u8; 32],
            commit_event_id: [0u8; 32],
            release_event_id: [0u8; 32],
            c_m: [0u8; 32],
            c_v: [0u8; 32],
        };
        j.protocol_hash[0] = 0xA0;
        j.c_m[0] = 0xB1;
        let enc = j.encode();
        assert_eq!(&enc[10..11], &[0xA0]);
        assert_eq!(&enc[138..139], &[0xB1]);
        assert_eq!(&enc[0..8], b"MZKJNL01");
        assert_eq!(&enc[8..10], &[0, 1]);
    }
}
