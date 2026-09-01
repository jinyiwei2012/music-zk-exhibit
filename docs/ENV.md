# ENV.md — 环境版本事实表

> 约定见 docs/AGENTS.md 格式。每行 = `组件 | 版本 | 实测日期 | 来源命令`;环境版本变更追加新行,不改旧行。

## WSL2(guest 构建环境,2026-09-01 起不再承担 prove/verify)

| 组件 | 版本 | 实测日期 | 来源命令 |
|------|------|----------|----------|
| Ubuntu(WSL distro) | 26.04 (Resolute Raccoon) | 2026-08-31 | `grep VERSION /etc/os-release` |
| WSL2 内核 | 6.18.33.2-microsoft-standard-WSL2 | 2026-08-31 | `uname -a` |
| rustup | 1.29.0 | 2026-08-31 | `rustup --version` |
| cargo(稳定) | 1.98.0 | 2026-08-31 | `cargo --version` |
| rustc(稳定) | 1.98.0 | 2026-08-31 | `rustc --version` |
| rzup | 0.5.0 | 2026-08-31 | `rzup --version` |
| cargo-risczero | 3.0.6 | 2026-08-31 | `cargo risczero --version` |
| r0vm | 3.0.6 | 2026-08-31 | `r0vm --version` |
| risc0 工具链 | rustc 1.97.0-dev (e638c6cfe 2026-07-15);target `riscv32im-risc0-zkvm-elf` | 2026-08-31 | `rustc +risc0-1.97.0 --version` |
| risc0-zkvm / risc0-build crate | 3.0.6 | 2026-08-31 | rust/ 各 crate Cargo.toml(`^3.0.6`) |

## Windows 宿主(host)

| 组件 | 版本 | 实测日期 | 来源命令 |
|------|------|----------|----------|
| Windows build | 10.0.28120 | 2026-08-31 | 根 AGENTS.md §2 |
| git | 2.55.0 | 2026-08-31 | 根 AGENTS.md §2 |
| conda env music-zk | Python 3.12.14 | 2026-08-31 | 根 AGENTS.md §2 |
| rustup | 1.29.0 | 2026-09-01 | `rustup --version` |
| cargo(msvc) | 1.98.0 | 2026-09-01 | `cargo +stable-x86_64-pc-windows-msvc --version` |
| rustc(msvc) | 1.98.0 | 2026-09-01 | `rustc +stable-x86_64-pc-windows-msvc --version` |
| MSVC(Build Tools 18) | VC Tools 14.51.36231 / SDK 10.0.26100 | 2026-09-01 | `vswhere -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64` |
| CUDA Toolkit(Windows) | **13.2(V13.2.51,官方支持 VS 2026;12.4 的 cudafe++ 与 VS 18 不兼容已移除)** | 2026-09-01 | `nvcc --version`;安装路径 `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2` |
| NVIDIA 驱动 | **616.56(≥580 才支持 CUDA 13.x;原 551.78 是 12.4 捆绑驱动)** | 2026-09-01 | `nvidia-smi` |
| GPU | RTX 4060 Laptop 8GB + Intel Arc | 2026-08-31 | `nvidia-smi` |

## Windows 原生(2026-09-01 起,Win 原生迁移后的 build/prove 环境)

