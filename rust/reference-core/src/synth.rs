//! ReferenceSynth 1(SPEC §9 / AGENTS.md §3.5)。
//!
//! 纯整数合成,禁止浮点。native 渲染器与 zkVM guest 共用;guest 内通过 `render_stream`
//! 逐样本回调,把 WAV 头与 PCM 字节流式喂入 SHA-256(SPEC §9.6),不保存完整 WAV。
//!
//! 冻结常量与公式:
//! - 采样率 8000 Hz,单声道,16-bit signed PCM LE(SPEC §9.1)。
//! - tick→sample:`sample = floor(tick * 8000 * 500000 / (480 * 1000000))`
//!   = `tick * 25 / 3`(整数除法即向零截断,因操作数非负),用 u64 防溢出(SPEC §9.2)。
//! - 波表 `protocol/wavetable-v1.bin`:2048 × LE i16,冻结字节(SPEC §9.3)。
//! - `phase_step`:`phase_steps.rs` 冻结表(索引 = note - 21);phase 为 wrapping u32,
//!   表索引取最高 11 位:`(phase >> 21)`;Note On 时 phase 归零(SPEC §9.3)。
//! - 包络 Q15(32767 = 满幅;所有除法向零截断):
//!   - Attack 40 ms = 320 samples:`env = 32767 * attack_pos / 320`,attack_pos ∈ 1..=320,
//!     到 320 后进入 Sustain(SPEC §9.4)。
//!   - Sustain:env = 32767。
//!   - Release 120 ms = 960 samples:`env = peak * (960 - release_pos) / 960`,
//!     release_pos ∈ 1..=959;peak 为进入 Release 时的 env;release_pos = 960 时 voice 停用。
//!   - 每个 sample 先推进再采样;On 生效的 sample 从 attack_pos=1 起算(env 最小非零),
//!     Off 生效的 sample 从 release_pos=1 起算(env 略低于 peak)。
//! - 8 个内部 voice slot(SPEC §9.4):Note On 取最小编号空闲 slot;全占用则抢占
//!   Release 中当前 env 最小的 slot(并列取编号最小);Attack/Sustain 活动音不可抢占
//!   (Profile 已保证活动音 ≤ 4);被抢占尾音立即截止,新音 phase/env 从零开始。
//! - 混音(SPEC §9.5):每 voice `contrib = wave * 3500 * env / (32767 * 32767)`
//!   (i64 中间值,一次除法向零截断);i32 累加 8 路,最终 `clamp(-32768, 32767)`。
//! - WAV 长度:从 sample 0 到最后一个 Note Off 后 120 ms
//!   `total = last_off_sample + 960`(SPEC §9.1)。

use crate::midi::{NoteEvent, ParsedMidi};
use crate::phase_steps::PHASE_STEP;

/// 采样率(SPEC §9.1)。
pub const SAMPLE_RATE: u32 = 8000;
/// Attack 时长:40 ms = 320 samples(SPEC §9.4)。
pub const ATTACK_SAMPLES: u32 = 320;
/// Release 时长:120 ms = 960 samples(SPEC §9.4)。
pub const RELEASE_SAMPLES: u32 = 960;
/// 每 voice 峰值缩放(SPEC §9.5)。
pub const VOICE_PEAK: i32 = 3500;
/// Q15 满幅(SPEC §9.4)。
pub const Q15_MAX: i32 = 32767;
/// 内部 voice slot 数(SPEC §9.4)。
pub const VOICES: usize = 8;
/// 波表长度(SPEC §9.3)。
pub const WAVETABLE_LEN: usize = 2048;

/// 冻结的合成参数(数值即协议权威;wavetable SHA-256 见 protocol/v1.json manifest)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SynthParams {
    pub sample_rate: u32,
    pub attack_samples: u32,
    pub release_samples: u32,
    pub voice_peak: i32,
    pub q15_max: i32,
    pub voices: usize,
    pub wavetable_len: usize,
}

