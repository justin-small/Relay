//! Properties that must hold for any input, checked on random ones by quickcheck.

use quickcheck::quickcheck;
use relay_helper::tls::{clean_pin, fingerprint_of, normalise};

quickcheck! {
    fn normalise_is_idempotent(s: String) -> bool {
        let once = normalise(&s);
        normalise(&once) == once
    }

    fn normalise_leaves_only_upper_hex(s: String) -> bool {
        normalise(&s).chars().all(|c| c.is_ascii_digit() || ('A'..='F').contains(&c))
    }

    fn a_fingerprint_is_64_upper_hex_digits(cert: Vec<u8>) -> bool {
        let fp = fingerprint_of(&cert);
        fp.len() == 64 && normalise(&fp) == fp
    }

    fn a_fingerprint_survives_colons_and_lowercase(cert: Vec<u8>) -> bool {
        let fp = fingerprint_of(&cert);
        let spaced: Vec<&str> = (0..fp.len()).step_by(2).map(|i| &fp[i..i + 2]).collect();
        clean_pin(&spaced.join(":").to_lowercase()) == Ok(fp)
    }

    fn clean_pin_never_accepts_a_wrong_length(s: String) -> bool {
        match clean_pin(&s) {
            Ok(pin) => pin.len() == 64,
            Err(_) => true,
        }
    }
}
