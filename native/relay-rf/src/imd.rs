//! Clean-frequency finder (issue 368): choose N channels from a sweep that avoid occupied spectrum and keep
//! third-order intermodulation products (2a-b) off every chosen channel.

/// Third-order products 2a-b and 2b-a for every pair.
pub fn third_order(freqs: &[f64]) -> Vec<f64> {
    let mut v = vec![];
    for (i, a) in freqs.iter().enumerate() {
        for b in &freqs[i + 1..] {
            v.push(2.0 * a - b);
            v.push(2.0 * b - a);
        }
    }
    v
}

fn clashes(set: &[f64], guard: f64) -> bool {
    let p = third_order(set);
    set.iter().any(|f| p.iter().any(|x| (x - f).abs() < guard))
}

/// `occupied(f)` says a candidate frequency has a carrier or interferer. Greedy from the band bottom.
pub fn pick(start: f64, stop: f64, step: f64, n: usize, spacing: f64, guard: f64, occupied: impl Fn(f64) -> bool) -> Vec<f64> {
    let mut chosen: Vec<f64> = vec![];
    let mut f = start;
    while f <= stop && chosen.len() < n {
        if !occupied(f) && chosen.iter().all(|c| (c - f).abs() >= spacing) {
            chosen.push(f);
            if clashes(&chosen, guard) {
                chosen.pop();
            }
        }
        f += step;
    }
    chosen
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn products_are_computed() {
        let p = third_order(&[600.0, 610.0]);
        assert!(p.contains(&590.0) && p.contains(&620.0));
    }

    #[test]
    fn picks_n_clean_channels_without_clashes() {
        let busy = |f: f64| (f - 510e6).abs() < 1e6; // an interferer at 510 MHz
        let got = pick(500e6, 520e6, 25e3, 6, 400e3, 100e3, busy);
        assert_eq!(got.len(), 6);
        assert!(got.iter().all(|f| (f - 510e6).abs() >= 1e6));
        assert!(!clashes(&got, 100e3));
    }

    #[test]
    fn gives_fewer_when_the_band_is_full() {
        assert!(pick(500e6, 501e6, 25e3, 6, 400e3, 100e3, |_| false).len() < 6);
    }
}
