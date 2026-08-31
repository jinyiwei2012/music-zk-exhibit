# 实施计划:Music-ZK Exhibit 怎么做

- 状态:v0.1(2026-08-31)
- 依据:`PRD.md`、`SPEC.md` Draft v0.1
- 本文回答一个问题:从零开始,按什么顺序、分几步、先赌哪个风险,把 SPEC 变成能跑的展品。

## 1. 总体判断

这个项目本质是 **三件事的组合**,难度极不均匀:

| 模块 | 难度 | 说明 |
|---|---|---|
| A. 确定性 MIDI 解析 + 整数合成器 | 中 | 纯 Rust 工程问题,规则已在 SPEC §8/§9 写死,量大但不冒险 |
| B. RISC Zero zkVM 真实证明 | **高,且是唯一硬风险** | 内存/耗时预算、Windows 必须走 WSL2、版本必须锁死 |
| C. 日志/签名/API/网页 | 低-中 | 常规 Web 工程,FastAPI + Ed25519 + Merkle,全是成熟零件 |

SPEC §20 已给出关键指令:**M0/M1 先做,基准门不过就砍范围,不许先包 UI**。本计划按此执行,并把 B 的风险压到第一周就见分晓。

## 2. 两个必须最先做的环境决策

1. **证明环境 = WSL2 Ubuntu(x86-64)**。RISC Zero 官方预构建只支持 x86-64 Linux / arm64 macOS;本机已确认 WSL 可用。所有 Rust/zkVM 构建在 WSL2 内进行,Windows 侧只跑编辑器和浏览器。
2. **锁版本**:RISC Zero 安装后立即固定 `cargo-risczero` 版本、Rust toolchain(rust-toolchain.toml)、Cargo.lock,guest ELF 的 Image ID 记入 protocol manifest。**永不自动升级**(SPEC §2.1、§17.5)。

已知偏差:本机 Python 是 3.14,SPEC 写 3.12。用 `uv`/`pyenv` 在项目 venv 里钉住 3.12 即可,不为 3.14 做适配。

## 3. 里程碑与顺序(带验收动作)

### Phase 0:冒烟测试(几天)——整项目的 go/no-go

目标:在 WSL2 里跑通一个**与音乐无关**的最小 RISC Zero 闭环。

1. WSL2 装 Rust + rzup,RISC Zero 版本写入 `docs/ENV.md`。
2. Hello-guest:输入 x,journal 输出 `sha256(x)`,本地 prove → verify 成功。
3. 记录:证明耗时、峰值内存、receipt 大小(空载基线)。

**验收**:一个真实(非 dev mode)receipt 被禁用 dev-mode 的 verifier 通过。
**不通过怎么办**:这基本否决整个项目形态,立即公开结论,不要继续。

### Phase 1 = SPEC M0:关系最小闭环(约 1 周)

1. `rust/reference-core`:实现 SPEC §7 的三个哈希 framing(`CommitMidi` / `CommitReferenceWav` / `CommitSong`),含域分离与长度前缀;单元测试锁字节。
2. `zkvm-guest`:读私有 `M`、`r`(env-io),重算 `C_M`,输出 SPEC §6.4 的**定长二进制 journal**(magic `MZKJNL01`,拒绝尾随字节)。
3. `zkvm-host`:executor 先跑拿 cycle 统计 → 真实 prove → **独立 verifier 进程**复验(与 `prove` 分离,便于复现 SPEC §13 的要求)。
4. Python `music_zk/verifier`:能验 receipt、journal、Image ID(Python 侧调 host verifier 二进制或用 risc0 的 verifier crate 编译出的 CLI)。
5. 负向测试先写:改 M 一字节、换盐、错 Image ID、dev-mode fake receipt 交给 production verifier——全部必须失败。

**验收**:`cargo test` + 一条脚本演示"有效证明通过、五种篡改全部失败"。

### Phase 2 = SPEC M1:MIDI Profile + ReferenceSynth(1–2 周,最大工作量)

1. **严格 parser**(reference-core):SPEC §8 全部规则 fail-closed;§17.2 的拒绝测试逐条对应。
2. **整数合成器**:u32 相位累加、2048 点 i16 波表、Q15 包络、8 voice slot 抢占规则(SPEC §9.4)——**全部整数运算,禁止 float**;除法向零截断的公式写进代码注释和 golden vector 文档。
3. **波表冻结**:写一次性生成脚本产出 `wavetable-v1.bin`,从此只读其字节 + SHA-256,不再改。
4. **流式摘要**:guest 不存完整 WAV,按 SPEC §9.6 边生成边喂 SHA-256;60 秒 WAV 约 960 KB,配合 RISC Zero 的 SHA-256 加速,这个量级是安全的。
5. **Golden vectors**(SPEC §17.1 六个向量):native 渲染、guest 执行、Python 重算三方逐字节一致,进 CI。
6. **基准门 B1/B2**:15 秒 / 30 秒、4 voices,按 SPEC §18 表格逐项记录机器、耗时、峰值 RSS、receipt 大小。

