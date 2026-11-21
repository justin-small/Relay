//! The NDI analysis picture scopes (issue 300) for one frame: waveform, RGB parade, vectorscope, luma histogram,
//! the readouts and the alarms. A port of `analysis.scopes` in Python (issue 606) that follows its arithmetic step by
//! step (float32 where numpy used float32, round-half-even where it rounded), so the two agree to the rounding of the
//! last digit. 8-bit code values, legal range 16 to 235.

pub const MAX_SCOPE_SAMPLES: usize = 200_000;
const BLACK_PEAK: u8 = 20;
const FROZEN_DIFF: f64 = 0.05;
const GAMUT_FRACTION: f64 = 0.001;
const GAMUT_MIN: usize = 64;

pub struct Frame<'a> {
    pub data: &'a [u8],
    pub width: usize,
    pub height: usize,
    /// "UYVY", "BGRA", "BGRX", "RGBA" or "RGBX"; anything else has no scopes.
    pub fourcc: &'a str,
}

/// The 128 by 128 luma samples a frame leaves behind for the next frame's difference.
#[derive(Clone, Debug, PartialEq)]
pub struct Grid {
    pub w: usize,
    pub h: usize,
    pub v: Vec<f32>,
}

pub struct Pack {
    pub w: usize,
    pub h: usize,
    pub b64: String,
}

pub struct Read {
    pub luma_min: u8,
    pub luma_max: u8,
    pub luma_mean: f64,
    pub sub_black: usize,
    pub super_white: usize,
    pub illegal_rgb: usize,
    pub frame_diff: Option<f64>,
    pub sharpness: f64,
    pub letterbox: [usize; 2],
    pub pillarbox: [usize; 2],
    pub aspect: f64,
    pub matrix: &'static str,
    pub width: usize,
    pub height: usize,
}

pub struct Scopes {
    pub waveform: Pack,
    pub parade: [Pack; 3],
    pub vectorscope: Pack,
    pub histogram: Vec<u32>,
    pub read: Read,
    pub alarms: Vec<(&'static str, &'static str)>,
}

/// numpy's rint: round half to even.
fn rint(v: f32) -> f32 {
    v.round_ties_even()
}

fn c8(v: f32) -> u8 {
    rint(v).clamp(0.0, 255.0) as u8
}

fn round_to(v: f64, places: i32) -> f64 {
    let m = 10f64.powi(places);
    (v * m).round_ties_even() / m
}

/// The picture as full-size luma plus a way to read chroma at a sample position.
struct Planes<'a> {
    w: usize,
    h: usize,
    y: Vec<u8>,
    kr: f64,
    kb: f64,
    src: &'a [u8],
    uyvy: bool,
    order: [usize; 3], // byte offsets of r, g, b inside a 4-byte pixel (RGB sources)
}

impl Planes<'_> {
    fn chroma(&self, x: usize, y: usize) -> (u8, u8) {
        if self.uyvy {
            let o = (y * self.w + (x & !1)) * 2;
            (self.src[o], self.src[o + 2])
        } else {
            let o = (y * self.w + x) * 4;
            let (r, g, b) = (self.src[o + self.order[0]] as f32, self.src[o + self.order[1]] as f32, self.src[o + self.order[2]] as f32);
            let (kr, kb) = (self.kr as f32, self.kb as f32);
            let kg = (1.0 - self.kr - self.kb) as f32;
            let yy = kr * r + kg * g + kb * b;
            let cb = (b - yy) / (2.0 * (1.0 - self.kb) as f32);
            let cr = (r - yy) / (2.0 * (1.0 - self.kr) as f32);
            (c8(128.0 + cb * 224.0 / 255.0), c8(128.0 + cr * 224.0 / 255.0))
        }
    }
}

