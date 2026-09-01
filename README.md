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

**Phase 2(SPEC M1:MIDI Profile + ReferenceSynth)已完成 · 2026-09-01(门禁「30s 负载 ≤60 min」达标)**

- `reference-core`:MIDI Profile 1 解析器(fail-closed 全量拒绝测试)+ ReferenceSynth 1 纯整数合成(编译期波表、Q15 包络、8 voice 抢占)
- 冻结 `wavetable-v1.bin` + `phase_step` 表入 manifest;golden vectors ×6 native == guest == Python 三方逐字节一致
- zkVM guest(statement-2):解析 + Profile 检查 + 合成 + 流式 SHA-256,输出定长 202 B journal
- **真实负载基准(Win 原生 CUDA)**:B1(15s/4v)= 637 s、B2(30s/4v)= 1269 s ≈ 21.2 min、B3(60s/4v)= 2544 s;独立 verifier 全过
- **内存限制(蓝屏防护)**:`--segment-po2 18 --keccak-po2 18`(默认 po2=20 曾打爆 8GB 显存蓝屏;限制后峰值显存 ~4.3 GB、prover 内存 < 1 GB)
- 基准与限制详见 `docs/benchmarks.md` B1/B2/B3 节;构建产物在 `C:\music-zk-target\debug\`

**Phase 3(SPEC M2:身份、日志、服务端)已完成 · 2026-09-01(门禁:真实端到端 t0→t1→t2 通过)**

- `identity init`:Ed25519 keypair + `creator-secret/` 原子创建(已存在即停)+ README-PRIVATE.txt
- **RFC 8785 JCS**:Python(`jcs` 包)与 Rust `serde_jcs` 12 样本(三类事件体 + 对抗用例)逐字节对拍
- **FastAPI + SQLite 服务端**:三事件端点(签名/去重/引用顺序/字段黑名单 midi·salt·private_key/大小限制/两阶段发布);PROOF 上传前服务端本地跑标准 verifier
- **RFC 6962 Merkle 日志**:Google CT 官方向量锁定树根字节级正确;STH 服务端 Ed25519 签名 + inclusion proof
- **CLI 六步流程**:`identity init` / `commit create` / `song publish` / `prove`(真实 CUDA 证明 + 内存限制 + 降级提示)/ `proof publish`;另加 `server init/run`、`midi preflight`
- **门禁**:minimal-onenote 从 CLI 走通 t0→t1→t2(服务端本地 verifier 复验通过);checkpoint + inclusion proof 独立实现验算全过
- 产物:`music_zk/{protocol,server,cli}` 全层;zkvm-verify 支持无 witness 模式(服务端永无 midi/salt,红线 1)

**进行中:Phase 4(SPEC M3)**——结果页(§3.7 文案常量逐字)+ 技术详情页 + 公开证据包 + `music-zk verify`(SPEC §15 十一项)+ `reveal-check` / `demo tamper` 五案例 + 一键演示。

## 环境要求(已实测)

- **本地 proving 为 Windows 原生**(x86-64,MSVC + CUDA 13.2,驱动 ≥580);guest 构建仍需 WSL2(risc0 工具链无 Windows 二进制),但产物预构建入库,prove 端不依赖 WSL;详见 [docs/PLAN.md §6](docs/PLAN.md) 与 [docs/ENV.md](docs/ENV.md)
- Python 3.12(conda env `music-zk`);Windows Rust 稳定版(`stable-x86_64-pc-windows-msvc`)+ WSL risc0 toolchain 1.97.0(仅 guest)
- RISC Zero zkVM 3.0.6(版本已冻结,不得跟随 latest);版本事实表见 [docs/ENV.md](docs/ENV.md)
- 基准:B0 hello-guest(WSL)7.31 s / 604 MiB;M0(WSL)9.30 s / 606 MiB;M0-Win 原生 CPU 106–120 s;M0-Win CUDA 4.1 s;**Phase 2 B1/B2/B3 = 637 / 1269 / 2544 s**(CUDA,内存限制 po2=18,峰值显存 ~4.3 GB);([docs/benchmarks.md](docs/benchmarks.md))
