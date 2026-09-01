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
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let cxx_root = ascii_mirror_if_needed(&manifest_dir.join("cxx"), "cxx");
    println!("cargo:cxx_root={}", cxx_root.to_string_lossy());

    if env::var("CARGO_FEATURE_CUDA").is_ok() {
        let cuda_root = ascii_mirror_if_needed(&manifest_dir.join("kernels/zkp/cuda"), "cuda");
        println!("cargo:cuda_root={}", cuda_root.to_string_lossy());
        build_cuda_kernels(&cxx_root);
    }

    if env::var("CARGO_CFG_TARGET_OS").is_ok_and(|os| os == "macos" || os == "ios") {
        println!(
            "cargo:metal_root={}",
            manifest_dir.join("kernels/zkp/metal").to_string_lossy()
        );
        build_metal_kernels();
    }
}

// [Win 原生迁移 2026-09-01 patch] 本 crate 被 vendor 到含 CJK 的仓库路径
// ("非AI音乐的零知识证明"),risc0-circuit-* 的 CUDA 编译把 DEP_RISC0_SYS_*
// ROOT 作为 include 路径,而 nvcc 的 cudafe++ 无法解析非 ASCII 路径(C1083/
// 崩溃)。因此在 Windows + 非 ASCII manifest 下,把 cxx 与 CUDA 内核目录
// 分别镜像到 OUT_DIR(ASCII,子目录区分,避免互相覆盖)并输出 ASCII 版 ROOT;
// 下游拿到的 include 即纯 ASCII。(与 vendor/sppark/build.rs 的 mirror 同思路。)
fn ascii_mirror_if_needed(path: &Path, tag: &str) -> PathBuf {
    let is_ascii = path
        .as_os_str()
        .to_str()
        .map(|s| s.is_ascii())
        .unwrap_or(false);
    if is_ascii {
        return path.to_path_buf();
    }
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("$OUT_DIR is not set"));
    let mirror = out_dir.join(format!("ascii-{tag}"));
    copy_dir(path, &mirror);
    println!("cargo:rerun-if-changed={}", path.to_string_lossy());
    println!("[risc0-sys] mirrored to ASCII: {}", mirror.display());
    mirror
}

fn copy_dir(src: &Path, dst: &Path) {
    use std::fs;
    fs::create_dir_all(dst).expect("create mirror dir");
    for entry in fs::read_dir(src).expect("read src dir") {
        let entry = entry.expect("readdir entry");
        let ty = entry.file_type().expect("entry file type");
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_dir(&from, &to);
        } else if ty.is_file() {
            fs::copy(&from, &to).expect("copy file to mirror");
        }
    }
}

fn build_cuda_kernels(cxx_root: &Path) {
    let mut kb = KernelBuild::new(KernelType::Cuda);
    kb.files([
            "kernels/zkp/cuda/combos.cu",
            "kernels/zkp/cuda/eltwise.cu",
            "kernels/zkp/cuda/ffi.cu",
            "kernels/zkp/cuda/kernels.cu",
            "kernels/zkp/cuda/sha.cu",
            "kernels/zkp/cuda/supra/api.cu",
            "kernels/zkp/cuda/supra/ntt.cu",
        ])
        .deps(["kernels/zkp/cuda", "kernels/zkp/cuda/supra"])
        .flag("-DFEATURE_BABY_BEAR")
        .include(cxx_root)
        .include(env::var("DEP_BLST_C_SRC").unwrap())
        .include(env::var("DEP_SPPARK_ROOT").unwrap());
    // [Win 原生迁移 2026-09-01 patch] 本 crate 的 .cu 直接用 `uint`
    // (Linux 由 sys/types.h 间接定义),MSVC 无此类型 → identifier "uint"
    // is undefined。用 nvcc `-include` force-include 一个 typedef 头
    // (不能用 -Duint=unsigned int:nvcc 按空格拆分 -D 值,`int` 被当第二
    // 输入文件报 "A single input file is required";也不能放通用层,会破坏
    // CUDA 13 thrust 的 vector_types)。
    if env::var("CARGO_CFG_TARGET_ENV").is_ok_and(|v| v == "msvc") {
        let out_dir = PathBuf::from(env::var("OUT_DIR").expect("$OUT_DIR is not set"));
        let uint_fix = out_dir.join("uint_fix.h");
        std::fs::write(&uint_fix, "#pragma once\ntypedef unsigned int uint;\n")
            .expect("write uint_fix.h");
        kb.flag("-include")
            .flag(uint_fix.to_string_lossy().as_ref());
    }
    kb.compile("risc0_zkp_cuda");
}

fn build_metal_kernels() {
    const METAL_KERNELS: &[(&str, &[&str])] = &[(
        "zkp",
        &[
            "eltwise.metal",
            "fri.metal",
            "mix.metal",
            "ntt.metal",
            "poseidon2.metal",
            "sha.metal",
            "zk.metal",
        ],
    )];

    let inc_path = Path::new("kernels/zkp/metal");
    for (name, srcs) in METAL_KERNELS {
        let dir = Path::new("kernels").join(name).join("metal");
        let src_paths = srcs.iter().map(|x| dir.join(x));
        let out = format!("metal_kernels_{name}");
        KernelBuild::new(KernelType::Metal)
            .files(src_paths)
            .include(inc_path)
            .dep(inc_path.join("sha256.h"))
            .compile(&out);
    }
}
