# scripts/bench-phase2.ps1 - Phase 2 真实负载基准(B1/B2/B3),Windows 原生 + CUDA + 内存限制
#
# 用法(仓库根,PowerShell):
#   . .\scripts\env-win.ps1            # 导入 MSVC/CUDA 环境(每次新 shell 都要)
#   powershell -File .\scripts\bench-phase2.ps1 [-Cases b1,b2,b3] [-SegmentPo2 18] [-KeccakPo2 18]
#
# 每个 case:reference-native render 出 WAV 与 C_V → zkvm-prove(带 --segment-po2/--keccak-po2
# 内存限制,蓝屏防护)→ 独立 zkvm-verify → 后台监控采样峰值显存(nvidia-smi)/峰值进程内存。
# 产物写入 proof-work/bench-<case>/(gitignore)。任一步失败则该 case 记 FAIL,继续下一个。
param(
    [string]$Cases = "b1-15s-4v,b2-30s-4v,b3-60s-4v",
    [int]$SegmentPo2 = 18,
    [int]$KeccakPo2 = 18
)
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
# 二进制在 C:/music-zk-target(CJK 仓库路径规避,见 rust/.cargo/config.toml target-dir);
# rust\target\debug\ 下的 exe 是旧拷贝,勿用。
$PROVE = "C:\music-zk-target\debug\zkvm-prove.exe"
$VERIFY = "C:\music-zk-target\debug\zkvm-verify.exe"
$RN = "C:\music-zk-target\debug\reference-native.exe"
$BENCH_DIR = Join-Path $ROOT "protocol\bench-midis"
$OUT_ROOT = Join-Path $ROOT "proof-work"
$SALT_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"

foreach ($bin in @($PROVE, $VERIFY, $RN)) {
    if (-not (Test-Path $bin)) { Write-Host "[FATAL] 缺少 $bin —— 先 cargo build"; exit 1 }
}
New-Item -ItemType Directory -Path $OUT_ROOT -Force | Out-Null

# ---------- 后台内存/显存监控脚本(每 250ms 采样,实时覆写峰值) ----------
$MONITOR = @'
param($OutFile, $DurationSec)
$maxVram = 0; $maxProveRam = 0; $minFreeRam = [double]::MaxValue
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $DurationSec) {
    $v = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
    if ($v -match '^\s*(\d+)') { $val = [int]$matches[1]; if ($val -gt $maxVram) { $maxVram = $val } }
    foreach ($p in (Get-Process -Name zkvm-prove -ErrorAction SilentlyContinue)) {
        if ($p.WorkingSet64 -gt $maxProveRam) { $maxProveRam = $p.WorkingSet64 }
    }
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    if ($os) { $free = [double]$os.FreePhysicalMemory; if ($free -lt $minFreeRam) { $minFreeRam = $free } }
    "VRAM_MIB=$maxVram PROVE_RAM_B=$maxProveRam FREE_RAM_KB=$minFreeRam" |
        Set-Content -Path $OutFile -Encoding ASCII
    Start-Sleep -Milliseconds 250
}
'@
$MONITOR_FILE = Join-Path $env:TEMP "music-zk-monitor.ps1"
Set-Content -Path $MONITOR_FILE -Value $MONITOR -Encoding ASCII

