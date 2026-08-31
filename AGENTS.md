# AGENTS.md — Music-ZK Exhibit 执行手册

> 本文件是唯一执行入口。任何 AI agent(或人)拿到本仓库后:**读本文 → 从 §9 的第一步开始干**。
> 冲突裁决:协议语义以 `SPEC.md` 为准,产品语义以 `PRD.md` 为准,执行顺序以 `docs/PLAN.md` 为准。若本文与上游文档冲突,以上游为准,把冲突记入 `docs/OPEN-QUESTIONS.md` 并向用户报告,**不要自行发明协议参数**。

## 0. 你要造什么(60 秒理解)

一个本地运行的密码学概念展品。创作者本地持有一份私有 MIDI `M` 和随机盐 `r`:

- **t0**:本地计算承诺 `C_M`,签名后提交给 Demo 服务端(只上传统诺,不上传 M、r)。
- **t1**:公开歌曲 `S`(服务端记录摘要与时间)。
- **t2**:本地用 RISC Zero zkVM 生成真实零知识证明:存在 `M,r`,满足 MIDI Profile 1、打开 `C_M`、且经 ReferenceSynth 1 渲染出的 WAV 摘要等于公开的 `C_V`。公开参考音频 `V` 和 proof。

公开可验证"结构化音乐材料的预先持有",**不**证明原创、非 AI、版权。技术栈:Python 3.12(CLI/FastAPI/展示页)+ Rust(reference-core、zkVM guest/host)+ SQLite + Ed25519 + SHA-256 + RFC 8785 JCS + RFC 6962 风格 Merkle。

## 1. 红线(任何任务、任何阶段都不可违反)

1. **M、r、私钥只存在于本地 `creator-secret/`**。服务端代码、公开证据包、日志、测试夹具中永不出现;服务端必须拒绝请求中名为 `midi`、`salt`、`private_key` 的字段。
2. **只用真实证明**。生产/展示 verifier 必须禁用 dev mode(`RISC0_DEV_MODE=0` / `disable-dev-mode` feature);dev-mode 收据只能作为负向测试素材,页面标记 `DEV_ONLY` + 红色"不是密码学证明"。
3. **禁止远程 proving**。远程 prover 看得见 witness。
4. **页面文案是常量,不是自由写作**(见 §3.7),禁止出现"原创已验证""非 AI(已认证)"及任何意思等价的徽章;"本系统不能证明"区块默认展开;`S/V 相似性`永远显示"未由系统判断"。
5. **版本全锁**:RISC Zero 版本、Rust toolchain、Cargo.lock、guest Image ID 固定后不得自动升级;影响协议行为的变化必须产生新 `protocol_id`。
6. **禁止 git add -f** 绕过 `.gitignore`;私密文件(密钥、盐、MIDI)永不入库。
7. **阶段门禁**(§5)不过不许进下一阶段;基准门不过就缩范围并公开记录,不许用假收据糊弄。

## 2. 本机环境事实(2026-08-31 实测)

- Windows(build 10.0.28120),Git Bash;git 2.55.0
- 系统全局 Python 3.14.6(**不使用**)← 项目环境用 conda:`conda create -n music-zk python=3.12 -y`;每次开工先 `conda activate music-zk`,所有 Python 工作(CLI/server/verifier/tests)都在该环境内
- Rust 1.97.1 / cargo 1.97.1(WSL 内需另装)
- WSL 可用 ← Rust/zkVM 全部工作在 WSL2 内进行
- 仓库已存在:`main` 分支,仅文档;`.gitignore` 已覆盖私密与构建产物

## 3. 冻结常量(直接抄进代码,一个字符都不许改)

### 3.1 protocol_id

```text
music-zk-exhibit/midi-profile-1/reference-synth-1/statement-1
```

### 3.2 哈希 framing(SPEC §7;`||` 为字节拼接,U64BE 为 8 字节大端长度)

```text
CommitMidi(M, r)        = SHA256( "MUSIC-ZK\0MIDI-COMMIT\0V1\0" || U64BE(len(M)) || M || r )   # r 恰 32 字节
CommitReferenceWav(V)   = SHA256( "MUSIC-ZK\0REF-WAV\0V1\0"     || U64BE(len(V)) || V )
CommitSong(S)           = SHA256( "MUSIC-ZK\0SONG\0V1\0"        || U64BE(len(S)) || S )
```

ASCII 字面量中的 `\0` 是真实的 0x00 字节。v1 承诺**原始文件字节**,不做 MIDI 规范化。

### 3.3 Journal 二进制布局(SPEC §6.4,总长固定 202 字节)