**验收/砍范围线**:30 秒工作负载 ≤ 60 分钟出真实证明。若 8 GB 机器不行:降 segment size limit;再不行:缩到 30 秒时长或降采样率——注意这会产生**新 protocol_id**,要在 manifest 里如实反映。16 GB 机器通过也算概念成立,但"8 GB 未达成"写进公开文档。

### Phase 3 = SPEC M2:身份、日志、服务端(1–2 周)

1. **Ed25519 创作者身份**:`identity init` 生成密钥,私钥落 `creator-secret/`(目录原子创建、已存在即停)。
2. **RFC 8785 JCS**:Python 侧没有标准库,选一个经过交叉测试的实现,并用小样本与 Rust `serde_jcs` 对拍;签名体保持小而简单(事件 body 只有几个字段),降低规范化踩坑面。
3. **FastAPI 服务端**:三类事件接口、`creator_pubkey+client_nonce` 去重、顺序校验 `COMMIT.seq < RELEASE.seq < PROOF.seq`、上传前服务端本地跑 verifier(SPEC §11.1)。
4. **透明日志**:RFC 6962 域分离 Merkle、Signed Tree Head 签名、inclusion proof;提交回执 = 事件 + STH + 包含证明。
5. **字段黑名单**:服务端拒绝名为 `midi`/`salt`/`private_key` 的字段(SPEC §11.1)。
6. SQLite 两阶段发布:先落文件再提交日志,避免悬空引用。

### Phase 4 = SPEC M3:展品体验(约 1 周)

1. 普通页:结论 → 三条"密码学已证明" → S/V 双播放器(不默认同步) → **默认展开的"不能证明"声明**(文案逐字取 PRD §1/§11)。
2. 技术页:公钥、承诺、Image ID、日志根、下载链接。
3. `music-zk verify public-evidence/`:SPEC §15 的 11 步逐项输出,不合并成单一布尔。
4. 五个篡改实验的演示脚本(`music-zk demo tamper --case ...`)。

### Phase 5 = SPEC M4:审查收尾(几天)

- 隐私扫描:全库 grep 私密字节、断网 proving 测试、崩溃残留检查(SPEC §17.4)。
- 可复现构建:CI 干净环境双构建,两次 Image ID 一致;README 给出源码算 Image ID 的命令。
- 性能报告、`ENV.md`、演示脚本定稿。

## 4. 并行线

Phase 1 通过后,C 线(日志/签名/API)不依赖证明性能,可以和 Phase 2 的合成器并行做;Web 页面(Phase 4)可以再往后并行,但**文案不许提前上线上页**。单人开发就按串行走,顺序即优先级。

## 5. 关键风险与对策(浓缩)

| 风险 | 对策 |
|---|---|
| 8 GB 内存/30 分钟证明超预算 | Phase 0/2 两道基准门;segment limit 可调;范围可砍(SPEC 允许) |
| Windows 原生踩坑 | 一切 Rust/zkVM 工作进 WSL2,不试原生 |
| 合成器跨平台不一致 | 单一 reference-core、纯整数、golden vectors 三方对拍 |
| guest 内存爆(全 WAV 常驻) | 强制流式 SHA-256;60s 音频仅 ~1 MB,量级安全 |
| JCS 实现坑 | 签名体字段极简;Python/Rust 交叉测试 |
| 证明含义被夸大 | 文案来自 PRD 固定字符串,代码里作为常量渲染,不自由发挥 |
| RISC Zero 版本漂移/安全公告 | 全链路锁版本;公告触发新 manifest,不悄悄换 Image ID |

## 6. Windows 兼容策略

结论:可以兼容,但要按角色分层。"生成证明"是唯一真正依赖 Linux 的环节;WSL2 本身是 Windows 第一方组件,所以 v1 的官方口径是——**prove 环节运行在 WSL2 内,其余全部原生 Windows**。原生 Windows prover 只做一次性实验,用实测数据回答 PRD §16 的开放项,不作为 v1 承诺。

### 6.1 角色 × Windows 需求矩阵

| 角色 | Windows 兼容方式 |
|---|---|
| 普通质疑者 | 浏览器 + 证据包,零依赖,天然兼容 |
| 技术质疑者 | 原生 Windows `verify.exe`:risc0 的 verify 路径是纯 Rust,交叉编译 `x86_64-pc-windows-msvc` 应可行,需在 CI 实测确认 |
| 展示者/创作者(非 prove 环节) | 原生 Python 3.12 venv:CLI、FastAPI、SQLite 日志、网页全部跨平台 |
| 创作者(prove 环节) | WSL2 内运行 Rust prover;Windows CLI 自动检测并透明委托 |

### 6.2 四项落地工作

