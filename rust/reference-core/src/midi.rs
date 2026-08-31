//! MIDI Profile 1 解析器(SPEC §8 / AGENTS.md §3.4)。
//!
//! 解析器 MUST fail closed:一切未列出的事件、损坏长度、溢出、非最短 VLQ 或超范围值都拒绝,
//! 绝不静默忽略。native 渲染器与 zkVM guest 共用本模块(SPEC §4)。

use std::fmt;

/// 原始 MIDI 文件上限(SPEC §8.1)。
pub const MIDI_MAX_BYTES: usize = 64 * 1024;
/// division 固定 480 PPQ(SPEC §8.1)。
pub const DIVISION: u16 = 480;
/// note number 范围(SPEC §8.3)。
pub const NOTE_MIN: u8 = 21;
pub const NOTE_MAX: u8 = 108;
/// 最多 Note On 数(SPEC §8.3)。
pub const MAX_NOTE_ONS: usize = 256;
/// 同时活动音符上限(SPEC §8.3)。
pub const MAX_SIMULTANEOUS: usize = 4;
/// 最后 Note Off 的 tick 上限 = 60 s(SPEC §8.3)。
pub const MAX_TICK: u32 = 57_600;
/// Set Tempo 值:500000 微秒/四分音符 = 120 BPM(SPEC §8.2)。
pub const TEMPO_US_PER_QUARTER: u32 = 500_000;

/// 渲染用音符事件(Set Tempo / Time Signature 只做解析期验证,不进入渲染)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoteEvent {
    On { tick: u32, note: u8 },
    Off { tick: u32, note: u8 },
}

/// 解析成功的输出。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedMidi {
    /// 按文件顺序(即 tick 非递减)排列的音符事件。
    pub events: Vec<NoteEvent>,
    /// 最后一个 Note Off 的 tick(≤ MAX_TICK)。
    pub last_note_off_tick: u32,
}