fn planes<'a>(f: &Frame<'a>) -> Option<Planes<'a>> {
    let (mut w, h) = (f.width, f.height);
    if w < 2 || h < 2 || f.data.is_empty() {
        return None;
    }
    let (kr, kb) = if h <= 576 { (0.299, 0.114) } else { (0.2126, 0.0722) };
    match f.fourcc {
        "UYVY" => {
            w -= w % 2;
            // The frame's own rows are `f.width * 2` bytes; a trimmed odd width only drops the last column.
            if f.data.len() < f.width * h * 2 {
                return None;
            }
            let mut y = vec![0u8; w * h];
            for r in 0..h {
                let src = &f.data[r * f.width * 2..r * f.width * 2 + w * 2];
                let dst = &mut y[r * w..(r + 1) * w];
                for (d, px) in dst.chunks_exact_mut(2).zip(src.chunks_exact(4)) {
                    d[0] = px[1];
                    d[1] = px[3];
                }
            }
            // Chroma reads use the trimmed width as the row pitch only when no trim happened; keep the real pitch.
            let src = f.data;
            let pitch_ok = w == f.width;
            if !pitch_ok {
                return None; // odd widths do not occur in NDI; refuse rather than misread
            }
            Some(Planes { w, h, y, kr, kb, src, uyvy: true, order: [0, 1, 2] })
        }
        "BGRA" | "BGRX" | "RGBA" | "RGBX" => {
            if f.data.len() < w * h * 4 {
                return None;
            }
            let order = if f.fourcc.starts_with("BGR") { [2, 1, 0] } else { [0, 1, 2] };
            let (kr32, kb32) = (kr as f32, kb as f32);
            let kg32 = (1.0 - kr - kb) as f32;
            let mut y = vec![0u8; w * h];
            for (d, px) in y.iter_mut().zip(f.data.chunks_exact(4)) {
                let (r, g, b) = (px[order[0]] as f32, px[order[1]] as f32, px[order[2]] as f32);
                *d = c8(16.0 + (kr32 * r + kg32 * g + kb32 * b) * 219.0 / 255.0);
            }
            Some(Planes { w, h, y, kr, kb, src: f.data, uyvy: false, order })
        }
        _ => None,
    }
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn base64(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for c in bytes.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        out.push(B64[(n >> 18) as usize & 63] as char);
        out.push(B64[(n >> 12) as usize & 63] as char);
        out.push(if c.len() > 1 { B64[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if c.len() > 2 { B64[n as usize & 63] as char } else { '=' });
    }
    out
}

/// Counts to 0..255 brightness on a log scale (so a faint trace still shows), as an image of `rows` by `cols` where
/// `at(row, col)` gives the count.
fn pack(rows: usize, cols: usize, at: impl Fn(usize, usize) -> u32) -> Pack {
    let mut m = 0u32;
    for r in 0..rows {
        for c in 0..cols {
            m = m.max(at(r, c));
        }
    }
    let m = if m == 0 { 1.0 } else { m as f64 };
    let lm = m.ln_1p();
    let mut img = Vec::with_capacity(rows * cols);
    for r in 0..rows {
        for c in 0..cols {
            img.push(((at(r, c) as f64).ln_1p() / lm * 255.0) as u8);
        }
    }
    Pack { w: cols, h: rows, b64: base64(&img) }
}

/// Counts of how many samples fell at each of 256 levels in each of `cols` column buckets: `[col * 256 + level]`.
fn level_grid(level: &[u8], hs: usize, ws: usize, cols: usize) -> Vec<u32> {
    let mut counts = vec![0u32; cols * 256];
    let bucket: Vec<usize> = (0..ws).map(|x| x * cols / ws).collect();
    for r in 0..hs {
        for (x, &l) in level[r * ws..(r + 1) * ws].iter().enumerate() {
            counts[bucket[x] * 256 + l as usize] += 1;
        }
    }
    counts
}

fn first_over(v: impl Iterator<Item = u8>, len: usize) -> usize {
    v.enumerate().find(|(_, x)| *x > BLACK_PEAK).map(|(i, _)| i).unwrap_or(len)
}

/// The scopes and readouts for one frame, and the grid to pass as `prev` next time. None when the pixel format is
/// not handled.
pub fn scopes(f: &Frame, prev: Option<&Grid>) -> Option<(Scopes, Grid)> {
    let p = planes(f)?;
    let (w, h) = (p.w, p.h);
    let s = ((w * h) as f64 / MAX_SCOPE_SAMPLES as f64).sqrt().ceil().max(1.0) as usize;
    let (ws, hs) = (w.div_ceil(s), h.div_ceil(s));

    // Whole picture: luma statistics, histogram, and the brightest value in each row and column.
    let mut hist = [0u32; 256];
    let (mut sub, mut sup) = (0usize, 0usize);
    let (mut ymin, mut ymax, mut sum) = (255u8, 0u8, 0u64);
    let mut rows = vec![0u8; h];
    let mut cols = vec![0u8; w];
    for r in 0..h {
        let line = &p.y[r * w..(r + 1) * w];
        let mut rmax = 0u8;
        for (x, &v) in line.iter().enumerate() {
            hist[v as usize] += 1;
            rmax = rmax.max(v);
            cols[x] = cols[x].max(v);
        }
        rows[r] = rmax;
    }
    for (v, &n) in hist.iter().enumerate() {
        if n > 0 {
            ymin = ymin.min(v as u8);
            ymax = ymax.max(v as u8);
        }
        sum += v as u64 * n as u64;
        if v < 16 {
            sub += n as usize;
        }
        if v > 235 {
            sup += n as usize;
        }
    }

    // The subsampled picture the scopes draw from.
    let mut ys = vec![0u8; hs * ws];
    let (mut lr, mut lg, mut lb) = (vec![0u8; hs * ws], vec![0u8; hs * ws], vec![0u8; hs * ws]);
    let mut vec_counts = vec![0u32; 128 * 128];
    let (kr, kb) = (p.kr as f32, p.kb as f32);
    let (k_r, k_b, k_g) = ((2.0 * (1.0 - p.kr)) as f32, (2.0 * (1.0 - p.kb)) as f32, (1.0 - p.kr - p.kb) as f32);
    let lvl = |v: f32| -> u8 { rint(v * 219.0 / 255.0 + 16.0).clamp(0.0, 255.0) as u8 };
    let mut bad = 0usize;
    for i in 0..hs {
        for j in 0..ws {
            let (sy, sx) = (i * s, j * s);
            let y = p.y[sy * w + sx];
            let (cb, cr) = p.chroma(sx, sy);
            let yy = (y as f32 - 16.0) * 255.0 / 219.0;
            let pb = (cb as f32 - 128.0) * 255.0 / 224.0;
            let pr = (cr as f32 - 128.0) * 255.0 / 224.0;
            let r = yy + k_r * pr;
            let b = yy + k_b * pb;
            let g = (yy - kr * r - kb * b) / k_g;
            let (a, bb, c) = (lvl(r), lvl(g), lvl(b));
            let o = i * ws + j;
            ys[o] = y;
            lr[o] = a;
            lg[o] = bb;
            lb[o] = c;
            if a < 13 || a > 238 || bb < 13 || bb > 238 || c < 13 || c > 238 {
                bad += 1;
            }
            vec_counts[(cb as usize >> 1) * 128 + (cr as usize >> 1)] += 1;
        }
    }
    let illegal = bad * s * s;

    // The grid for the frame difference.
    let (step_y, step_x) = ((h / 128).max(1), (w / 128).max(1));
    let (gh, gw) = (h.div_ceil(step_y).min(128), w.div_ceil(step_x).min(128));
    let mut gv = Vec::with_capacity(gh * gw);
    for i in 0..gh {
        for j in 0..gw {
            gv.push(p.y[i * step_y * w + j * step_x] as f32);
        }
    }
    let grid = Grid { w: gw, h: gh, v: gv };
    let diff = prev.filter(|g| g.w == grid.w && g.h == grid.h).map(|g| {
        let total: f64 = g.v.iter().zip(&grid.v).map(|(a, b)| (b - a).abs() as f64).sum();
        total / grid.v.len() as f64
    });

    let sharp = if ws > 1 {
        let mut t = 0u64;
        for i in 0..hs {
            for j in 1..ws {
                t += (ys[i * ws + j] as i32 - ys[i * ws + j - 1] as i32).unsigned_abs() as u64;
            }
        }
        t as f64 / (hs * (ws - 1)) as f64
    } else {
        0.0
    };

    let wave = level_grid(&ys, hs, ws, 256);
    let parade_counts = [level_grid(&lr, hs, ws, 128), level_grid(&lg, hs, ws, 128), level_grid(&lb, hs, ws, 128)];
    let level_img = |counts: &Vec<u32>, cols: usize| pack(256, cols, |r, c| counts[c * 256 + (255 - r)]);

    let mut alarms = Vec::new();
    if ymax <= BLACK_PEAK {
        alarms.push(("BLACK", "bad"));
    }
    if diff.is_some_and(|d| d < FROZEN_DIFF) {
        alarms.push(("FROZEN", "bad"));
    }
    if illegal >= GAMUT_MIN && illegal as f64 / (w * h) as f64 > GAMUT_FRACTION {
        alarms.push(("GAMUT", "warn"));
    }

    let out = Scopes {
        waveform: level_img(&wave, 256),
        parade: [level_img(&parade_counts[0], 128), level_img(&parade_counts[1], 128), level_img(&parade_counts[2], 128)],
        vectorscope: pack(128, 128, |r, c| vec_counts[c * 128 + (127 - r)]),
        histogram: hist.to_vec(),
        read: Read {
            luma_min: ymin,
            luma_max: ymax,
            luma_mean: round_to(sum as f64 / (w * h) as f64, 1),
            sub_black: sub,
            super_white: sup,
            illegal_rgb: illegal,
            frame_diff: diff.map(|d| round_to(d, 3)),
            sharpness: round_to(sharp, 2),
            letterbox: [first_over(rows.iter().copied(), h), first_over(rows.iter().rev().copied(), h)],
            pillarbox: [first_over(cols.iter().copied(), w), first_over(cols.iter().rev().copied(), w)],
            aspect: round_to(w as f64 / h as f64, 4),
            matrix: if h <= 576 { "BT.601" } else { "BT.709" },
            width: w,
            height: h,
        },
        alarms,
    };
    Some((out, grid))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uyvy(w: usize, h: usize, y: u8, cb: u8, cr: u8) -> Vec<u8> {
        let mut d = Vec::with_capacity(w * h * 2);
        for _ in 0..w * h / 2 {
            d.extend_from_slice(&[cb, y, cr, y]);
        }
        d
    }

    #[test]
    fn base64_matches_the_standard() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn black_frame_alarms_and_freezes() {
        let d = uyvy(64, 48, 16, 128, 128);
        let f = Frame { data: &d, width: 64, height: 48, fourcc: "UYVY" };
        let (a, g) = scopes(&f, None).unwrap();
        assert!(a.alarms.contains(&("BLACK", "bad")));
        assert_eq!(a.read.letterbox, [48, 48]);
        assert!(a.read.frame_diff.is_none());
        let (b, _) = scopes(&f, Some(&g)).unwrap();
        assert!(b.alarms.contains(&("FROZEN", "bad")));
        assert_eq!(b.read.frame_diff, Some(0.0));
    }

    #[test]
    fn mid_grey_is_legal_and_centred() {
        let d = uyvy(64, 48, 126, 128, 128);
        let f = Frame { data: &d, width: 64, height: 48, fourcc: "UYVY" };
        let (a, _) = scopes(&f, None).unwrap();
        assert_eq!((a.read.luma_min, a.read.luma_max), (126, 126));
        assert_eq!(a.read.illegal_rgb, 0);
        assert_eq!(a.read.sub_black + a.read.super_white, 0);
        assert!(a.alarms.is_empty());
        assert_eq!(a.histogram[126], 64 * 48);
    }

    #[test]
    fn an_unknown_format_has_no_scopes() {
        let f = Frame { data: &[0; 64], width: 4, height: 4, fourcc: "P216" };
        assert!(scopes(&f, None).is_none());
    }
}
