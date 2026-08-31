"""verifier —— 公开证据验证(M0 骨架)。

M0 范围(SPEC §15 的子集):
- journal 语义层(结构 / protocol_hash / C_M 重算 / t0 承诺绑定):纯 Python,权威对拍 reference-core。
- 密码学复验:委托 RISC Zero Rust 二进制(zkvm-verify),本包不重新实现 zkVM 验证器。

Phase 2+ 补齐:C_V 重算、签名、Merkle 日志与 inclusion proof(SPEC §15 步骤 2-8、10)。
"""