/// 解析失败原因(SPEC §17.2 拒绝测试逐一对应)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// 文件超过 64 KiB。
    TooLarge(usize),
    /// 缺 MThd 或 magic 不匹配。
    MissingHeader,
    /// MThd 声明长度 ≠ 6。
    HeaderLengthNot6(u32),
    /// format ≠ 0。
    NotFormat0(u16),
    /// track count ≠ 1。
    TrackCountNot1(u16),
    /// division bit15 置位(SMPTE 模式)。
    SmpteDivision,
    /// division ≠ 480。
    DivisionNot480(u16),
    /// 缺 MTrk 或 magic 不匹配。
    MissingTrack,
    /// 声明 track 长度超出可用字节。
    TrackLenExceeds(u32),
    /// 文件尾仍有字节(MTrk 声明长度小于实际)。
    TrailingBytes(usize),
    /// EOT 之后还有事件字节。
    EotNotLast,
    /// 事件中途截断。
    TruncatedEvent,
    /// VLQ 超过 4 字节。
    VlvTooLong,
    /// VLQ 非最短编码。
    VlvNotShortest,
    /// 非法 status 字节(0x00..=0x7F 等)。
    UnknownStatus(u8),
    /// SysEx(0xF0/0xF7)。
    SysExNotAllowed,
    /// system common(0xF1..=0xF6)。
    SystemCommonNotAllowed(u8),
    /// realtime(0xF8..=0xFE)。
    RealtimeNotAllowed(u8),
    /// 未知 meta 事件。
    UnknownMeta(u8),
    /// Set Tempo 不在 tick 0。
    TempoNotAtZero(u32),
    /// Set Tempo 出现多次。
    DuplicateTempo,
    /// Set Tempo 长度 ≠ 3。
    BadTempoLen(u32),
    /// Set Tempo 值 ≠ 500000。
    BadTempoValue(u32),
    /// Time Signature 不在 tick 0。
    TimeSigNotAtZero(u32),
    /// Time Signature 出现多次。
    DuplicateTimeSig,
    /// Time Signature 长度 ≠ 4。
    BadTimeSigLen(u32),
    /// Time Signature 值非 4/4、24、8。
    BadTimeSigValues(u8, u8, u8, u8),
    /// End of Track 长度 ≠ 0。
    BadEotLen(u32),
    /// End of Track 出现多次。
    DuplicateEot,
    /// 缺 Set Tempo。
    MissingTempo,
    /// 缺 End of Track。
    MissingEot,
    /// channel ≠ 0。
    ChannelNotZero(u8),
    /// 未知 channel 事件。
    UnknownChannelEvent(u8),
    /// note number 超出 21..=108。
    NoteOutOfRange(u8),
    /// Note On 总数超过 256。
    TooManyNoteOns,
    /// 同一音高未 Off 再次 On。
    DuplicateNoteOn { tick: u32, note: u8 },
    /// Note Off 无匹配活动音符。
    NoteOffWithoutOn { tick: u32, note: u8 },
    /// Note Off 与 Note On 同 tick(每音 ≥ 1 tick)。
    NoteOffSameTick { tick: u32, note: u8 },
    /// 同时活动音符超过 4。
    TooManySimultaneous(usize),
    /// 音符事件 tick 超过 57600(60 s)。
    TickOverflow { tick: u64 },
    /// EOT 时仍有悬挂音符。
    HangNote,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use ParseError::*;
        match self {
            TooLarge(n) => write!(f, "文件超过 64 KiB({n} B)"),
            MissingHeader => write!(f, "缺少 MThd"),
            HeaderLengthNot6(n) => write!(f, "MThd 长度必须为 6,收到 {n}"),
            NotFormat0(n) => write!(f, "必须为 Format 0,收到 {n}"),
            TrackCountNot1(n) => write!(f, "track 数必须为 1,收到 {n}"),
            SmpteDivision => write!(f, "禁止 SMPTE division"),
            DivisionNot480(n) => write!(f, "division 必须为 480 PPQ,收到 {n}"),
            MissingTrack => write!(f, "缺少 MTrk"),
            TrackLenExceeds(n) => write!(f, "MTrk 声明长度 {n} 超出文件"),
            TrailingBytes(n) => write!(f, "文件尾有多余 {n} 字节"),
            EotNotLast => write!(f, "End of Track 之后仍有事件"),
            TruncatedEvent => write!(f, "事件中途截断"),
            VlvTooLong => write!(f, "VLQ 超过 4 字节"),
            VlvNotShortest => write!(f, "VLQ 非最短编码"),
            UnknownStatus(s) => write!(f, "非法 status 字节 0x{s:02X}"),
            SysExNotAllowed => write!(f, "SysEx 被拒绝"),
            SystemCommonNotAllowed(s) => write!(f, "system common 0x{s:02X} 被拒绝"),
            RealtimeNotAllowed(s) => write!(f, "realtime 0x{s:02X} 被拒绝"),
            UnknownMeta(m) => write!(f, "未知 meta 事件 0x{m:02X}"),
            TempoNotAtZero(t) => write!(f, "Set Tempo 必须位于 tick 0,实际 {t}"),
            DuplicateTempo => write!(f, "Set Tempo 出现多次"),
            BadTempoLen(n) => write!(f, "Set Tempo 长度必须为 3,收到 {n}"),
            BadTempoValue(v) => write!(f, "Set Tempo 必须为 500000,收到 {v}"),
            TimeSigNotAtZero(t) => write!(f, "Time Signature 必须位于 tick 0,实际 {t}"),
            DuplicateTimeSig => write!(f, "Time Signature 出现多次"),
            BadTimeSigLen(n) => write!(f, "Time Signature 长度必须为 4,收到 {n}"),
            BadTimeSigValues(n, d, c, b) => {
                write!(f, "Time Signature 必须为 4/4、24、8,收到 {n}/{d}/{c}/{b}")
            }
            BadEotLen(n) => write!(f, "End of Track 长度必须为 0,收到 {n}"),
            DuplicateEot => write!(f, "End of Track 出现多次"),
            MissingTempo => write!(f, "缺少 Set Tempo"),
            MissingEot => write!(f, "缺少 End of Track"),
            ChannelNotZero(c) => write!(f, "channel 必须为 0,收到 {c}"),
            UnknownChannelEvent(s) => write!(f, "未知 channel 事件 0x{s:02X}"),
            NoteOutOfRange(n) => write!(f, "note {n} 超出 21..=108"),
            TooManyNoteOns => write!(f, "Note On 超过 256 个"),
            DuplicateNoteOn { tick, note } => write!(f, "note {note} 在 tick {tick} 重复 Note On"),
            NoteOffWithoutOn { tick, note } => write!(f, "note {note} 在 tick {tick} Note Off 无匹配"),
            NoteOffSameTick { tick, note } => write!(f, "note {note} 在 tick {tick} Off 与 On 同 tick"),
            TooManySimultaneous(n) => write!(f, "同时活动音符 {n} 超过 4"),
            TickOverflow { tick } => write!(f, "音符 tick {tick} 超过 57600(60 s)"),
            HangNote => write!(f, "End of Track 时仍有悬挂音符"),
        }
    }
}