```text
offset  size  字段
0       8     magic = "MZKJNL01"
8       2     statement_version_u16 = 1 (big-endian)
10      32    protocol_hash = SHA256(UTF8(protocol_id))
42      32    creator_pubkey
74      32    commit_event_id
106     32    release_event_id
138     32    C_M
170     32    C_V
202           ← 结束;拒绝尾随字节、未知版本、长度不符
```

### 3.4 MIDI Profile 1(SPEC §8;解析器 fail-closed,一切未列出的都拒绝)

容器:Standard MIDI File **Format 0**;恰好一个 `MThd`(长度 6,format=0,ntrks=1)+ 一个 `MTrk`;division=480 PPQ(禁 SMPTE);文件 ≤64 KiB;track 声明长度与实际严格一致,无尾随字节;禁 running status;VLQ ≤4 字节且最短编码。

允许的事件(全部 channel 0):Note On(velocity 1..127);Note Off(release velocity 忽略);velocity=0 的 Note On 视为 Note Off;Set Tempo 恰好一次且在 tick 0、值 500000;Time Signature 可选零或一次、须在 tick 0、严格 4/4 / 24 / 8;End of Track 恰好一次且为最后事件。其余(Program Change、CC、弯音、Aftertouch、SysEx、其他 meta……)全部拒绝。

音乐限制:note number 21..108;≥1 且 ≤256 个 Note On;同一音高未 Off 不得再 On;Note Off 必须匹配活动音符,EOT 时无悬挂;每音 ≥1 tick;同时活动 ≤4;tick 单调不减;最后 Note Off 的 tick ≤ 57600(即 ≤60 秒)。

### 3.5 ReferenceSynth 1(SPEC §9;纯整数,禁浮点)

- 输出 Canonical WAV 1:RIFF/WAVE 44 字节标准头,无额外 chunk;单声道 8000 Hz、16-bit signed PCM LE;长度 = sample 0 到最后 Note Off 后 120 ms(960 samples)。
- tick→sample:`sample_index = floor(tick * 8000 * 500000 / (480 * 1000000))`,用足够宽的无符号整数,防溢出;同 sample 事件按文件顺序先生效。
- 波表:`protocol/wavetable-v1.bin`,2048 个 LE i16,冻结后只认字节与 SHA-256;每 note 21..108 对应冻结 `u32 phase_step`(协议常量);相位累加 u32 wrapping,Note On 时归零,索引取相位最高 11 位;无插值/滤波/LFO/随机。
- 包络(Q15,32767=满幅):Attack 40 ms=320 samples 从 0 线性升到 32767;Sustain 保持;Release 120 ms=960 samples 线性降到 0;除法向零截断,精确公式写进 `reference-core` 注释与 golden vector 文档。
- voices:内部固定 8 个 slot(容纳 release 尾音);Note On 取最小编号空闲 slot;全占用则抢占 release 中**当前包络值最小**的 slot(并列取编号最小);Attack/Sustain 活动音不可抢占;被抢占尾音立即截止,新音 phase/包络从零开始。
- 混音:每 voice 峰值 3500 × Q15 包络;i32 累加,最终 clamp 到 [-32768, 32767];运算顺序与截断点用显式整数除法表达。
- guest 内**流式**喂 SHA-256(SPEC §9.6),不保存完整 WAV。

### 3.6 签名与日志(SPEC §10–11)

- 创作者:Ed25519;`signature = Ed25519.Sign(sk, ASCII("MUSIC-ZK\0CREATOR-EVENT\0V1\0") || JCS(event_body))`;body 含 16 字节随机 `client_nonce`;服务端按 `(creator_pubkey, client_nonce)` 去重。
- 事件:`COMMIT`(C_M, key, protocol_id)→ `RELEASE`(引用 COMMIT event_id,C_S,歌曲文件信息)→ `PROOF`(引用两者,C_V、journal hash、receipt hash、V hash、manifest hash)。顺序严格 `COMMIT.seq < RELEASE.seq < PROOF.seq`;接受 PROOF 前服务端本地跑标准 verifier 成功。
- `event_id = SHA256( ASCII("MUSIC-ZK\0LOG-EVENT\0V1\0") || JCS(accepted_event_without_server_fields) )`。
- Merkle:叶 `SHA256(0x00 || JCS(server_event_record))`,节点 `SHA256(0x01 || L || R)`;Signed Tree Head 含 tree_size、tree_root、issued_at_utc、previous_tree_size、previous_tree_root,服务端 Ed25519 签名;回执 = 事件 + STH + 该叶 inclusion proof。
- 上传限制:S ≤20 MB,V ≤2 MB,bundle ≤20 MB,JSON body ≤256 KiB;先写临时目录,失败即删;文件名不可信,按 event_id 存储路径;SQLite 与文件发布两阶段。

