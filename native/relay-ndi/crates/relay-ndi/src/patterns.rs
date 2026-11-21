//! The NDI test patterns (issue 302): colour bars, ramp, grid, alpha checker, solid colours and a moving element,
//! drawn as BGRA, plus the test tone. A port of the picture and sound code in Python's `testpatterns.py` (issue 606),
//! following its arithmetic. Text stays Python's: the burned-in name and clock are small patches Pillow draws once a
//! second and `draw_frame` blends in; only the moving pattern's frame counter is drawn here, with a built-in font.

use std::f64::consts::PI;

pub const RATE: u32 = 48000;
pub const TONE_HZ: f64 = 1000.0;

const BARS: [[u8; 3]; 7] = [[191, 191, 191], [191, 191, 0], [0, 191, 191], [0, 191, 0], [191, 0, 191], [191, 0, 0], [0, 0, 191]];

/// RGB(A) canvas with the fills the patterns need.
struct Canvas {
    w: usize,
    h: usize,
    px: Vec<[u8; 4]>,
}

impl Canvas {
    fn new(w: usize, h: usize, c: [u8; 4]) -> Canvas {
        Canvas { w, h, px: vec![c; w * h] }
    }

    /// Fill rows `y0..y1` and columns `x0..x1`, clipped to the canvas (numpy slices clip the same way).
    fn fill(&mut self, y0: usize, y1: usize, x0: usize, x1: usize, c: [u8; 4]) {
        let (y1, x1) = (y1.min(self.h), x1.min(self.w));
        for y in y0.min(y1)..y1 {
            for p in &mut self.px[y * self.w + x0.min(x1)..y * self.w + x1] {
                *p = c;
            }
        }
    }

    fn rgba_to_bgra(&self) -> Vec<u8> {
        self.px.iter().flat_map(|p| [p[2], p[1], p[0], p[3]]).collect()
    }
}

fn rgb(c: [u8; 3]) -> [u8; 4] {
    [c[0], c[1], c[2], 255]
}

fn rnd(v: f64) -> usize {
    v.round_ties_even().max(0.0) as usize
}

fn bars(w: usize, h: usize) -> Canvas {
    let mut c = Canvas::new(w, h, [0, 0, 0, 255]);
    let (top, mid) = ((h as f64 * 0.67) as usize, (h as f64 * 0.75) as usize);
    let edges: Vec<usize> = (0..8).map(|i| rnd(i as f64 * w as f64 / 7.0)).collect();
    for (i, b) in BARS.iter().enumerate() {
        c.fill(0, top, edges[i], edges[i + 1], rgb(*b));
    }
    let rev = [BARS[6], [19, 19, 19], BARS[4], [19, 19, 19], BARS[2], [19, 19, 19], BARS[0]];
    for (i, b) in rev.iter().enumerate() {
        c.fill(top, mid, edges[i], edges[i + 1], rgb(*b));
    }
    c.fill(mid, h, 0, w, rgb([16, 16, 16]));
    let q = rnd(w as f64 * 5.0 / 7.0 / 4.0);
    for (i, b) in [[0u8, 33, 76], [255, 255, 255], [50, 0, 106], [16, 16, 16]].iter().enumerate() {
        c.fill(mid, h, i * q, (i + 1) * q, rgb(*b));
    }
    c
}

fn ramp(w: usize, h: usize) -> Canvas {
    let mut c = Canvas::new(w, h, [0, 0, 0, 255]);
    let step = if w > 1 { 255.0 / (w as f64 - 1.0) } else { 0.0 };
    let row: Vec<u8> = (0..w).map(|i| (i as f64 * step).round_ties_even() as u8).collect();
    for y in 0..h {
        for (x, v) in row.iter().enumerate() {
            c.px[y * w + x] = [*v, *v, *v, 255];
        }
    }
    let per = w.div_ceil(11);
    let top = (h as f64 * 0.75) as usize;
    for y in top..h {
        for x in 0..w {
            let v = (((x / per).min(10)) as f64 * 25.5).round_ties_even() as u8;
            c.px[y * w + x] = [v, v, v, 255];
        }
    }
    c
}

