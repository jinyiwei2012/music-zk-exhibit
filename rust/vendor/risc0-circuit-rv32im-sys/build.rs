// Copyright 2025 RISC Zero, Inc.
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
        .deps(glob_paths("kernels/cxx/*.h"))
        .deps(glob_paths("kernels/cxx/*.cpp.inc"))
        .deps(glob_paths("kernels/cxx/*.h.inc"))
        .include(env::var("DEP_RISC0_SYS_CXX_ROOT").unwrap())
        .compile("risc0_rv32im_cpu");
}

fn build_cuda_kernels() {
    let output = "risc0_rv32im_cuda";

    println!("cargo:rerun-if-env-changed=NVCC_APPEND_FLAGS");
    println!("cargo:rerun-if-env-changed=NVCC_PREPEND_FLAGS");
    println!("cargo:rerun-if-env-changed=SCCACHE_RECACHE");
    rerun_if_changed("kernels/cuda");

    env::set_var("SCCACHE_IDLE_TIMEOUT", "0");

    // [Win 原生迁移 2026-09-01 patch] 移除 CXXFLAGS(MSVC 风格)避免 cudafe++ 崩溃
    let _ = env::remove_var("CXXFLAGS");

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
        .flag("-O3")
        .flag("-Xptxas")
        .flag("-O3")
        .include(env::var("DEP_RISC0_SYS_CUDA_ROOT").unwrap())
        .include(env::var("DEP_RISC0_SYS_CXX_ROOT").unwrap())
        .include(env::var("DEP_SPPARK_ROOT").unwrap());
    if is_msvc_host {
        // [Win 原生迁移 2026-09-01 patch] VS Build Tools 2026 / CUDA 13.2:
        // 系统 cuda.h force-include(CCCL driver_api include 顺序)+ thrust/CUB
        // ABI 命名空间宏(_SM_ 展开破坏)+ CCCL 标准预处理器 + cl /utf-8
        let cuda_h = PathBuf::from(env::var("CUDA_PATH").unwrap())
            .join("include")
            .join("cuda.h");
        build
            .flag("-allow-unsupported-compiler")
            .flag("-include")
            .flag(cuda_h.to_string_lossy().as_ref())
            .flag("-DTHRUST_DISABLE_ABI_NAMESPACE")
            .flag("-DTHRUST_IGNORE_ABI_NAMESPACE_ERROR")
            .flag("-DCUB_DISABLE_NAMESPACE_MAGIC")
            .flag("-DCUB_IGNORE_NAMESPACE_MAGIC_ERROR")
            .flag("-Xcompiler")
            .flag("/DCCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING")
            .flag("-Xcompiler")
            .flag("/utf-8");
    } else {
        // 上游:仅非 MSVC host 传 GNU 警告抑制
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

fn glob_paths(pattern: &str) -> Vec<PathBuf> {
    glob::glob(pattern).unwrap().map(|x| x.unwrap()).collect()
}