### 3.7 状态机与文案常量(SPEC §16,PRD §1/§11)

状态:`COMMITTED` / `RELEASED_UNPROVEN` / `PROOF_VALID` / `PROOF_INVALID` / `DEV_ONLY`。

文案常量(集中放在一个模块如 `music_zk/web/copy.py`,页面只引用常量):

```text
RESULT_TITLE  = "结构化音乐材料的预先持有证明有效"
LIMITATION    = "本证明不判断公开歌曲 S 与参考音频 V 是否相似;不证明 MIDI 的原创性、获得方式、完整 DAW 工程的存在、版权归属或创作者未使用 SUNO 等生成式工具;也不排除创作者在发布前根据生成音频或其他来源扒谱制作 MIDI。S 与 V 的音乐对应关系由听众自行判断。"
SIMILARITY    = "S/V similarity: not evaluated by this system"(单列,固定输出)
DEV_WARNING   = "不是密码学证明"(红色,DEV_ONLY 状态)
NOT_PROVEN_HEADER = "本系统不能证明"(默认展开,不藏 tooltip/页脚)
```

首屏顺序:结论 → 密码学已证明(≤3 条)→ S/V 双播放器(不默认同步)→ 不能证明(展开)→ 时间线 → 技术细节。密码学检查任一失败不得显示总体有效。

## 4. 仓库目标结构(SPEC §4)

```text
pyproject.toml
music_zk/{cli,server,protocol,verifier,web}
rust/  Cargo.toml
  reference-core/      # MIDI 语义 + 合成 + hash framing 的唯一实现,native 与 guest 共用
  reference-native/    # 出真实 V 文件 + golden vector 工具
  zkvm-guest/          # 被证明的程序
  zkvm-host/           # execute/prove/verify 包装
protocol/  v1.json  wavetable-v1.bin  guest-v1.elf  golden-vectors/
examples/twinkle-v1/
tests/
docs/  PLAN.md  ENV.md  benchmarks.md  OPEN-QUESTIONS.md
scripts/
```

## 5. 执行阶段(严格按序;每阶段末有门禁)

### Phase 0:冒烟(WSL2,go/no-go,几天)

- [ ] WSL2 内装 rustup 与 RISC Zero 工具链(以官方文档为准),实际版本记入 `docs/ENV.md`
- [ ] hello-guest:env-io 读字节 `x`,journal 输出 `SHA256(x)`;host 真实 prove(禁 dev mode)→ 独立 verify 通过
- [ ] 记录耗时/峰值内存/receipt 大小 → `docs/benchmarks.md`

**门禁**:真实 receipt + production verifier 成功。失败 = 项目形态被否决,立即公开结论并停。

### Phase 1 = SPEC M0:关系最小闭环

- [ ] `reference-core`:§3.2 三个 framing + 字节级单测
- [ ] guest:读 `M,r` → 重算 `C_M` → 输出 §3.3 定长 journal(最终形态须含完整解析与 Profile 检查,Phase 2 补齐)
- [ ] `zkvm-host`:executor 统计 → prove → **独立 verifier 进程**复验
- [ ] Python verifier 骨架:验 receipt、journal、Image ID
- [ ] 负向测试:改 M 一字节 / 错盐 / 错 Image ID / dev-mode 收据 → 全部失败
- [ ] guest ELF 入库 `protocol/guest-v1.elf`,Image ID 写 manifest
- [ ] (timebox 2 天)Windows 原生 prove feature 编译实验 → 结论记 `docs/ENV.md`

**门禁**:一条脚本演示 1 正 4 负全部符合预期;`cargo test` + `pytest` 绿。

### Phase 2 = SPEC M1:MIDI Profile + ReferenceSynth(工作量最大)

- [ ] parser:§3.4 全量;SPEC §17.2 每条拒绝测试各一个 case
- [ ] 波表生成脚本 → 冻结 `wavetable-v1.bin` + SHA-256 入 manifest,从此只读字节
- [ ] 合成器:§3.5 全常量;native 渲染器输出真实 WAV
- [ ] guest 内:解析 + Profile 检查 + 合成 + 流式 SHA-256
- [ ] golden vectors ×6(SPEC §17.1):最短单音 / 四音和弦 / 四句小星星 / 同 tick Off-On / Attack 中提前 Off / 60s+尾音;每个含 MIDI SHA-256、盐、C_M、事件表、sample 数、头尾样本、完整 C_V;**native == guest == Python 三方逐字节一致**
- [ ] 基准 B1(15s/4v)、B2(30s/4v)入册

