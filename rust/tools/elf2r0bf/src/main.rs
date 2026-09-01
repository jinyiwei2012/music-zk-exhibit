//! elf2r0bf:将标准 ELF 转换为 R0BF(RISC Zero Binary Format)。
//!
//! risc0 2.x/3.x 的 `ProgramBinary` 不是标准 ELF,而是魔数 `R0BF` 的自定义
//! 容器:ProgramBinaryHeader + user_elf + kernel_elf(V1COMPAT)。risc0-build
//! 在构建 guest 时自动完成转换,`embed_methods!()` 内嵌的也是 R0BF。
//!
//! 本工具让 WSL 构建产物(标准 ELF)可被独立转换为 R0BF 入库
//! `protocol/guest-v1.elf`,Windows 宿主 `include_bytes!` 加载的必须是
//! R0BF(否则 prove 报 "Malformed ProgramBinary")。

use risc0_binfmt::ProgramBinary;
use risc0_zkos_v1compat::V1COMPAT_ELF;
use std::fs;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: elf2r0bf <user.elf> <out.r0bf>");
        std::process::exit(2);
    }
    let user_elf = fs::read(&args[1]).unwrap_or_else(|e| panic!("读取 {} 失败: {e}", args[1]));
    assert!(
        user_elf.len() > 4 && &user_elf[0..4] == b"\x7fELF",
        "输入不是标准 ELF(魔数 0x7f 'ELF')"
    );

    let binary = ProgramBinary::new(&user_elf, V1COMPAT_ELF);
    let r0bf = binary.encode();
    fs::write(&args[2], &r0bf).unwrap_or_else(|e| panic!("写入 {} 失败: {e}", args[2]));

    // 自校验:转换结果可被 ProgramBinary::decode 解析
    ProgramBinary::decode(&r0bf).expect("R0BF 自校验失败: decode 不通过");

    println!(
        "OK  {} -> {}  ({} bytes R0BF, user_elf {} bytes)",
        args[1],
        args[2],
        r0bf.len(),
        user_elf.len()
    );
}
