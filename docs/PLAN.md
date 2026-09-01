# 实施计划:Music-ZK Exhibit 怎么做

- 状态:v0.2(2026-09-01,Win 原生迁移后修订)
- 依据:`PRD.md`、`SPEC.md` Draft v0.1
- 本文回答一个问题:从零开始,按什么顺序、分几步、先赌哪个风险,把 SPEC 变成能跑的展品。

## 1. 总体判断

这个项目本质是 **三件事的组合**,难度极不均匀:

| 模块 | 难度 | 说明 |
|---|---|---|
| A. 确定性 MIDI 解析 + 整数合成器 | 中 | 纯 Rust 工程问题,规则已在 SPEC §8/§9 写死,量大但不冒险 |
| B. RISC Zero zkVM 真实证明 | **高,且是唯一硬风险** | 内存/耗时预算、**guest 必须 WSL 构建、host 已迁 Win 原生**、版本必须锁死 |
| C. 日志/签名/API/网页 | 低-中 | 常规 Web 工程,FastAPI + Ed25519 + Merkle,全是成熟零件 |

SPEC §20 已给出关键指令:**M0/M1 先做,基准门不过就砍范围,不许先包 UI**。本计划按此执行,并把 B 的风险压到第一周就见分晓。

## 2. 两个必须最先做的环境决策

1. **证明环境 = Windows 原生(host)+ WSL2 仅 guest 构建**。2026-09-01 实测:risc0 3.0.6 的 host prove/verify 可在 Win 原生编译运行(CPU 路径过门禁、CUDA feature 已启用);**risc0 guest 工具链无 Windows 二进制,rzup 硬性不可用**,guest ELF(R0BF)仍由 WSL2 构建后入库 `protocol/guest-v1.elf`,host 只加载 ELF 文件。Windows 侧只跑编辑器/浏览器/prove/verify。详情见 `docs/ENV.md`。
2. **锁版本**:RISC Zero 安装后立即固定 `cargo-risczero` 版本、Rust toolchain(`rust/zkvm-methods/guest/rust-toolchain.toml`)、Cargo.lock,guest ELF 的 Image ID 记入 protocol manifest。**永不自动升级**(SPEC §2.1、§17.5)。

已知偏差:本机系统 Python 是 3.14,SPEC 写 3.12。项目 Python 环境用 conda 创建:`conda create -n music-zk python=3.12 -y`,开工前 `conda activate music-zk`;不使用系统 3.14,也不为它做适配。

## 3. 里程碑与顺序(带验收动作)

### Phase 0:冒烟测试(几天)——整项目的 go/no-go

> ✅ **已完成(2026-08-31)**:hello-guest 真实证明 7.31 s / 604 MiB / receipt 216 KiB,独立 verifier 复验通过(B0 入 `docs/benchmarks.md`)。门禁通过,项目形态成立。

目标:在 WSL2 里跑通一个**与音乐无关**的最小 RISC Zero 闭环。

1. WSL2 装 Rust + rzup,RISC Zero 版本写入 `docs/ENV.md`。
2. Hello-guest:输入 x,journal 输出 `sha256(x)`,本地 prove → verify 成功。
3. 记录:证明耗时、峰值内存、receipt 大小(空载基线)。

**验收**:一个真实(非 dev mode)receipt 被禁用 dev-mode 的 verifier 通过。
**不通过怎么办**:这基本否决整个项目形态,立即公开结论,不要继续。

### Phase 1 = SPEC M0:关系最小闭环(约 1 周)

> ✅ **已完成(2026-09-01,Win 原生)**:framing/journal/Python verifier 全绿;真实证明 CPU 106–120 s、独立 verifier 复验通过;1 正 + 3 负 + dev-mode 编译期硬禁 PASS=5/FAIL=0。**prove/verify 已迁 Windows 原生**,guest 构建保留 WSL(M0-Win 基准入 `docs/benchmarks.md`);protocol_id 已按 SPEC §5 升为 statement-2。

1. `rust/reference-core`:实现 SPEC §7 的三个哈希 framing(`CommitMidi` / `CommitReferenceWav` / `CommitSong`),含域分离与长度前缀;单元测试锁字节。
2. `zkvm-guest`:读私有 `M`、`r`(env-io),重算 `C_M`,输出 SPEC §6.4 的**定长二进制 journal**(magic `MZKJNL01`,拒绝尾随字节)。
3. `zkvm-host`:executor 先跑拿 cycle 统计 → 真实 prove → **独立 verifier 进程**复验(与 `prove` 分离,便于复现 SPEC §13 的要求)。
4. Python `music_zk/verifier`:能验 receipt、journal、Image ID(Python 侧调 host verifier 二进制或用 risc0 的 verifier crate 编译出的 CLI)。
5. 负向测试先写:改 M 一字节、换盐、错 Image ID、dev-mode fake receipt 交给 production verifier——全部必须失败。