**门禁**:30 秒负载 ≤60 分钟真实证明。不达标 → 调低 segment size limit;再不达标 → 缩时长/采样率(**新 protocol_id**)并公开记录。

### Phase 3 = SPEC M2:身份、日志、服务端

- [ ] `identity init`:Ed25519,`creator-secret/` 原子创建、已存在即停、README-PRIVATE.txt
- [ ] JCS(RFC 8785):选定 Python 实现 + 与 Rust 侧对拍小样本
- [ ] FastAPI + SQLite:三事件端点、签名/引用/顺序校验、字段黑名单、大小限制、两阶段发布
- [ ] Merkle 日志 + STH 签名 + inclusion proof(SPEC §11.3)
- [ ] CLI:`commit create` / `song publish` / `prove`(§SPEC 13 六步流程)/ `proof publish`
- [ ] Windows 检测:无 WSL 时按 `docs/PLAN.md §6.4` 提示降级路径

**门禁**:示例 MIDI 从 CLI 完整走通 t0→t1→t2;用 curl 取 checkpoint 与 inclusion proof 独立验算通过。

### Phase 4 = SPEC M3:展品体验

- [ ] 结果页:首屏顺序、状态机、§3.7 文案常量逐字、S/V 播放器
- [ ] 技术详情页 + 公开证据包下载(SPEC §12.2 内容清单 + VERIFYING.md)
- [ ] `music-zk verify public-evidence/`:SPEC §15 十一步逐项输出(总体有效要求 2..10 全过);`reveal-check`、`demo tamper` 五案例
- [ ] 一键演示脚本(暂停讲解每步)

**门禁**:PRD §13.1 表述验收逐条过;红 7(文案)零违反。

### Phase 5 = SPEC M4:审查收尾

- [ ] 隐私扫描(SPEC §17.4:数据库/日志/zip 全 grep 私密字节;断网 proving;崩溃残留)
- [ ] CI:干净环境双构建 Image ID 一致;windows-latest 跑 verifier 构建 + golden vectors(+ 可选 prover 实验)
- [ ] 性能报告 B0–B3 全量入册;README 状态更新

**门禁**:PRD §13.2、§13.3 全过。

## 6. 测试基线(每阶段都要绿)

- Rust:`cargo test`(reference-core 单测 + guest/host 集成 + 负向)
- Python:`pytest`(framing 与 Rust 对拍、journal 编解码、日志、verifier 负向)
- 拒绝测试清单 = SPEC §17.2;密码学负向 = §17.3;隐私 = §17.4;可复现构建 = §17.5

## 7. Windows / 老电脑交付形态(不改变以上任何协议)

prove 走 WSL2(第一方组件)+ CLI 自动委托;其余全部原生 Windows;原生 prover 实验结果决定是否免除 WSL;WSL 不可用的机器走 `docs/PLAN.md §6.4`:Live USB 静态 prover 或"证明一次生成、证据包搬运、老电脑只验证"。验证器编译 Windows 原生目标进 CI。

## 8. 工作约定

- 一个任务一个 commit;消息中文祈使句,如 `feat(reference-core): 实现 CommitMidi framing`
- 环境版本 → `docs/ENV.md`;所有基准数字 → `docs/benchmarks.md`;冲突与未定义点 → `docs/OPEN-QUESTIONS.md`(不许静默自创参数)
- 估算可疑时先写 microbenchmark 再写功能(SPEC §18 精神)
- 文档路由:干活前读 SPEC 对应节;PRD 只在文案/验收争议时读;PLAN.md 管顺序与 Windows 策略

## 9. 当前状态与你的第一步

- 仓库:`main`,HEAD 含 PRD/SPEC/ZKP_EXPLAINED/README/PLAN/.gitignore,无任何代码
- 环境:§2 所列;conda env `music-zk`(Python 3.12.14)已创建;WSL2 内尚未安装 Rust/RISC Zero

**第一步(现在就做)**:

```bash
wsl -d Ubuntu -- bash -lc 'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y'
# 然后按 RISC Zero 官方文档安装 rzup 并安装工具链,把实际版本写入 docs/ENV.md
# 接着建 rust workspace 骨架(§4),开始 Phase 0 的 hello-guest
```

完成 Phase 0 门禁后向用户汇报实测数字,再继续 Phase 1。
