# OPEN-QUESTIONS.md — 协议冲突与未定义点

> 约定见 docs/AGENTS.md。每条 = `日期 | 出处 | 冲突或未定义点 | 建议 | 状态`。解决后更新状态,不删历史。

| 日期 | 出处 | 冲突或未定义点 | 建议 | 状态 |
|------|------|----------------|------|------|
| 2026-08-31 | SPEC §9.3 | `phase_step` 只定义为"每个 MIDI note 21..108 对应冻结的 u32 协议常量",未给出计算公式。这是影响 PCM 字节与 C_V 的协议参数,冻结后不可改。 | 采用标准 DDS 公式:`phase_step(n) = floor(freq(n) * 2^32 / 8000)`,`freq(n) = 440 * 2^((n-69)/12)`,对 n∈21..108 逐项整数计算(无浮点参与冻结值本身的计算结果以精确有理数定)。相位累加 wrapping u32、表索引取最高 11 位均与 SPEC §9.3 一致。 | **已确认(2026-08-31,用户采纳 DDS 公式)** |
| 2026-08-31 | SPEC §9.3 | `wavetable-v1.bin`(2048×LE i16)只要求"听感较柔和的周期音色,有限谐波设计",未冻结具体谐波成分。协议权威值是冻结后的字节及其 SHA-256。 | 建议基音正弦 + 第 2/3/4 次谐波按 1/2、1/3、1/4 幅值衰减叠加,归一化到满幅 i16(幅值裕度由混音阶段 3500/32768 缩放保证);表内不设直流分量。具体做法:对 k=0..2047,`s[k] = 32767 * Σ_{h=1..4} (sin(2π h k/2048) / h)`(用 64 位定点计算再四舍五入,冻结字节)。 | **已确认(2026-08-31,用户采纳基音+2~4次谐波 1/h 衰减)** |
| 2026-09-01 | SPEC §11/§15、risc0 3.0.6 | **image_id 字节序陷阱**:`receipt.verify(image_id)` 的 image_id 若传 `[u32; 8]`,走 `impl From<[u32;8]>`(word 直拷,内部字节序为小端),与 manifest 记录的"大端 hex"(`[u8;32].into()` 按大端读)不同,导致 `ClaimDigestMismatch` 误报。这不是 risc0 bug,是 API 的字节序约定。 | 所有 prove/verify 调用必须以 `[u8;32]` 大端字节构造 `Digest`(即 manifest hex 逐字节 `try_into().into()`);`[u32;8].into()` 不可用于跨模块传递 image_id。记录于 zkvm-prove/verify 注释与 docs/ENV.md。 | **已解决(2026-09-01,verify/prove 已统一用大端 `[u8;32]` 构造;manifest image_id 保持大端 hex)** |
| 2026-09-01 | SPEC §11.2-11.3 | **叶含 tree_size/tree_root 造成循环依赖**:§11.2 把 tree_size/tree_root 列入"服务端附加字段",而 §11.3 的叶 = JCS(server_event_record)——若二者同属一个记录,根哈希依赖叶、叶又含根哈希,无法实现。 | 采用 CT 标准设计(RFC 6962):叶/事件记录只含**状态无关**字段(sequence / received_at_utc / event_id),tree_size ↔ tree_root 绑定由每次 append 后独立签名的 STH 承担;回执 = 事件记录 + STH + inclusion proof。实现于 `music_zk/protocol/log.py`。 | **已采用(2026-09-01,最小非循环解读)** |
| 2026-09-01 | SPEC §11.3 | **STH 签名 framing 未定义**:SPEC 只说"再用服务端 Ed25519 key 签名",未给前缀/编码。creator 事件签名有 `MUSIC-ZK\0CREATOR-EVENT\0V1\0` 前缀(§10.2),STH 无。 | 最小化选择:直接对 `JCS(sth_body)` 做 Ed25519 签名(不加前缀);服务端密钥与 creator 密钥分离,天然区分两类签名。实现于 `music_zk/protocol/log.py`。 | **已采用(2026-09-01,不发明冻结前缀;如需前缀须新协议变更并升 protocol_id)** |
| 2026-09-01 | SPEC §17.5 | **guest Image ID 构建路径敏感**:guest ELF 经 `file!()`/panic 定位把**绝对构建路径**编译进 .rodata(实测含 `/home/luotianyi/.cargo/registry/src/rsproxy.cn-.../anyhow-1.0.104/src/error.rs` 与 `/mnt/c/` 仓库路径),故 Image ID = f(源码, 工具链, **CARGO_HOME 路径, 仓库构建路径**)。CI(不同路径/CARGO_HOME)构建出的 R0BF 与本地冻结产物字节不同(实测 CI `7db8d0a2...` vs 冻结 `ce00d244...`),**跨机器字节级复现冻结 Image ID 不可行**——除非用 `remap-path-prefix` 归一化路径,但那会改变 guest 字节 → 新 Image ID → 必须升 protocol_id。 | §17.5 门禁按字面执行:CI 干净环境**双构建** R0BF SHA-256 必须一致(同环境字节级确定 ⇒ Image ID 一致,CI 实测通过);**不做**与冻结产物的跨环境字节比对。冻结 Image ID 的权威锚点是 windows job 对入库收据的真实密码学复验(Image ID == 5e06801b)。修复路径敏感须新协议变更(remap-path-prefix + 重新生成全部收据 + 升 protocol_id),v1 不采用,记录待议。 | **已确认(2026-09-01,CI 门禁取同环境双构建一致;跨路径复现冻结值列入待议)** |

## 说明

- 上述两项一旦生成 `protocol/wavetable-v1.bin` 与 phase_step 表并入 manifest 即视为冻结(SPEC §5:任何影响 PCM 字节的变化须新 protocol_id)。
- 确认后从本表删除"待确认"标记,参数入 `protocol/v1.json` 并写生成脚本(`scripts/gen-wavetable.py`)。