**验收**:`cargo test` + 一条脚本演示"有效证明通过、五种篡改全部失败"。

### Phase 2 = SPEC M1:MIDI Profile + ReferenceSynth(1–2 周,最大工作量)

> ✅ **已完成(2026-09-01,Win 原生 CUDA)**:parser fail-closed 全量 + 纯整数合成器 + 波表冻结 + 流式摘要 + golden vectors ×6(native == guest == Python 三方逐字节一致)+ 真实负载基准 **B1=637 s、B2=1269 s(≈21.2 min)、B3=2544 s**,独立 verifier 全过、峰值显存 ~4.3 GB 零蓝屏。门禁「30s ≤ 60min」**达标**。内存限制与耗时权衡见 `docs/benchmarks.md` B1/B2/B3 节。

1. **严格 parser**(reference-core):SPEC §8 全部规则 fail-closed;§17.2 的拒绝测试逐条对应。
2. **整数合成器**:u32 相位累加、2048 点 i16 波表、Q15 包络、8 voice slot 抢占规则(SPEC §9.4)——**全部整数运算,禁止 float**;除法向零截断的公式写进代码注释和 golden vector 文档。
3. **波表冻结**:写一次性生成脚本产出 `wavetable-v1.bin`,从此只读其字节 + SHA-256,不再改。
4. **流式摘要**:guest 不存完整 WAV,按 SPEC §9.6 边生成边喂 SHA-256;60 秒 WAV 约 960 KB,配合 RISC Zero 的 SHA-256 加速,这个量级是安全的。
5. **Golden vectors**(SPEC §17.1 六个向量):native 渲染、guest 执行、Python 重算三方逐字节一致,进 CI。
6. **基准门 B1/B2**:15 秒 / 30 秒、4 voices,按 SPEC §18 表格逐项记录机器、耗时、峰值 RSS、receipt 大小。

**验收/砍范围线**:30 秒工作负载 ≤ 60 分钟出真实证明。若 8 GB 机器不行:降 segment size limit;再不行:缩到 30 秒时长或降采样率——注意这会产生**新 protocol_id**,要在 manifest 里如实反映。16 GB 机器通过也算概念成立,但"8 GB 未达成"写进公开文档。

### Phase 3 = SPEC M2:身份、日志、服务端(1–2 周)

> ✅ **已完成(2026-09-01)**:`identity init`(Ed25519 + `creator-secret/` 原子创建)+ JCS(RFC 8785,Python `jcs` 包与 Rust `serde_jcs` 12 样本逐字节对拍)+ FastAPI/SQLite 三事件端点(签名/去重/引用顺序/字段黑名单/大小限制/两阶段发布)+ RFC 6962 Merkle 日志(Google CT 官方向量锁定)+ CLI 六步流程。**门禁:minimal-onenote 真实端到端 t0→t1→t2**(真实 CUDA 证明,服务端本地 verifier 复验),checkpoint + inclusion proof 独立实现(不 import music_zk)重建树根/STH 签名全过。zkvm-verify 新增无 witness 模式(服务端永无 midi/salt,红线 1)。

1. **Ed25519 创作者身份**:`identity init` 生成密钥,私钥落 `creator-secret/`(目录原子创建、已存在即停)。
2. **RFC 8785 JCS**:Python 侧没有标准库,选一个经过交叉测试的实现,并用小样本与 Rust `serde_jcs` 对拍;签名体保持小而简单(事件 body 只有几个字段),降低规范化踩坑面。
3. **FastAPI 服务端**:三类事件接口、`creator_pubkey+client_nonce` 去重、顺序校验 `COMMIT.seq < RELEASE.seq < PROOF.seq`、上传前服务端本地跑 verifier(SPEC §11.1)。
4. **透明日志**:RFC 6962 域分离 Merkle、Signed Tree Head 签名、inclusion proof;提交回执 = 事件 + STH + 包含证明。
5. **字段黑名单**:服务端拒绝名为 `midi`/`salt`/`private_key` 的字段(SPEC §11.1)。
6. SQLite 两阶段发布:先落文件再提交日志,避免悬空引用。

### Phase 4 = SPEC M3:展品体验(约 1 周)

> ✅ **已完成(2026-09-01)**:结果页/技术页/首页(§3.7 文案常量逐字、状态机、S/V 双播放器不默认同步、不能证明默认展开)+ 公开证据包导出(SPEC §12.2:claim/manifest/三回执/journal/zkvm-receipt/song-S/reference-V/checksums/VERIFYING.md)+ `music-zk verify`(SPEC §15 十一项逐项,步骤 1 包损坏不得总体有效)+ `reveal-check` + `demo tamper` 五案例 + `scripts/demo.ps1` 一键演示(暂停讲解每步)。**实测:离线 verify 十项全有效、五个 tamper 案例全部检出、PRD §13.1 表述验收过**。演示链路修复三处:identity ACL 完整控制、prove 填 journal 上下文、song-S glob 校验。

