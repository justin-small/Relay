//! What the analysis tab measures about a source's timing, in Rust (issues 300, 561): frame arrivals, jitter and late
//! frames, the health verdict built from them, and how far the audio runs from the video by the senders' timestamps.
//! The thresholds are CloudCast Scope's.

use std::collections::VecDeque;

const LATE_FACTOR: f64 = 2.0; // an interval this many frame times long is a late frame
const LONG_GAP_S: f64 = 0.150; // a gap longer than this is flagged whatever the frame rate
pub const QUIET_S: f64 = 2.0; // no frame for this long: quiet (a still picture), or stalled
pub const HEALTH_WINDOW_S: f64 = 10.0;
const KEEP: usize = 600; // frame arrivals kept
const EVENTS: usize = 200;
const RECENT: usize = 300;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Flag {
    None,
    Late,
    Gap,
}

impl Flag {
    pub fn name(self) -> &'static str {
        match self {
            Flag::None => "",
            Flag::Late => "late",
            Flag::Gap => "gap",
        }
    }
}

#[derive(Default, Clone, Debug)]
pub struct Measured {
    pub iv_min: f64,
    pub iv_avg: f64,
    pub iv_max: f64,
    pub iv_sd: f64,
    pub measured_fps: f64,
    pub jitter_mean: f64,
    pub jitter_peak: f64,
    pub rate_mbps: f64,
    pub bytes_min: u64,
    pub bytes_avg: u64,
    pub bytes_max: u64,
}

#[derive(Default, Clone, Debug)]
pub struct Stats {
    pub frames: u64,
    pub late: u64,
    pub long_gaps: u64,
    pub declared_fps: f64,
    pub recent_late: usize,
    pub since_last_s: Option<f64>,
    /// None until three frames have arrived.
    pub measured: Option<Measured>,
}

pub struct Timing {
    t: VecDeque<(f64, u64)>, // (arrival, decoded bytes)
    events: VecDeque<f64>,
    frames: u64,
    late: u64,
    long_gaps: u64,
    fps: f64,
}

impl Default for Timing {
    fn default() -> Self {
        Timing::new()
    }
}

impl Timing {
    pub fn new() -> Timing {
        Timing { t: VecDeque::new(), events: VecDeque::new(), frames: 0, late: 0, long_gaps: 0, fps: 0.0 }
    }

    /// Note a frame that arrived at `now` (monotonic seconds); says whether it was late or after a long gap.
    pub fn record(&mut self, now: f64, nbytes: u64, fps: f64) -> Flag {
        let mut flag = Flag::None;
        if let Some(&(last, _)) = self.t.back() {
            let dt = now - last;
            let frame = if self.fps > 0.0 { 1.0 / self.fps } else { 0.0 };
            if dt > LONG_GAP_S && dt > frame * LATE_FACTOR {
                flag = Flag::Gap;
                self.long_gaps += 1;
            } else if frame > 0.0 && dt > frame * LATE_FACTOR {
                flag = Flag::Late;
            }
            if flag != Flag::None {
                self.late += 1;
                if self.events.len() == EVENTS {
                    self.events.pop_front();
                }
                self.events.push_back(now);
            }
        }
        if self.t.len() == KEEP {
            self.t.pop_front();
        }
        self.t.push_back((now, nbytes));
        self.frames += 1;
        if fps > 0.0 {
            self.fps = fps;
        }
        flag
    }

    pub fn stats(&self, now: f64) -> Stats {
        let recent_late = self.events.iter().filter(|&&e| now - e <= HEALTH_WINDOW_S).count();
        let mut out = Stats {
            frames: self.frames,
            late: self.late,
            long_gaps: self.long_gaps,
            declared_fps: self.fps,
            recent_late,
            since_last_s: self.t.back().map(|&(last, _)| ((now - last) * 100.0).round() / 100.0),
            measured: None,
        };
        if self.t.len() < 3 {
            return out;
        }
        let ts: Vec<(f64, u64)> = self.t.iter().copied().collect();
        let iv: Vec<f64> = ts.windows(2).map(|w| (w[1].0 - w[0].0) * 1000.0).collect();
        let recent = &iv[iv.len().saturating_sub(RECENT)..];
        let n = recent.len() as f64;
        let avg = recent.iter().sum::<f64>() / n;
        let sd = (recent.iter().map(|x| (x - avg).powi(2)).sum::<f64>() / n).sqrt();
        let nominal = if self.fps > 0.0 { 1000.0 / self.fps } else { avg };
        let jit: Vec<f64> = recent.iter().map(|x| (x - nominal).abs()).collect();
        let sizes: Vec<u64> = ts.iter().map(|&(_, b)| b).collect();
        let span = ts[ts.len() - 1].0 - ts[0].0;
        let r2 = |x: f64| (x * 100.0).round() / 100.0;
        out.measured = Some(Measured {
            iv_min: r2(recent.iter().copied().fold(f64::INFINITY, f64::min)),
            iv_avg: r2(avg),
            iv_max: r2(recent.iter().copied().fold(0.0, f64::max)),
            iv_sd: r2(sd),
            measured_fps: if avg > 0.0 { (1000.0 / avg * 1000.0).round() / 1000.0 } else { 0.0 },
            jitter_mean: r2(jit.iter().sum::<f64>() / jit.len() as f64),
            jitter_peak: r2(jit.iter().copied().fold(0.0, f64::max)),
            rate_mbps: if span > 0.0 { r2(sizes[1..].iter().sum::<u64>() as f64 * 8.0 / span / 1e6) } else { 0.0 },
            bytes_min: *sizes.iter().min().unwrap_or(&0),
            bytes_avg: (sizes.iter().sum::<u64>() as f64 / sizes.len() as f64).round() as u64,
            bytes_max: *sizes.iter().max().unwrap_or(&0),
        });
        out
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Ok,
    Warn,
    Bad,
    Quiet,
    Unknown,
}

impl State {
    pub fn name(self) -> &'static str {
        match self {
            State::Ok => "ok",
            State::Warn => "warn",
            State::Bad => "bad",
            State::Quiet => "quiet",
            State::Unknown => "unknown",
        }
    }
}

