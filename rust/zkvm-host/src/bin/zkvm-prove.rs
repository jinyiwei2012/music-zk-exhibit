//! zkvm-prove:M1 真实证明(禁 dev mode)——读 midi/salt 与公共上下文,输出 receipt/journal 到当前目录。
//! 用法:zkvm-prove [--allow-dev-mode] <midi.bin> <salt.bin> [选项]
//! 选项:
//!   --cm <hex32>               :t0 已提交承诺 C_M(缺省由本地 (M,r) 计算)
//!   --cv <hex32>               :参考音频承诺 C_V(由 reference-native render 计算)
//!   --creator-pubkey <hex32>   :创作者公钥(缺省全零,Phase 3 填充)
//!   --commit-event-id <hex32>  :COMMIT 事件 ID(缺省全零,Phase 3 填充)
//!   --release-event-id <hex32> :RELEASE 事件 ID(缺省全零,Phase 3 填充)
//!   --allow-dev-mode           :跳过 dev-mode 拒绝断言(仅负向测试素材,红线 2)
use reference_core::{commit_midi, Journal, SALT_LEN};
use risc0_zkvm::{default_prover, ExecutorEnv};
use std::fs;
use zkvm_methods::{ZKVM_GUEST_ELF, ZKVM_GUEST_ID};

fn parse_hex32(s: &str, what: &str) -> [u8; 32] {
    let b = hex::decode(s).unwrap_or_else(|e| panic!("{what} hex 非法: {e}"));
    assert_eq!(b.len(), 32, "{what} 必须 32 字节");
    b.try_into().unwrap()
}

fn main() {
    // Windows 兼容(方案 A/C):risc0 的 C++ poly_fp 是 20~57 层深递归巨帧,
    // 跑在 rayon 全局池 worker(Windows 默认 2MiB 栈)与调用线程上,必炸。
    // 必须在任何 prove/into_par_iter 之前配置全局池栈;build_global 只能成功一次。
    unsafe { std::env::set_var("RUST_MIN_STACK", "67108864") }; // 64 MiB,覆盖其他 std 线程
    let _ = rayon::ThreadPoolBuilder::new()
        .stack_size(64 * 1024 * 1024) // 64 MiB,覆盖 keccak_56 链(最深 57 层)
        .build_global();

    let args: Vec<String> = std::env::args().collect();

    // Windows 兼容(方案 D):主线程栈由 PE 头决定(默认 1MiB,见 .cargo/config.toml
    // 的 /STACK 参数),prove 在显式大栈 worker 线程中运行更稳。
    let worker = std::thread::Builder::new()
        .name("prove-worker".to_string())
        .stack_size(64 * 1024 * 1024)
        .spawn(move || run_prove(args))
        .expect("spawn prove worker");
    match worker.join() {
        Ok(Ok(())) => {}
        Ok(Err(msg)) => {
            eprintln!("{msg}");
            std::process::exit(1);
        }
        Err(_) => {
            eprintln!("prove worker panicked");
            std::process::exit(1);
        }
    }
}