### Phase 5 = SPEC M4:审查收尾(几天)**← 当前阶段(2026-09-01 起)**

- 隐私扫描:全库 grep 私密字节、断网 proving 测试、崩溃残留检查(SPEC §17.4)。
- 可复现构建:CI 干净环境双构建,两次 Image ID 一致;README 给出源码算 Image ID 的命令。
- 性能报告、`ENV.md`、演示脚本定稿。

## 4. 并行线

Phase 1 通过后,C 线(日志/签名/API)不依赖证明性能,可以和 Phase 2 的合成器并行做;Web 页面(Phase 4)可以再往后并行,但**文案不许提前上线上页**。单人开发就按串行走,顺序即优先级。

## 5. 关键风险与对策(浓缩)

| 风险 | 对策 |
|---|---|
| 8 GB 内存/30 分钟证明超预算 | Phase 0/2 两道基准门;segment limit 可调;范围可砍(SPEC 允许) |
| Windows 原生 prover 踩坑 | **已趟平(2026-09-01)**:C++ 栈溢出(poly_fp 深递归)→ rayon 64 MiB 栈 + `/STACK`;image_id 字节序陷阱 → 统一 `[u8;32]` 大端构造;guest 工具链无 Win 二进制 → guest 构建保留 WSL,host 加载 R0BF。见 `docs/ENV.md` / `docs/OPEN-QUESTIONS.md` |
| 合成器跨平台不一致 | 单一 reference-core、纯整数、golden vectors 三方对拍 |
| guest 内存爆(全 WAV 常驻) | 强制流式 SHA-256;60s 音频仅 ~1 MB,量级安全 |
| JCS 实现坑 | 签名体字段极简;Python/Rust 交叉测试 |
| 证明含义被夸大 | 文案来自 PRD 固定字符串,代码里作为常量渲染,不自由发挥 |
| RISC Zero 版本漂移/安全公告 | 全链路锁版本;公告触发新 manifest,不悄悄换 Image ID |

## 6. Windows 兼容策略

**2026-09-01 更新:prove/verify 已迁移到 Windows 原生。** 官方口径从"prove 走 WSL2"改为:**host 全链路 Windows 原生(CPU + CUDA),唯一保留在 WSL 的环节是 guest 构建**(risc0 guest 工具链无 Windows 二进制,rzup 硬性不可用)。guest 构建的产物是 R0BF(`protocol/guest-v1.elf`,`scripts/build-guest-wsl.sh`),host 只加载 ELF 文件,因此 prove 端机器完全不需要 rzup/risc0 客户工具链——这同时让老电脑(只有 verify 需求)不依赖 WSL。

### 6.1 角色 × Windows 需求矩阵

| 角色 | Windows 兼容方式 |
|---|---|
| 普通质疑者 | 浏览器 + 证据包,零依赖,天然兼容 |
| 技术质疑者 | 原生 Windows `verify.exe`:risc0 verify 路径纯 Rust,已实测 Win 原生编译运行 |
| 展示者/创作者(非 prove 环节) | 原生 Python 3.12 venv:CLI、FastAPI、SQLite 日志、网页全部跨平台 |
| 创作者(prove 环节) | **Windows 原生 prover(CPU 已验证、CUDA 已启用)**;WSL 仅在 guest 改动时用于重建 R0BF |

### 6.2 四项落地工作(2026-09-01 完成状态)

1. **`scripts/env-win.ps1`**(已落地):构建前置——导入 MSVC vcvars64 环境、设 `CXXFLAGS=/std:c++20 /DNOMINMAX`、指认 `CUDA_PATH`;构建命令 `cargo +stable-x86_64-pc-windows-msvc build`。注意 dot-source 后须 `$ErrorActionPreference="Continue"`,否则 cargo 的 stderr 进度会被 PowerShell 误判为终止错误而中断构建。
2. **CLI 透明委托(原方案,已取消)**:不再需要 `music-zk prove` 委托 WSL——prove 已原生跑在 Windows 上。秘密文件仍始终留在本机,与"proving 断网"的隐私要求一致。
3. **原生 Windows 验证器**(已实测):verifier CLI 编译 Windows 目标并进 GitHub Actions `windows-latest` CI;同一 runner 上跑 `reference-core` golden vectors,钉死合成输出跨平台逐字节一致。
4. **原生 Windows prover(已完成迁移,升级为正式路径)**:risc0-zkvm prove feature 在 Win 原生编译运行,前置改造即 **guest ELF 预构建入库**(`protocol/guest-v1.elf` R0BF + manifest Image ID,CI 在 Linux 重建并核对)。两条 Win 原生关键坑已解决:C++ 栈溢出(poly_fp 深递归)→ rayon 64 MiB 栈 + `/STACK:0x4000000`;image_id 字节序(`[u32;8]` 与 `[u8;32]` 大小端不同)→ 统一大端 `[u8;32]` 构造 `Digest`。详见 `docs/ENV.md`。