/// 游标读取器(所有读取带边界检查)。
struct Reader<'a> {
    d: &'a [u8],
    p: usize,
}

impl<'a> Reader<'a> {
    fn new(d: &'a [u8]) -> Self {
        Reader { d, p: 0 }
    }

    fn take(&mut self, n: usize) -> Result<&'a [u8], ParseError> {
        if self.p + n > self.d.len() {
            return Err(ParseError::TruncatedEvent);
        }
        let s = &self.d[self.p..self.p + n];
        self.p += n;
        Ok(s)
    }

    fn u8(&mut self) -> Result<u8, ParseError> {
        Ok(self.take(1)?[0])
    }

    fn u16be(&mut self) -> Result<u16, ParseError> {
        Ok(u16::from_be_bytes(self.take(2)?.try_into().unwrap()))
    }

    fn u32be(&mut self) -> Result<u32, ParseError> {
        Ok(u32::from_be_bytes(self.take(4)?.try_into().unwrap()))
    }

    /// 标准 VLQ:≤4 字节、最短编码、值以 u32 返回。
    fn vlq(&mut self) -> Result<u32, ParseError> {
        let mut bytes = [0u8; 4];
        let mut n = 0usize;
        while n < 4 {
            let b = self.u8()?;
            bytes[n] = b;
            n += 1;
            if b & 0x80 == 0 {
                break;
            }
        }
        if n == 4 && bytes[3] & 0x80 != 0 {
            return Err(ParseError::VlvTooLong);
        }
        // 最短编码:多字节时首字节低 7 位必须非零(否则可少一个字节)。
        if n > 1 && bytes[0] & 0x7F == 0 {
            return Err(ParseError::VlvNotShortest);
        }
        let mut v: u32 = 0;
        for b in &bytes[..n] {
            v = (v << 7) | u32::from(b & 0x7F);
        }
        Ok(v)
    }
}

