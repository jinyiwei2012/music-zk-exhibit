#!/usr/bin/env powershell
# scripts/offline-prove-test.ps1 - SPEC §17.4 断网 proving 测试
#
# 防火墙阻断全部出站 TCP/UDP → 跑一个真实最小证明(statement-2 guest, CUDA/CPU)
# → 独立 verifier 复验 → finally 恢复防火墙规则。证明成功即"proving 不依赖云端"。
#
# 用法(仓库根):  powershell -File scripts\offline-prove-test.ps1
# 退出码: 0 = 断网证明 + 复验通过; 1 = 失败。
$ErrorActionPreference = "Stop"
$ROOT = "C:\非AI音乐的零知识证明"
$BIN = "C:\music-zk-target\debug"
$PROVE = "$BIN\zkvm-prove.exe"
$VERIFY = "$BIN\zkvm-verify.exe"
$NATIVE = "$BIN\reference-native.exe"
$RULE = "mzk-offline-test"

$env:RAYON_NUM_THREADS = "8"
foreach ($exe in @($PROVE, $VERIFY, $NATIVE)) {
    if (-not (Test-Path $exe)) { Write-Error "缺少 $exe"; exit 1 }
}

$work = Join-Path $env:TEMP "mzk-offline-prove"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null

# 1) 输入:golden minimal-onenote MIDI + 新鲜 32B 盐
$midi = "$ROOT\protocol\golden-vectors\midi\minimal-onenote.mid"
$salt = Join-Path $work "salt.bin"
conda run -n music-zk python -c "import secrets, pathlib; pathlib.Path(r'$salt').write_bytes(secrets.token_bytes(32))"
if (-not (Test-Path $salt)) { Write-Error "盐生成失败"; exit 1 }

# 2) 渲染 V 并算 C_V / C_M(与 guest 同 framing)
$wav = Join-Path $work "v.wav"
& $NATIVE render $midi $wav
if (-not (Test-Path $wav)) { Write-Error "V 渲染失败"; exit 1 }
$cv = (conda run -n music-zk python -c "from music_zk.verifier.framing import commit_reference_wav; from pathlib import Path; print(commit_reference_wav(Path(r'$wav').read_bytes()).hex())" | Select-Object -Last 1).Trim()
$cm = (conda run -n music-zk python -c "from music_zk.verifier.framing import commit_midi; from pathlib import Path; print(commit_midi(Path(r'$midi').read_bytes(), Path(r'$salt').read_bytes()).hex())" | Select-Object -Last 1).Trim()
foreach ($h in @($cv, $cm)) {
    if ($h -notmatch '^[0-9a-f]{64}$') { Write-Error "哈希计算失败: $h"; exit 1 }
}
Write-Host "C_M = $cm"
Write-Host "C_V = $cv"

# 3) 断网:阻断全部出站(防火墙规则,finally 恢复)
Write-Host "阻断出站流量(防火墙规则 $RULE)..."
netsh advfirewall firewall delete rule name="$RULE" 2>$null | Out-Null
netsh advfirewall firewall add rule name="$RULE" dir=out action=block enable=yes | Out-Null
try {
    # 4) 断网下真实证明(内存限制防蓝屏;dev-mode 编译期硬禁);产物写入 $work
    Write-Host "断网下运行真实证明(statement-2 guest, minimal-onenote)..."
    Push-Location $work
    try {
        & $PROVE --cv $cv --creator-pubkey ("0" * 64) --commit-event-id ("0" * 64) --release-event-id ("0" * 64) --segment-po2 18 --keccak-po2 18 $midi $salt
        if ($LASTEXITCODE -ne 0) { throw "prove 失败(exit=$LASTEXITCODE):断网下证明不成立" }

        # 5) 独立 verifier 复验(收据 + C_M/C_V 绑定)
        & $VERIFY --expect-c-m $cm --expect-c-v $cv
        if ($LASTEXITCODE -ne 0) { throw "独立复验失败(exit=$LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    Write-Host "[PASS] 断网 proving:证明生成 + 独立复验均成功,proving 不依赖云端(SPEC §17.4 第 2 条)。"
} finally {
    # 6) 恢复网络(必须成功)
    netsh advfirewall firewall delete rule name="$RULE" 2>$null | Out-Null
    Write-Host "已恢复出站流量。"
    Get-NetFirewallRule -Name $RULE -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
}
exit 0
