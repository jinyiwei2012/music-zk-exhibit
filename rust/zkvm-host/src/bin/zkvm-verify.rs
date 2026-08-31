//! zkvm-verify:独立 verifier 进程——从 receipt.bin 反序列化收据,复验方法 ID 与 journal 内容。
//! 不信任 prover 写出的 method_id.txt;方法 ID 由本进程内嵌的 guest ELF 映像得出。
use risc0_zkvm::Receipt;
use sha2::{Digest, Sha256};
use zkvm_methods::ZKVM_GUEST_ID;

fn main() {
    let receipt: Receipt =
        bincode::deserialize(&std::fs::read("receipt.bin").expect("缺少 receipt.bin")).unwrap();

    // 1) 复验 receipt 与 Image ID
    receipt.verify(ZKVM_GUEST_ID).unwrap();

    // 2) 复验 journal 内容 == SHA256(input.bin)
    let journal: [u8; 32] = receipt.journal.decode().unwrap();
    let input = std::fs::read("input.bin").expect("缺少 input.bin");
    let expected: [u8; 32] = Sha256::digest(&input).into();
    assert_eq!(
        journal, expected,
        "journal 必须等于 SHA256(input)"
    );

    println!(
        "VERIFY OK  journal == SHA256(input) == {}",
        hex::encode(journal)
    );
}