### 6.4 绕开 WSL 的降级路径(老电脑 / 无法启用虚拟化)

前提判断:老电脑的真实瓶颈是**证明资源**(RAM、CPU),不是操作系统本身。资源阶梯:

| 任务 | 资源需求 | 老电脑可行性 |
|---|---|---|
| 浏览网页、播放 S/V | 极低 | 无条件可行 |
| 离线验证证据包 | <10 秒、低内存 | 几乎任何 x64 电脑可行 |
| 本地生成真实证明 | 8–16 GB RAM + 现代 CPU | 取决于硬件,与 OS 无关 |

在此前提下,无法启用 WSL 的机器按序走:

1. **原生 Windows prover(已上线)**:不需要任何虚拟化,瓶颈只剩内存;内存不足就按 SPEC §18 缩短音频(5–15 秒)或调低 segment size limit。guest 构建是本机一次性动作(或 CI 产物),老电脑只消费 R0BF。
2. **Linux Live USB 兜底**(不依赖虚拟化、不装系统、不动硬盘):把 prover 以静态链接 Linux 二进制发布,创作者从 U 盘启动 Ubuntu live,完全离线生成证明后拷回结果。比 WSL2 兼容面更宽(只需 64 位 CPU,不需要 VT-x、不需要 Win10 1903+),且天然满足"proving 断网"。配套 `docs/LIVE-USB.md` + 构建脚本。
3. **证明/验证分离的演示模式**:证明是**一次性的公开数据**——receipt、journal、证据包都可以在任何一台有能力的机器上生成,然后搬运到老电脑上做展示。老电脑只跑原生验证器(秒级、低内存),密码学强度不打折,完全符合产品语义(验证者本来就不需要证明能力)。

明确不可行 / 不做的路:
- 远程 prover 兜底:PRD §13.2 隐私红线,远程看得见 witness。
- TEE(SGX/TDX)或 MPC 证明:超出 v1 威胁模型,列为未来方向。
- QEMU 纯软件模拟(无 VT-x 跑 Linux):可行但慢一个数量级,30 秒工作负载不现实,不提供。
- 为兼容性更换 zk 技术栈(纯 Rust 电路如 halo2/Winterfell 需手写电路,等于重写)。

### 6.3 边界与不做的事

- **guest 构建必须 WSL2**(工具链与文件系统语义;risc0 guest 工具链无 Windows 二进制,rzup 硬性不可用)。host 侧不依赖 WSL。
- 私密目录权限:Windows 没有 POSIX 权限位,用用户目录 + ACL(`icacls` 或 API)近似 SPEC §10.1 的"仅当前用户可读",README 说明差异。
- **禁止**用远程 prover 给 Windows 用户兜底——PRD §13.2 隐私红线,远程 prover 看得见 witness。
- 不为 Windows 换技术栈:SP1 无零知识性,手写 halo2 电路等价于推翻重来,均非 v1 选项。

## 7. 下一步(具体到命令)

```powershell
# 0. 构建前置(每个新 shell 一次):导入 MSVC + CUDA 环境
. .\scripts\env-win.ps1
# 注:dot-source 后务必 $ErrorActionPreference="Continue",否则 cargo stderr 进度被误杀

# 1. host 构建/测试(Windows 原生)
cargo +stable-x86_64-pc-windows-msvc build -p zkvm-host
cargo +stable-x86_64-pc-windows-msvc test

# 2. guest 改动后重建 R0BF(WSL 侧;不常改)
bash scripts/build-guest-wsl.sh   # 产物 protocol/guest-v1.elf;核对 Image ID 是否变化

# 3. 跑基准/门禁
powershell -ExecutionPolicy Bypass -File scripts/phase1-m0.ps1
powershell -ExecutionPolicy Bypass -File scripts/bench-phase2.ps1 -Cases b1-15s-4v   # 内存限制已内置(--segment-po2 18)

# 4. Phase 3(进行中):Python 全在 conda env music-zk 内
conda run -n music-zk pytest
conda run -n music-zk python -m music_zk.cli identity init --out creator-secret
```

工作量粗估(单人):Phase 1–2 已完成(2026-09-01,含 Win 原生迁移与内存限制落地);Phase 3(身份/日志/服务端)约 2 周,Phase 4–5 约 1.5 周,合计剩余 3–4 周。
