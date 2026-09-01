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

## B1/B2/B3 — Phase 2 真实负载(statement-2 完整 guest,Win 原生 CUDA + 内存限制)

| 项 | B1(15s/4v) | B2(30s/4v) | B3(60s/4v) |
|----|-----------|-----------|-----------|
| 日期 | 2026-09-01 | 2026-09-01 | 2026-09-01 |
| 机器 | Windows 10.0.28120 原生;Intel Core Ultra 9 185H;RTX 4060 Laptop 8GB;RAM 15.4 GB + pagefile 15 GB | 同左 | 同左 |
| 负载 | 4 voice 长音 On@tick0、Off@14400(15 s,480 PPQ);MIDI 66 B | 同左,Off@28800(30 s),67 B | 同左,Off@57600(60 s,Profile 1 上限),67 B |
| 构建 | `cargo +stable-x86_64-pc-windows-msvc` dev profile(opt-level=3)+ `cuda`;risc0-zkvm 3.0.6;CUDA 13.2;驱动 616.56 | 同左 | 同左 |
| **内存限制** | `--segment-po2 18`(2^18 cycles/seg,显存≈默认 po2=20 的 1/4)、`--keccak-po2 18`、`RAYON_NUM_THREADS≤8` | 同左 | 同左 |
| 证明耗时 | **637.2 s**(wall) | **1269.4 s**(≈21.2 min) | **2544.1 s**(≈42.4 min) |
| 峰值显存 | **4255 MiB** | **4309 MiB** | **4309 MiB** |
| 峰值 prover 内存 | 535.6 MiB | 896.8 MiB | 845.2 MiB |
| 系统空闲 RAM 最低 | 5519 MiB | 4806 MiB | 4625 MiB |
| guest 统计 | total 154,730,496 / user 129,631,593 / **591 segments** | total 308,084,736 / user 258,178,963 / **1176 segments** | total 614,989,824 / user 515,410,631 / **2346 segments** |
| receipt 大小 | 151,168,394 B(≈144 MiB) | 300,835,964 B(≈287 MiB) | 600,205,856 B(≈572 MiB) |
| 独立验证 | `zkvm-verify` 通过(C_M/C_V 绑定一致) | 通过 | 通过 |
| Image ID | `5e06801b...`(与 manifest 一致) | 同左 | 同左 |
| C_M | `e87a055e...` | `9381ae4e...` | `9bd55634...` |
| C_V | `fc7d96fd...` | `b2c0b2e1...` | `a40cb7e6...` |

**门禁**:SPEC §18「30 秒负载 ≤ 60 分钟真实证明」→ B2 = 21.2 min **达标**;峰值显存 ≤ 4.4 GB(8 GB 卡安全),prover 内存 < 1 GB,**全程零蓝屏**。

> **蓝屏根因(2026-09-01 实测推论)**:默认 `segment_limit_po2=20` 时单 segment 的 GPU 缓冲按 2^20 行分配,8 GB 显存机(实测约 7 GB 空闲)在真实负载下被单 segment 缓冲打爆 → 驱动级故障蓝屏。`--segment-po2 18` 使峰值显存稳定在 ~4.3 GB(三个负载一致,印证按 segment 计),代价是 segment 数 ×4、单 segment 固定开销放大:同负载 B1 在 po2=20 下估计 ≈4–5 min,po2=18 为 637 s。**内存限制与耗时是显式权衡,基准必须带限制参数记录。**
> **执行注意**:构建产物在 `C:\music-zk-target\debug\`(`rust/.cargo/config.toml` target-dir 因 CJK 路径规避);`rust\target\debug\` 下是迁移前旧拷贝,勿用。

## 备注

- **WSL 行(B0/M0)为历史记录**:2026-09-01 起 prove/verify 已迁 Windows 原生,新基准一律 Win 原生出数;WSL 仅用于 guest 构建。
- **CUDA 路径已验证**(M0-Win-CUDA):statement-2 完整 guest 4.1 s,独立 verifier 通过。CUDA 12.4 与 VS Build Tools 2026 不兼容(cudafe++ 崩),已升 13.2;驱动须 ≥580(实测 616.56)。
- 首次 prove 未见额外参数下载,default_prover 本地直接出真实 STARK 证明(非 dev-mode:receipt 含完整 seal)。
- **内存约束**:Win 原生 RAM 15.4 GB、显存 8 GB(2026-09-01 实测 `Get-CimInstance` / `nvidia-smi`;此前「物理 8 GB」为误记)。**蓝屏防护必带 `--segment-po2 ≤18`**:默认 po2=20 单 segment 显存可达 8 GB 卡上限(实测 po2=18 峰值 ~4.3 GB,三负载一致)。Phase 2 门禁 B2(30 s)已达标;若再触发内存瓶颈,先降 po2 至 17,再不行按 PLAN.md 规则缩范围并公开记录(不得假收据)。
- 证明产物写入 `proof-work/`(gitignore),不入库。
