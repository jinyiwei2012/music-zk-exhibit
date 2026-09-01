#!/usr/bin/env bash
# scripts/build-guest-wsl.sh - 在 WSL 内构建 guest 并生成 R0BF 入库(Win 原生迁移后唯一保留在 WSL 的环节)。
#
# 背景:risc0 工具链(risc0-1.97.0)与 cargo-risczero 只有 Linux/macOS 二进制,
# rzup 在 Windows 硬性不可用 → guest 只能在 WSL/CI 构建。Windows 宿主通过
# include_bytes! 加载 protocol/guest-v1.elf(R0BF 格式),零工具链依赖。
#
# 用法(仓库根,在 WSL 内):bash scripts/build-guest-wsl.sh
# 产物:
#   protocol/guest-v1.elf   <- R0BF(user ELF + V1COMPAT kernel),risc0 prove 直接可用
# 输出:Image ID(需同步更新 protocol/v1.json 的 guest.image_id / elf_sha256)
#
# 前置:WSL 内 risc0-1.97.0 工具链可用(见 docs/ENV.md);本脚本需在仓库根执行。
set -euo pipefail

# 加载 rustup 环境(非交互 shell 里 cargo 通常不在 PATH)
if [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi
# 加载本机代理/镜像配置(如存在)
if [ -f "$HOME/.zk-env.sh" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.zk-env.sh"
fi

# WSL 路径映射:脚本在 /mnt/c/... 下运行,仓库根就是当前目录
ROOT="$(pwd)"
GUEST_DIR="$ROOT/rust/zkvm-methods/guest"
OUT_R0BF="$ROOT/protocol/guest-v1.elf"
ELF2R0BF="$ROOT/rust/target/debug/elf2r0bf.exe"   # Windows 构建的转换工具;WSL 内则用 target/debug/elf2r0bf

# 1) 构建 guest(risc0 工具链,riscv32im-risc0-zkvm-elf target)
echo "=== [1/4] cargo build guest (risc0-1.97.0) ==="
cd "$GUEST_DIR"
# rust-toolchain.toml 已 pin risc0-1.97.0,无需显式 +toolchain
# 构建 flags 见 guest/.cargo/config.toml 的 [target.riscv32im-risc0-zkvm-elf] rustflags
# (与 risc0-build encode_rust_flags 一致;用 target 专属 rustflags 避免污染宿主 build script)
cargo build --release --target riscv32im-risc0-zkvm-elf

USER_ELF="$GUEST_DIR/target/riscv32im-risc0-zkvm-elf/release/zkvm-guest"
# CARGO_TARGET_DIR 若被 .zk-env.sh / .cargo/config.toml 覆盖,ELF 在别处。
# 注意:target 目录下可能存在多个 guest ELF(旧的 riscv-guest/ 缓存目录、
# 新的 riscv32im-risc0-zkvm-elf/ 目录),必须取**修改时间最新**的,
# 否则会用旧 ELF 转 R0BF 导致协议内容过期。
if [ ! -f "$USER_ELF" ]; then
  USER_ELF="$(find "$ROOT/rust" "$HOME/.music-zk-target" -path '*riscv32im-risc0-zkvm-elf/release/zkvm-guest' -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
fi
if [ ! -f "$USER_ELF" ]; then
  echo "错误:guest ELF 未生成" >&2
  exit 1
fi
echo "user ELF: $USER_ELF ($(stat -c%s "$USER_ELF") bytes)"

# 2) 转 R0BF(优先用 Windows 构建的 elf2r0bf;WSL 内没有就现场构建)
echo "=== [2/4] convert to R0BF ==="
cd "$ROOT/rust"
if [ -x "$ELF2R0BF" ]; then
  TOOL="$ELF2R0BF"
else
  echo "未找到 Windows elf2r0bf,改用 WSL 构建..."
  cargo build -p elf2r0bf >/dev/null 2>&1
  TOOL="$(find "$ROOT/rust" -path '*/debug/elf2r0bf' -type f 2>/dev/null | head -1)"
  if [ -z "$TOOL" ]; then
    TOOL="$HOME/.music-zk-target/debug/elf2r0bf"
  fi
fi
echo "elf2r0bf: $TOOL"
"$TOOL" "$USER_ELF" "$OUT_R0BF"

# 3) 校验 R0BF 与 Image ID
echo "=== [3/4] verify + compute Image ID ==="
R0VM="${R0VM:-$HOME/.risc0/bin/r0vm}"
if [ -x "$R0VM" ]; then
  IMAGE_ID="$("$R0VM" --elf "$OUT_R0BF" --id 2>/dev/null | tail -1)"
else
  echo "警告:未找到 r0vm,跳过 Image ID 计算(r0vm 路径可用环境变量 R0VM= 覆盖)" >&2
  IMAGE_ID="<unknown>"
fi
SHA256="$(sha256sum "$OUT_R0BF" | cut -d' ' -f1)"
echo "R0BF:  $OUT_R0BF ($(stat -c%s "$OUT_R0BF") bytes)"
echo "sha256: $SHA256"
echo "image_id: $IMAGE_ID"

# 4) 提示同步 manifest
echo "=== [4/4] 请同步更新 protocol/v1.json ==="
echo "  guest.elf_sha256 = $SHA256"
echo "  guest.image_id    = $IMAGE_ID"
echo "  如协议行为变化,按 SPEC §5 产生新 protocol_id(AGENTS.md §3.1)"
echo "DONE"