/// The verdict for a source from its stats and the SDK's dropped-frame count. Frame rate off nominal by 0.2% is amber
/// and 1% red; jitter above a quarter of a frame is amber and above half a frame is red.
pub fn health(s: &Stats, dropped: i64) -> (State, String) {
    if s.frames == 0 {
        return (State::Unknown, "Not being received".into());
    }
    if let Some(since) = s.since_last_s {
        if since > QUIET_S {
            return (State::Quiet, format!("No frames for {since:.0} s: a still picture sends none, so this is not always a fault"));
        }
    }
    let Some(m) = &s.measured else { return (State::Unknown, "Measuring".into()) };
    let nominal = if s.declared_fps > 0.0 { 1000.0 / s.declared_fps } else { m.iv_avg };
    let off = if s.declared_fps > 0.0 { (m.measured_fps - s.declared_fps).abs() / s.declared_fps } else { 0.0 };
    if s.recent_late >= 3 || off > 0.01 || m.jitter_mean > nominal / 2.0 || dropped != 0 {
        let why = if s.recent_late >= 3 {
            format!("{} late or long gaps in the last {} s", s.recent_late, HEALTH_WINDOW_S as i64)
        } else if dropped != 0 {
            "Dropping frames".into()
        } else {
            "Frame rate or timing is off".into()
        };
        return (State::Bad, why);
    }
    if s.recent_late > 0 || off > 0.002 || m.jitter_mean > nominal / 4.0 {
        return (State::Warn, "Timing is a little uneven".into());
    }
    (State::Ok, "Steady".into())
}

// ------------------------------------------------------------------------------------------------- audio/video sync
const SKEWS: usize = 120;
const MIN_SKEWS: usize = 10;
const TICKS_PER_MS: f64 = 10_000.0; // NDI times are 100 ns units
/// Both streams' timecodes within this of their timestamps: the sender let the SDK make them up.
const SYNTH_MS: f64 = 2.0;
pub const SYNC_WARN_MS: f64 = 20.0;
pub const SYNC_BAD_MS: f64 = 40.0;

#[derive(Clone, Debug, PartialEq)]
pub struct Sync {
    /// Enough frames of both kinds have arrived to say.
    pub valid: bool,
    /// Both streams' timecodes are the SDK's own (no timecode from the sender), so the offset cannot be told.
    pub synthesized: bool,
    /// Positive: the audio is labelled later than the picture it arrives with (it runs ahead); negative: behind.
    pub offset_ms: f64,
    pub state: &'static str,
}

/// How far the audio runs from the picture. Every NDI frame has a timestamp (when the sender's SDK took it) and a
/// timecode (what the sender labelled it with, or the SDK's own clock when it was asked to make one up). A sender that
/// labels audio and picture from the capture clock shows any delay between them as a different timestamp-to-timecode
/// gap on each stream, so the offset is the audio's gap minus the picture's, each as the median of the last 120 frames.
/// It cannot see a delay the sender's own timecodes do not carry. A sender that submits audio and picture in separate
/// calls adds up to one frame of offset from the order of the calls alone, so a steady reading is the sender's own
/// habit and a change in it is what to look at (proved on real NDI, issue 561).
#[derive(Default)]
pub struct SyncMeter {
    v: VecDeque<i64>,
    a: VecDeque<i64>,
}

fn median(d: &VecDeque<i64>) -> i64 {
    let mut s: Vec<i64> = d.iter().copied().collect();
    s.sort_unstable();
    s[s.len() / 2]
}

fn skew(timecode: i64, timestamp: i64) -> Option<i64> {
    if timecode == i64::MAX || timestamp <= 0 || timecode < 0 {
        return None;
    }
    Some(timestamp.saturating_sub(timecode))
}

impl SyncMeter {
    pub fn new() -> SyncMeter {
        SyncMeter::default()
    }

    pub fn reset(&mut self) {
        self.v.clear();
        self.a.clear();
    }

    pub fn video(&mut self, timecode: i64, timestamp: i64) {
        Self::push(&mut self.v, timecode, timestamp);
    }

