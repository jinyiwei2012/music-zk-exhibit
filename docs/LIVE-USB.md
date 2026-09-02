# LIVE-USB.md — Linux Live USB 兜底证明路径(规划中 → 本文档为方案设计)

> 出处:根 AGENTS.md §7、`docs/PLAN.md §6.4`(绕开 WSL 的降级路径第 2 条)。
> 状态:**方案设计,未实测**。本文档是"无法运行 Windows 原生 prover 的机器"的兜底路线图;
> 常规路径仍是 Windows 原生 prove/verify(CPU + CUDA,见 `docs/ENV.md`)。
> 红线:proving 全程断网(PRD §13.2)、私密材料不落他人机器、真实证明(禁 dev-mode)。

## 1. 适用对象与前提判断

老电脑的真实瓶颈是**证明资源(RAM、CPU)**,不是操作系统(PLAN §6.4 前提判断)。

| 任务 | 资源需求 | 可行性 |
|---|---|---|
| 浏览网页、播放 S/V | 极低 | 任何机器 |
| 离线验证证据包 | <10 秒、低内存 | 几乎任何 x64 机器(原生 `verify.exe`) |
| 本地生成真实证明 | 8–16 GB RAM + 现代 CPU | 取决于硬件,与 OS 无关 |

LIVE-USB 是"无法启用 Windows 原生 prover 或 WSL2"的最后一档兜底——比 WSL2 兼容面更宽:
只需 64 位 CPU,**不需要 VT-x、不需要 Win10 1903+、不装系统、不动硬盘**;Ubuntu live
从 U 盘启动即获得干净 Linux 环境,**天然满足"proving 断网"**。

## 2. 方案总览

```
[有能力的机器]                          [老电脑/无法用 WSL 的机器]
 1. 构建静态 Linux prover 二进制  ──▶   2. 拷到 U 盘
 3. 用同款 U 盘启动 Ubuntu live(断网) ──▶ 4. 跑 prove(只读 U 盘,输出写 U 盘)
                                         5. 拷回 receipt/journal/证据包
 6. 任意机器离线 verify(原生 verify.exe 或 Python)
```

证明是**一次性的公开数据**(receipt、journal、证据包):在有能力处生成后,可搬运到任何
老机器做展示/验证,密码学强度不打折——验证者本来就不需要证明能力(PLAN §6.4 第 3 条)。

## 3. 构建静态 Linux prover(步骤 1,未实测)

> ⚠️ 以下为设计路径,尚未在干净 Linux 上实测。两个现实障碍需先验证:
> ① risc0 C++ CPU 内核(sppark/risc0-sys)静态链接需 musl + 静态 libstdc++;② 仓库
> `rust/.cargo/config.toml` 的 `target-dir = "C:/music-zk-target"` 在 Linux 是非法相对
> 路径,构建必须 `CARGO_TARGET_DIR` 覆盖(CI 已验证此模式)。

```bash
# 干净 Ubuntu/Debian 机器(或 CI ubuntu runner):
sudo apt install -y musl-tools cmake ninja-build  # 或先跑 .github/workflows 的 CPU 路径
export CARGO_TARGET_DIR=/tmp/mzk-linux-target
cd rust
cargo +stable build --release --no-default-features -p zkvm-host --bins -p reference-native
# 产物:/tmp/mzk-linux-target/release/{zkvm-prove,zkvm-verify,reference-native}
```

静态链接备选(若 glibc 动态链接在 live 环境不通用):
```bash
rustup target add x86_64-unknown-linux-musl
cargo +stable build --release --target x86_64-unknown-linux-musl --no-default-features \
  -p zkvm-host --bins -p reference-native
# 注意:risc0 C++ 内核经 cc crate 编译,musl 下需 CC=x86_64-linux-musl-gcc;未实测
```

产物校验:三个二进制 + `protocol/`(guest-v1.elf、wavetable-v1.bin、v1.json)+ 发布到
release 页,附 SHA-256。老电脑侧可离线核对 SHA-256 后再用(供应链红线,AGENTS.md §1)。

## 4. 老电脑侧流程(步骤 2–5,未实测)

1. **准备 U 盘**:Rufus/balenaEtcher 写 Ubuntu 24.04 LTS live;再拷入
   `music-zk-live/` 目录:{ zkvm-prove, zkvm-verify, reference-native, protocol/, 自己的
   `midi.bin` + `salt.bin`, `scripts/` 里的 `gen-bench-midis.py` 产物或任意 Profile 1 MIDI }。
2. **启动 live(断网)**:BIOS 选 U 盘启动,进 Ubuntu live 桌面。**保持网线拔除/不连 Wi-Fi**
   (红线 3:proving 断网;live 环境本就无持久网络配置,断网是默认态)。
3. **渲染 V + 算 C_V**(live 内,本地):`./reference-native render midi.bin v.wav`
   → 输出含 `C_V=...`。
4. **真实证明**(live 内,内存限制防 OOM):
   ```bash
   RAYON_NUM_THREADS=8 ./zkvm-prove --cv <C_V> \
     --creator-pubkey <hex32> --commit-event-id <hex32> --release-event-id <hex32> \
     --segment-po2 18 --keccak-po2 18 midi.bin salt.bin
   # 产物:receipt.bin / journal.bin / method_id.txt(写入 U 盘;RAM 不足降 --segment-po2 至 17)
   ```
5. **拷回结果**:receipt.bin、journal.bin、v.wav 拷回原机器;私密材料(midi.bin/salt.bin/
   私钥)**只留在自己的 U 盘/本地**,不随证据包发布(红线 1)。
6. **离线验证**(任意机器):`verify.exe --expect-c-m <C_M> --expect-c-v <C_V>` 或
   `music-zk verify` 走公开证据包流程(SPEC §15)。

## 5. 与现状的关系(2026-09-01 后)

- **首选路径已不是 LIVE-USB**:prove/verify 已迁 **Windows 原生**(CPU 已验证、CUDA 已启用),
  大多数机器直接跑 `C:\music-zk-target\debug\zkvm-prove.exe`(见 docs/ENV.md)。
- LIVE-USB 保留为**更宽兼容面兜底**:老电脑若连 Windows 原生 prover 都跑不动
  (RAM 不足 CUDA/CPU 路径),或用户想要绝对隔离的断网证明环境。
- **证明/验证分离模式**(PLAN §6.4 第 3 条)与 LIVE-USB 互补:证明一次生成、证据包搬运、
  老电脑只验证——这是成本最低的展览部署形态。

## 6. 待办(未做,需按此推进)

- [ ] 在 CI/干净 Ubuntu 实测 §3 静态构建(先动态 glibc,再评估 musl);落地 `scripts/build-linux-prover.sh`
- [ ] 实测 Ubuntu live 冷启动 → prove → 拷回的完整流程;记录耗时/峰值内存
- [ ] 若静态链接遇阻(risc0 C++ 内核 musl 兼容),退化为"Ubuntu live + 预装 glibc 动态二进制"
- [ ] 产物发布通道(如 GitHub Release)与 SHA-256 清单
- [ ] 老电脑侧"离线核对 SHA-256 → 使用"的防供应链检查脚本
