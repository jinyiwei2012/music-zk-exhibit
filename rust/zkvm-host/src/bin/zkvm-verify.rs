//! zkvm-verify:独立 verifier 进程——复验 receipt/Image ID、journal 结构、C_M 重算。
//! 不信任 prover 写出的 method_id.txt;Image ID 由本进程内嵌的 guest ELF 映像得出(可 --expect-image-id 覆盖作负向测试)。
//!
//! 用法:zkvm-verify [--expect-image-id <hex32>] [--expect-c-m <hex32>] [--allow-dev-receipt]
//! 选项:
//!   --expect-image-id <hex>:用外部 Image ID 复验(错 ID → 复验失败,负向测试)
//!   --expect-c-m <hex>    :要求 journal.C_M 等于给定承诺(演示"绑定 t0 已提交承诺";改 M/错盐 → 失败)
//!   --allow-dev-receipt   :跳过 dev-mode 收据结构检查(负向测试观察用,默认拒绝)
use reference_core::{commit_midi, protocol_hash, Journal, PROTOCOL_ID, SALT_LEN};
use risc0_zkvm::{InnerReceipt, Receipt};
use std::fs;
use zkvm_methods::ZKVM_GUEST_ID;

/// 从 hex 解析 Image ID([u32; 8],按大端字节序)。
fn parse_image_id_hex(s: &str) -> [u32; 8] {
    let bytes = hex::decode(s).unwrap_or_else(|e| panic!("image id hex 非法: {e}"));
    assert_eq!(bytes.len(), 32, "Image ID 必须 32 字节");
    let mut id = [0u32; 8];
    for (k, chunk) in bytes.chunks_exact(4).enumerate() {
        id[k] = u32::from_be_bytes(chunk.try_into().unwrap());
    }
    id
}

/// dev-mode 收据检测(红线 2):dev 收据是 `InnerReceipt::Fake`,无密码学完整性,
/// 只在 dev mode 下自洽。生产 verifier 一律拒绝。
fn receipt_is_dev_receipt(r: &Receipt) -> bool {
    matches!(r.inner, InnerReceipt::Fake(_))
}

fn main() {
    let mut expect_id: [u32; 8] = ZKVM_GUEST_ID;
    let mut expect_c_m: Option<[u8; 32]> = None;
    let mut allow_dev_receipt = false;
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--expect-image-id" => {
                expect_id = parse_image_id_hex(&args[i + 1]);
                i += 2;
            }
            "--expect-c-m" => {
                let hexs = &args[i + 1];
                let bytes = hex::decode(hexs).unwrap_or_else(|e| panic!("C_M hex 非法: {e}"));
                assert_eq!(bytes.len(), 32, "C_M 必须 32 字节");
                expect_c_m = Some(bytes.try_into().unwrap());
                i += 2;
            }
            "--allow-dev-receipt" => {
                allow_dev_receipt = true;
                i += 1;
            }
            other => {
                eprintln!("未知参数: {other}");
                std::process::exit(2);
            }
        }
    }

    let receipt: Receipt =
        bincode::deserialize(&fs::read("receipt.bin").expect("缺少 receipt.bin")).unwrap();

    // 1) 密码学复验(receipt vs Image ID)——负向:错 Image ID 在此失败
    receipt.verify(expect_id).unwrap();

    // 2) dev-mode 收据拒绝(红线 2)——负向:dev 收据在此失败
    if !allow_dev_receipt {
        assert!(!receipt_is_dev_receipt(&receipt), "拒绝 dev-mode 收据:不是密码学证明");
    }

    // 3) journal 结构:202B + magic + version + protocol_hash
    let journal_bytes = &receipt.journal.bytes;
    assert_eq!(journal_bytes.len(), 202, "journal 必须 202 字节");
    let journal = Journal::decode(journal_bytes).unwrap_or_else(|e| panic!("journal 结构无效: {e:?}"));
    assert_eq!(
        journal.protocol_hash,
        protocol_hash(PROTOCOL_ID),
        "protocol_hash 与 protocol_id 不匹配"
    );

    // 4) C_M 重算对拍——负向:改 M 一字节 / 错盐在此失败
    let midi = fs::read("midi.bin").expect("缺少 midi.bin");
    let salt = fs::read("salt.bin").expect("缺少 salt.bin");
    assert_eq!(salt.len(), SALT_LEN);
    let salt_arr: [u8; SALT_LEN] = salt.try_into().unwrap();
    let expect_c_m_local = commit_midi(&midi, &salt_arr);
    assert_eq!(
        journal.c_m, expect_c_m_local,
        "C_M 与本地 (M,r) 重算不一致"
    );

    // 5) 绑定先前提交的承诺(负向:用修改后的 M/salt 证明 → 与 t0 承诺不符 → 失败)
    if let Some(committed) = expect_c_m {
        assert_eq!(
            journal.c_m, committed,
            "C_M 与 t0 已提交承诺不一致(修改 M 或盐?)"
        );
    }

    println!("VERIFY OK  receipt/Image ID ✓  journal 202B+magic+version+protocol_hash ✓  C_M ✓");
}