fn grid(w: usize, h: usize) -> Canvas {
    let mut c = Canvas::new(w, h, rgb([40, 40, 40]));
    let (cx, cy) = (w / 2, h / 2);
    let t = (h / 540).max(1);
    let line = rgb([120, 120, 120]);
    for i in 0..17 {
        let x = (w - t).min(rnd(i as f64 * w as f64 / 16.0));
        c.fill(0, h, x, x + t, line);
    }
    for j in 0..10 {
        let y = (h - t).min(rnd(j as f64 * h as f64 / 9.0));
        c.fill(y, y + t, 0, w, line);
    }
    let white = rgb([255, 255, 255]);
    c.fill(0, h, cx.saturating_sub(t), cx + t, white);
    c.fill(cy.saturating_sub(t), cy + t, 0, w, white);
    let r = h as f64 * 0.45;
    for y in 0..h {
        for x in 0..w {
            if ((x as f64 - cx as f64).hypot(y as f64 - cy as f64) - r).abs() < t as f64 * 1.5 {
                c.px[y * w + x] = white;
            }
        }
    }
    for (frac, col) in [(0.9, [255u8, 220, 0]), (0.8, [0, 220, 255])] {
        let col = rgb(col);
        let (x0, x1) = (rnd(w as f64 * (1.0 - frac) / 2.0), rnd(w as f64 * (1.0 + frac) / 2.0));
        let (y0, y1) = (rnd(h as f64 * (1.0 - frac) / 2.0), rnd(h as f64 * (1.0 + frac) / 2.0));
        c.fill(y0, y0 + 2 * t, x0, x1, col);
        c.fill(y1.saturating_sub(2 * t), y1, x0, x1, col);
        c.fill(y0, y1, x0, x0 + 2 * t, col);
        c.fill(y0, y1, x1.saturating_sub(2 * t), x1, col);
    }
    c
}

/// A checker of opaque white and fully clear squares, so a keyer shows what it keeps, with a half-transparent band.
fn alpha(w: usize, h: usize) -> Canvas {
    let size = (h / 18).max(8);
    let mut c = Canvas::new(w, h, [0, 0, 0, 0]);
    for y in 0..h {
        for x in 0..w {
            if (y / size + x / size) % 2 == 1 {
                c.px[y * w + x] = [255, 255, 255, 255];
            }
        }
    }
    c.fill(h / 3, 2 * h / 3, w / 4, 3 * w / 4, [255, 40, 40, 128]);
    c
}

/// The solid colour of a one-colour pattern, if it is one.
fn solid(pattern: &str) -> Option<[u8; 3]> {
    Some(match pattern {
        "white" => [255, 255, 255],
        "black" => [0, 0, 0],
        "red" => [255, 0, 0],
        "green" => [0, 255, 0],
        "blue" => [0, 0, 255],
        _ => return None,
    })
}

/// The picture that does not change from frame to frame, as BGRA (`w * h * 4` bytes). Unknown patterns (and the
/// moving one, which paints over it) are the dark grey Python used.
pub fn base_picture(pattern: &str, w: usize, h: usize) -> Vec<u8> {
    let c = match pattern {
        "bars" => bars(w, h),
        "ramp" => ramp(w, h),
        "grid" => grid(w, h),
        "alpha" => alpha(w, h),
        other => Canvas::new(w, h, rgb(solid(other).unwrap_or([16, 16, 16]))),
    };
    c.rgba_to_bgra()
}

/// A patch of BGRA pixels to draw over a frame: the burned-in text, with its own alpha.
pub struct Patch<'a> {
    pub x: i64,
    pub y: i64,
    pub w: usize,
    pub h: usize,
    pub bgra: &'a [u8],
}

/// Draw `patch` over `frame` (BGRA, `w` by `h`, in place) with its alpha, clipped to the frame.
pub fn blend(frame: &mut [u8], w: usize, h: usize, patch: &Patch) {
    let (x0, y0) = (patch.x.max(0), patch.y.max(0));
    let (x1, y1) = ((patch.x + patch.w as i64).min(w as i64), (patch.y + patch.h as i64).min(h as i64));
    if x1 <= x0 || y1 <= y0 || patch.bgra.len() < patch.w * patch.h * 4 {
        return;
    }
    for y in y0..y1 {
        for x in x0..x1 {
            let s = ((y - patch.y) as usize * patch.w + (x - patch.x) as usize) * 4;
            let d = (y as usize * w + x as usize) * 4;
            let a = patch.bgra[s + 3] as f32 / 255.0;
            for k in 0..3 {
                frame[d + k] = (patch.bgra[s + k] as f32 * a + frame[d + k] as f32 * (1.0 - a)) as u8;
            }
            frame[d + 3] = frame[d + 3].max(patch.bgra[s + 3]);
        }
    }
}

