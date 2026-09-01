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

# CUDA Toolkit(risc0 cuda feature 的 nvcc 需求;与 WSL 侧同为 12.4)
$cudaPath = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
if (Test-Path $cudaPath) {
    $env:CUDA_PATH = $cudaPath
    $env:PATH = "$cudaPath\bin;$env:PATH"
}

Write-Host "[env-win] MSVC imported; CXXFLAGS='$env:CXXFLAGS'; CUDA='$env:CUDA_PATH'"