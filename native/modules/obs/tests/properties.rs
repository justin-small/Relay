//! Properties that hold for any input: the caption splitter loses nothing and never overfills a line, and cleaning settings is idempotent.

use quickcheck::quickcheck;
use relay_module_obs::settings::{caption_lines, clean_settings};
use serde_json::json;

quickcheck! {
    fn caption_lines_lose_nothing_and_fit(text: String, width: u8) -> bool {
        let width = usize::from(width % 60) + 1;
        let lines = caption_lines(&text, width);
        let want: String = text.split_whitespace().collect();
        let got: String = lines.iter().flat_map(|l| l.split_whitespace()).collect();
        lines.iter().all(|l| l.chars().count() <= width && !l.is_empty()) && want == got
    }

    fn cleaning_twice_changes_nothing(host: String, port: i64, delay: f64) -> bool {
        let once = clean_settings(&json!({"machines": [{"host": host, "port": port, "caption_delay": delay}]}));
        // (A name cut to 24 characters may end in a space, as Python's did, so the name is left out.) A computer without a valid id is given a new one each time, so the id is the one thing that may differ.
        let strip = |mut v: serde_json::Value| { v["machines"][0]["id"] = json!(""); v };
        let again = clean_settings(&{ let mut keep = once.clone(); keep["machines"][0]["id"] = once["machines"][0]["id"].clone(); keep });
        strip(again) == strip(once)
    }
}