function Invoke-Case($name, $mid, $wav) {
    $work = Join-Path $OUT_ROOT "bench-$name"
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $peakFile = Join-Path $work "peak.txt"

    Write-Host "`n=== $name ==="

    # 1) render 出真实 WAV 并取 C_V(与 guest 内流式 SHA-256 对拍的公共承诺)
    $rout = & $RN render $mid $wav 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] render: $rout"; return $null }
    $cv = [regex]::Match($rout, "C_V=([0-9a-f]{64})").Groups[1].Value
    if ($cv.Length -ne 64) { Write-Host "  [FAIL] render 输出无 C_V: $rout"; return $null }
    Write-Host "  C_V = $cv"

    # 2) 准备 witness(固定测试盐,与 phase1-m0 一致)
    Copy-Item $mid "$work\midi.bin"
    $salt = [byte[]]::new(32)
    for ($i = 0; $i -lt 32; $i++) { $salt[$i] = [Convert]::ToByte($SALT_HEX.Substring($i * 2, 2), 16) }
    [System.IO.File]::WriteAllBytes("$work\salt.bin", $salt)

    # 3) 启动监控,跑 prove(内存限制)
    $mon = Start-Process powershell -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $MONITOR_FILE,
        $peakFile, "1800"
    ) -PassThru -WindowStyle Hidden
    $cm = ""
    $proveOk = $false
    $elapsed = Measure-Command {
        Push-Location $work
        try {
            $pout = & $PROVE --cv $cv --segment-po2 $SegmentPo2 --keccak-po2 $KeccakPo2 midi.bin salt.bin 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) {
                $cm = [regex]::Match($pout, "journal C_M: ([0-9a-f]{64})").Groups[1].Value
                $proveOk = $LASTEXITCODE -eq 0 -and $cm.Length -eq 64
            }
            Write-Host $pout
        } finally { Pop-Location }
    }
    Stop-Process -Id $mon.Id -Force -ErrorAction SilentlyContinue
    if (-not $proveOk) { Write-Host "  [FAIL] prove(exit=$LASTEXITCODE)"; return $null }
    Write-Host "  prove OK  耗时 $([math]::Round($elapsed.TotalSeconds, 1)) s"

    # 4) 独立 verify(绑定 t0 承诺 C_M 与参考音频 C_V)
    $vok = $false
    Push-Location $work
    try {
        $vout = & $VERIFY --expect-c-m $cm --expect-c-v $cv 2>&1 | Out-String
        $vok = ($LASTEXITCODE -eq 0)
        if (-not $vok) { Write-Host $vout }
    } finally { Pop-Location }
    Write-Host "  verify     $(if ($vok) { 'OK' } else { 'FAIL' })"

    # 5) 峰值监控读数
    $vram = 0; $prm = 0; $free = 0
    if (Test-Path $peakFile) {
        $peak = Get-Content $peakFile -Raw
        $vram = [int]([regex]::Match($peak, "VRAM_MIB=(\d+)").Groups[1].Value)
        $prm  = [int]([regex]::Match($peak, "PROVE_RAM_B=(\d+)").Groups[1].Value)
        $free = [int]([regex]::Match($peak, "FREE_RAM_KB=(\d+)").Groups[1].Value)
    }
    $receiptSize = (Get-Item "$work\receipt.bin" -ErrorAction SilentlyContinue).Length

    $row = [pscustomobject]@{
        case = $name
        wall_s = [math]::Round($elapsed.TotalSeconds, 1)
        peak_vram_mib = $vram
        peak_prover_ram_mib = [math]::Round($prm / 1MB, 1)
        min_free_sys_ram_mib = [math]::Round($free / 1024, 1)
        receipt_bytes = $receiptSize
        verify = $(if ($vok) { "OK" } else { "FAIL" })
    }
    $row | ConvertTo-Json | Set-Content -Path "$work\summary.json" -Encoding UTF8
    return $row
}

$results = @()
foreach ($c in ($Cases -split "," | ForEach-Object { $_.Trim() })) {
    $mid = Join-Path $BENCH_DIR "$c.mid"
    $wav = Join-Path $BENCH_DIR "$c.wav"
    if (-not (Test-Path $mid)) { Write-Host "[SKIP] 缺少 $mid(先跑 scripts/gen-bench-midis.py)"; continue }
    $r = Invoke-Case $c $mid $wav
    if ($r) { $results += $r }
}

Write-Host "`n=== 汇总 ==="
$results | Format-Table -AutoSize | Out-String -Width 160 | Write-Host
if ($results.Count -gt 0) {
    $results | ConvertTo-Json | Set-Content -Path (Join-Path $OUT_ROOT "bench-phase2-summary.json") -Encoding UTF8
}
