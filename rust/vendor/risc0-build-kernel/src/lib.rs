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
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

use rayon::prelude::*;
use sha2::{Digest, Sha256};
use tempfile::tempdir_in;

const METAL_INCS: &[(&str, &str)] = &[
    ("fp.h", include_str!("../kernels/metal/fp.h")),
    ("fpext.h", include_str!("../kernels/metal/fpext.h")),
];

#[derive(Eq, PartialEq, Hash)]
#[non_exhaustive]
pub enum KernelType {
    Cpp,
    Cuda,
    Metal,
}

pub struct KernelBuild {
    kernel_type: KernelType,
    flags: Vec<String>,
    files: Vec<PathBuf>,
    inc_dirs: Vec<PathBuf>,
    deps: Vec<PathBuf>,
}

impl KernelBuild {
    pub fn new(kernel_type: KernelType) -> Self {
        Self {
            kernel_type,
            flags: Vec::new(),
            files: Vec::new(),
            inc_dirs: Vec::new(),
            deps: Vec::new(),
        }
    }

    /// Add a directory to the `-I` or include path for headers
    pub fn include<P: AsRef<Path>>(&mut self, dir: P) -> &mut KernelBuild {
        self.inc_dirs.push(dir.as_ref().to_path_buf());
        self
    }

    /// Add an arbitrary flag to the invocation of the compiler
    pub fn flag(&mut self, flag: &str) -> &mut KernelBuild {
        self.flags.push(flag.to_string());
        self
    }

    /// Add a file which will be compiled
    pub fn file<P: AsRef<Path>>(&mut self, p: P) -> &mut KernelBuild {
        self.files.push(p.as_ref().to_path_buf());
        self
    }

    /// Add files which will be compiled
    pub fn files<P>(&mut self, p: P) -> &mut KernelBuild
    where
        P: IntoIterator,
        P::Item: AsRef<Path>,
    {
        for file in p.into_iter() {
            self.file(file);
        }
        self
    }

    /// Add a file which will be compiled
    pub fn file_opt<P: AsRef<Path>>(&mut self, _p: P, _opt: usize) -> &mut KernelBuild {
        self
    }

    /// Add files which will be compiled
    pub fn files_opt<P>(&mut self, _p: P, _opt: usize) -> &mut KernelBuild
    where
        P: IntoIterator,
        P::Item: AsRef<Path>,
    {
        self
    }

    /// Add a dependency
    pub fn dep<P: AsRef<Path>>(&mut self, p: P) -> &mut KernelBuild {
        self.deps.push(p.as_ref().to_path_buf());
        self
    }

    /// Add dependencies
    pub fn deps<P>(&mut self, p: P) -> &mut KernelBuild
    where
        P: IntoIterator,
        P::Item: AsRef<Path>,
    {
        for file in p.into_iter() {
            self.dep(file);
        }
        self
    }

    pub fn compile(&mut self, output: &str) {
        println!("cargo:rerun-if-env-changed=RISC0_SKIP_BUILD_KERNELS");
        for src in self.files.iter() {
            rerun_if_changed(src);
        }
        for dep in self.deps.iter() {
            rerun_if_changed(dep);
        }
        match &self.kernel_type {
            KernelType::Cpp => self.compile_cpp(output),
            KernelType::Cuda => self.compile_cuda(output),
            KernelType::Metal => self.compile_metal(output),
        }
    }

    fn compile_cpp(&mut self, output: &str) {
        if env::var("RISC0_SKIP_BUILD_KERNELS").is_ok() {
            return;
        }

        // [Win 原生迁移 2026-09-01 patch] keccak-sys 的 CPU 内核
        // (layout.cpp.inc)使用 C++20 designated initializer;上游 /std:c++17
        // 在 MSVC 下报 C7555。MSVC 用 /std:c++20(clang/GCC 的 c++17 保留;
        // 非 MSVC 平台上游原样)。
        // 另:rv32im-sys 的 ffi.cpp → poolstl.hpp 用 std::min/max,Windows 的
        // min/max 宏污染报 C2589 → /DNOMINMAX(仅 MSVC)。
        let is_msvc = env::var("CARGO_CFG_TARGET_ENV").is_ok_and(|v| v == "msvc");
        let std_flag = if is_msvc { "/std:c++20" } else { "-std=c++17" };

        let mut build = cc::Build::new();
        build
            .cpp(true)
            .debug(false)
            .files(&self.files)
            .includes(&self.inc_dirs)
            .flag_if_supported(std_flag);
        if is_msvc {
            build.flag("/DNOMINMAX");
        }
        build
            .flag_if_supported("-fno-var-tracking")
            .flag_if_supported("-fno-var-tracking-assignments")
            .flag_if_supported("-g0")
            .compile(output);
    }