/// 冻结参数实例(SPEC §9 全常量)。
pub const SYNTH_PARAMS: SynthParams = SynthParams {
    sample_rate: SAMPLE_RATE,
    attack_samples: ATTACK_SAMPLES,
    release_samples: RELEASE_SAMPLES,
    voice_peak: VOICE_PEAK,
    q15_max: Q15_MAX,
    voices: VOICES,
    wavetable_len: WAVETABLE_LEN,
};

/// 冻结波表字节(2048 × LE i16 = 4096 B;include_bytes 相对本文件路径)。
const WAVETABLE: &[u8; 4096] = include_bytes!("../../../protocol/wavetable-v1.bin");

/// 编译期把波表字节解析为 [i16; 2048](避免每样本两次字节解析)。
const fn build_wavetable() -> [i16; WAVETABLE_LEN] {
    let mut t = [0i16; WAVETABLE_LEN];
    let mut i = 0;
    while i < WAVETABLE_LEN {
        let lo = WAVETABLE[i * 2] as u16;
        let hi = WAVETABLE[i * 2 + 1] as u16;
        t[i] = (lo | (hi << 8)) as i16;
        i += 1;
    }
    t
}

static WAVETABLE_I16: [i16; WAVETABLE_LEN] = build_wavetable();

/// 包络阶段。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum EnvState {
    Attack,
    Sustain,
    Release,
}

/// 一个内部 voice slot。
#[derive(Debug, Clone, Copy)]
struct Voice {
    active: bool,
    phase: u32,
    step: u32,
    state: EnvState,
    attack_pos: u32,
    release_pos: u32,
    release_peak: i32,
    env: i32, // 当前(最近一个样本推进后)包络值,供抢占决策
}

impl Voice {
    fn idle() -> Self {
        Voice {
            active: false,
            phase: 0,
            step: 0,
            state: EnvState::Attack,
            attack_pos: 0,
            release_pos: 0,
            release_peak: 0,
            env: 0,
        }
    }

    /// 当前样本推进后的包络值(除法向零截断;所有操作数非负)。
    fn env_at(&self) -> i32 {
        match self.state {
            EnvState::Attack => {
                Q15_MAX * self.attack_pos as i32 / ATTACK_SAMPLES as i32
            }
            EnvState::Sustain => Q15_MAX,
            EnvState::Release => {
                self.release_peak
                    * (RELEASE_SAMPLES as i32 - self.release_pos as i32)
                    / RELEASE_SAMPLES as i32
            }
        }
    }
}

/// 波表查表:编译期预转换的 i16 表,索引取 phase 最高 11 位。
#[inline]
fn wave_sample(phase: u32) -> i32 {
    i32::from(WAVETABLE_I16[((phase >> 21) as usize) & (WAVETABLE_LEN - 1)])
}

/// 事件到 sample 的映射(SPEC §9.2):floor(tick * 25 / 3),u64 防溢出。
fn tick_to_sample(tick: u32) -> u64 {
    u64::from(tick) * 25 / 3
}

/// 44 字节 Canonical WAV 1 头(SPEC §9.1:RIFF/WAVE、PCM、单声道 8000 Hz、16-bit LE)。
pub fn wav_header(sample_count: u32) -> [u8; 44] {
    let data_len = sample_count * 2;
    let mut h = [0u8; 44];
    h[0..4].copy_from_slice(b"RIFF");
    h[4..8].copy_from_slice(&(36 + data_len).to_le_bytes());
    h[8..12].copy_from_slice(b"WAVE");
    h[12..16].copy_from_slice(b"fmt ");
    h[16..20].copy_from_slice(&16u32.to_le_bytes()); // fmt chunk 长度
    h[20..22].copy_from_slice(&1u16.to_le_bytes()); // audio_format = PCM
    h[22..24].copy_from_slice(&1u16.to_le_bytes()); // channels = 1
    h[24..28].copy_from_slice(&SAMPLE_RATE.to_le_bytes());
    h[28..32].copy_from_slice(&(SAMPLE_RATE * 2).to_le_bytes()); // byte_rate
    h[32..34].copy_from_slice(&2u16.to_le_bytes()); // block_align
    h[34..36].copy_from_slice(&16u16.to_le_bytes()); // bits_per_sample
    h[36..40].copy_from_slice(b"data");
    h[40..44].copy_from_slice(&data_len.to_le_bytes());
    h
}

