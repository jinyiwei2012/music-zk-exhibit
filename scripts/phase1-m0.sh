#!/usr/bin/env bash
# Phase 1(M0)门禁演示:1 正 4 负。
#   正  :真实 prove → 独立 verifier 复验 receipt/Image ID/journal/C_M 并绑定 t0 承诺
#   负 1:修改 MIDI 一字节 → verifier 拒绝
#   负 2:使用错误盐 → verifier 拒绝
#   负 3:错误 Image ID → verifier 拒绝
#   负 4:dev-mode fake receipt → production verifier 拒绝
# 用法(在 WSL 内):bash scripts/phase1-m0.sh
# 任一预期不符则退出码非零。
set -euo pipefail
source ~/.zk-env.sh
ROOT=/mnt/c/非AI音乐的零知识证明
PROVE="$CARGO_TARGET_DIR/debug/zkvm-prove"
VERIFY="$CARGO_TARGET_DIR/debug/zkvm-verify"
WORK="$ROOT/proof-work/gate"

echo "=== Phase 1 M0 门禁演示(1 正 4 负)==="
rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"
PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
trap 'echo "=== 结果: PASS=$PASS FAIL=$FAIL ==="; [ $FAIL -eq 0 ]' EXIT

# ---------------------------------------------------------------- 正样例
echo "[1/5] 正样例:真实 prove + 独立 verify"
mkdir -p pos && cd pos
# t0:本地构造 witness(M,r),Python hashlib 独立计算承诺 C_M
printf 'midi-bytes\x01\x02\x03\x04' > midi.bin
head -c 32 /dev/urandom > salt.bin
python3 << 'PYEOF'
import hashlib
m = open("midi.bin","rb").read(); r = open("salt.bin","rb").read()
assert len(r) == 32
cm = hashlib.sha256(b"MUSIC-ZK\x00MIDI-COMMIT\x00V1\x00"
                    + len(m).to_bytes(8,"big") + m + r).hexdigest()
open("cm_t0.txt","w").write(cm)
PYEOF
# t2:真实 prove(禁 dev mode)
"$PROVE" midi.bin salt.bin
# 独立 verifier:绑定 t0 已提交承诺
"$VERIFY" --expect-c-m "$(cat cm_t0.txt)" && ok "真实 receipt + journal + C_M 绑定 t0 通过"
cd "$WORK"

# ---------------------------------------------------------------- 负 1:改 M
echo "[2/5] 负向 1:修改 MIDI 一字节"
mkdir -p neg1 && cd neg1
cp ../pos/receipt.bin ../pos/journal.bin ../pos/midi.bin ../pos/salt.bin .
# 翻转 midi.bin 第 3 字节(0x01 -> 0x02),其余不动
printf '\x02' | dd of=midi.bin bs=1 seek=2 count=1 conv=notrunc status=none
if "$VERIFY" >/dev/null 2>&1; then bad "verifier 未拒绝被篡改的 M"; else ok "verifier 拒绝被篡改的 M"; fi
cd "$WORK"

# ---------------------------------------------------------------- 负 2:错盐
echo "[3/5] 负向 2:错误盐"
mkdir -p neg2 && cd neg2
cp ../pos/receipt.bin ../pos/journal.bin ../pos/midi.bin ../pos/salt.bin .
printf '\x00' | dd of=salt.bin bs=1 seek=0 count=1 conv=notrunc status=none
if "$VERIFY" >/dev/null 2>&1; then bad "verifier 未拒绝错误盐"; else ok "verifier 拒绝错误盐"; fi
cd "$WORK"

# ---------------------------------------------------------------- 负 3:错 Image ID
echo "[4/5] 负向 3:错误 Image ID"
mkdir -p neg3 && cd neg3
cp ../pos/receipt.bin ../pos/journal.bin ../pos/midi.bin ../pos/salt.bin .
if "$VERIFY" --expect-image-id 0000000000000000000000000000000000000000000000000000000000000000 >/dev/null 2>&1; then
  bad "verifier 未拒绝错误 Image ID"
else
  ok "verifier 拒绝错误 Image ID"
fi
cd "$WORK"

# ---------------------------------------------------------------- 负 4:dev 收据
echo "[5/5] 负向 4:dev-mode fake receipt"
mkdir -p neg4 && cd neg4
cp ../pos/midi.bin ../pos/salt.bin .
RISC0_DEV_MODE=1 "$PROVE" --allow-dev-mode midi.bin salt.bin >/dev/null 2>&1
if "$VERIFY" >/dev/null 2>&1; then bad "production verifier 放行了 dev-mode 收据"; else ok "production verifier 拒绝 dev-mode 收据"; fi
cd "$WORK"

echo "=== 结果: PASS=$PASS FAIL=$FAIL ==="
[ $FAIL -eq 0 ]