    pub fn audio(&mut self, timecode: i64, timestamp: i64) {
        Self::push(&mut self.a, timecode, timestamp);
    }

    fn push(d: &mut VecDeque<i64>, timecode: i64, timestamp: i64) {
        if let Some(s) = skew(timecode, timestamp) {
            if d.len() == SKEWS {
                d.pop_front();
            }
            d.push_back(s);
        }
    }

    pub fn snapshot(&self) -> Sync {
        if self.v.len() < MIN_SKEWS || self.a.len() < MIN_SKEWS {
            return Sync { valid: false, synthesized: false, offset_ms: 0.0, state: "unknown" };
        }
        let (mv, ma) = (median(&self.v), median(&self.a));
        let synthesized = (mv as f64 / TICKS_PER_MS).abs() < SYNTH_MS && (ma as f64 / TICKS_PER_MS).abs() < SYNTH_MS;
        // The audio's timestamp lags its timecode by less when the audio is labelled later than the picture.
        let ms = ((mv - ma) as f64 / TICKS_PER_MS * 10.0).round() / 10.0;
        let state = if synthesized {
            "unknown"
        } else if ms.abs() > SYNC_BAD_MS {
            "bad"
        } else if ms.abs() > SYNC_WARN_MS {
            "warn"
        } else {
            "ok"
        };
        Sync { valid: true, synthesized, offset_ms: ms, state }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn steady(fps: f64, n: usize, step_s: f64, t: &mut Timing, start: f64) -> f64 {
        let mut now = start;
        for _ in 0..n {
            t.record(now, 1000, fps);
            now += step_s;
        }
        now
    }

    #[test]
    fn steady_stream_is_ok() {
        let mut t = Timing::new();
        let end = steady(30.0, 100, 1.0 / 30.0, &mut t, 0.0);
        let s = t.stats(end);
        assert_eq!(health(&s, 0).0, State::Ok);
        let m = s.measured.unwrap();
        assert!((m.measured_fps - 30.0).abs() < 0.01);
    }

    #[test]
    fn gaps_are_flagged_and_turn_bad() {
        let mut t = Timing::new();
        let mut now = steady(30.0, 30, 1.0 / 30.0, &mut t, 0.0);
        for _ in 0..4 {
            now += 0.4;
            assert_eq!(t.record(now, 1000, 30.0), Flag::Gap);
            now = steady(30.0, 5, 1.0 / 30.0, &mut t, now);
        }
        let s = t.stats(now);
        assert_eq!(health(&s, 0).0, State::Bad);
        assert_eq!(s.long_gaps, 4);
    }

    #[test]
    fn a_still_picture_is_quiet_and_nothing_is_unknown() {
        let mut t = Timing::new();
        let end = steady(30.0, 20, 1.0 / 30.0, &mut t, 0.0);
        assert_eq!(health(&t.stats(end + 5.0), 0).0, State::Quiet);
        assert_eq!(health(&Timing::new().stats(1.0), 0).0, State::Unknown);
    }

    #[test]
    fn dropped_frames_and_wrong_rate_are_bad() {
        let mut t = Timing::new();
        let end = steady(30.0, 100, 1.0 / 30.0, &mut t, 0.0);
        assert_eq!(health(&t.stats(end), 3), (State::Bad, "Dropping frames".to_string()));
        let mut slow = Timing::new();
        let end = steady(30.0, 100, 1.0 / 25.0, &mut slow, 0.0);
        assert_eq!(health(&slow.stats(end), 0).0, State::Bad);
    }

    fn feed(m: &mut SyncMeter, audio_label_ms: f64) {
        // A sender whose picture timecodes lag their timestamps by 500 ms and whose audio is labelled
        // `audio_label_ms` later than the picture it goes with.
        for i in 0..40i64 {
            let ts = 1_000_000_000 + i * 333_333;
            m.video(ts - 5_000_000, ts);
            m.audio(ts - 5_000_000 + (audio_label_ms * TICKS_PER_MS) as i64, ts);
        }
    }

    #[test]
    fn known_offsets_read_back() {
        let mut m = SyncMeter::new();
        assert!(!m.snapshot().valid);
        feed(&mut m, 0.0);
        let s = m.snapshot();
        assert_eq!((s.valid, s.offset_ms, s.state), (true, 0.0, "ok"));
        for (ms, want) in [(15.0, "ok"), (-30.0, "warn"), (60.0, "bad")] {
            let mut m = SyncMeter::new();
            feed(&mut m, ms);
            let s = m.snapshot();
            assert_eq!((s.offset_ms, s.state), (ms, want));
        }
    }

    #[test]
    fn synthesized_timecodes_say_nothing() {
        let mut m = SyncMeter::new();
        for i in 0..20i64 {
            let ts = 5_000_000_000 + i * 333_333;
            m.video(ts, ts);
            m.audio(ts + 100, ts);
        }
        let s = m.snapshot();
        assert!(s.valid && s.synthesized);
        assert_eq!(s.state, "unknown");
        m.reset();
        m.video(i64::MAX, 1);
        assert!(!m.snapshot().valid);
    }
}
