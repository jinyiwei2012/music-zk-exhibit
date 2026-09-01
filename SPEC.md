# Music-ZK Exhibit 技术规格

- 文档状态：Draft v0.1
- 日期：2026-08-30
- 对应 PRD：`PRD.md` Draft v0.1
- 规范关键字：`MUST`、`MUST NOT`、`SHOULD`、`MAY` 分别表示必须、禁止、建议和可选

## 1. 范围

本规格定义一个可本地运行的密码学概念展品。它证明私有原始 MIDI 与公开承诺、固定 ReferenceSynth 和公开参考 WAV 摘要之间的关系，并用假名创作者签名和受信任 Demo 日志表达提交、歌曲发布、证明发布的先后顺序。

本规格不定义音乐原创性、版权、AI 检测、`S/V` 相似度或完整 DAW 工程语义。

## 2. 技术选择

### 2.1 zkVM：RISC Zero

v1 选择 RISC Zero zkVM，实施时 MUST 固定到经过测试的完整版本、Rust toolchain、Cargo.lock 和 guest Image ID。截至本文调研日，最新稳定 release 为 `v3.0.6`；不得自动跟随 `main` 或未固定的 latest。

选择理由：

- guest 可使用普通 Rust 实现解析和整数合成，不需要手写大规模算术电路。
- 私有输入由 guest 读取，只有主动 commit 到 journal 的结果成为公开输出。
- receipt 验证同时绑定执行结果和 guest `ImageID`，便于公众确认“证明的是哪段代码”。
- prover 和 verifier 开源，仓库采用 MIT/Apache-2.0 双许可。
- 官方明确把本地 proving 作为处理私有输入的路径，并说明内存少于 10 GB 时可调整 segment size limit。
- STARK 路径可避免本展品引入 Groth16 的可信设置。

