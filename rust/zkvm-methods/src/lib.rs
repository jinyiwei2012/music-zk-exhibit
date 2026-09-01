//! 冻结的 guest 产物(R0BF + Image ID)。
//!
//! 构建期不再调用 `risc0_build::embed_methods!()`:那会在宿主编译时触发
//! rzup/risc0 工具链,而它们不提供 Windows 原生支持(红线 5 的版本锁定 +
//! Win 原生迁移,见 docs/ENV.md)。guest 由 WSL 侧脚本构建(标准 ELF),
//! 再经 `elf2r0bf` 转为 R0BF(RISC Zero Binary Format)入库
//! `protocol/guest-v1.elf`,此处只做编译期字节加载,宿主(含 Windows)零工具链依赖。
//!
//! 格式说明:risc0 2.x/3.x 的 `ProgramBinary` 不是标准 ELF,而是魔数 `R0BF`
//! 的自定义容器(user ELF + V1COMPAT kernel ELF)。`prove()` 只接受 R0BF,
//! 标准 ELF 会报 "Malformed ProgramBinary"。
//!
//! 一致性校验:ELF 的 SHA-256 与 `protocol/v1.json` 的 `guest.elf_sha256`
//! 必须逐字节一致;Image ID 与 `guest.image_id` 一致。任何变更都必须同时更新
//! manifest 并产生新 `protocol_id`(SPEC §5)。

/// guest-v1.elf(R0BF 格式)原始字节(入库冻结,sha256 = ce00d244... 见 protocol/v1.json)。
pub const ZKVM_GUEST_ELF: &[u8] = include_bytes!("../../../protocol/guest-v1.elf");

/// guest Image ID(`[u32; 8]`,protocol/v1.json 中 `image_id` 为 32 字节 hex,
/// 每 4 字节一组按大端解析为 u32;Phase 2 完整 guest,statement-2)。
pub const ZKVM_GUEST_ID: [u32; 8] = [
    0x5e06801b, 0x5e97e4c3, 0xd7bcbc99, 0xbf5432ff, 0x3fc4056a, 0x9cf71b41, 0x75038a7e, 0x895c7d8a,
];

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    /// ELF 必须是 R0BF 格式(risc0 2.x/3.x ProgramBinary,prove 只接受此格式)。
    #[test]
    fn elf_is_r0bf_format() {
        assert_eq!(
            &ZKVM_GUEST_ELF[0..4], b"R0BF",
            "guest-v1.elf 必须是 R0BF 格式(标准 ELF 会让 prove 报 Malformed ProgramBinary)"
        );
    }

    /// Image ID 与 ELF 的绑定:这里锁定常量与 ELF 字节、manifest 记录三方一致,防止误改。
    #[test]
    fn elf_matches_manifest_sha256() {
        let digest = Sha256::digest(ZKVM_GUEST_ELF);
        let hex = digest.iter().map(|b| format!("{b:02x}")).collect::<String>();
        assert_eq!(
            hex, "ce00d244e68735f15c32778051317d5da1992d42a3b06c7f92ce6d86fddc3dff",
            "guest-v1.elf 与 manifest guest.elf_sha256 不一致"
        );
    }

    #[test]
    fn image_id_matches_manifest() {
        let bytes: Vec<u8> = ZKVM_GUEST_ID
            .iter()
            .flat_map(|w| w.to_be_bytes())
            .collect();
        let hex = bytes.iter().map(|b| format!("{b:02x}")).collect::<String>();
        assert_eq!(
            hex, "5e06801b5e97e4c3d7bcbc99bf5432ff3fc4056a9cf71b4175038a7e895c7d8a",
            "ZKVM_GUEST_ID 与 manifest guest.image_id 不一致"
        );
    }
}
