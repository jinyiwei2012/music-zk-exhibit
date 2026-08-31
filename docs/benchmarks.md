# benchmarks.md — 基准表

> 约定见 docs/AGENTS.md;模板精神见 SPEC §18。新基准追加行;修订旧值须注明原因。

## B0 — hello-guest 冒烟(Phase 0)

| 项 | 值 |
|----|----|
| 日期 | 2026-08-31 |
| 机器 | WSL2 Ubuntu 26.04 on Windows 10.0.28120;Intel Core Ultra 9 185H(16 核);WSL 可见内存 11 GiB |
| 负载 | hello-guest:输入 `hello-zk`(8 B),guest 内 SHA256,journal 输出 32 B |
| 构建 | dev profile(opt-level=3);risc0-zkvm 3.0.6 |
| 证明耗时 | **7.31 s**(wall,`/usr/bin/time -v` Elapsed) |
| 峰值内存 | **618,736 KB ≈ 604 MiB**(Maximum resident set size) |
| receipt 大小 | **221,466 B ≈ 216 KiB**(bincode 序列化,含 STARK seal) |
| 独立验证 | `zkvm-verify` 独立进程复验通过;journal == SHA256(input) |
| Image ID | `25973754979fd3a9f03da513970c1d789f808756aa3bbb140cd4388025819e02`(hello-guest) |

## 备注

- 首次 prove 未见额外参数下载,default_prover 本地直接出真实 STARK 证明(非 dev-mode:receipt 含完整 seal,时序 7.31 s)。
- **内存约束**:WSL2 默认上限 11 GiB。Phase 2 的 B1(15 s/4 声部)负载若触发内存瓶颈,先调低 segment size limit;再不行按 PLAN.md 规则缩范围并公开记录(不得假收据)。
- 证明产物写入 `proof-work/`(gitignore),不入库。
