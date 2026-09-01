# scripts/demo.ps1 - Phase 4 一键演示(按时间线暂停讲解每一步)
#
# 用法(仓库根,PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 [-Port 8472] [-Auto]
#   -Auto:不暂停(CI/自动化);默认每步暂停讲解。
#
# 流程:身份 → 服务端 → t0 承诺 → t1 发布 → t2 真实证明与发布 → 证据包导出 →
#       离线 verify(SPEC §15 十一项)→ reveal-check → demo tamper 五案例 → 结果页。
param(
    [int]$Port = 8472,
    [switch]$Auto
)
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot
$PY = "conda"
$PYARGS = @("run", "-n", "music-zk", "python", "-m", "music_zk.cli.main")
$MIDI = "protocol\golden-vectors\midi\minimal-onenote.mid"
$SONG = "protocol\golden-vectors\minimal-onenote.wav"
$SERVER = "http://127.0.0.1:$Port"

function Step($title) {
    Write-Host "`n=== $title ==="
    if (-not $Auto) { Read-Host "按回车继续..." | Out-Null }
}

Push-Location $ROOT
try {
    Write-Host "Music-ZK 一键演示(Phase 4) — 真实零知识证明展品"

    Step "1/9 清理并创建创作者身份(Ed25519 私钥只落本地 creator-secret/)"
    # 重置可能存在的旧 ACL(identity init 曾以 icacls 收紧,含 RX,W 无删除权)后删除
    foreach ($d in @("creator-secret", "server-data")) {
        if (Test-Path $d) { icacls $d /reset /T /Q 2>$null | Out-Null; Remove-Item $d -Recurse -Force }
    }
    Remove-Item public-evidence -Recurse -Force -ErrorAction SilentlyContinue
    & $PY @PYARGS identity init --out creator-secret
    & $PY @PYARGS server init --data server-data

    Step "2/9 启动 demo 服务端(透明日志 + SQLite)"
    $srvErr = Join-Path $env:TEMP "mzk-demo-server.log"
    $serverProc = Start-Process conda -ArgumentList @(
        "run", "-n", "music-zk", "python", "-m", "music_zk.cli.main",
        "server", "run", "--data", "server-data", "--port", "$Port"
    ) -PassThru -WindowStyle Hidden -RedirectStandardError $srvErr -WorkingDirectory $ROOT
    # 就绪等待:checkpoint 返回 404 = 服务端已监听(空日志属正常)
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-WebRequest -Uri "$SERVER/api/v1/log/checkpoint" -UseBasicParsing -TimeoutSec 3 | Out-Null
            $ready = $true
            break
        } catch {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) { $ready = $true; break }
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        Write-Host "服务端未能启动,日志尾部:" -ForegroundColor Red
        Get-Content $srvErr -ErrorAction SilentlyContinue | Select-Object -Last 15
        exit 1
    }
    Write-Host "服务端就绪(端口 $Port)"

    Step "3/9 t0:提交私有 MIDI 的承诺(只上传统诺 C_M,不上传 MIDI/盐)"
    & $PY @PYARGS commit create $MIDI --server $SERVER

    Step "4/9 t1:发布公开歌曲 S(服务端记录 C_S 与文件)"
    & $PY @PYARGS song publish $SONG --secret creator-secret --server $SERVER
    $releaseId = (Get-Content creator-secret\release-receipt.json -Raw |
        ConvertFrom-Json).server.event.event_id

    Step "5/9 t2:本地真实零知识证明(CUDA;约 5-600 秒,内存限制防蓝屏)"
    & $PY @PYARGS prove --secret creator-secret --release $releaseId --out proof-work\demo

    Step "6/9 发布证明 PROOF(服务端本地 verifier 复验通过后才接受)"
    & $PY @PYARGS proof publish --work proof-work\demo --secret creator-secret --server $SERVER

    Step "7/9 导出公开证据包(SPEC §12.2;不含任何私密材料)"
    Remove-Item public-evidence -Recurse -Force -ErrorAction SilentlyContinue
    & $PY @PYARGS evidence export --secret creator-secret --work proof-work\demo --server $SERVER --song $SONG --out public-evidence

    Step "8/9 离线验证证据包(SPEC §15 十一项)+ reveal-check + 篡改演示五案例"
    $serverPk = (Get-Content server-data\server-public-key.txt -Raw).Trim()
    & $PY @PYARGS verify public-evidence --server-key $serverPk
    Write-Host "`n--- reveal-check((midi, salt) 打开 t0 承诺)---"
    & $PY @PYARGS reveal-check creator-secret\original.mid creator-secret\salt.bin creator-secret\commit-receipt.json
    foreach ($case in @("midi-byte", "wav-sample", "salt", "log-receipt", "event-order")) {
        Write-Host "`n--- demo tamper --case $case ---"
        & $PY @PYARGS demo tamper --case $case --evidence public-evidence --secret creator-secret
    }

    Step "9/9 结果页与技术页(浏览器打开)"
    $claimId = (Get-Content public-evidence\claim.json -Raw | ConvertFrom-Json).claim_id
    Write-Host "结果页:  http://127.0.0.1:$Port/claim/$claimId"
    Write-Host "技术页:  http://127.0.0.1:$Port/claim/$claimId/tech"
    if (-not $Auto) { Start-Process "http://127.0.0.1:$Port/claim/$claimId" }

    Write-Host "`n演示完成。"
    if (-not $Auto) {
        Write-Host "服务端仍在运行(端口 $Port);访问结果页后按回车关闭。"
        Read-Host "按回车关闭服务端..." | Out-Null
        taskkill /PID $serverProc.Id /T /F 2>$null | Out-Null
        Write-Host "服务端已关闭。"
    } else {
        Write-Host "Auto 模式:关闭服务端(树杀,避免遗留进程占用控制台句柄)。"
        taskkill /PID $serverProc.Id /T /F 2>$null | Out-Null
        Write-Host "服务端已关闭。"
    }
} catch {
    Write-Host "演示失败: $_" -ForegroundColor Red
    if ($serverProc) { taskkill /PID $serverProc.Id /T /F 2>$null | Out-Null }
    exit 1
}