fn run_prove(args: Vec<String>) -> Result<(), String> {
    let mut allow_dev_mode = false;
    let mut files: Vec<String> = Vec::new();
    let mut cm: Option<[u8; 32]> = None;
    let mut cv: Option<[u8; 32]> = None;
    let mut pubkey = [0u8; 32];
    let mut commit_id = [0u8; 32];
    let mut release_id = [0u8; 32];
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--allow-dev-mode" => allow_dev_mode = true,
            "--cm" => {
                cm = Some(parse_hex32(&args[i + 1], "C_M"));
                i += 2;
                continue;
            }
            "--cv" => {
                cv = Some(parse_hex32(&args[i + 1], "C_V"));
                i += 2;
                continue;
            }
            "--creator-pubkey" => {
                pubkey = parse_hex32(&args[i + 1], "creator_pubkey");
                i += 2;
                continue;
            }
            "--commit-event-id" => {
                commit_id = parse_hex32(&args[i + 1], "commit_event_id");
                i += 2;
                continue;
            }
            "--release-event-id" => {
                release_id = parse_hex32(&args[i + 1], "release_event_id");
                i += 2;
                continue;
            }
            other => files.push(other.to_string()),
        }
        i += 1;
    }

    // 红线 2:只用真实证明——dev mode 必须禁用;--allow-dev-mode 仅显式放开(负向测试)
    if !allow_dev_mode {
        #[allow(deprecated)]
        let dev_mode = risc0_zkvm::is_dev_mode();
        if dev_mode {
            return Err("RISC0_DEV_MODE 必须禁用:dev-mode 收据不是密码学证明".to_string());
        }
    } else {
        eprintln!("DEV_ONLY: --allow-dev-mode 只用于负向测试,产出收据不是密码学证明");
    }

    if files.len() < 2 {
        return Err("usage: zkvm-prove [options] <midi.bin> <salt.bin>".to_string());
    }
    let midi = fs::read(&files[0]).map_err(|e| format!("读取 midi 失败: {e}"))?;
    let salt = fs::read(&files[1]).map_err(|e| format!("读取 salt 失败: {e}"))?;
    if salt.len() != SALT_LEN {
        return Err("盐必须恰 32 字节(SPEC §6.1)".to_string());
    }
    let salt_arr: [u8; SALT_LEN] = salt.as_slice().try_into().unwrap();

    // 公共上下文缺省值
    let c_m = cm.unwrap_or_else(|| commit_midi(&midi, &salt_arr));
    let c_v = cv.ok_or("缺少 --cv:参考音频承诺 C_V 必须显式提供")?;

    // 输入布局(guest 约定):
    // U64BE(len(M)) || M || r(32) || pubkey(32) || commit_id(32) || release_id(32) || C_M(32) || C_V(32)
    let mut input = Vec::with_capacity(8 + midi.len() + 32 * 6);
    input.extend_from_slice(&(midi.len() as u64).to_be_bytes());
    input.extend_from_slice(&midi);
    input.extend_from_slice(&salt_arr);
    input.extend_from_slice(&pubkey);
    input.extend_from_slice(&commit_id);
    input.extend_from_slice(&release_id);
    input.extend_from_slice(&c_m);
    input.extend_from_slice(&c_v);

    let env = ExecutorEnv::builder()
        .write(&input)
        .map_err(|e| format!("ExecutorEnv write 失败: {e}"))?
        .build()
        .map_err(|e| format!("ExecutorEnv build 失败: {e}"))?;

    let prover = default_prover();
    // 诊断:打印 prove 用的 ELF 的真实 image_id(应与 manifest 一致)
    match risc0_zkvm::compute_image_id(ZKVM_GUEST_ELF) {
        Ok(id) => println!("runtime image_id: {}", id),
        Err(e) => eprintln!("compute_image_id 失败: {e}"),
    }
    let prove_info = prover
        .prove(env, ZKVM_GUEST_ELF)
        .map_err(|e| format!("prove 失败: {e}"))?;
    let receipt = prove_info.receipt;

    // executor 统计(SPEC §18 记录项的一部分)
    println!("guest total_cycles: {}", prove_info.stats.total_cycles);
    println!("guest user_cycles:  {}", prove_info.stats.user_cycles);
    println!("segments:           {}", prove_info.stats.segments);

    let journal = receipt.journal.bytes.clone();
    if journal.len() != 202 {
        return Err("journal 必须 202 字节(M1 定长,SPEC §6.4)".to_string());
    }
    let parsed = Journal::decode(&journal).map_err(|e| format!("journal 结构无效: {e:?}"))?;
    println!("journal C_M: {}", hex::encode(parsed.c_m));
    println!("journal C_V: {}", hex::encode(parsed.c_v));

    fs::write("receipt.bin", bincode::serialize(&receipt).unwrap())
        .map_err(|e| format!("写 receipt.bin 失败: {e}"))?;
    fs::write("input.bin", &input).map_err(|e| format!("写 input.bin 失败: {e}"))?;
    fs::write("journal.bin", &journal).map_err(|e| format!("写 journal.bin 失败: {e}"))?;
    fs::write("midi.bin", &midi).map_err(|e| format!("写 midi.bin 失败: {e}"))?;
    fs::write("salt.bin", &salt).map_err(|e| format!("写 salt.bin 失败: {e}"))?;
    fs::write(
        "method_id.txt",
        hex::encode(ZKVM_GUEST_ID.iter().flat_map(|w| w.to_le_bytes()).collect::<Vec<u8>>()),
    )
    .map_err(|e| format!("写 method_id.txt 失败: {e}"))?;

    // 自验一次(prove 尾部亦会校验,这里显式再验)
    // 注意:image_id 必须用 [u8;32] 大端字节构造 Digest(与 verify 一致),
    // [u32;8].into() 的字节序陷阱会导致误报 ClaimDigestMismatch。
    let image_id_hex: Vec<u8> = ZKVM_GUEST_ID
        .iter()
        .flat_map(|w| w.to_be_bytes())
        .collect();
    let image_id_digest: risc0_zkvm::Digest =
        <[u8; 32]>::try_from(image_id_hex.as_slice()).unwrap().into();
    receipt
        .verify(image_id_digest)
        .map_err(|e| format!("自验失败: {e}"))?;

    println!("PROVE OK  journal = 202 bytes (M1 定长)");
    println!(
        "receipt.bin = {} bytes",
        fs::metadata("receipt.bin").unwrap().len()
    );
    Ok(())
}
