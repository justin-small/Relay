//! A received frame as an ordinary RGB picture, scaled down and, for the browser viewer, encoded as JPEG (issue 296).
//! The Python viewer did the conversion in numpy and the scaling and encoding in Pillow, and managed 5 to 8 frames a
//! second at 1080p; this does the same work in one native pass.

/// An 8-bit RGB picture, rows packed.
pub struct Rgb {
    pub width: usize,
    pub height: usize,
    pub data: Vec<u8>,
}

/// The frame as RGB no wider than `max_width` (smaller pictures are not enlarged). UYVY is read as BT.709 limited
/// range with each pixel pair sharing its chroma, as the viewer always did. None for a pixel format not handled.
pub fn to_rgb(data: &[u8], width: usize, height: usize, fourcc: &str, max_width: usize) -> Option<Rgb> {
    if width < 2 || height < 2 || data.is_empty() {
        return None;
    }
    let full = match fourcc {
        "UYVY" => {
            let w = width - width % 2;
            if data.len() < w * height * 2 || w != width {
                return None;
            }
            let mut out = vec![0u8; w * height * 3];
            for (src, dst) in data.chunks_exact(w * 2).zip(out.chunks_exact_mut(w * 3)).take(height) {
                for (px, o) in src.as_chunks::<4>().0.iter().zip(dst.as_chunks_mut::<6>().0.iter_mut()) {
                    let (u, v) = (px[0] as f32 - 128.0, px[2] as f32 - 128.0);
                    let (r, g, b) = (1.7927 * v, -0.2132 * u - 0.5329 * v, 2.1124 * u);
                    for (k, y) in [px[1], px[3]].into_iter().enumerate() {
                        let y = (y as f32 - 16.0) * 1.1644;
                        o[3 * k] = (y + r).clamp(0.0, 255.0) as u8;
                        o[3 * k + 1] = (y + g).clamp(0.0, 255.0) as u8;
                        o[3 * k + 2] = (y + b).clamp(0.0, 255.0) as u8;
                    }
                }
            }
            Rgb { width: w, height, data: out }
        }
        "BGRA" | "BGRX" | "RGBA" | "RGBX" => {
            if data.len() < width * height * 4 {
                return None;
            }
            let bgr = fourcc.starts_with("BGR");
            let mut out = vec![0u8; width * height * 3];
            for (px, o) in data.as_chunks::<4>().0.iter().zip(out.as_chunks_mut::<3>().0.iter_mut()) {
                if bgr {
                    (o[0], o[1], o[2]) = (px[2], px[1], px[0]);
                } else {
                    (o[0], o[1], o[2]) = (px[0], px[1], px[2]);
                }
            }
            Rgb { width, height, data: out }
        }
        _ => return None,
    };
    Some(if full.width > max_width.max(1) { scale(&full, max_width.max(1)) } else { full })
}

/// For each output index, the first input index and the weights of the input cells it covers (a box filter, so
/// shrinking averages every pixel it covers).
fn spans(src: usize, dst: usize) -> Vec<(usize, Vec<f32>)> {
    let ratio = src as f64 / dst as f64;
    (0..dst)
        .map(|i| {
            let (a, b) = (i as f64 * ratio, (i + 1) as f64 * ratio);
            let first = a.floor() as usize;
            let last = (b.ceil() as usize).min(src);
            let w: Vec<f32> = (first..last).map(|j| ((b.min(j as f64 + 1.0) - a.max(j as f64)) / ratio) as f32).collect();
            (first, w)
        })
        .collect()
}

fn scale(img: &Rgb, new_w: usize) -> Rgb {
    let new_h = ((img.height as f64 * new_w as f64 / img.width as f64).round() as usize).max(1);
    let (xs, ys) = (spans(img.width, new_w), spans(img.height, new_h));
    // Across first, over every input row, then down.
    let mut mid = vec![0f32; img.height * new_w * 3];
    for (row, out) in img.data.chunks_exact(img.width * 3).zip(mid.chunks_exact_mut(new_w * 3)) {
        for ((first, w), o) in xs.iter().zip(out.as_chunks_mut::<3>().0.iter_mut()) {
            let (mut r, mut g, mut b) = (0f32, 0f32, 0f32);
            for (k, wt) in w.iter().enumerate() {
                let p = &row[(first + k) * 3..(first + k) * 3 + 3];
                r += p[0] as f32 * wt;
                g += p[1] as f32 * wt;
                b += p[2] as f32 * wt;
            }
            *o = [r, g, b];
        }
    }
    let mut data = vec![0u8; new_w * new_h * 3];
    for ((first, w), out) in ys.iter().zip(data.chunks_exact_mut(new_w * 3)) {
        for (x, o) in out.iter_mut().enumerate() {
            let s: f32 = w.iter().enumerate().map(|(k, wt)| mid[(first + k) * new_w * 3 + x] * wt).sum();
            *o = (s + 0.5).clamp(0.0, 255.0) as u8;
        }
    }
    Rgb { width: new_w, height: new_h, data }
}

/// The frame as a JPEG no wider than `max_width`. None for a pixel format not handled or a failed encode.
pub fn jpeg(data: &[u8], width: usize, height: usize, fourcc: &str, max_width: usize, quality: u8) -> Option<Vec<u8>> {
    let img = to_rgb(data, width, height, fourcc, max_width)?;
    let mut out = Vec::with_capacity(img.data.len() / 8);
    let enc = jpeg_encoder::Encoder::new(&mut out, quality.clamp(1, 100));
    enc.encode(&img.data, u16::try_from(img.width).ok()?, u16::try_from(img.height).ok()?, jpeg_encoder::ColorType::Rgb).ok()?;
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grey_uyvy_is_grey_rgb() {
        let d: Vec<u8> = [128u8, 126, 128, 126].repeat(8 * 4 / 2);
        let img = to_rgb(&d, 8, 4, "UYVY", 100).unwrap();
        assert_eq!((img.width, img.height), (8, 4));
        assert!(img.data.iter().all(|v| (*v as i32 - 128).abs() <= 1), "{:?}", &img.data[..6]);
    }

    #[test]
    fn bgra_swaps_to_rgb_and_scales_down_by_averaging() {
        let mut d = Vec::new();
        for x in 0..8 {
            d.extend_from_slice(if x % 2 == 0 { &[0, 0, 255, 255] } else { &[0, 0, 0, 255] }); // red, black
        }
        let img = to_rgb(&d.repeat(4), 8, 4, "BGRA", 4).unwrap();
        assert_eq!((img.width, img.height), (4, 2));
        assert!(img.data.chunks(3).all(|p| p[0] == 128 && p[1] == 0 && p[2] == 0), "{:?}", &img.data[..6]);
    }

    #[test]
    fn jpeg_has_the_jpeg_markers_and_unknown_formats_give_none() {
        let d = vec![90u8; 64 * 36 * 4];
        let j = jpeg(&d, 64, 36, "BGRA", 32, 80).unwrap();
        assert_eq!(&j[..2], &[0xFF, 0xD8]);
        assert_eq!(&j[j.len() - 2..], &[0xFF, 0xD9]);
        assert!(jpeg(&d, 64, 36, "P216", 32, 80).is_none());
    }
}