    fn compile_cuda(&mut self, output: &str) {
        println!("cargo:rerun-if-env-changed=NVCC_APPEND_FLAGS");
        println!("cargo:rerun-if-env-changed=NVCC_PREPEND_FLAGS");
        println!("cargo:rerun-if-env-changed=RISC0_CUDART_LINKAGE");
        println!("cargo:rerun-if-env-changed=NVCC_CCBIN");

        for inc_dir in self.inc_dirs.iter() {
            rerun_if_changed(inc_dir);
        }

        if env::var("RISC0_SKIP_BUILD_KERNELS").is_ok() {
            let out_dir = env::var("OUT_DIR").map(PathBuf::from).unwrap();
            let out_path = out_dir.join(format!("lib{output}-skip.a"));
            fs::OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&out_path)
                .unwrap();
            println!("cargo:{}={}", output, out_path.display());
            return;
        }

        let mut build = cc::Build::new();

        for file in self.files.iter() {
            build.file(file);
        }

        for inc in self.inc_dirs.iter() {
            build.include(inc);
        }

        for flag in self.flags.iter() {
            build.flag(flag);
        }

        // [Win 原生迁移 2026-09-01 patch] `-Xcompiler -Wno-missing-braces,...` 是
        // GNU 风格 host warning 抑制;MSVC cl.exe 不认(`-Wno-*`),报 D8021 致命错误。
        // 上游硬编码这两行,未考虑 MSVC host。此处仅对该两 flag 做 MSVC 门控
        // (nvcc 的 -diag-suppress / -Xcudafe 是 nvcc 自身的,MSVC 无关,保留)。
        // 若需恢复上游行为:删除本 if 门控。
        let is_msvc_host = env::var("CARGO_CFG_TARGET_ENV").is_ok_and(|v| v == "msvc");
        // 系统 cuda.h 绝对路径(force-include,解决 CCCL driver_api include 顺序)
        let cuda_h_path = PathBuf::from(env::var("CUDA_PATH").expect("$CUDA_PATH is not set"))
            .join("include")
            .join("cuda.h");

        if env::var_os("NVCC_PREPEND_FLAGS").is_none() && env::var_os("NVCC_APPEND_FLAGS").is_none()
        {
            build.flag("-arch=native");
        }

        let cudart = env::var("RISC0_CUDART_LINKAGE").unwrap_or("static".to_string());

