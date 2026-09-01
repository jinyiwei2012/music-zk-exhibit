//! M0→M1 关系最小闭环的最终形态(SPEC §6.3 全 5 步;Phase 3 事件 ID/公钥由 host 传入):
//! 1. 读入输入 `U64BE(len(M)) || M || r(32B) || creator_pubkey(32B) || commit_event_id(32B)
//!    || release_event_id(32B) || C_M(32B) || C_V(32B)`;
//! 2. 严格解析 `M` 并验证 MIDI Profile 1(失败即 panic,不产生可接受 journal);
//! 3. 重算 `C_M = CommitMidi(M, r)`,断言等于公共上下文中的 C_M;
//! 4. 以 ReferenceSynth 1 流式合成,`C_V = CommitReferenceWav(头 || PCM 字节)`,断言等于上下文中的 C_V;
//! 5. 输出 §6.4 定长 202 字节 journal(公共字段;`commit_slice` 保证原始字节,无 serde 包装)。

use reference_core::midi::parse_midi;
use reference_core::synth::{render_stream, sample_count, wav_header};
use reference_core::{
    commit_midi, protocol_hash, Journal, REF_WAV_PREFIX, PROTOCOL_ID, SALT_LEN,
};
use risc0_zkvm::guest::env;
use sha2::{Digest, Sha256};

fn main() {
    let input: Vec<u8> = env::read();

    // --- 输入解析(fail-closed)---
    // 布局:U64BE(len(M)) || M || r(32) || pubkey(32) || commit_id(32) || release_id(32) || C_M(32) || C_V(32)
    const CTX_BYTES: usize = 32 * 5;
    if input.len() < 8 + SALT_LEN + CTX_BYTES {
        panic!("input too short");
    }
    let m_len = u64::from_be_bytes(input[0..8].try_into().unwrap()) as usize;
    if input.len() != 8 + m_len + SALT_LEN + CTX_BYTES {
        panic!("input length mismatch");
    }
    let m = &input[8..8 + m_len];
    let r: [u8; SALT_LEN] = input[8 + m_len..8 + m_len + SALT_LEN].try_into().unwrap();
    let creator_pubkey: [u8; 32] =
        input[8 + m_len + 32..8 + m_len + 64].try_into().unwrap();
    let commit_event_id: [u8; 32] =
        input[8 + m_len + 64..8 + m_len + 96].try_into().unwrap();
    let release_event_id: [u8; 32] =
        input[8 + m_len + 96..8 + m_len + 128].try_into().unwrap();
    let ctx_c_m: [u8; 32] = input[8 + m_len + 128..8 + m_len + 160].try_into().unwrap();
    let ctx_c_v: [u8; 32] = input[8 + m_len + 160..8 + m_len + 192].try_into().unwrap();

    // --- 2) MIDI Profile 1(fail-closed;失败即 panic,无 journal)---
    let parsed = parse_midi(m).expect("MIDI Profile 1 检查失败");

    // --- 3) C_M 断言(SPEC §6.3 步骤 2)---
    let c_m = commit_midi(m, &r);
    assert_eq!(c_m, ctx_c_m, "C_M 与公共上下文不一致");

    // --- 4) 流式合成 + C_V 断言(SPEC §6.3 步骤 3-4 / §9.6)---
    let total = sample_count(&parsed);
    let wav_len = 44 + total * 2;
    let mut hasher = Sha256::new();
    hasher.update(REF_WAV_PREFIX);
    hasher.update((wav_len as u64).to_be_bytes());
    hasher.update(wav_header(total as u32));
    // PCM 按 64 字节块喂入,避免每样本一次 update 的 VM 调用开销
    let mut chunk = [0u8; 64];
    let mut n = 0usize;
    render_stream(&parsed, |s| {
        let b = s.to_le_bytes();
        chunk[n * 2] = b[0];
        chunk[n * 2 + 1] = b[1];
        n += 1;
        if n == 32 {
            hasher.update(&chunk);
            n = 0;
        }
    });
    if n > 0 {
        hasher.update(&chunk[..n * 2]);
    }
    let c_v: [u8; 32] = hasher.finalize().into();
    assert_eq!(c_v, ctx_c_v, "C_V 与公共上下文不一致");

    // --- 5) journal(§6.4 定长 202 字节;protocol_hash 由协议常量内部计算)---
    let journal = Journal {
        protocol_hash: protocol_hash(PROTOCOL_ID),
        creator_pubkey,
        commit_event_id,
        release_event_id,
        c_m,
        c_v,
    };
    env::commit_slice(&journal.encode());
}
