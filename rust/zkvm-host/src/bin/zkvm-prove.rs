//! zkvm-prove:M0 真实证明(禁 dev mode)——读 midi/salt 文件,输出 receipt/journal 到当前目录。
//! 用法:zkvm-prove [--allow-dev-mode] <midi.bin> <salt.bin>
//!   --allow-dev-mode:跳过 dev-mode 拒绝断言,配合 RISC0_DEV_MODE=1 生成 fake receipt。
//!   仅用于负向测试素材(红线 2);该收据不是密码学证明。
use reference_core::Journal;
use risc0_zkvm::{default_prover, ExecutorEnv};
use std::fs;
use zkvm_methods::{ZKVM_GUEST_ELF, ZKVM_GUEST_ID};

fn main() {
    let args: Vec<String> = std::env::args().collect();

    // 红线 2:只用真实证明——dev mode 必须禁用;--allow-dev-mode 仅显式放开(负向测试)
    let mut allow_dev_mode = false;
    let mut files: Vec<String> = Vec::new();
    for a in &args[1..] {
        match a.as_str() {
            "--allow-dev-mode" => allow_dev_mode = true,
            other => files.push(other.to_string()),
        }
    }
    if !allow_dev_mode {
        #[allow(deprecated)]
        let dev_mode = risc0_zkvm::is_dev_mode();
        assert!(!dev_mode, "RISC0_DEV_MODE 必须禁用:dev-mode 收据不是密码学证明");
    } else {
        eprintln!("DEV_ONLY: --allow-dev-mode 只用于负向测试,产出收据不是密码学证明");
    }

    if files.len() < 2 {
        eprintln!("usage: zkvm-prove [--allow-dev-mode] <midi.bin> <salt.bin>");
        std::process::exit(2);
    }
    let midi = fs::read(&files[0]).unwrap_or_else(|e| panic!("读取 midi 失败: {e}"));
    let salt = fs::read(&files[1]).unwrap_or_else(|e| panic!("读取 salt 失败: {e}"));
    assert_eq!(salt.len(), 32, "盐必须恰 32 字节(SPEC §6.1)");

    // 输入布局:U64BE(len(M)) || M || r(32B)
    let mut input = Vec::with_capacity(8 + midi.len() + 32);
    input.extend_from_slice(&(midi.len() as u64).to_be_bytes());
    input.extend_from_slice(&midi);
    input.extend_from_slice(&salt);

    let env = ExecutorEnv::builder()
        .write(&input)
        .unwrap()
        .build()
        .unwrap();

    let prover = default_prover();
    let prove_info = prover.prove(env, ZKVM_GUEST_ELF).unwrap();
    let receipt = prove_info.receipt;

    // executor 统计(SPEC §18 记录项的一部分)
    println!("guest total_cycles: {}", prove_info.stats.total_cycles);
    println!("guest user_cycles:  {}", prove_info.stats.user_cycles);
    println!("segments:           {}", prove_info.stats.segments);

    let journal = receipt.journal.bytes.clone();
    assert_eq!(journal.len(), 202, "journal 必须 202 字节(M0 定长,SPEC §6.4)");
    let parsed = Journal::decode(&journal).expect("journal 结构无效");
    println!("journal C_M: {}", hex::encode(parsed.c_m));

    fs::write("receipt.bin", bincode::serialize(&receipt).unwrap()).unwrap();
    fs::write("input.bin", &input).unwrap();
    fs::write("journal.bin", &journal).unwrap();
    fs::write("midi.bin", &midi).unwrap();
    fs::write("salt.bin", &salt).unwrap();
    fs::write(
        "method_id.txt",
        hex::encode(ZKVM_GUEST_ID.iter().flat_map(|w| w.to_le_bytes()).collect::<Vec<u8>>()),
    )
    .unwrap();

    // 自验一次(prove 尾部亦会校验,这里显式再验)
    receipt.verify(ZKVM_GUEST_ID).unwrap();

    println!("PROVE OK  journal = 202 bytes (M0 定长)");
    println!(
        "receipt.bin = {} bytes",
        fs::metadata("receipt.bin").unwrap().len()
    );
}
