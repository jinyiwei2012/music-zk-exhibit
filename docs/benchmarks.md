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

## M0 — 关系最小闭环(Phase 1 门禁)

| 项 | 值 |
|----|----|
| 日期 | 2026-08-31 |
| 机器 | 同 B0:WSL2 Ubuntu 26.04 on Windows 10.0.28120;Intel Core Ultra 9 185H;WSL 可见内存 11 GiB |
| 负载 | M0 guest:输入 `U64BE(len(M)) || M || r`(M=14 B,r=32 B),重算 `CommitMidi` + 输出定长 202 B journal |
| 构建 | dev profile;risc0-zkvm 3.0.6;guest 镜像 ID `f75836df6efa7464738a9c19119b1e7aeebb7e97cd2e0520b627bfeb1b662045` |
| 证明耗时 | **9.30 s**(wall,`/usr/bin/time -v` Elapsed;User 123.70 s,CPU 1345%) |
| 峰值内存 | **620,304 KB ≈ 606 MiB**(Maximum resident set size) |
| guest 统计 | total_cycles 65536 / user_cycles 31307 / segments 1 |
| receipt 大小 | **221,614 B ≈ 216 KiB**(bincode 序列化) |
| 独立验证 | `zkvm-verify` 独立进程复验通过;1 正 4 负门禁演示 `scripts/phase1-m0.sh` 全过 |

## M0-Win — 关系最小闭环(Phase 1 门禁,Windows 原生 CPU)

| 项 | 值 |
|----|----|
| 日期 | 2026-09-01 |
| 机器 | Windows 10.0.28120 原生(MSVC);Intel Core Ultra 9 185H(16 核);RTX 4060 Laptop 8GB(**CPU 路径**) |
| 构建 | `cargo +stable-x86_64-pc-windows-msvc`;risc0-zkvm 3.0.6;`disable-dev-mode` feature;栈修复(rayon 64 MiB + `/STACK:0x4000000`) |
| 负载 | 同 M0:输入 `U64BE(len(M)) || M || r`(M=14 B,r=32 B),重算 `CommitMidi` + 输出定长 202 B journal |
| 证明耗时 | **106–120 s**(wall,多次测量) |
| 峰值内存 | 未单独记录(约 ≤8 GB 预算内) |
| guest 统计 | 同 M0(65536 total_cycles / 31307 user_cycles / 1 segment) |
| receipt 大小 | 与 M0 同量级 ≈ 216 KiB(bincode) |
| 独立验证 | `zkvm-verify` 独立进程复验通过;门禁 `scripts/phase1-m0.ps1` PASS=5/FAIL=0(1 正 + 3 负 + dev-mode 编译期硬禁) |
| journal 值 | C_M=`0717cc99...`、C_V=`24984545...`(与 golden vector 一致) |
| protocol_id | `music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2`(Image ID `5e06801b...`) |

## M0-Win-CUDA — statement-2 完整 guest,Windows 原生 CUDA

| 项 | 值 |
|----|----|
| 日期 | 2026-09-01 |
| 机器 | Windows 10.0.28120 原生(MSVC);Intel Core Ultra 9 185H;RTX 4060 Laptop 8GB(**CUDA 路径**) |
| 构建 | `cargo +stable-x86_64-pc-windows-msvc` + `cuda` feature;risc0-zkvm 3.0.6;**CUDA 13.2**(cudafe++ 与 VS 2026 兼容,12.4 不兼容);**驱动 616.56**(≥580,CUDA 13.x 必需) |
| 负载 | statement-2 完整 guest:解析 MIDI Profile 1 + ReferenceSynth 1 合成 + C_M/C_V 断言 + 流式 SHA-256;minimal-onenote(41 B MIDI) |
| 证明耗时 | **4.1 s**(wall;CPU 路径 106–120 s,**加速约 27×**) |
| guest 统计 | total_cycles 524288 / user_cycles 396368 / segments 1 |
| receipt 大小 | **268,590 B ≈ 262 KiB** |
| 独立验证 | `zkvm-verify` 独立进程复验通过(0.2 s) |
| journal 值 | C_M=`0717cc99...`、C_V=`24984545...`(与 golden vector 逐字节一致) |
| 关键坑 | ① CUDA 12.4 cudafe++ 崩(MSVC 18)→ 升 13.2;② 驱动被 12.4 捆绑 551.78 覆盖(CUDA 13 需 ≥580)→ 升 616.56;③ CJK 仓库路径 → sppark/risc0-sys ASCII 镜像;④ CXXFLAGS 透传 → cudafe++ 崩;⑤ thrust/CUB `_SM_` 宏污染 → 官方开关;⑥ `-include cuda.h` force-include(CCCL driver_api 顺序) |

> **与 WSL 对比**:WSL CPU 9.30 s / 606 MiB;Win 原生 CPU 106–120 s——**慢一个数量级**(多核并行度/调度差异),门禁仍绿。CUDA 路径基准待 `cuda` feature 构建验证后补录。

## 备注

- **WSL 行(B0/M0)为历史记录**:2026-09-01 起 prove/verify 已迁 Windows 原生,新基准一律 Win 原生出数;WSL 仅用于 guest 构建。
- **CUDA 路径已验证**(M0-Win-CUDA):statement-2 完整 guest 4.1 s,独立 verifier 通过。CUDA 12.4 与 VS Build Tools 2026 不兼容(cudafe++ 崩),已升 13.2;驱动须 ≥580(实测 616.56)。
- 首次 prove 未见额外参数下载,default_prover 本地直接出真实 STARK 证明(非 dev-mode:receipt 含完整 seal)。
- **内存约束**:Win 原生不受 WSL 11 GiB 上限约束(物理 8 GB)。Phase 2 的 B1(15 s/4 声部)负载若触发内存瓶颈,先调低 segment size limit;再不行按 PLAN.md 规则缩范围并公开记录(不得假收据)。
- 证明产物写入 `proof-work/`(gitignore),不入库。