/// 解析 MIDI Profile 1(SPEC §8)。失败返回具体原因,无部分结果。
pub fn parse_midi(data: &[u8]) -> Result<ParsedMidi, ParseError> {
    if data.len() > MIDI_MAX_BYTES {
        return Err(ParseError::TooLarge(data.len()));
    }
    let mut r = Reader::new(data);

    // --- MThd(SPEC §8.1)---
    if r.take(4)? != b"MThd" {
        return Err(ParseError::MissingHeader);
    }
    let hlen = r.u32be()?;
    if hlen != 6 {
        return Err(ParseError::HeaderLengthNot6(hlen));
    }
    let format = r.u16be()?;
    if format != 0 {
        return Err(ParseError::NotFormat0(format));
    }
    let ntrks = r.u16be()?;
    if ntrks != 1 {
        return Err(ParseError::TrackCountNot1(ntrks));
    }
    let division = r.u16be()?;
    if division & 0x8000 != 0 {
        return Err(ParseError::SmpteDivision);
    }
    if division != DIVISION {
        return Err(ParseError::DivisionNot480(division));
    }

    // --- MTrk(SPEC §8.1)---
    if r.take(4)? != b"MTrk" {
        return Err(ParseError::MissingTrack);
    }
    let tlen = r.u32be()? as usize;
    let track = r.take(tlen).map_err(|_| ParseError::TrackLenExceeds(tlen as u32))?;
    if r.p != r.d.len() {
        return Err(ParseError::TrailingBytes(r.d.len() - r.p));
    }

    // --- 事件循环(fail-closed)---
    let mut tr = Reader::new(track);
    let mut tick: u64 = 0;
    let mut events: Vec<NoteEvent> = Vec::new();
    let mut tempo_seen = false;
    let mut timesig_seen = false;
    let mut eot_seen = false;
    let mut last_note_off_tick: u32 = 0;
    let mut active: [Option<u32>; 128] = [None; 128]; // note -> on tick
    let mut active_count = 0usize;
    let mut note_on_count = 0usize;

    macro_rules! note_off {
        ($note:expr, $tick:expr) => {{
            let note = $note;
            if !(NOTE_MIN..=NOTE_MAX).contains(&note) {
                return Err(ParseError::NoteOutOfRange(note));
            }
            match active[note as usize] {
                None => return Err(ParseError::NoteOffWithoutOn { tick: $tick, note }),
                Some(on_tick) => {
                    if $tick <= on_tick {
                        return Err(ParseError::NoteOffSameTick { tick: $tick, note });
                    }
                    active[note as usize] = None;
                    active_count -= 1;
                    events.push(NoteEvent::Off { tick: $tick, note });
                    last_note_off_tick = $tick;
                }
            }
        }};
    }

    loop {
        // 数据耗尽却未遇 End of Track(SPEC §8.2:EOT 恰一次且为最后)
        if tr.p >= track.len() {
            return Err(ParseError::MissingEot);
        }
        let delta = tr.vlq()?;
        tick += u64::from(delta);
        if tick > u64::from(MAX_TICK) {
            return Err(ParseError::TickOverflow { tick });
        }
        let status = tr.u8()?;
        match status {
            0xFF => {
                let mt = tr.u8()?;
                let len = tr.vlq()?;
                match mt {
                    0x51 => {
                        // Set Tempo:恰一次、tick 0、值 500000(SPEC §8.2)
                        if tick != 0 {
                            return Err(ParseError::TempoNotAtZero(tick as u32));
                        }
                        if tempo_seen {
                            return Err(ParseError::DuplicateTempo);
                        }
                        if len != 3 {
                            return Err(ParseError::BadTempoLen(len));
                        }
                        let b = tr.take(3)?;
                        let value =
                            (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
                        if value != TEMPO_US_PER_QUARTER {
                            return Err(ParseError::BadTempoValue(value));
                        }
                        tempo_seen = true;
                    }
                    0x58 => {
                        // Time Signature:零或一次、tick 0、严格 4/4、24、8(SPEC §8.2)
                        if tick != 0 {
                            return Err(ParseError::TimeSigNotAtZero(tick as u32));
                        }
                        if timesig_seen {
                            return Err(ParseError::DuplicateTimeSig);
                        }
                        if len != 4 {
                            return Err(ParseError::BadTimeSigLen(len));
                        }
                        let b = tr.take(4)?;
                        if b != [4, 2, 24, 8] {
                            return Err(ParseError::BadTimeSigValues(b[0], b[1], b[2], b[3]));
                        }
                        timesig_seen = true;
                    }
                    0x2F => {
                        // End of Track:恰一次、len 0、最后事件(SPEC §8.2)
                        if eot_seen {
                            return Err(ParseError::DuplicateEot);
                        }
                        if len != 0 {
                            return Err(ParseError::BadEotLen(len));
                        }
                        eot_seen = true;
                        if tr.p != track.len() {
                            return Err(ParseError::EotNotLast);
                        }
                        break;
                    }
                    other => return Err(ParseError::UnknownMeta(other)),
                }
            }
            0xF0 | 0xF7 => return Err(ParseError::SysExNotAllowed),
            0xF1..=0xF6 => return Err(ParseError::SystemCommonNotAllowed(status)),
            0xF8..=0xFE => return Err(ParseError::RealtimeNotAllowed(status)),
            0x80..=0xEF => {
                let channel = status & 0x0F;
                if channel != 0 {
                    return Err(ParseError::ChannelNotZero(channel));
                }
                match status & 0xF0 {
                    0x80 => {
                        // Note Off(SPEC §8.2;release velocity 忽略)
                        let note = tr.u8()?;
                        let _rel_vel = tr.u8()?;
                        note_off!(note, tick as u32);
                    }
                    0x90 => {
                        // Note On;velocity 0 视为 Note Off(SPEC §8.2)
                        let note = tr.u8()?;
                        let vel = tr.u8()?;
                        if vel == 0 {
                            note_off!(note, tick as u32);
                        } else {
                            if !(NOTE_MIN..=NOTE_MAX).contains(&note) {
                                return Err(ParseError::NoteOutOfRange(note));
                            }
                            if active[note as usize].is_some() {
                                return Err(ParseError::DuplicateNoteOn { tick: tick as u32, note });
                            }
                            if active_count >= MAX_SIMULTANEOUS {
                                return Err(ParseError::TooManySimultaneous(active_count + 1));
                            }
                            note_on_count += 1;
                            if note_on_count > MAX_NOTE_ONS {
                                return Err(ParseError::TooManyNoteOns);
                            }
                            active[note as usize] = Some(tick as u32);
                            active_count += 1;
                            events.push(NoteEvent::On { tick: tick as u32, note });
                        }
                    }
                    other => return Err(ParseError::UnknownChannelEvent(other)),
                }
            }
            other => return Err(ParseError::UnknownStatus(other)),
        }
    }

    if !eot_seen {
        return Err(ParseError::MissingEot);
    }
    if !tempo_seen {
        return Err(ParseError::MissingTempo);
    }
    if active.iter().any(|a| a.is_some()) {
        return Err(ParseError::HangNote);
    }

    Ok(ParsedMidi { events, last_note_off_tick })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vlq(mut v: u32) -> Vec<u8> {
        let mut out = vec![(v & 0x7F) as u8];
        v >>= 7;
        while v > 0 {
            out.push(0x80 | ((v & 0x7F) as u8));
            v >>= 7;
        }
        out.reverse();
        out
    }

    /// 组装一个合法容器:MThd(Format 0, ntrks 1, division 480)+ MTrk(events)。
    fn build(events: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(b"MThd");
        out.extend_from_slice(&6u32.to_be_bytes());
        out.extend_from_slice(&0u16.to_be_bytes());
        out.extend_from_slice(&1u16.to_be_bytes());
        out.extend_from_slice(&480u16.to_be_bytes());
        out.extend_from_slice(b"MTrk");
        out.extend_from_slice(&(events.len() as u32).to_be_bytes());
        out.extend_from_slice(events);
        out
    }

    fn tempo() -> Vec<u8> {
        vec![0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20] // tick 0, 500000
    }

    fn timesig() -> Vec<u8> {
        vec![0x00, 0xFF, 0x58, 0x04, 0x04, 0x02, 0x18, 0x08] // tick 0, 4/4/24/8
    }

    fn eot() -> Vec<u8> {
        vec![0x00, 0xFF, 0x2F, 0x00]
    }

    fn on(delta: u32, note: u8, vel: u8) -> Vec<u8> {
        let mut v = vlq(delta);
        v.extend_from_slice(&[0x90, note, vel]);
        v
    }

    fn off(delta: u32, note: u8) -> Vec<u8> {
        let mut v = vlq(delta);
        v.extend_from_slice(&[0x80, note, 0x40]); // release velocity 忽略
        v
    }

    fn on_vel0(delta: u32, note: u8) -> Vec<u8> {
        // velocity 0 的 Note On 视为 Note Off
        let mut v = vlq(delta);
        v.extend_from_slice(&[0x90, note, 0x00]);
        v
    }

    fn minimal() -> Vec<u8> {
        // 单音 60,On@0 Off@480
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        build(&e)
    }

    #[test]
    fn parses_minimal() {
        let p = parse_midi(&minimal()).unwrap();
        assert_eq!(
            p.events,
            vec![
                NoteEvent::On { tick: 0, note: 60 },
                NoteEvent::Off { tick: 480, note: 60 },
            ]
        );
        assert_eq!(p.last_note_off_tick, 480);
    }

    #[test]
    fn accepts_timesig_and_vel0_noteoff() {
        let mut e = tempo();
        e.extend_from_slice(&timesig());
        e.extend_from_slice(&on(0, 72, 90));
        e.extend_from_slice(&on_vel0(240, 72)); // vel0 Note On = Note Off
        e.extend_from_slice(&eot());
        let p = parse_midi(&build(&e)).unwrap();
        assert_eq!(p.last_note_off_tick, 240);
    }

    #[test]
    fn rejects_format1() {
        let mut f = build(&[]);
        f[8] = 0; // format 高字节
        f[9] = 1; // format = 1
        assert_eq!(parse_midi(&f), Err(ParseError::NotFormat0(1)));
    }

    #[test]
    fn rejects_multiple_tracks() {
        let mut f = build(&[]);
        f[10] = 0; // ntrks 高字节
        f[11] = 2; // ntrks = 2
        assert_eq!(parse_midi(&f), Err(ParseError::TrackCountNot1(2)));
    }

    #[test]
    fn rejects_wrong_division() {
        let mut f = build(&[]);
        f[12] = 0x01; // division 高字节
        f[13] = 0xE1; // 481
        assert_eq!(parse_midi(&f), Err(ParseError::DivisionNot480(481)));
    }

    #[test]
    fn rejects_smpte_division() {
        let mut f = build(&[]);
        f[12] = 0xE2; // bit15 置位 = SMPTE
        f[13] = 0x80;
        assert_eq!(parse_midi(&f), Err(ParseError::SmpteDivision));
    }

    #[test]
    fn rejects_running_status() {
        // 合法事件后省略 status byte 直接跟数据 → 0x3C 不是合法 status
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&[0x01, 0x3C, 0x64]); // 缺 status 的数据(note=0x3C, vel=0x64)
        e.extend_from_slice(&eot());
        assert!(matches!(
            parse_midi(&build(&e)),
            Err(ParseError::UnknownStatus(0x3C))
        ));
    }

    #[test]
    fn rejects_unknown_meta() {
        let mut e = tempo();
        e.extend_from_slice(&[0x00, 0xFF, 0x03, 0x04, b'N', b'a', b'm', b'e']); // track name
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::UnknownMeta(0x03)));
    }

    #[test]
    fn rejects_sysex() {
        let mut e = tempo();
        e.extend_from_slice(&[0x00, 0xF0, 0x03, 0x01, 0x02, 0x03]);
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::SysExNotAllowed));
    }

    #[test]
    fn rejects_non_shortest_vlq() {
        // delta 127 写成 2 字节:0x80 0x7F → 非最短
        let mut e = tempo();
        e.extend_from_slice(&[0x80, 0x7F, 0x90, 60, 100]);
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::VlvNotShortest));
    }

    #[test]
    fn rejects_vlv_too_long() {
        // 5 字节 VLQ:0xFF 0xFF 0xFF 0xFF 0x7F
        let mut e = tempo();
        e.extend_from_slice(&[0xFF, 0xFF, 0xFF, 0xFF, 0x7F, 0x90, 60, 100]);
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::VlvTooLong));
    }

    #[test]
    fn rejects_track_len_overflow() {
        // MTrk 声明长度超过实际
        let mut f = build(&[]);
        // MTrk len 字段在 offset 18..22
        f[18] = 0x00;
        f[19] = 0x00;
        f[20] = 0x01;
        f[21] = 0x00; // 声明 256,实际 0
        assert_eq!(parse_midi(&f), Err(ParseError::TrackLenExceeds(256)));
    }

    #[test]
    fn rejects_trailing_bytes() {
        let mut f = minimal();
        f.push(0x00);
        assert!(matches!(parse_midi(&f), Err(ParseError::TrailingBytes(1))));
    }

    #[test]
    fn rejects_eot_not_last() {
        let mut e = tempo();
        e.extend_from_slice(&eot());
        e.extend_from_slice(&[0x00, 0x90, 60, 100]); // EOT 后还有事件
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::EotNotLast));
    }

    #[test]
    fn rejects_too_many_voices() {
        // 5 个同时活动音符
        let mut e = tempo();
        for (i, n) in [60u8, 64, 67, 71, 74].iter().enumerate() {
            let d = if i == 0 { 0 } else { 0 };
            e.extend_from_slice(&on(d, *n, 100));
        }
        e.extend_from_slice(&eot());
        assert_eq!(
            parse_midi(&build(&e)),
            Err(ParseError::TooManySimultaneous(5))
        );
    }

    #[test]
    fn rejects_duplicate_note_on() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&on(0, 60, 100)); // 未 Off 再 On
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        assert_eq!(
            parse_midi(&build(&e)),
            Err(ParseError::DuplicateNoteOn { tick: 0, note: 60 })
        );
    }

    #[test]
    fn rejects_hang_note() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&eot()); // 无 Off
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::HangNote));
    }

    #[test]
    fn rejects_bad_note_off() {
        let mut e = tempo();
        e.extend_from_slice(&off(0, 60)); // 无匹配 On
        e.extend_from_slice(&eot());
        assert_eq!(
            parse_midi(&build(&e)),
            Err(ParseError::NoteOffWithoutOn { tick: 0, note: 60 })
        );
    }

    #[test]
    fn rejects_too_long() {
        // 最后 Note Off tick > 57600
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&off(57601, 60));
        e.extend_from_slice(&eot());
        assert!(matches!(
            parse_midi(&build(&e)),
            Err(ParseError::TickOverflow { .. })
        ));
    }

    #[test]
    fn rejects_illegal_pitch() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 20, 100)); // 低于 21
        e.extend_from_slice(&off(480, 20));
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::NoteOutOfRange(20)));

        let mut e2 = tempo();
        e2.extend_from_slice(&on(0, 109, 100)); // 高于 108
        e2.extend_from_slice(&off(480, 109));
        e2.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e2)), Err(ParseError::NoteOutOfRange(109)));
    }

    #[test]
    fn rejects_note_off_same_tick() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&off(0, 60)); // 同 tick → 每音 < 1 tick
        e.extend_from_slice(&eot());
        assert_eq!(
            parse_midi(&build(&e)),
            Err(ParseError::NoteOffSameTick { tick: 0, note: 60 })
        );
    }

    #[test]
    fn rejects_channel_not_zero() {
        let mut e = tempo();
        e.extend_from_slice(&[0x00, 0x91, 60, 100]); // channel 1
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::ChannelNotZero(1)));
    }

    #[test]
    fn rejects_program_change() {
        let mut e = tempo();
        e.extend_from_slice(&[0x00, 0xC0, 5]); // Program Change
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::UnknownChannelEvent(0xC0)));
    }

    #[test]
    fn rejects_missing_tempo() {
        let mut e = on(0, 60, 100);
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::MissingTempo));
    }

    #[test]
    fn rejects_tempo_not_at_zero() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&[0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20]); // 第二个 tempo
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::DuplicateTempo));
    }

    #[test]
    fn rejects_bad_tempo_value() {
        let mut e = vec![0x00, 0xFF, 0x51, 0x03, 0x00, 0x00, 0x01]; // tempo=1
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::BadTempoValue(1)));
    }

    #[test]
    fn rejects_bad_timesig_values() {
        let mut e = tempo();
        e.extend_from_slice(&[0x00, 0xFF, 0x58, 0x04, 0x03, 0x02, 0x18, 0x08]); // 3/4
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        assert_eq!(
            parse_midi(&build(&e)),
            Err(ParseError::BadTimeSigValues(3, 2, 24, 8))
        );
    }

    #[test]
    fn rejects_timesig_not_at_zero() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&[0x01, 0xFF, 0x58, 0x04, 0x04, 0x02, 0x18, 0x08]); // tick 1
        e.extend_from_slice(&off(480, 60));
        e.extend_from_slice(&eot());
        assert!(matches!(
            parse_midi(&build(&e)),
            Err(ParseError::TimeSigNotAtZero(1))
        ));
    }

    #[test]
    fn rejects_missing_eot() {
        let mut e = tempo();
        e.extend_from_slice(&on(0, 60, 100));
        e.extend_from_slice(&off(480, 60));
        assert_eq!(parse_midi(&build(&e)), Err(ParseError::MissingEot));
    }
}