        build
            .cuda(true)
            .cudart(&cudart)
            .debug(false)
            .ccbin(env::var("NVCC_CCBIN").is_err())
            .flag("-diag-suppress=177")
            .flag("-diag-suppress=2922")
            .flag("-Xcudafe")
            .flag("--display_error_number");
        if !is_msvc_host {
            build
                .flag("-Xcompiler")
                .flag("-Wno-missing-braces,-Wno-unused-function");
        } else {
            // [Win 原生迁移 2026-09-01 patch] VS Build Tools 2026(VC 14.51)对
            // CUDA 13.2 已官方支持,-allow-unsupported-compiler 无害(保留防御)。
            // CUDA 13 的 CCCL 头要求 MSVC 标准预处理器(官方逃生宏)。
            // -std=c++17:risc0-sys 的 sppark 风格 .cu(mont_t.cuh 的
            // `size_t n=n` 默认参数)在 C++20 下 nvcc 解析报错;
            // 需要 C++20 designated initializer 的 keccak-sys/rv32im-sys
            // 各自手写 cc::Build 时显式 -std=c++20,不经由此通用层。
            // risc0-circuit-recursion-sys 的 ffi.cu 在 include <cuda_runtime.h>
            // 之前先 include <thrust/tuple.h>(通过 context.h),CUDA 13 的
            // CCCL driver_api.h 在 `_CCCL_HAS_CTK()` 分支需要 `cuGetProcAddress`
            // 声明(来自 <cuda.h>),include 顺序错误 → identifier undefined。
            // 用 -include 系统 cuda.h(绝对路径,不经过 include 搜索路径,
            // 避免被 risc0 本地同名 cuda.h 遮蔽)强制最先包含。
            build
                .flag("-include")
                .flag(cuda_h_path.as_os_str())
                .flag("-allow-unsupported-compiler")
                .flag("-std=c++17")
                .flag("-DTHRUST_DISABLE_ABI_NAMESPACE")
                .flag("-DTHRUST_IGNORE_ABI_NAMESPACE_ERROR")
                .flag("-DCUB_DISABLE_NAMESPACE_MAGIC")
                .flag("-DCUB_IGNORE_NAMESPACE_MAGIC_ERROR")
                .flag("-Xcompiler")
                .flag("/DCCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING")
                .flag("-Xcompiler")
                .flag("/utf-8");
        }
        // [Win 原生迁移 2026-09-01 patch] CXXFLAGS(env-win.ps1 的 MSVC 风格
        // /std:c++20 /DNOMINMAX)经 cc-rs 透传为 `-Xcompiler /std:c++20` 给
        // cl.exe 后,nvcc 内置 cudafe++(device 代码前端)解析崩溃
        // (0xC0000409 STATUS_STACK_BUFFER_OVERRUN,实测触发源)。CUDA 内核
        // 不需要 CXXFLAGS(与 sppark 同理,见 vendor/sppark/build.rs)。
        // 注意:本 build script 进程内移除,不影响其他 crate 的 build script。
        let _ = env::remove_var("CXXFLAGS");
        build.compile(output);
    }

    fn compile_metal(&mut self, output: &str) {
        let target = env::var("TARGET").unwrap();
        let sdk_name = if target.ends_with("ios") {
            "iphoneos"
        } else if target.ends_with("ios-sim") {
            "iphonesimulator"
        } else if target.ends_with("darwin") {
            "macosx"
        } else {
            panic!("unsupported target: {target}")
        };

        self.cached_compile(
            output,
            "metallib",
            METAL_INCS,
            &[],
            &[sdk_name.to_string()],
            |out_dir, out_path, sys_inc_dir, _flags| {
                let files: Vec<_> = self.files.iter().map(|x| x.as_path()).collect();

                let air_paths: Vec<_> = files
                    .into_par_iter()
                    .map(|src| {
                        let air_path = out_dir.join(src).with_extension("").with_extension("air");
                        if let Some(parent) = air_path.parent() {
                            fs::create_dir_all(parent).unwrap();
                        }
                        let mut cmd = Command::new("xcrun");
                        cmd.args(["--sdk", sdk_name]);
                        cmd.arg("metal");
                        cmd.arg("-o").arg(&air_path);
                        cmd.arg("-c").arg(src);
                        cmd.arg("-I").arg(sys_inc_dir);
                        cmd.arg("-Wno-unused-variable");
                        for inc_dir in self.inc_dirs.iter() {
                            cmd.arg("-I").arg(inc_dir);
                        }
                        println!("Running: {cmd:?}");
                        let status = cmd.status().unwrap();
                        if !status.success() {
                            panic!("Could not build metal kernels");
                        }
                        air_path
                    })
                    .collect();

                let result = Command::new("xcrun")
                    .args(["--sdk", sdk_name])
                    .arg("metallib")
                    .args(air_paths)
                    .arg("-o")
                    .arg(out_path)
                    .status()
                    .unwrap();
                if !result.success() {
                    panic!("Could not build metal kernels");
                }
            },
        );
    }

    fn cached_compile<F: Fn(&Path, &Path, &Path, &[String])>(
        &self,
        output: &str,
        extension: &str,
        assets: &[(&str, &str)],
        flags: &[String],
        tags: &[String],
        inner: F,
    ) {
        let out_dir = env::var("OUT_DIR").map(PathBuf::from).unwrap();
        if env::var("RISC0_SKIP_BUILD_KERNELS").is_ok() {
            let out_path = out_dir
                .join("skip-".to_string() + output)
                .with_extension(extension);
            fs::OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&out_path)
                .unwrap();
            println!("cargo:{}={}", output, out_path.display());
            return;
        }

        let out_path = out_dir.join(output).with_extension(extension);
        let sys_inc_dir = out_dir.join("_sys_");

        let cache_dir = risc0_cache();
        if !cache_dir.is_dir() {
            fs::create_dir_all(&cache_dir).unwrap();
        }

        let temp_dir = tempdir_in(&cache_dir).unwrap();
        let mut hasher = Hasher::new();
        for flag in flags {
            hasher.add_flag(flag);
        }
        for tag in tags {
            hasher.add_flag(tag);
        }
        for src in self.files.iter() {
            hasher.add_file(src);
        }
        for (name, contents) in assets {
            let path = sys_inc_dir.join(name);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(&path, contents).unwrap();
            hasher.add_file(path);
        }
        for dep in self.deps.iter() {
            hasher.add_file(dep);
        }
        let digest = hasher.finalize();
        let cache_path = cache_dir.join(digest).with_extension(extension);
        if !cache_path.is_file() {
            let tmp_dir = temp_dir.path();
            let tmp_path = tmp_dir.join(output).with_extension(extension);
            inner(tmp_dir, &tmp_path, &sys_inc_dir, flags);
            fs::rename(tmp_path, &cache_path).unwrap();
        }
        fs::copy(cache_path, &out_path).unwrap();

        println!("cargo:{}={}", output, out_path.display());
    }
}

fn risc0_cache() -> PathBuf {
    directories::ProjectDirs::from("com.risczero", "RISC Zero", "risc0")
        .unwrap()
        .cache_dir()
        .into()
}

struct Hasher {
    sha: Sha256,
}

impl Hasher {
    pub fn new() -> Self {
        Self { sha: Sha256::new() }
    }

    pub fn add_flag(&mut self, flag: &str) {
        self.sha.update(flag);
    }

    pub fn add_file<P: AsRef<Path>>(&mut self, path: P) {
        let bytes = fs::read(path).unwrap();
        self.sha.update(bytes);
    }

    pub fn finalize(self) -> String {
        hex::encode(self.sha.finalize())
    }
}

fn rerun_if_changed<P: AsRef<Path>>(path: P) {
    println!("cargo:rerun-if-changed={}", path.as_ref().display());
}