/// The moving pattern's element: a bar sweeping across every two seconds over a ruler, painted over the base.
fn motion(out: &mut [u8], w: usize, h: usize, now: f64) {
    let mut put = |y0: usize, y1: usize, x0: usize, x1: usize, c: [u8; 4]| {
        let (y1, x1) = (y1.min(h), x1.min(w));
        for y in y0.min(y1)..y1 {
            for x in x0.min(x1)..x1 {
                out[(y * w + x) * 4..(y * w + x) * 4 + 4].copy_from_slice(&c);
            }
        }
    };
    put(0, h, 0, w, [24, 24, 24, 255]);
    let bw = (w / 16).max(8);
    let x = ((now / 2.0).rem_euclid(1.0) * (w - bw) as f64) as usize;
    put(0, h, x, x + bw, [255, 255, 255, 255]);
    put(h / 3, 2 * h / 3, 0, w, [40, 40, 40, 255]);
    put(h / 3, 2 * h / 3, x, x + bw, [0, 220, 255, 255]);
    let step = (w / 40).max(1);
    for i in (0..w).step_by(step * 2) {
        put(h - h / 12, h, i, i + step, [90, 90, 90, 255]);
    }
}

/// 5 by 7 glyphs for "frame 0123456789": rows top to bottom, bit 4 the left column.
fn glyph(c: char) -> [u8; 7] {
    match c {
        '0' => [14, 17, 19, 21, 25, 17, 14],
        '1' => [4, 12, 4, 4, 4, 4, 14],
        '2' => [14, 17, 1, 2, 4, 8, 31],
        '3' => [14, 17, 1, 6, 1, 17, 14],
        '4' => [2, 6, 10, 18, 31, 2, 2],
        '5' => [31, 16, 30, 1, 1, 17, 14],
        '6' => [6, 8, 16, 30, 17, 17, 14],
        '7' => [31, 1, 2, 4, 8, 8, 8],
        '8' => [14, 17, 17, 14, 17, 17, 14],
        '9' => [14, 17, 17, 15, 1, 2, 12],
        'f' => [6, 8, 8, 28, 8, 8, 8],
        'r' => [0, 0, 22, 25, 16, 16, 16],
        'a' => [0, 0, 14, 1, 15, 17, 15],
        'm' => [0, 0, 26, 21, 21, 17, 17],
        'e' => [0, 0, 14, 17, 31, 16, 14],
        _ => [0; 7],
    }
}

/// "frame N" in white on a dark plate, as a BGRA patch sized for a picture `h` tall.
pub fn frame_label(frame: u64, h: usize) -> (usize, usize, Vec<u8>) {
    let text = format!("frame {frame}");
    let k = (h / 22 / 8).max(1);
    let (pad, cell_w) = (3 * k, 6 * k);
    let (w, hh) = (text.chars().count() * cell_w + 2 * pad, 7 * k + 2 * pad);
    let mut px = vec![0u8; w * hh * 4];
    for p in px.as_chunks_mut::<4>().0 {
        *p = [0, 0, 0, 200];
    }
    for (i, ch) in text.chars().enumerate() {
        for (row, bits) in glyph(ch).iter().enumerate() {
            for col in 0..5 {
                if bits >> (4 - col) & 1 == 1 {
                    for dy in 0..k {
                        for dx in 0..k {
                            let (x, y) = (pad + i * cell_w + col * k + dx, pad + row * k + dy);
                            px[(y * w + x) * 4..(y * w + x) * 4 + 4].copy_from_slice(&[255, 255, 255, 255]);
                        }
                    }
                }
            }
        }
    }
    (w, hh, px)
}

/// One frame, BGRA: the base picture plus the moving element and the burned-in text.
pub fn draw_frame(pattern: &str, base: &[u8], w: usize, h: usize, now: f64, frame: u64, burn: Option<&Patch>) -> Vec<u8> {
    let mut out = base.to_vec();
    let moving = pattern == "motion";
    if moving {
        motion(&mut out, w, h, now);
    }
    if let Some(p) = burn {
        blend(&mut out, w, h, p);
        if moving {
            let (lw, lh, px) = frame_label(frame, h);
            blend(&mut out, w, h, &Patch { x: p.x, y: p.y - (h as f64 * 0.1) as i64, w: lw, h: lh, bgra: &px });
        }
    }
    out
}

/// The test audio, one video frame's worth at a time: 48 kHz, two channels.
pub struct Tone {
    mode: String,
    fps: f64,
    gain: f64,
    n: u64,
    frames: u64,
}

impl Tone {
    /// `mode` is "tone", "id" (left, then right) or "silence".
    pub fn new(mode: &str, level_db: f64, fps: f64) -> Tone {
        Tone { mode: mode.to_string(), fps: if fps > 0.0 { fps } else { 30.0 }, gain: 10f64.powf(level_db / 20.0), n: 0, frames: 0 }
    }