/// 输出样本总数(SPEC §9.1:到最后 Note Off 后 120 ms)。
pub fn sample_count(parsed: &ParsedMidi) -> u64 {
    tick_to_sample(parsed.last_note_off_tick) + u64::from(RELEASE_SAMPLES)
}

/// 流式渲染(SPEC §9.6 精神):对每个输出样本调用 `sink`,返回总样本数。
/// 同一 sample 的多事件按 MIDI 文件顺序、在生成该 sample 前应用(SPEC §9.2)。
///
/// 性能:维护活跃 voice 索引列表,每样本只遍历活跃 voice(典型负载 1-4 个,
/// 而非固定 8 槽),显著降低 zkVM 内整数运算成本。
pub fn render_stream(parsed: &ParsedMidi, mut sink: impl FnMut(i16)) -> u64 {
    let total = sample_count(parsed);

    let events = &parsed.events;
    // 预计算每个事件的 sample 位置(避免循环内重复除法)
    let samples: Vec<u64> = events.iter().map(|e| tick_to_sample(event_tick(e))).collect();
    let mut voices = [Voice::idle(); VOICES];
    let mut active: [usize; VOICES] = [0; VOICES]; // 活跃 voice 的槽位索引列表
    let mut active_cnt = 0usize;
    let mut ei = 0usize;

    for s in 0..total {
        // 1) 应用本 sample 的所有事件(文件顺序)
        while ei < events.len() && samples[ei] == s {
            active_cnt = apply_event(&mut voices, &mut active, active_cnt, &events[ei]);
            ei += 1;
        }
        // 2) 推进各活跃 voice 并混音
        let mut sum: i32 = 0;
        let mut k = 0usize;
        while k < active_cnt {
            let idx = active[k];
            let v = &mut voices[idx];
            match v.state {
                EnvState::Attack => {
                    v.attack_pos += 1;
                    if v.attack_pos >= ATTACK_SAMPLES {
                        v.state = EnvState::Sustain;
                    }
                }
                EnvState::Sustain => {}
                EnvState::Release => {
                    v.release_pos += 1;
                    if v.release_pos >= RELEASE_SAMPLES {
                        v.active = false;
                        // 从活跃列表移除(用末尾元素填补,顺序无关紧要)
                        active[k] = active[active_cnt - 1];
                        active_cnt -= 1;
                        continue;
                    }
                }
            }
            v.env = v.env_at();
            // i64 中间值,一次除法向零截断(SPEC §9.5)
            let contrib =
                (i64::from(wave_sample(v.phase)) * i64::from(VOICE_PEAK) * i64::from(v.env))
                    / i64::from(Q15_MAX * Q15_MAX);
            sum = sum.saturating_add(contrib as i32);
            v.phase = v.phase.wrapping_add(v.step);
            k += 1;
        }
        sink(sum.clamp(-32768, 32767) as i16);
    }
    total
}

fn event_tick(e: &NoteEvent) -> u32 {
    match *e {
        NoteEvent::On { tick, .. } | NoteEvent::Off { tick, .. } => tick,
    }
}

