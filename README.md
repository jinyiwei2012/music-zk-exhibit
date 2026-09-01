# Music-ZK Exhibit(非AI音乐的零知识证明 · 概念展品)

用 RISC Zero zkVM 证明:**在公开歌曲发布前,某创作者公钥已提交了一份私有 MIDI 的承诺,且该 MIDI 经固定的 ReferenceSynth 渲染后对应公开的参考音频**——但不泄露 MIDI 本身。

这不是"原创认证"或"非 AI 认证"。它只证明一个范围明确的窄声明,证明不了音乐的来源、动机或版权。详见 [ZKP_EXPLAINED.md](ZKP_EXPLAINED.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | **执行入口**:红线、冻结常量、阶段门禁、逐任务清单——AI agent 拿到仓库从这份开始 |
| [PRD.md](PRD.md) | 产品需求:目标、非目标、时间线、页面文案边界、验收标准 |
| [SPEC.md](SPEC.md) | 技术规格:协议、MIDI Profile、合成器、日志、API、测试与里程碑 |
| [ZKP_EXPLAINED.md](ZKP_EXPLAINED.md) | 通俗解释:零知识证明的动机、思想与能力边界 |
| [docs/PLAN.md](docs/PLAN.md) | 实施计划:落地顺序、Windows 兼容与降级路径、风险 |

## 状态

**Phase 1(SPEC M0 关系最小闭环)已完成 · 2026-09-01(Windows 原生门禁 1 正 4 负通过)**

- `reference-core`:三个哈希 framing + 202 字节 journal 编解码,Rust 与 Python 逐字节对拍一致
- zkVM guest 重算 `C_M` 输出 journal;host 真实证明 + 独立 verifier 复验(dev mode 编译期硬禁)
- 负向测试:改 M 一字节 / 错盐 / 错 Image ID / dev-mode 收据,全部拒绝
- **prove/verify 已迁移到 Windows 原生**(CPU 门禁绿,**CUDA 13.2 路径已验证:4.1 s**,约 27× 加速);guest 构建保留 WSL(R0BF 入库)
- protocol_id 已按 SPEC §5 升级:`music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2`
- 产物:`protocol/guest-v1.elf`(R0BF)+ `protocol/v1.json` manifest;基准入 `docs/benchmarks.md`

**进行中:Phase 2(SPEC M1)**——MIDI Profile 1 解析器 + ReferenceSynth 1 纯整数合成 + golden vectors ×6。

## 环境要求(已实测)

- **本地 proving 为 Windows 原生**(x86-64,MSVC + CUDA 13.2,驱动 ≥580);guest 构建仍需 WSL2(risc0 工具链无 Windows 二进制),但产物预构建入库,prove 端不依赖 WSL;详见 [docs/PLAN.md §6](docs/PLAN.md) 与 [docs/ENV.md](docs/ENV.md)
- Python 3.12(conda env `music-zk`);Windows Rust 稳定版(`stable-x86_64-pc-windows-msvc`)+ WSL risc0 toolchain 1.97.0(仅 guest)
- RISC Zero zkVM 3.0.6(版本已冻结,不得跟随 latest);版本事实表见 [docs/ENV.md](docs/ENV.md)
- 基准:B0 hello-guest(WSL)7.31 s / 604 MiB;M0(WSL)9.30 s / 606 MiB;M0-Win 原生 CPU 106–120 s;**M0-Win CUDA 4.1 s(~27× 加速)**;([docs/benchmarks.md](docs/benchmarks.md))