    /// Which side is sounding now, for the on-screen label ("" unless the mode names one).
    pub fn channel(&self) -> &'static str {
        if self.mode != "id" {
            ""
        } else if (self.n / RATE as u64).is_multiple_of(2) {
            "LEFT"
        } else {
            "RIGHT"
        }
    }

    /// The audio for the next video frame, planar: all of channel 0, then all of channel 1. Frame lengths follow the
    /// exact rate (1600, 1600, ... or 1601, 1602 at 29.97), so sound and picture stay together however long it runs.
    pub fn next_frame(&mut self) -> (Vec<f32>, usize) {
        let r = RATE as f64;
        let count = (((self.frames + 1) as f64 * r / self.fps).floor() - (self.frames as f64 * r / self.fps).floor()) as usize;
        let mut out = vec![0f32; 2 * count];
        if self.mode == "tone" || self.mode == "id" {
            for i in 0..count {
                let t = (self.n + i as u64) as f64 / r;
                let wave = ((2.0 * PI * TONE_HZ * t).sin() * self.gain) as f32;
                if self.mode == "tone" {
                    out[i] = wave;
                    out[count + i] = wave;
                } else if (t.floor() as i64) % 2 == 0 {
                    out[i] = wave;
                } else {
                    out[count + i] = wave;
                }
            }
        }
        self.n += count as u64;
        self.frames += 1;
        (out, count)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn px(b: &[u8], w: usize, x: usize, y: usize) -> [u8; 4] {
        let o = (y * w + x) * 4;
        [b[o], b[o + 1], b[o + 2], b[o + 3]]
    }

    #[test]
    fn bars_have_white_bar_first_and_blue_last_in_bgra() {
        let b = base_picture("bars", 700, 360);
        assert_eq!(px(&b, 700, 10, 10), [191, 191, 191, 255]);
        assert_eq!(px(&b, 700, 690, 10), [191, 0, 0, 255]); // blue (0,0,191) as BGRA
        assert_eq!(px(&b, 700, 10, 350)[3], 255);
    }

    #[test]
    fn ramp_climbs_left_to_right() {
        let b = base_picture("ramp", 640, 360);
        assert_eq!(px(&b, 640, 0, 10)[0], 0);
        assert_eq!(px(&b, 640, 639, 10)[0], 255);
        assert!(px(&b, 640, 320, 10)[0] > 120);
    }

    #[test]
    fn alpha_has_clear_and_opaque_squares_and_a_half_band() {
        let b = base_picture("alpha", 640, 360);
        assert_eq!(px(&b, 640, 0, 0)[3], 0);
        assert_eq!(px(&b, 640, 25, 0)[3], 255);
        assert_eq!(px(&b, 640, 320, 180)[3], 128);
    }

    #[test]
    fn motion_bar_moves_with_time() {
        let base = base_picture("motion", 640, 360);
        let a = draw_frame("motion", &base, 640, 360, 0.0, 0, None);
        let b = draw_frame("motion", &base, 640, 360, 0.5, 0, None);
        assert_ne!(a, b);
        assert_eq!(px(&a, 640, 5, 5), [255, 255, 255, 255]);
    }

    #[test]
    fn a_burn_patch_blends_and_the_motion_label_is_added() {
        let base = base_picture("motion", 640, 360);
        let patch = vec![255u8; 20 * 10 * 4];
        let p = Patch { x: 100, y: 200, w: 20, h: 10, bgra: &patch };
        let f = draw_frame("motion", &base, 640, 360, 0.0, 7, Some(&p));
        assert_eq!(px(&f, 640, 105, 205), [255, 255, 255, 255]);
        let plain = draw_frame("motion", &base, 640, 360, 0.0, 7, None);
        assert_ne!(f, plain);
    }

    #[test]
    fn tone_frames_follow_the_exact_rate_and_id_alternates_sides() {
        let mut t = Tone::new("tone", -20.0, 29.97);
        let total: usize = (0..30).map(|_| t.next_frame().1).sum();
        assert_eq!(total, 48048); // 30 frames at 29.97 fps last 30 * 1001 / 30000 s
        let mut id = Tone::new("id", 0.0, 30.0);
        let (a, n) = id.next_frame();
        assert!(a[..n].iter().any(|v| *v != 0.0) && a[n..].iter().all(|v| *v == 0.0));
        assert_eq!(id.channel(), "LEFT");
        for _ in 0..30 {
            id.next_frame();
        }
        assert_eq!(id.channel(), "RIGHT");
    }
}