- **prove/verify 全部 Windows 原生**(CPU 已验证 1 正 4 负;**CUDA 已验证**:statement-2 完整 guest 4.1 s vs CPU 106–120 s,约 27× 加速)。
- **CUDA 13.2 是硬性要求**:CUDA 12.4 的 nvcc 内置 cudafe++ 无法解析 VS Build Tools 2026(VC 14.51)的 MSVC 18 头文件(0xC0000409 崩溃),CUDA 13.2 起官方支持 VS 2026;同时驱动须 ≥580(12.4 捆绑驱动 551.78 会被 cudafe++ 报错,已升 616.56)。
- **guest 构建仍只在 WSL**(risc0 工具链无 Windows 二进制;rzup 硬性不可用),产物 R0BF 入库 `protocol/guest-v1.elf`。
- 构建前置:`powershell` 里 `. .\scripts\env-win.ps1`(导入 vcvars64 环境 + `CXXFLAGS=/std:c++20 /DNOMINMAX` + CUDA_PATH),再 `cargo +stable-x86_64-pc-windows-msvc build`。
- **构建产物位置**:`rust/.cargo/config.toml` 的 `[build] target-dir = "C:/music-zk-target"`(仓库路径含 CJK,cl.exe LINK 对非 ASCII 路径编码错乱 LNK1104)。**一切脚本/手工跑 exe 都用 `C:\music-zk-target\debug\` 下的二进制**;`rust\target\debug\` 下是迁移前的旧拷贝(不认 `--segment-po2`,勿用)。
- **内存限制(蓝屏防护,2026-09-01)**:`zkvm-prove --segment-po2 18 --keccak-po2 18` + `RAYON_NUM_THREADS` 默认 ≤8。默认 segment po2=20 时单 segment GPU 缓冲按 2^20 行分配,8 GB 显存卡真实负载(B1 15s/4v)被单 segment 缓冲打爆蓝屏;po2=18 使峰值显存稳定 ~4.3 GB(实测 B1/B2/B3 一致)。限制与耗时权衡见 benchmarks.md B1/B2/B3 节。
- **栈溢出规避**(risc0 C++ poly_fp 深递归巨帧):代码内 `rayon::ThreadPoolBuilder::stack_size(64MiB).build_global()` + `rust/.cargo/config.toml` 的 `/STACK:0x4000000` + `RUST_MIN_STACK=64MiB`。
- **image_id 字节序陷阱**:`receipt.verify()` 的 image_id 必须用 `[u8;32]` 大端字节构造 `Digest`(`[u32;8].into()` 走 word 直拷,字节序不同会误报 ClaimDigestMismatch)。记录于 OPEN-QUESTIONS。
- **CJK 仓库路径**("非AI音乐的零知识证明")对 CUDA 工具链是硬伤:nvcc 的 cudafe++ 无法解析非 ASCII include 路径(C1083),且 cargo canonicalize 掉 subst 盘符。解法:vendor patch 的 sppark/risc0-sys build.rs 在 Windows+非 ASCII 路径下把 C++ 源码树镜像到 `OUT_DIR`(ASCII)并输出 ASCII 版 ROOT。
- **CUDA 编译的 MSVC 兼容 patch 链**(全部在 `rust/vendor/`,经 `[patch.crates-io]` 挂载,均只改 build 配置不改内核语义):
  1. `sppark` build.rs:移除 `CXXFLAGS`(MSVC 风格 `/std:c++20` 经 `-Xcompiler` 传给 cl 触发 cudafe++ 崩溃)+ ASCII 镜像。
  2. `risc0-build-kernel` compile_cuda:MSVC 门控 GNU 警告 flag + `-allow-unsupported-compiler` + `-std=c++17` + `-DTHRUST_DISABLE_ABI_NAMESPACE` + `-DCUB_DISABLE_NAMESPACE_MAGIC`(均需 paired `_IGNORE_` 宏,CUDA 13 的 `_SM_` 宏在 sm_89 展开破坏命名空间)+ force-include 系统 `cuda.h`(CCCL driver_api include 顺序)+ 移除 CXXFLAGS + `/utf-8`;compile_cpp:MSVC 用 `/std:c++20`(keccak CPU 内核用 designated initializer)+ `/DNOMINMAX`。
  3. `risc0-sys` build.rs:ASCII 镜像 cxx/cuda 目录(分开,避免互相覆盖)+ `-include` `typedef unsigned int uint` 头(`-Duint=unsigned int` 会被 nvcc 按空格拆分报 "A single input file is required")。
  4. `risc0-circuit-keccak-sys` / `risc0-circuit-rv32im-sys` build.rs:同 2 的 CUDA MSVC 修复(keccak 加 uint force-include)。
  5. `risc0-zkvm` Cargo.toml:cuda feature 移除 `risc0-groth16/cuda`(其 CUDA 内核无条件 include POSIX `sys/mman.h`,Windows 无此头;本项目只用 STARK 收据,不需要 Groth16 GPU 加速)。
- 构建时的 `RECURSION_SRC_PATH` 环境变量指向本地 `recursion_zkr.zip`(risc0-circuit-recursion 下载 S3 被代理损坏,手动下载校验 SHA256 通过;临时目录 `%TEMP%\opencode\recursion_zkr.zip`)。

## 工具链接线(WSL 侧 guest 构建,2026-08-31 手动安装;勿删,CI/新机器照此复现)

- 本机 GitHub 直连被阻断;组件全部经镜像落地:
  - cargo-risczero / r0vm:ghfast.top 镜像下载 `v3.0.6/cargo-risczero-x86_64-unknown-linux-gnu.tgz` → 解压安装到 `~/.risc0/bin/`
  - risc0 工具链:ghfast.top 镜像下载 `risc0/rust` 发布 `r0.1.97.0/rust-toolchain-x86_64-unknown-linux-gnu.tar.gz` → 解压到 **`~/.risc0/toolchains/r0.1.97.0/`**(目录名必须能被 rzup 解析:见 rzup paths.rs)
  - `rustup toolchain link risc0-1.97.0 ~/.risc0/toolchains/r0.1.97.0`
  - `~/.risc0/settings.toml` 手工写入(risc0-build 经 rzup 库读取;rzup install 的网络下载在本机失败):
    ```toml
    [default_versions]
    rust = "1.97.0"
    cargo-risczero = "3.0.6"
    ```
- 代理/镜像(WSL 侧,固化于 `~/.zk-env.sh` 与 `~/.cargo/config.toml`):
  - 出口代理:socks5h://172.17.16.1:10808(Windows 宿主 v2ray,端口 10808,网关 IP 实测)
  - crates.io 镜像:rsproxy.cn(sparse,`~/.cargo/config.toml`)
  - rustup 分发镜像:rsproxy.cn(`RUSTUP_DIST_SERVER` / `RUSTUP_UPDATE_ROOT`)
  - GitHub 发布资产镜像:ghfast.top(前缀 `https://ghfast.top/https://github.com/...`)
- `CARGO_TARGET_DIR=$HOME/.music-zk-target`:构建产物放 WSL 内,避免 /mnt/c 9p 慢盘
- **WSL 重启会清空 /tmp**:工具链 tarball 与解压目标都在 `~/.risc0/`(持久),勿放 /tmp

## 版本锁定(红线 5)

- `rust/zkvm-methods/guest/rust-toolchain.toml` 固定 `risc0-1.97.0`(仅 guest 构建使用;Windows 宿主用 `stable-x86_64-pc-windows-msvc`);Cargo.lock 入库冻结。
- guest 产物 `protocol/guest-v1.elf`(R0BF 格式)与 Image ID(`protocol/v1.json` 的 `guest.image_id`)冻结后不得改动;任何影响协议行为的变化必须产生新 `protocol_id`(SPEC §5);与协议无关的升级也要先在本表追加行再动。