参考：[zkVM Overview](https://dev.risczero.com/api/zkvm/)、[Guest Code 101](https://dev.risczero.com/api/zkvm/guest-code-101)、[Receipts 101](https://dev.risczero.com/api/zkvm/receipts)、[Local Proving](https://dev.risczero.com/api/generating-proofs/local-proving)、[RISC Zero repository](https://github.com/risc0/risc0)。

### 2.2 未选择 SP1 的原因

SP1 同样支持 Rust/RISC-V 和开源 prover/verifier，但其当前安全模型明确说明 individual STARK proofs 不具备零知识性质，零知识由 Groth16 或 PLONK 包装提供；其官方本地硬件表对 Core/Compress/Groth16 至少建议 16 GB，PLONK 建议 64 GB。由于隐藏 MIDI 是本项目的核心要求，v1 不选择 SP1。

参考：[SP1 Security Model](https://docs.succinct.xyz/docs/sp1/security/security-model)、[SP1 Hardware Requirements](https://docs.succinct.xyz/docs/sp1/getting-started/hardware-requirements)。

### 2.3 应用栈

- Python 3.12：CLI、FastAPI 服务、SQLite 日志、展示页编排和测试工具。
- Rust：共享 `reference-core`、zkVM guest、host/prover/verifier 二进制。
- SQLite：本地 Demo 数据，不作为不可篡改存储本身。
- Ed25519：创作者事件签名和服务端回执/检查点签名，遵循 [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032.html)。
- SHA-256：MIDI、WAV、歌曲、事件和证据文件摘要。
- RFC 8785 JCS：JSON 签名体规范化，见 [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html)。
- RFC 6962 风格 Merkle Tree：叶、内部节点域分离和包含证明，见 [RFC 6962](https://www.rfc-editor.org/rfc/rfc6962.html)。
- HTML + 少量原生 JavaScript：展示页；不需要前端框架。

RISC Zero 官方预构建工具当前面向 x86-64 Linux 和 arm64 macOS。Windows 开发机 SHOULD 使用 WSL2/Linux 环境，并把该要求写入 README；不得宣称原生 Windows 已支持，除非实际验证。

## 3. 架构与信任边界

```text
creator-secret/               Public demo server              Skeptical verifier
M, r, creator private key     signed append-only log           public key + source
        |                              |                         |
        | C_M + creator signature      |                         |
        +----------------------------->| t0 commit receipt       |
        |                              |                         |
        | public S + signature         | t1 release              |
        +----------------------------->|------------------------>|
        |                              |                         |
        | local zkVM prove(M, r)       |                         |
        | V + receipt + signature      | t2 proof publication    |
        +----------------------------->|------------------------>|
                                       |                         |
                                       +---- public evidence --->|
```

信任边界：

- verifier MUST NOT 信任 creator CLI 的“成功”文字，只信任签名、日志证据、哈希和 zkVM receipt。
- Demo 服务端被信任为不倒签、不删除、不分叉日志的第三方。签名与 Merkle 结构使普通篡改可见，但在没有外部 witness/锚定时，不能阻止服务端秘密重写全量历史或向不同用户展示不同分支。
- zkVM verifier 信任固定的 RISC Zero 版本、验证代码、guest Image ID 和对应源码/构建映射。
- v1 MUST 使用本地 prover。远程 prover 能看到私有 witness，因而不属于本项目的隐私边界。

## 4. 仓库建议结构

```text
music-zk-exhibit/
  pyproject.toml
  music_zk/
    cli/
    server/
    protocol/
    verifier/
    web/
  rust/
    Cargo.toml
    reference-core/       # no_std-compatible parser + synth + hash framing
    reference-native/     # native V renderer and golden-vector tool
    zkvm-guest/           # proven program
    zkvm-host/            # local execute/prove/verify wrapper
  protocol/
    v1.json
    wavetable-v1.bin
    golden-vectors/
  examples/
    twinkle-v1/
  tests/
  docs/
```

`reference-core` MUST 是 MIDI 语义与合成算法的唯一实现来源，由 native renderer 与 zkVM guest 共同调用。Python 不得重新实现一份权威合成器。

## 5. 协议标识与版本治理

`protocol_id` 采用固定 ASCII：

```text
music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2
```

> `statement-1` = M0 guest(仅重算 C_M);`statement-2` = Phase 2 完整 guest(MIDI Profile 1 + ReferenceSynth 1),按本 § 版本治理于 2026-09-01 升级(Image ID `5e06801b...`)。旧 `statement-1` 值保留于 git 历史,verifier 不向后兼容(每次升级都按本 § 记录)。

协议 manifest MUST 至少包含：

- `protocol_id`
- MIDI Profile 参数
- synth 参数及 `wavetable_sha256`
- guest source commit
- Rust/RISC Zero 版本
- guest ELF SHA-256
- guest Image ID
- receipt 类型
- hash framing 版本
- canonical WAV 版本

任何会影响接受的 MIDI、PCM 字节、journal 或证明关系的变化 MUST 产生新 `protocol_id` 或新子版本。旧验证器 MUST 保留对已发布证据的验证能力。

## 6. 正式证明语句

### 6.1 私有 witness

- `M`: 原始 MIDI 文件全部字节。
- `r`: 恰好 32 字节，由操作系统 CSPRNG 生成。

guest 不需要创作者私钥。身份由先前签名提交事件和后续签名发布事件绑定；proof journal 固定该公钥和事件 ID，使复制 proof 不能改写为另一个身份。

这实现的是“同一公钥签署承诺并背书该 proof”的归属关系，不证明公钥控制者本人独立生成 proof，也不排除 witness、proof 或私钥共享。产品文案 MUST 使用“该公钥关联/签署/发布”，不得把签名误译为对自然人心理状态的证明。

### 6.2 公共上下文

- `protocol_id`
- `creator_pubkey`
- `commit_event_id`
- `release_event_id`
- `C_M`
- `C_V`
- `midi_profile_id`
- `reference_synth_id`

### 6.3 关系

guest MUST 完成以下操作，任一步失败则 execution panic/reject，不产生可接受 journal：

1. 严格解析 `M` 并验证 MIDI Profile 1。
2. 重新计算 `CommitMidi(M,r)`，断言等于公共上下文中的 `C_M`。
3. 以 ReferenceSynth 1 从 `M` 流式生成 Canonical WAV 1 字节。
4. 计算 `CommitReferenceWav(V)`，断言等于公共上下文中的 `C_V`。
5. 仅把 6.2 所列公共字段编码到 journal。

协议语义可写为：

```text
存在原始 MIDI 字节 M 和 32 字节盐 r，使得：
  M 满足 midi-profile-1
  CommitMidi(M, r) == C_M
  CommitReferenceWav(ReferenceSynth1(M)) == C_V
```

`S`、`C_S` 和任何 `Similarity(S,V)` MUST NOT 出现在关系中。`release_event_id` 只是把证明展示绑定到某次公开发布事件的上下文，不表示 guest 检查了歌曲内容。

### 6.4 Journal 编码

journal MUST 使用固定长度二进制结构，而不是 JSON，避免多种编码：

```text
magic[8]              = "MZKJNL01"
statement_version_u16 = 1, big-endian
protocol_hash[32]     = SHA256(UTF8(protocol_id))
creator_pubkey[32]
commit_event_id[32]
release_event_id[32]
C_M[32]
C_V[32]
```

总长度 MUST 固定。验证器 MUST 拒绝尾随字节、未知版本或字段长度不符。

## 7. 哈希与域分离

### 7.1 MIDI 承诺

```text
CommitMidi(M, r) = SHA256(
  ASCII("MUSIC-ZK\0MIDI-COMMIT\0V1\0") ||
  U64BE(len(M)) ||
  M ||
  r
)
```

`r` MUST 恰好 32 字节。域分离和长度前缀 MUST 保留。v1 承诺原始文件字节，不做 MIDI 规范化；重新保存、修改元数据或改变编码都会产生不同承诺。

### 7.2 参考 WAV 承诺

```text
CommitReferenceWav(V) = SHA256(
  ASCII("MUSIC-ZK\0REF-WAV\0V1\0") ||
  U64BE(len(V)) ||
  V
)
```

### 7.3 发布歌曲摘要

```text
CommitSong(S) = SHA256(
  ASCII("MUSIC-ZK\0SONG\0V1\0") ||
  U64BE(len(S)) ||
  S
)
```

`CommitSong` 只保证服务器公开的 `S` 字节与发布事件一致，不参与零知识关系。

## 8. MIDI Profile 1

### 8.1 容器限制

- Standard MIDI File Format 0。
- 恰好一个 `MThd` 和一个 `MTrk` chunk。
- `MThd` 长度恰好为 6，format 恰好为 0，track count 恰好为 1。
- division 恰好为 480 PPQ；SMPTE division 禁止。
- 原始文件最大 64 KiB，track 声明长度必须与实际字节严格一致，无尾随 chunk 或字节。
- 不支持 running status；每个 channel event 必须显式携带 status byte。
- delta time 使用标准 VLQ，最多 4 字节且必须是最短编码。

### 8.2 允许事件

- `Note On`，channel 0，velocity 1..127。
- `Note Off`，channel 0；release velocity 被忽略。
- velocity 0 的 `Note On` 作为 `Note Off`。
- `Set Tempo`：必须在 absolute tick 0 恰好出现一次，值必须为 500000 微秒/四分音符，即 120 BPM。
- `Time Signature`：可在 tick 0 出现零次或一次；若出现必须严格为 4/4、24 MIDI clocks/拍、每四分音符 8 个三十二分音符。
- `End of Track`：恰好一次且为最后事件。

所有其他 channel、meta、system common、realtime 和 SysEx 事件 MUST 被拒绝，包括 Program Change、CC、踏板、弯音、Aftertouch、歌词和轨道名。

### 8.3 音乐限制

- MIDI note number 范围 21..108。
- 至少一个音符，最多 256 个 Note On。
- 同一音高不得在尚未 Note Off 时再次 Note On。
- Note Off 必须匹配活动音符；EOT 前不得存在悬挂音符。
- 每个音符持续至少 1 tick。
- 同时活动音符数最多 4。
- 事件 absolute tick 必须单调不减；同 tick 事件按文件顺序应用。
- 最后一个 Note Off 的时间不超过 60 秒，对应 tick 不超过 57600。

解析器 MUST fail closed。不得静默忽略未知事件、损坏长度、溢出、非最短 VLQ 或超范围值。

## 9. ReferenceSynth 1

### 9.1 输出

- Canonical WAV 1：RIFF/WAVE，44 字节标准头，无额外 chunk。
- 单声道、8000 Hz、16-bit signed PCM little-endian。
- WAV 长度精确覆盖从 sample 0 到最后 Note Off 后 120 ms release tail。
- 不写 LIST、fact、时间、软件名或其他元数据。

### 9.2 时间映射

固定 120 BPM、480 PPQ 下，事件 tick 到 sample index：

```text
sample_index = floor(tick * 8000 * 500000 / (480 * 1000000))
```

实现 MUST 使用足够宽的无符号整数并检查溢出，不使用浮点数。发生在同一 sample 的事件按 MIDI 文件顺序、在生成该 sample 前应用。

### 9.3 波表与频率

- 使用冻结的 `wavetable-v1.bin`，2048 个 little-endian `i16` 样本。
- 波表应为听感较柔和的周期音色，可在生成阶段使用有限谐波设计，但协议权威值是冻结后的整数表字节及其 manifest SHA-256。
- 每个 MIDI note 21..108 对应一个冻结的 `u32 phase_step`，作为协议常量。
- 每个 voice 使用 wrapping `u32` phase accumulator；表索引取 phase 的最高 11 位。
- Note On 时 phase 归零，保证结果不依赖之前的 voice slot 历史。
- 不做插值、滤波、LFO、混响或随机化。

### 9.4 极简包络

- Attack：40 ms，即 320 samples，从 0 线性上升到 Q15 的 32767。
- Sustain：Attack 后保持 32767，直至 Note Off。
- Release：120 ms，即 960 samples，从 Note Off 当时的包络值线性下降到 0。
- 所有除法采用向零截断；每个阶段的精确整数公式 MUST 写入 `reference-core` 注释和 golden vector 文档。
- MIDI Profile 中“最多 4 音”只计算尚未收到 Note Off 的活动音符。合成器内部固定使用 8 个 voice slot，以容纳上一组音符的 release 尾音。
- Note On 优先使用编号最小的空闲 slot。若 8 个 slot 均被占用，则 MUST 确定性地抢占 release 中当前包络值最小的 slot；并列时抢占编号最小者。仍在 Attack/Sustain 的活动 voice 不得被抢占，因为 Profile 已保证其数量不超过 4。
- 被抢占的 release tail 在该 sample 边界立即结束，新音符 phase 与 envelope 从零开始。该规则保证快速音符或四音和弦切换不会因尾音占槽而被解析器拒绝。

### 9.5 混音

- 每 voice 峰值缩放到 3500，再乘 Q15 包络。
- 最多 8 个内部 voices，理论和不超过 28000；其中同时处于 Attack/Sustain 的音乐音符仍不超过 4。使用 `i32` 累加并定义最终 clamp 到 `[-32768,32767]`。
- 运算顺序、截断点和 signed shift 语义 MUST 通过显式整数除法实现，避免编译器/平台差异。

### 9.6 流式摘要

guest SHOULD 先根据事件计算输出 sample 数和 WAV 头，再把 WAV 头与逐 sample PCM 字节流式送入 SHA-256，不在 guest 内保存完整 WAV。native renderer 使用同一 `reference-core` 生成实际 `V` 文件。

## 10. 创作者身份

### 10.1 密钥

- 首次运行生成 Ed25519 keypair。
- 私钥只存于 `creator-secret/creator-private-key`，文件权限尽可能限制为当前用户。
- 公钥以 32 字节和 lowercase hex 表示。
- 页面显示前 8 与后 8 个 hex 字符作为短指纹，但技术详情保留完整值。

公钥只表示同一假名控制主体。它不证明实名、版权、唯一作者或私钥从未共享。

### 10.2 事件签名

所有 creator event 的 `signature` 字段之外的 JSON body MUST 先按 RFC 8785 JCS 编码，再签署：

```text
creator_signature = Ed25519.Sign(
  creator_private_key,
  ASCII("MUSIC-ZK\0CREATOR-EVENT\0V1\0") || JCS(event_body)
)
```

事件 body 包含随机 16 字节 `client_nonce`、`creator_pubkey`、`event_type`、`protocol_id` 和事件 payload。服务端按 `creator_pubkey + client_nonce` 去重，防止网络重试产生重复事件。

## 11. 透明日志与时间戳

### 11.1 事件类型

`COMMIT`：`C_M`、creator key、protocol ID。

`RELEASE`：引用同一 creator 的 `COMMIT event_id`，包含 `C_S`、公开歌曲文件信息。

`PROOF`：引用 `COMMIT` 和 `RELEASE`，包含 `C_V`、journal hash、receipt hash、V hash、manifest hash。

服务端 MUST 验证：

- 三类事件的 creator signature。
- 引用事件存在且 creator key、protocol ID 一致。
- 顺序严格为 `COMMIT.seq < RELEASE.seq < PROOF.seq`。
- `PROOF` 上传前，服务端本地执行标准 verifier 成功。
- `V` 的 `C_V` 与 journal 一致。

服务端 MUST NOT 接收包含名为 `midi`、`salt`、`private_key` 或 witness blob 的字段；这不是完整防泄漏机制，但能阻止正常 API 误传。

### 11.2 事件 ID

```text
event_id = SHA256(
  ASCII("MUSIC-ZK\0LOG-EVENT\0V1\0") || JCS(accepted_event_without_server_fields)
)
```

服务端附加：`sequence`、`received_at_utc`、`event_id`、`tree_size`、`tree_root`。

### 11.3 Merkle 日志

采用 RFC 6962 风格域分离：

```text
leaf_hash = SHA256(0x00 || JCS(server_event_record))
node_hash = SHA256(0x01 || left_hash || right_hash)
```

每次 append 后服务端生成 Signed Tree Head：

```text
tree_size
tree_root
issued_at_utc
previous_tree_size
previous_tree_root
```

再用服务端 Ed25519 key 签名。提交回执至少包含事件记录、Signed Tree Head 和该叶的 inclusion proof。

该结构检测普通数据库篡改，但在无外部 witness 时不能阻止服务端同时重写数据库、根和历史签名，也不能阻止 split view。该限制 MUST 出现在技术页面。

## 12. 文件包

### 12.1 私密证据包

```text
creator-secret/
  original.mid
  salt.bin
  creator-private-key
  creator-public-key.txt
  commit-receipt.json
  protocol-manifest.json
  README-PRIVATE.txt
```

- CLI MUST 原子地创建该目录，若目标已存在则停止，不覆盖。
- `README-PRIVATE.txt` MUST 提醒：前三个文件不得公开；MIDI 或盐丢失将无法生成旧承诺的证明；私钥丢失将无法延续身份。
- 服务端和公开包 MUST NOT 包含 `creator-secret/` 中的前三项。

### 12.2 公开证据包

```text
public-evidence/
  claim.json
  protocol-manifest.json
  creator-public-key.txt
  commit-receipt.json
  release-receipt.json
  proof-receipt.json
  journal.bin
  zkvm-receipt.bin
  song-S.<original-extension>
  reference-V.wav
  checksums.sha256
  VERIFYING.md
```

`checksums.sha256` 方便传输完整性检查，但不替代签名或 zk proof。公开包 SHOULD 可完全离线验证，唯一外部信任材料是用户主动选择的 server public key 与已知 guest Image ID。

## 13. CLI

建议命令：

```text
music-zk identity init
music-zk midi preflight path.mid
music-zk commit create path.mid --server URL --out creator-secret
music-zk song publish song.wav --secret creator-secret --server URL
music-zk prove --secret creator-secret --release EVENT_ID --out proof-work
music-zk proof publish --work proof-work --server URL
music-zk verify public-evidence/
music-zk reveal-check original.mid salt.bin commit-receipt.json
music-zk demo tamper --case midi-byte|wav-sample|salt|log-receipt|event-order
```

`prove` MUST：

1. 再次校验当前 `original.mid + salt` 打开 `C_M`。
2. 运行 native ReferenceSynth 生成 `V`。
3. 先执行 zkVM executor 获取 cycle/segment 统计。
4. 明确以 `RISC0_DEV_MODE=0` 或生产构建禁用 dev mode，生成真实 receipt。
5. 用独立 verifier 进程立即验证 receipt、journal、Image ID 和 `V`。
6. 验证成功后才创建可上传目录。

生产/展示 verifier binary MUST 使用 RISC Zero 的 `disable-dev-mode` feature。根据官方说明，dev mode 会产生 fake receipt，只有同样启用 dev mode 的 verifier 才会放行；本项目不得接受这种结果。

## 14. HTTP API

最小 API：

- `POST /api/v1/commit-events`
- `POST /api/v1/release-events`，multipart 上传公开 `S`
- `POST /api/v1/proof-events`，multipart 上传 `V`、receipt、journal、manifest
- `GET /api/v1/claims/{claim_id}`
- `GET /api/v1/claims/{claim_id}/evidence.zip`
- `GET /api/v1/log/checkpoint`
- `GET /api/v1/log/entries/{sequence}`
- `GET /api/v1/log/inclusion/{sequence}`

限制：

- `S` 最大 20 MB，`V` 最大 2 MB，proof bundle 最大 20 MB。
- JSON body 最大 256 KiB。
- 所有上传先写临时目录，验证失败即删除；不得保留失败请求中的私有可疑字段。
- 文件名不可信；服务端按 event ID 生成存储路径。
- SQLite 写入和文件发布使用明确的两阶段流程，避免日志引用不存在文件。

这是本地展品，不要求鉴权账号。身份验证仅靠每个事件的 Ed25519 signature。

## 15. 验证算法

标准 verifier MUST 输出逐项结果：

1. 验证 public evidence checksums，报告传输完整性。
2. 验证 server public key 是否为用户指定的信任根。
3. 验证三个 server receipts、Signed Tree Heads 和 inclusion proofs。
4. 验证三类 creator signatures，且公钥一致。
5. 验证 `COMMIT.seq < RELEASE.seq < PROOF.seq`。
6. 重算 `C_S`，确认公开 `S` 对应 release event。
7. 重算 `C_V`，确认公开 `V` 对应 proof journal。
8. 验证 manifest hash、guest Image ID 与允许值一致。
9. 使用 RISC Zero verifier 验证真实 receipt 与 Image ID。
10. 严格解析 journal，确认上下文、`C_M`、`C_V` 与事件完全一致。
11. 输出：密码学证明有效/无效；`S/V` 相似性固定输出“未判断”。

总体有效必须要求步骤 2..10 全部成功。步骤 1 失败表示包损坏；即使其中部分文件仍可单独通过，也不得显示总体有效。

## 16. 展示页状态模型

- `COMMITTED`：只有 t0；不能声称存在有效 MIDI witness，只能说承诺已被时间戳记录。
- `RELEASED_UNPROVEN`：已有 t0/t1，无有效 proof；不能声称证明完成。
- `PROOF_VALID`：完整链通过。
- `PROOF_INVALID`：证明、哈希、签名、日志或上下文至少一项失败。
- `DEV_ONLY`：执行结果或 fake receipt；必须明确“不是密码学证明”。

页面始终单列：`S/V similarity: not evaluated by this system`。

## 17. 测试

### 17.1 Golden vectors

至少冻结：

- 最短单音 MIDI。
- 四音和弦。
- 四句《小星星》示例。
- 同 tick Note Off/Note On 边界。
- Attack 中提前 Note Off。
- 最大 60 秒与 release tail。

每个 vector 包含原始 MIDI SHA-256、盐、`C_M`、事件列表、WAV sample 数、前后样本片段、完整 `C_V`。native Rust、guest execution 和 Python verifier 结果必须一致。

### 17.2 拒绝测试

覆盖 Format 1、多 track、错误 PPQ、running status、未知 meta/SysEx、非最短 VLQ、长度溢出、尾随字节、超过 4 voices、重复 Note On、悬挂音符、错误 Note Off、超过时长和非法 pitch。

### 17.3 密码学负向测试

- 修改 MIDI 任意一字节。
- 使用错误盐。
- 修改 WAV 一个 sample 或 WAV header 一字节。
- 修改 journal 任意字段。
- 使用错误 Image ID。
- 用 dev mode fake receipt 交给 production verifier。
- 替换 creator signature 或 server signature。
- 改变事件顺序、inclusion path 或 tree root。
- 把他人 proof 绑定到不同 creator pubkey。

### 17.4 隐私测试

- 搜索服务器数据库、公开目录、访问日志和证据 zip，不得出现 MIDI bytes、盐或私钥。
- proving 全程断网运行；成功不依赖云端。
- 崩溃后临时目录不含上传到服务端的 witness。

### 17.5 可复现构建

- 固定所有依赖和 toolchain。
- CI 从干净环境构建 guest，两次产物 Image ID 必须一致。
- README 提供从源码计算 Image ID 的命令。
- 任何 RISC Zero 安全公告升级都产生新的 manifest；不得悄悄替换旧 Image ID。

## 18. 性能基准门

实现顺序 MUST 先做 microbenchmark，再完成全部 UI：

| 阶段 | 工作负载 | 目的 |
|---|---|---|
| B0 | 5 秒、1 voice | 验证真实 receipt、Image ID、内存测量链 |
| B1 | 15 秒、最多 4 voices | 测 parser/synth/SHA cycles |
| B2 | 30 秒、最多 4 voices | 最低展品基线 |
| B3 | 60 秒、最多 4 voices | 目标上限 |

每项记录：CPU、RAM、OS、RISC Zero 版本、receipt 类型、segment limit、guest cycles、segments、墙钟时间、峰值 RSS、receipt 大小和验证时间。

目标与最低线沿用 PRD。RISC Zero 官方一般建议至少 16 GB，但对低于 10 GB 的环境建议降低 segment size limit。实现者 MAY 调整 segment limit 或先使用 composite receipt；MUST 保持真实零知识证明，MUST NOT 通过远程 proving 泄露 `M,r`，MUST NOT 把 dev execution 当成证明。

receipt 策略：

1. 首先基准透明的 composite STARK receipt，以降低可信设置和额外包装复杂度。
2. 若文件大小或验证时间不达目标，再基准 succinct STARK receipt。
3. v1 不使用 Groth16 receipt；未来若使用，必须更新威胁模型并显著披露可信设置。

## 19. 威胁模型

### 19.1 资产

- 私有 MIDI、盐、创作者私钥。
- 公开声明的正确语义。
- 时间顺序和日志完整性。
- ReferenceSynth 与 guest Image ID 的对应关系。

### 19.2 对手与控制

恶意创作者可能提交无效 MIDI、复制 proof、篡改 V 或夸大结论。严格 guest、上下文绑定、creator signature 和固定文案限制这些行为，但不阻止提前扒谱或共享 witness。

恶意质疑者可能篡改证据包或只展示部分失败结果。签名、哈希、完整公开包和逐项 verifier 使第三方能重验。

恶意 Demo 服务端可能倒签、删除或 split-view。v1 明确信任该服务端；Merkle 结构只提高可审计性。现实部署需要外部锚定或独立 witness。

供应链攻击可能替换 guest、compiler 或 verifier。固定版本、Image ID、源码重建、依赖锁和安全公告流程降低风险，但 POC 不声称生产级安全审计。

### 19.3 残余风险

- `V` 可能足以让人恢复 MIDI 结构。
- 零知识证明无法判断 witness 是如何获得的。
- 公钥控制者可以与他人共享 MIDI、盐或私钥。
- Demo 时间戳不等同于法律认可的时间戳。
- zkVM、编译器、ReferenceSynth 或 verifier 的实现缺陷可能破坏结论。
- 音乐对应关系仍是主观/专业判断。

## 20. 实施里程碑

1. **M0：关系最小闭环**——固定字节输入、承诺、真实 zkVM receipt、journal 和 verifier。
2. **M1：MIDI/Synth**——严格 parser、整数 wavetable synth、golden vectors、30 秒基准。
3. **M2：时间与身份**——Ed25519 creator、服务端日志、t0/t1/t2、公开包。
4. **M3：展品体验**——S/V 播放、边界文案、普通与技术视图、五个篡改实验。
5. **M4：证据审查**——隐私扫描、可复现 Image ID、真实 proof 复验、性能报告。

M0 或 M1 若无法在可接受资源内生成真实零知识证明，项目应先公开该结果并缩小音频时长/采样率，而不是继续包装 UI。
