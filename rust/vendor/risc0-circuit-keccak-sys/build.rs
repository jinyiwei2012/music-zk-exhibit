// Copyright 2024 RISC Zero, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use std::{
    env,
    path::{Path, PathBuf},
};

use risc0_build_kernel::{KernelBuild, KernelType};

fn main() {
    if env::var("CARGO_FEATURE_CUDA").is_ok() {
        build_cuda_kernels();
    }

    build_cpu_kernels();
}

fn build_cpu_kernels() {
    rerun_if_changed("kernels/cxx");
    KernelBuild::new(KernelType::Cpp)
        .files(glob_paths("kernels/cxx/*.cpp"))
        .include(env::var("DEP_RISC0_SYS_CXX_ROOT").unwrap())
        .compile("risc0_keccak_cpu");
}

fn build_cuda_kernels() {
    let output = "risc0_keccak_cuda";

    println!("cargo:rerun-if-env-changed=NVCC_APPEND_FLAGS");
    println!("cargo:rerun-if-env-changed=NVCC_PREPEND_FLAGS");
    println!("cargo:rerun-if-env-changed=SCCACHE_RECACHE");
    rerun_if_changed("kernels/cuda");

    if env::var("RISC0_SKIP_BUILD_KERNELS").is_ok() {
        let out_dir = env::var("OUT_DIR").map(PathBuf::from).unwrap();
        let out_path = out_dir.join(format!("lib{output}-skip.a"));
        std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&out_path)
            .unwrap();
        println!("cargo:{}={}", output, out_path.display());
        return;
    }

    env::set_var("SCCACHE_IDLE_TIMEOUT", "0");

    // [Win 原生迁移 2026-09-01 patch] 移除 CXXFLAGS(MSVC 风格 /std:c++20 /DNOMINMAX)
    // 避免 cc-rs 透传 -Xcompiler /std:c++20 给 cl.exe 触发 nvcc 内置 cudafe++
    // 崩溃(0xC0000409)。CUDA 内核不需要 CXXFLAGS。
    let _ = env::remove_var("CXXFLAGS");

    let is_msvc_host = env::var("CARGO_CFG_TARGET_ENV").is_ok_and(|v| v == "msvc");

    let mut build = cc::Build::new();
    build
        .cuda(true)
        .cudart("static")
        .debug(false)
        .flag("-diag-suppress=177")
        .flag("-diag-suppress=550")
        .flag("-diag-suppress=2922")
        .flag("-std=c++20")
        .include(env::var("DEP_RISC0_SYS_CUDA_ROOT").unwrap())
        .include(env::var("DEP_SPPARK_ROOT").unwrap());
    if is_msvc_host {
        // [Win 原生迁移 2026-09-01 patch] VS Build Tools 2026 / CUDA 13.2:
        // CCCL 标准预处理器宏 + cl 中文代码页 /utf-8(见 risc0-build-kernel)
        let cuda_h = PathBuf::from(env::var("CUDA_PATH").unwrap())
            .join("include")
            .join("cuda.h");
        build
            .flag("-allow-unsupported-compiler")
            .flag("-include")
            .flag(cuda_h.to_string_lossy().as_ref())
            .flag("-Xcompiler")
            .flag("/DCCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING")
            .flag("-Xcompiler")
            .flag("/utf-8")
            // [Win 原生迁移 2026-09-01 patch] 本 crate 的 .cu 直接用 `uint`
            // (Linux sys/types.h 间接提供),MSVC 无此类型 → identifier "uint"
            // is undefined(ffi.cu:161 等)。不能用 -Duint=unsigned int
            // (nvcc 内部按空格拆分 -D 值,`int` 被当第二输入文件报
            // "A single input file is required"),故写头文件 + nvcc -include。
            .flag("-include")
            .flag(out_dir_uint_fix().to_string_lossy().as_ref());
    } else {
        // 上游:仅非 MSVC host 传 GNU 警告抑制(clang/GCC 接受)
        build
            .flag("-Xcompiler")
            .flag("-Wno-unused-function,-Wno-unused-parameter");
    }
    if env::var_os("NVCC_PREPEND_FLAGS").is_none() && env::var_os("NVCC_APPEND_FLAGS").is_none() {
        build.flag("-arch=native");
    }
    build.files(glob_paths("kernels/cuda/*.cu")).compile(output);
}

fn rerun_if_changed<P: AsRef<Path>>(path: P) {
    println!("cargo:rerun-if-changed={}", path.as_ref().display());
}

// [Win 原生迁移 2026-09-01 patch] 生成 `typedef unsigned int uint;` 头文件
// (OUT_DIR,ASCII)并返回路径,供 nvcc `-include` force-include。
// MSVC 无 Linux sys/types.h 提供的 `uint` 类型。
fn out_dir_uint_fix() -> PathBuf {
    let out_dir = env::var("OUT_DIR").map(PathBuf::from).unwrap();
    let path = out_dir.join("uint_fix.h");
    std::fs::write(&path, "#pragma once\ntypedef unsigned int uint;\n").expect("write uint_fix.h");
    println!("cargo:rerun-if-changed={}", path.display());
    path
}

fn glob_paths(pattern: &str) -> Vec<PathBuf> {
    glob::glob(pattern).unwrap().map(|x| x.unwrap()).collect()
}
