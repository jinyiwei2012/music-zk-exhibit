//! zkvm-prove:真实证明 hello-guest(禁 dev mode),序列化 receipt/journal/input 到当前目录。
use risc0_zkvm::{default_prover, ExecutorEnv};
use zkvm_methods::{ZKVM_GUEST_ELF, ZKVM_GUEST_ID};

fn main() {
    // 红线 2:只用真实证明——dev mode 必须禁用
    #[allow(deprecated)] // is_dev_mode() 仍可用;3.x 中 deprecated,暂以显式断言兜底
    let dev_mode = risc0_zkvm::is_dev_mode();
    assert!(!dev_mode, "RISC0_DEV_MODE 必须禁用:dev-mode 收据不是密码学证明");

    let x = std::env::args().nth(1).unwrap_or_else(|| "hello".into());
    let input: Vec<u8> = x.into_bytes();

    let env = ExecutorEnv::builder()
        .write(&input)
        .unwrap()
        .build()
        .unwrap();

    let prover = default_prover();
    let prove_info = prover.prove(env, ZKVM_GUEST_ELF).unwrap();
    let receipt = prove_info.receipt;

    let journal: [u8; 32] = receipt.journal.decode().unwrap();
    std::fs::write("receipt.bin", bincode::serialize(&receipt).unwrap()).unwrap();
    std::fs::write("input.bin", &input).unwrap();
    std::fs::write("journal.bin", &journal).unwrap();
    std::fs::write(
        "method_id.txt",
        hex::encode(ZKVM_GUEST_ID.iter().flat_map(|w| w.to_le_bytes()).collect::<Vec<u8>>()),
    )
    .unwrap();

    // 自验一次(prove 尾部亦会校验,这里显式再验)
    receipt.verify(ZKVM_GUEST_ID).unwrap();

    println!(
        "PROVE OK  journal = SHA256({:?}) = {}",
        String::from_utf8_lossy(&input),
        hex::encode(journal)
    );
    println!(
        "receipt.bin = {} bytes",
        std::fs::metadata("receipt.bin").unwrap().len()
    );
}