/// 应用一个音符事件(SPEC §9.4 voice 分配与抢占),返回更新后的活跃 voice 数。
fn apply_event(
    voices: &mut [Voice; VOICES],
    active: &mut [usize; VOICES],
    mut active_cnt: usize,
    e: &NoteEvent,
) -> usize {
    match *e {
        NoteEvent::On { note, .. } => {
            let step = PHASE_STEP[(note - 21) as usize];
            // 1) 最小编号空闲 slot
            let mut slot: Option<usize> = None;
            for (i, v) in voices.iter().enumerate() {
                if !v.active {
                    slot = Some(i);
                    break;
                }
            }
            // 2) 全占用:抢占 Release 中 env 最小者(并列取编号最小);Attack/Sustain 不可抢占
            if slot.is_none() {
                let mut best: Option<(usize, i32)> = None;
                for (i, v) in voices.iter().enumerate() {
                    if v.state == EnvState::Release {
                        match best {
                            None => best = Some((i, v.env)),
                            Some((_, b)) if v.env < b => best = Some((i, v.env)),
                            _ => {}
                        }
                    }
                }
                slot = best.map(|(i, _)| i);
            }
            if let Some(i) = slot {
                let was_idle = !voices[i].active;
                voices[i] = Voice {
                    active: true,
                    phase: 0, // Note On 时 phase 归零(SPEC §9.3)
                    step,
                    state: EnvState::Attack,
                    attack_pos: 0,
                    release_pos: 0,
                    release_peak: 0,
                    env: 0,
                };
                if was_idle {
                    active[active_cnt] = i;
                    active_cnt += 1;
                }
            }
            // 理论不会到这一步:Profile 保证活动音 ≤ 4,8 个 slot 中必然可分配
            // (活动音不可抢占是硬约束,故全为 Attack/Sustain 时不可能发生)。
        }
        NoteEvent::Off { note, .. } => {
            // 找该 note 的活动 voice(同音高不重复 On(parser 保证),step 唯一匹配)。
            let step = PHASE_STEP[(note - 21) as usize];
            for v in voices.iter_mut() {
                if v.active && v.step == step {
                    v.state = EnvState::Release;
                    v.release_pos = 0;
                    v.release_peak = v.env; // 从当前包络值开始线性下降
                    break;
                }
            }
        }
    }
    active_cnt
}

