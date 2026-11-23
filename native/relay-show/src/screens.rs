//! Screens and places (#527): `app/screens.py`.

/// The id in `place:relay:5`, or `None` for anything else.
pub fn place_id(text: &str) -> Option<i64> {
    let parts: Vec<&str> = text.split(':').collect();
    if parts.len() != 3 || parts[0] != "place" || parts[1] != "relay" || parts[2].is_empty() || !parts[2].bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    parts[2].parse().ok()
}
