//! reference-native —— 真实 WAV 渲染 + golden vector 工具(Phase 2)。
//!
//! 用法:
//!   reference-native render  <input.mid> <output.wav>
//!   reference-native golden <input.mid> <salt.bin> <out.json> [--wav out.wav]
//!
//! `golden` 输出 SPEC §17.1 要求的对拍数据:MIDI SHA-256、盐、C_M、事件列表、
//! sample 数、头尾样本、完整 C_V(供 native == guest == Python 三方一致验证)。

use reference_core::midi::{parse_midi, NoteEvent};
use reference_core::synth::{render, wav_header};
use reference_core::{commit_midi, commit_reference_wav};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;

#[derive(Serialize)]
struct GoldenVector {
    midi_sha256: String,
    salt: String,
    c_m: String,
    events: Vec<GoldenEvent>,
    last_note_off_tick: u32,
    sample_count: usize,
    head_samples: Vec<i16>,
    tail_samples: Vec<i16>,
    c_v: String,
}

#[derive(Serialize)]
struct GoldenEvent {
    #[serde(rename = "type")]
    kind: &'static str,
    tick: u32,
    note: u8,
}

fn hex32(b: &[u8; 32]) -> String {
    hex::encode(b)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("render") => {
            let (midi, wav) = (&args[2], &args[3]);
            let data = fs::read(midi).unwrap_or_else(|e| panic!("读取 {midi} 失败: {e}"));
            let parsed = parse_midi(&data).unwrap_or_else(|e| panic!("MIDI 解析失败: {e}"));
            let samples = render(&parsed);
            let mut out = Vec::with_capacity(44 + samples.len() * 2);
            out.extend_from_slice(&wav_header(samples.len() as u32));
            for s in &samples {
                out.extend_from_slice(&s.to_le_bytes());
            }
            fs::write(wav, &out).unwrap_or_else(|e| panic!("写 {wav} 失败: {e}"));
            println!(
                "OK  {}  samples={}  bytes={}  C_V={}",
                wav,
                samples.len(),
                out.len(),
                hex32(&commit_reference_wav(&out))
            );
        }
        Some("golden") => {
            let (midi, salt_f, out_json) = (&args[2], &args[3], &args[4]);
            let data = fs::read(midi).unwrap_or_else(|e| panic!("读取 {midi} 失败: {e}"));
            let salt = fs::read(salt_f).unwrap_or_else(|e| panic!("读取 {salt_f} 失败: {e}"));
            assert_eq!(salt.len(), 32, "盐必须 32 字节");
            let salt_arr: [u8; 32] = salt.try_into().unwrap();

            let parsed = parse_midi(&data).unwrap_or_else(|e| panic!("MIDI 解析失败: {e}"));
            let samples = render(&parsed);

            let mut wav = Vec::with_capacity(44 + samples.len() * 2);
            wav.extend_from_slice(&wav_header(samples.len() as u32));
            for s in &samples {
                wav.extend_from_slice(&s.to_le_bytes());
            }

            let gv = GoldenVector {
                midi_sha256: hex::encode(Sha256::digest(&data)),
                salt: hex32(&salt_arr),
                c_m: hex32(&commit_midi(&data, &salt_arr)),
                events: parsed
                    .events
                    .iter()
                    .map(|e| match *e {
                        NoteEvent::On { tick, note } => GoldenEvent {
                            kind: "on",
                            tick,
                            note,
                        },
                        NoteEvent::Off { tick, note } => GoldenEvent {
                            kind: "off",
                            tick,
                            note,
                        },
                    })
                    .collect(),
                last_note_off_tick: parsed.last_note_off_tick,
                sample_count: samples.len(),
                head_samples: samples.iter().take(16).copied().collect(),
                tail_samples: samples.iter().rev().take(16).copied().collect::<Vec<_>>().into_iter().rev().collect(),
                c_v: hex32(&commit_reference_wav(&wav)),
            };
            let json = serde_json::to_string_pretty(&gv).unwrap();
            fs::write(out_json, &json).unwrap_or_else(|e| panic!("写 {out_json} 失败: {e}"));
            // 可选:同时输出 WAV
            if let Some(i) = args.iter().position(|a| a == "--wav") {
                let wav_path = &args[i + 1];
                fs::write(wav_path, &wav).unwrap_or_else(|e| panic!("写 {wav_path} 失败: {e}"));
                println!("wav: {wav_path}");
            }
            println!("golden: {out_json}");
        }
        _ => {
            eprintln!("usage: reference-native render <midi> <out.wav> | golden <midi> <salt.bin> <out.json> [--wav out.wav]");
            std::process::exit(2);
        }
    }
}
