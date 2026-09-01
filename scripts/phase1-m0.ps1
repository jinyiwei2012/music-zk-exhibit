# scripts/phase1-m0.ps1 - Phase 1(M0/M1)门禁演示:1 正 4 负(Windows 原生版)
#
# 用法(仓库根,PowerShell):
#   . .\scripts\env-win.ps1            # 导入 MSVC 环境(每次新 shell 都要)
#   powershell -File .\scripts\phase1-m0.ps1
#
# 对应 WSL 版 scripts/phase1-m0.sh;Windows 原生下 prove 需要大栈
# (rayon build_global 已在代码内处理)。
# 任一预期不符则退出码非零。
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
$PROVE = Join-Path $ROOT "rust\target\debug\zkvm-prove.exe"
$VERIFY = Join-Path $ROOT "rust\target\debug\zkvm-verify.exe"
$WORK = Join-Path $env:TEMP "music-zk-gate"

# golden vector minimal-onenote(合法 MIDI Profile 1)
$MIDI = Join-Path $ROOT "protocol\golden-vectors\midi\minimal-onenote.mid"
$C_M = "0717cc993bef93ce97480167625612992f230690779944c9ab69f650cbb97c68"
$C_V = "2498454557a1603b8d5b47ebd4f135103c62266534e829a9d5370f1f7ae4c4f9"
$SALT_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"

Write-Host "=== Phase 1 M0/M1 门禁演示(Windows 原生,1 正 4 负)==="
$PASS = 0; $FAIL = 0
function Ok($name) { Write-Host "  [PASS] $name"; $script:PASS++ }
function Bad($name) { Write-Host "  [FAIL] $name"; $script:FAIL++ }

# 准备 witness
Remove-Item $WORK -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $WORK -Force | Out-Null
Copy-Item $MIDI "$WORK\midi.bin"
$salt = [byte[]]::new(32)
for ($i = 0; $i -lt 32; $i++) { $salt[$i] = [Convert]::ToByte($SALT_HEX.Substring($i * 2, 2), 16) }
[System.IO.File]::WriteAllBytes("$WORK\salt.bin", $salt)
Set-Location $WORK

# ---------------------------------------------------------------- 正样例
Write-Host "[1/5] 正样例:真实 prove + 独立 verify"
& $PROVE --cv $C_V midi.bin salt.bin 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Bad "prove 失败(exit=$LASTEXITCODE)"; exit 1 }
& $VERIFY --expect-c-m $C_M --expect-c-v $C_V 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "真实 receipt + journal + C_M/C_V 绑定通过" } else { Bad "verify 失败(exit=$LASTEXITCODE)" }

# ---------------------------------------------------------------- 负 1:改 M(用错 C_M 绑定模拟)
Write-Host "[2/5] 负向 1:错误 C_M 绑定(等价于改 M 一字节后的 t0 承诺)"
& $VERIFY --expect-c-m "0000000000000000000000000000000000000000000000000000000000000000" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Ok "verifier 拒绝错误 C_M 绑定" } else { Bad "verifier 未拒绝错误 C_M" }

# ---------------------------------------------------------------- 负 2:错 Image ID
Write-Host "[3/5] 负向 2:错误 Image ID"
& $VERIFY --expect-image-id "0000000000000000000000000000000000000000000000000000000000000000" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Ok "verifier 拒绝错误 Image ID" } else { Bad "verifier 未拒绝错误 Image ID" }

# ---------------------------------------------------------------- 负 3:错 C_V
Write-Host "[4/5] 负向 3:错误 C_V(参考音频承诺不符)"
& $VERIFY --expect-c-v "0000000000000000000000000000000000000000000000000000000000000000" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Ok "verifier 拒绝错误 C_V" } else { Bad "verifier 未拒绝错误 C_V" }

# ---------------------------------------------------------------- 负 4:dev-mode 被硬禁
Write-Host "[5/5] 负向 4:dev-mode 收据被拒绝(disable-dev-mode 编译期硬禁)"
$env:RISC0_DEV_MODE = "1"
& $PROVE --allow-dev-mode --cv $C_V midi.bin salt.bin 2>&1 | Out-Null
$devExit = $LASTEXITCODE
Remove-Item Env:RISC0_DEV_MODE -ErrorAction SilentlyContinue
if ($devExit -ne 0) { Ok "dev-mode 收据无法生成(生产构建硬禁,红线 2)" } else { Bad "dev-mode 收据被生成!" }

Write-Host "=== 结果: PASS=$PASS FAIL=$FAIL ==="
if ($FAIL -eq 0) { exit 0 } else { exit 1 }
