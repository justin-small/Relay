//! Turning received NDI audio into something a browser can play (issue 301): the chosen channels mixed to interleaved
//! 16-bit stereo.

/// `planar` is `ch` rows of `n` float samples. `pick` lists the channels to hear (0-based, ones that do not exist are
/// ignored; empty means all of them). One channel plays on both sides; two play as left and right; more are shared
/// out alternately between the sides and scaled so a full-scale tone from each does not clip. Returns `n` frames of
/// little-endian i16, left then right.
pub fn mix_s16(planar: &[f32], ch: usize, n: usize, pick: &[usize]) -> Vec<u8> {
    let chosen: Vec<usize> = if pick.is_empty() { (0..ch).collect() } else { pick.iter().copied().filter(|&c| c < ch).collect() };
    let mut out = Vec::with_capacity(n * 4);
    if chosen.is_empty() || planar.len() < ch * n {
        out.resize(n * 4, 0);
        return out;
    }
    let (left, right): (Vec<usize>, Vec<usize>) = match chosen.len() {
        1 => (chosen.clone(), chosen.clone()),
        _ => {
            let l = chosen.iter().copied().step_by(2).collect();
            let r = chosen.iter().copied().skip(1).step_by(2).collect();
            (l, r)
        }
    };
    let side = |set: &[usize], i: usize| -> f32 {
        if set.is_empty() {
            return 0.0;
        }
        let sum: f32 = set.iter().map(|&c| planar[c * n + i]).sum();
        sum / (set.len() as f32).max(1.0)
    };
    for i in 0..n {
        for set in [&left, &right] {
            let v = (side(set, i).clamp(-1.0, 1.0) * 32767.0).round() as i16;
            out.extend_from_slice(&v.to_le_bytes());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frames(b: &[u8]) -> Vec<(i16, i16)> {
        b.chunks(4).map(|f| (i16::from_le_bytes([f[0], f[1]]), i16::from_le_bytes([f[2], f[3]]))).collect()
    }

    #[test]
    fn one_channel_plays_on_both_sides() {
        let planar = [0.5, -0.5, 0.0, 0.0];
        assert_eq!(frames(&mix_s16(&planar, 2, 2, &[0])), vec![(16384, 16384), (-16384, -16384)]);
    }

    #[test]
    fn two_channels_are_left_and_right_and_empty_means_all() {
        let planar = [1.0, 1.0, -1.0, -1.0];
        assert_eq!(frames(&mix_s16(&planar, 2, 2, &[])), vec![(32767, -32767); 2]);
        assert_eq!(frames(&mix_s16(&planar, 2, 2, &[1, 0])), vec![(-32767, 32767); 2]);
    }

    #[test]
    fn many_channels_share_the_sides_and_do_not_clip() {
        let planar = [1.0f32; 8]; // four channels, two frames, all full scale
        let f = frames(&mix_s16(&planar, 4, 2, &[]));
        assert_eq!(f, vec![(32767, 32767); 2]);
    }

    #[test]
    fn nothing_to_hear_is_silence_of_the_right_length() {
        assert_eq!(mix_s16(&[0.3, 0.3], 1, 2, &[5]), vec![0u8; 8]);
        assert_eq!(mix_s16(&[], 0, 3, &[]).len(), 12);
    }
}