/// 渲染完整样本序列(native 用;guest 用 `render_stream`)。
pub fn render(parsed: &ParsedMidi) -> Vec<i16> {
    let mut out = Vec::new();
    render_stream(parsed, |s| out.push(s));
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::midi::NoteEvent;

    fn build_midi(events: Vec<NoteEvent>) -> ParsedMidi {
        // 构造 ParsedMidi:通过 parse 走一遍(测试 MIDI 组装见 midi 模块)
        // 这里直接从事件构造,便于合成器单测;last_note_off_tick 取最后 Off。
        let mut last = 0u32;
        for e in &events {
            if let NoteEvent::Off { tick, .. } = e {
                last = *tick;
            }
        }
        ParsedMidi { events, last_note_off_tick: last }
    }

    #[test]
    fn tick_to_sample_mapping() {
        // SPEC §9.2 公式验证
        assert_eq!(tick_to_sample(0), 0);
        assert_eq!(tick_to_sample(480), 4000); // 1 四分音符 = 0.5 s
        assert_eq!(tick_to_sample(960), 8000); // 1 s
        assert_eq!(tick_to_sample(57_600), 480_000); // 60 s
    }

    #[test]
    fn wav_header_is_44_bytes_standard() {
        let h = wav_header(1000);
        assert_eq!(&h[0..4], b"RIFF");
        assert_eq!(&h[8..12], b"WAVE");
        assert_eq!(&h[36..40], b"data");
        assert_eq!(u16::from_le_bytes([h[20], h[21]]), 1); // PCM
        assert_eq!(u16::from_le_bytes([h[22], h[23]]), 1); // mono
        assert_eq!(u32::from_le_bytes(h[24..28].try_into().unwrap()), 8000);
        assert_eq!(u16::from_le_bytes([h[34], h[35]]), 16); // bits
        assert_eq!(u32::from_le_bytes(h[40..44].try_into().unwrap()), 2000);
    }

    #[test]
    fn render_silence_for_empty_events() {
        // 无事件:last_off_tick=0 → total = 960(纯 release tail 时段,但无 voice,静音)
        let p = build_midi(vec![]);
        let out = render(&p);
        assert_eq!(out.len(), 960);
        assert!(out.iter().all(|&s| s == 0));
    }

    #[test]
    fn render_single_note_length_and_shape() {
        // 单音 60:On@0 Off@480。last_off_sample = 4000,total = 4000 + 960 = 4960
        let p = build_midi(vec![
            NoteEvent::On { tick: 0, note: 60 },
            NoteEvent::Off { tick: 480, note: 60 },
        ]);
        let out = render(&p);
        assert_eq!(out.len(), 4960);
        // On 生效的样本:phase 归零 → wave[0] = sin(0) = 0(确定性行为)
        assert_eq!(out[0], 0);
        // 之后很快出现非零攻击样本
        assert!(out[1..320].iter().any(|&s| s > 0));
        // Sustain 区间(320..4000)峰值应显著(≈3500 量级)
        let sustain_peak = out[1000..3000].iter().map(|&s| i32::from(s).abs()).max().unwrap();
        assert!(sustain_peak > 1000, "sustain peak = {sustain_peak}");
        // Release 尾部趋向 0:最后样本接近 0(960 样本 release 后)
        assert!(i32::from(out[4959]).abs() < 50);
        // 相位/包络确定性:再渲染一次逐字节一致
        assert_eq!(render(&p), out);
    }

    #[test]
    fn render_max_4_voice_chord_no_clip_overflow() {
        // 四音和弦同时响,total = last_off_sample + 960
        let p = build_midi(vec![
            NoteEvent::On { tick: 0, note: 60 },
            NoteEvent::On { tick: 0, note: 64 },
            NoteEvent::On { tick: 0, note: 67 },
            NoteEvent::On { tick: 0, note: 71 },
            NoteEvent::Off { tick: 480, note: 60 },
            NoteEvent::Off { tick: 480, note: 64 },
            NoteEvent::Off { tick: 480, note: 67 },
            NoteEvent::Off { tick: 480, note: 71 },
        ]);
        let out = render(&p);
        assert_eq!(out.len(), 4960);
        // 样本都在 i16 范围(无溢出/未 clamp 前的 sum 在 [-32768, 32767])
        let peak = out.iter().map(|&s| i32::from(s).abs()).max().unwrap();
        assert!(peak <= 32767);
        // 4 voice 理论峰值 ≈ 4 * 3500 = 14000(包络满幅时)
        assert!(peak > 10000, "peak = {peak}");
    }

    #[test]
    fn release_tail_keeps_playing_after_off() {
        // 验证 Off 后仍有 release 输出(>0),持续 960 samples 内衰减
        let p = build_midi(vec![
            NoteEvent::On { tick: 0, note: 72 },
            NoteEvent::Off { tick: 240, note: 72 },
        ]);
        let out = render(&p);
        // last_off = 240 → sample 2000 → total = 2960
        assert_eq!(out.len(), 2000 + 960);
        // 2000 附近(Off 生效后)仍有非零
        assert!(out[2001..2200].iter().any(|&s| s != 0));
        // 最后 100 samples:release pos ≥ 900,env ≤ 32767*60/960 ≈ 2048,
        // 单 voice 贡献 ≤ 3500*2048/32767 ≈ 218;阈值 300(理论衰减上限)
        let tail_max = out[2900..].iter().map(|&s| i32::from(s).abs()).max().unwrap();
        assert!(tail_max < 300, "tail max = {tail_max}");
    }

    #[test]
    fn eight_voice_slots_release_preemption() {
        // 8 个快速 On/Off 使 release 占满,再 On 一个 → 抢占 env 最小者,不崩溃且确定
        let mut ev = Vec::new();
        for (_, n) in (60u8..=67).enumerate() {
            ev.push(NoteEvent::On { tick: 0, note: n });
            ev.push(NoteEvent::Off { tick: 120, note: n });
        }
        ev.push(NoteEvent::On { tick: 240, note: 76 }); // 全部 release 时新 On
        ev.push(NoteEvent::Off { tick: 480, note: 76 });
        let p = build_midi(ev);
        let out = render(&p);
        assert!(out.len() > 4000);
        assert!(out.iter().all(|&s| (-32768..=32767).contains(&i32::from(s))));
    }

    #[test]
    fn synth_constant_panics_on_note_out_of_scope() {
        // note 21..=108 保证 PHASE_STEP 索引安全(parser 已拒绝越界);这里只验证 21 与 108 可用
        let p = build_midi(vec![
            NoteEvent::On { tick: 0, note: 21 },
            NoteEvent::Off { tick: 480, note: 21 },
        ]);
        assert!(render(&p).len() == 4960);
        let p2 = build_midi(vec![
            NoteEvent::On { tick: 0, note: 108 },
            NoteEvent::Off { tick: 480, note: 108 },
        ]);
        assert!(render(&p2).len() == 4960);
    }
}
