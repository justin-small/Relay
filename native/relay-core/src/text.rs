//! Shared text helpers (`app/text.py`, #522): one way to compare names, for core and every module.
//!
//! `squeeze` makes runs of whitespace one space and trims the ends; `fold` is `squeeze` and case-insensitive; two names are "the same" when
//! their folds are equal, and two empty names are not a match.

/// `text` with each run of whitespace made one space and the ends trimmed.
pub fn squeeze(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `text` squeezed and case-folded: the key two names are compared by.
pub fn fold(text: &str) -> String {
    squeeze(text).to_lowercase()
}

/// Whether two names are the same, ignoring case and spacing. Two empty names are not a match.
pub fn same_name(a: &str, b: &str) -> bool {
    let fa = fold(a);
    !fa.is_empty() && fa == fold(b)
}

/// `text` with accents removed ("José" -> "Jose"); case and punctuation kept (`strip_accents`).
pub fn strip_accents(text: &str) -> String {
    use unicode_normalization::{char::is_combining_mark, UnicodeNormalization};
    text.nfkd().filter(|c| !is_combining_mark(*c)).collect()
}
