use reference_core::{commit_midi, protocol_hash, Journal, PROTOCOL_ID, SALT_LEN};
use risc0_zkvm::guest::env;

/// M0 关系最小闭环(SPEC §6.3 的前两步;Profile 解析与合成在 Phase 2 补齐):
/// 1. 读入输入 `U64BE(len(M)) || M || r`(r 恰 32 字节);
/// 2. 重算 `C_M = CommitMidi(M, r)`;
/// 3. 输出 §6.4 定长 202 字节 journal(`commit_slice` 保证原始字节,无 serde 包装)。
fn main() {
    let input: Vec<u8> = env::read();

    // --- 输入解析(fail-closed)---
    if input.len() < 8 + SALT_LEN {
        panic!("input too short");
    }
    let m_len = u64::from_be_bytes(input[0..8].try_into().unwrap()) as usize;
    if input.len() != 8 + m_len + SALT_LEN {
        panic!("input length mismatch");
    }
    let m = &input[8..8 + m_len];
    let r: [u8; SALT_LEN] = input[8 + m_len..].try_into().unwrap();

    // --- 关系重算 ---
    let c_m = commit_midi(m, &r);

    // --- journal(M0:事件 ID/公钥/C_V 为零占位,结构按 §6.4 冻结)---
    let journal = Journal {
        protocol_hash: protocol_hash(PROTOCOL_ID),
        creator_pubkey: [0u8; 32], // Phase 3 签名事件后填充
        commit_event_id: [0u8; 32], // Phase 3 填充
        release_event_id: [0u8; 32], // Phase 3 填充
        c_m,
        c_v: [0u8; 32], // Phase 2 ReferenceSynth 后填充
    };
    env::commit_slice(&journal.encode());
}
