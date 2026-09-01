# scripts/env-win.ps1 - Windows native Rust build env (risc0 3.0.6)
#
# Usage (repo root):
#   . .\scripts\env-win.ps1
#   cargo +stable-x86_64-pc-windows-msvc build ...
#
# Why:
#   - risc0-build-kernel hardcodes /std:c++17 for C++ kernels, but the kernel
#     code uses C++20 designated initializers (MSVC error C7555). GCC/Clang
#     accept it under c++17; MSVC rejects. Fix: /std:c++20 appended AFTER the
#     hardcoded flag (MSVC uses the last /std:).
#   - risc0-sys vendored poolstl.hpp conflicts with Windows min/max macros
#     -> /DNOMINMAX.
#   - cc-rs MSVC detection can fail on CJK systems -> import vcvars64.bat env
#     manually.

# 保存调用者原值,dot-source 结束后恢复,避免污染调用者作用域
# (cargo 的 stderr 进度在 $ErrorActionPreference="Stop" 下会被 PowerShell
#  误判为终止错误而中断构建 —— 见 PLAN.md §6.2)
$_prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Stop"

$vcvars = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) {
    throw "not found: $vcvars (need VS Build Tools C++ workload)"
}

$envDump = Join-Path $env:TEMP "music-zk-vcvars-env.txt"
cmd /c "call `"$vcvars`" >nul 2>&1 && set" | Out-File -FilePath $envDump -Encoding utf8

Get-Content -Path $envDump | ForEach-Object {
    $idx = $_.IndexOf('=')
    if ($idx -gt 0) {
        $name = $_.Substring(0, $idx)
        $value = $_.Substring($idx + 1)
        Set-Item -Path "Env:$name" -Value $value
    }
}
Remove-Item -Path $envDump -ErrorAction SilentlyContinue

$env:CXXFLAGS = "/std:c++20 /DNOMINMAX"

# CUDA Toolkit(risc0 cuda feature 的 nvcc 需求)
# 2026-09-01 升级 12.4.1 → 13.2:CUDA 12.4 的 cudafe++ 与 VS Build Tools 2026
# (VC 14.51,_MSC_VER 1951)不兼容,解析 MSVC 18 头文件时崩溃(0xC0000409);
# CUDA 13.2+ 官方支持 VS 2026(见 nvidia 安装指南 Windows 编译器支持表)。
$cudaPath = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2"
if (Test-Path $cudaPath) {
    $env:CUDA_PATH = $cudaPath
    $env:PATH = "$cudaPath\bin;$env:PATH"
}

Write-Host "[env-win] MSVC imported; CXXFLAGS='$env:CXXFLAGS'; CUDA='$env:CUDA_PATH'"

# 恢复调用者原始的 ErrorActionPreference(默认 Continue)
$ErrorActionPreference = $_prevEAP