1. **`scripts/setup-wsl.ps1`**:一键引导——检测/安装 WSL2 + Ubuntu、rustup、rzup,构建 guest,把实测版本号写入 `docs/ENV.md`。
2. **CLI 透明委托**:`music-zk prove` 在 Windows 上检测 `wsl.exe`,用 `wslpath` 转换路径,经 `wsl -e` 调用 WSL 内的 prover。秘密文件始终留在本机磁盘,经 WSL 互操作文件系统(`/mnt/c`)读取,不经过任何网络,与"proving 断网"的隐私要求一致。用户感知就是一个原生 Windows CLI。
3. **原生 Windows 验证器**:verifier CLI 以 verify-only feature 编译 Windows 目标,进 GitHub Actions `windows-latest` CI;同一 runner 上跑 `reference-core` golden vectors,顺带证明合成输出跨平台逐字节一致(整数运算 + 共享 Rust core 本身就保证了这一点,CI 只是把它钉死)。
4. **原生 Windows prover 实验(升级为正式路径,timebox 1–2 天)**:在 `windows-latest` 上尝试编译 risc0-zkvm 的 prove feature。前置改造:**guest ELF 预构建入库**(`protocol/guest-v1.elf`,Image ID 写入 manifest,CI 在 Linux 重建并核对)——这样 prove 端只加载 ELF 文件,Windows 机器完全不需要 rzup/客户工具链。成功 → Windows 无需 WSL 即可证明;失败 → 降级路径见 §6.4。

### 6.4 绕开 WSL 的降级路径(老电脑 / 无法启用虚拟化)

前提判断:老电脑的真实瓶颈是**证明资源**(RAM、CPU),不是操作系统本身。资源阶梯:

| 任务 | 资源需求 | 老电脑可行性 |
|---|---|---|
| 浏览网页、播放 S/V | 极低 | 无条件可行 |
| 离线验证证据包 | <10 秒、低内存 | 几乎任何 x64 电脑可行 |
| 本地生成真实证明 | 8–16 GB RAM + 现代 CPU | 取决于硬件,与 OS 无关 |

在此前提下,无法启用 WSL 的机器按序走:

1. **原生 Windows prover**(§6.2 第 4 项成功时):不需要任何虚拟化,瓶颈只剩内存;内存不足就按 SPEC §18 缩短音频(5–15 秒)或调低 segment size limit。
2. **Linux Live USB 兜底**(不依赖虚拟化、不装系统、不动硬盘):把 prover 以静态链接 Linux 二进制发布,创作者从 U 盘启动 Ubuntu live,完全离线生成证明后拷回结果。比 WSL2 兼容面更宽(只需 64 位 CPU,不需要 VT-x、不需要 Win10 1903+),且天然满足"proving 断网"。配套 `docs/LIVE-USB.md` + 构建脚本。
3. **证明/验证分离的演示模式**:证明是**一次性的公开数据**——receipt、journal、证据包都可以在任何一台有能力的机器上生成,然后搬运到老电脑上做展示。老电脑只跑原生验证器(秒级、低内存),密码学强度不打折,完全符合产品语义(验证者本来就不需要证明能力)。

明确不可行 / 不做的路:
- 远程 prover 兜底:PRD §13.2 隐私红线,远程看得见 witness。
- TEE(SGX/TDX)或 MPC 证明:超出 v1 威胁模型,列为未来方向。
- QEMU 纯软件模拟(无 VT-x 跑 Linux):可行但慢一个数量级,30 秒工作负载不现实,不提供。
- 为兼容性更换 zk 技术栈(纯 Rust 电路如 halo2/Winterfell 需手写电路,等于重写)。

### 6.3 边界与不做的事

- WSL1 不可用,必须 WSL2(工具链与文件系统语义)。
- 私密目录权限:Windows 没有 POSIX 权限位,用用户目录 + ACL(`icacls` 或 API)近似 SPEC §10.1 的"仅当前用户可读",README 说明差异。
- **禁止**用远程 prover 给 Windows 用户兜底——PRD §13.2 隐私红线,远程 prover 看得见 witness。
- 不为 Windows 换技术栈:SP1 无零知识性,手写 halo2 电路等价于推翻重来,均非 v1 选项。

## 7. 下一步(具体到命令)

```bash
# 1. WSL2 内
wsl -d Ubuntu
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install cargo-risczero && rzup install   # 记录版本号进 docs/ENV.md

# 2. 建工作区骨架(本仓库)
mkdir -p music_zk/{cli,server,protocol,verifier,web} rust/{reference-core,reference-native,zkvm-guest,zkvm-host} protocol/golden-vectors examples/twinkle-v1 tests

# 3. 跑 Phase 0 hello-guest,记录数字,决定继续
```

工作量粗估(单人):Phase 0–1 约 1.5 周,Phase 2 约 2 周,Phase 3 约 2 周,Phase 4–5 约 1.5 周,合计 6–7 周;其中 Phase 0 结束即可知道项目是否成立。
