//! Turn a recorded run into the files a client, or a fine-tune, wants (`app/exporting.py`). Everything is derived on download from
//! `lines.jsonl`; nothing is stored, so a better pairing improves every past event too.
//!
//! * `transcript-<LANG>.txt`: one timestamped line per committed caption, per language;
//! * `pairs-<TARGET>.jsonl`: every source line matched to its translation, each row with a `confidence`;
//! * `finetune-<TARGET>.jsonl`: the `exact` pairs only, in OpenAI chat format.
//!
//! There is no segment id tying a source line to its translation: the two feeds are split into lines by their own timers. What they
//! share is the clock, so lines are matched by overlapping time span and each row says how sure the match is: `exact`, `merged`
//! (several source lines in one translation), `split` (one source line, several translations), `loose` (no overlap, nearest line
//! in a wider window) or `unpaired`.
//!
//! JSON is written compact; Python's `json.dumps` put a space after each `,` and `:`. Either reads the same.

use std::collections::BTreeMap;
use std::io::Write;

use flate2::write::DeflateEncoder;
use flate2::Compression;
use serde_json::{json, Value};

use crate::clock::Zone;

/// A translation that overlaps nothing can land just after the source line it answers, as the model is interpreting. The rescue
/// only applies when it picks out exactly one source line.
pub const PAIR_TOLERANCE_S: f64 = 1.5;
/// Last resort: the nearest source line within this window, reported `loose`.
pub const LOOSE_TOLERANCE_S: f64 = 4.5;

pub const TRANSCRIPTION_STREAM: &str = "transcription";
pub const TRANSLATION_STREAM: &str = "translation";

/// One recorded line.
#[derive(Clone, Debug)]
pub struct Row {
    pub stream: String,
    pub lang: String,
    pub seq: Value,
    t0: f64,
    t1: f64,
    pub text: String,
}

/// Python's `x or y` on numbers: a zero or a missing value is passed over.
fn num(v: Option<&Value>) -> f64 {
    v.and_then(Value::as_f64).unwrap_or(0.0)
}

impl Row {
    pub fn from_json(v: &Value) -> Row {
        Row {
            stream: v.get("stream").and_then(Value::as_str).unwrap_or("").to_owned(),
            lang: v.get("lang").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("?").to_owned(),
            seq: v.get("seq").cloned().unwrap_or(Value::Null),
            t0: num(v.get("t0")),
            t1: num(v.get("t1")),
            text: v.get("text").and_then(Value::as_str).unwrap_or("").to_owned(),
        }
    }

    fn span(&self) -> (f64, f64) {
        let t0 = if self.t0 != 0.0 { self.t0 } else { self.t1 };
        let t1 = if self.t1 != 0.0 { self.t1 } else { t0 };
        if t1 >= t0 {
            (t0, t1)
        } else {
            (t1, t0)
        }
    }
}

fn capitalise_words(s: &str) -> String {
    let mut out = String::new();
    let mut new_word = true;
    for c in s.chars() {
        if c.is_alphabetic() {
            out.extend(if new_word { c.to_uppercase().collect::<Vec<_>>() } else { c.to_lowercase().collect() });
            new_word = false;
        } else {
            out.push(c);
            new_word = true;
        }
    }
    out
}

fn label(lang: &str) -> String {
    if relay_core::languages::is_source(lang) {
        return relay_core::languages::SOURCE.label.to_owned();
    }
    relay_core::languages::target(lang).map_or_else(|| capitalise_words(lang), |l| l.label.to_owned())
}

/// Source lines, and translation lines grouped by target language. Grouping is from the rows, not the run's metadata, so a target
/// enabled or disabled part-way through exports exactly the lines it produced.
pub fn split_streams(rows: &[Row]) -> (Vec<Row>, BTreeMap<String, Vec<Row>>) {
    let mut source = Vec::new();
    let mut targets: BTreeMap<String, Vec<Row>> = BTreeMap::new();
    for row in rows {
        if row.stream == TRANSCRIPTION_STREAM {
            source.push(row.clone());
        } else if row.stream == TRANSLATION_STREAM {
            targets.entry(row.lang.clone()).or_default().push(row.clone());
        }
    }
    source.sort_by(|a, b| a.t0.total_cmp(&b.t0));
    for lines in targets.values_mut() {
        lines.sort_by(|a, b| a.t0.total_cmp(&b.t0));
    }
    (source, targets)
}

fn overlaps(span: (f64, f64), a0: f64, a1: f64) -> bool {
    span.1 >= a0 && span.0 <= a1
}

/// One matched pair, or a line nothing matched.
#[derive(Clone, Debug, PartialEq)]
pub struct Pair {
    pub t0: f64,
    pub time: String,
    pub confidence: &'static str,
    pub source: String,
    pub target: String,
    pub source_seq: Vec<Value>,
    pub target_seq: Value,
}

fn nearest(source: &[Row], t0: f64, window: f64) -> Option<usize> {
    let (mut best, mut best_d) = (None, window);
    for (i, src) in source.iter().enumerate() {
        let d = (src.span().0 - t0).abs();
        if d < best_d {
            best = Some(i);
            best_d = d;
        }
    }
    best
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

/// Match translation lines to source lines by overlapping time span, one row per pair in time order. Source lines nothing matched
/// are emitted as `unpaired` rows, so a dropout is visible instead of silently absent.
pub fn pair_lines(source: &[Row], target: &[Row], tol: f64, zone: Zone) -> Vec<Pair> {
    // Pass 1: which source lines each translation could belong to. A genuine overlap decides the match; the tolerance only rescues a
    // translation that lands just after its source, and never widens a single match into several.
    let mut claims: Vec<Vec<usize>> = Vec::new();
    let mut rescued = Vec::new();
    let mut claimed_by = vec![0usize; source.len()];
    for tr in target {
        let (a0, a1) = tr.span();
        let mut hits: Vec<usize> = source.iter().enumerate().filter(|(_, s)| overlaps(s.span(), a0, a1)).map(|(i, _)| i).collect();
        let mut loose = false;
        if hits.is_empty() {
            // A translation cannot precede its source, so the only plausible candidate is the source line that had just finished.
            let near: Vec<usize> = source
                .iter()
                .enumerate()
                .filter(|(_, s)| {
                    let gap = a0 - s.span().1;
                    (0.0..=tol).contains(&gap)
                })
                .map(|(i, _)| i)
                .collect();
            if near.len() == 1 {
                hits = near;
            } else if let Some(pick) = nearest(source, a0, LOOSE_TOLERANCE_S) {
                hits = vec![pick];
                loose = true;
            }
        }
        for &i in &hits {
            claimed_by[i] += 1;
        }
        claims.push(hits);
        rescued.push(loose);
    }

    // Pass 2: grade. A source line claimed by two translations is only detectable once every translation has been looked at.
    let mut rows = Vec::new();
    for ((tr, hits), loose) in target.iter().zip(&claims).zip(&rescued) {
        let (a0, _) = tr.span();
        if hits.is_empty() {
            rows.push(pair_row(None, Some(tr), "unpaired", a0, zone));
            continue;
        }
        let srcs: Vec<&Row> = hits.iter().map(|&i| &source[i]).collect();
        let confidence = if hits.len() > 1 {
            "merged"
        } else if claimed_by[hits[0]] > 1 {
            "split"
        } else if *loose {
            "loose"
        } else {
            "exact"
        };
        rows.push(pair_row(Some(&srcs), Some(tr), confidence, a0, zone));
    }
    for (i, src) in source.iter().enumerate() {
        if claimed_by[i] == 0 {
            rows.push(pair_row(Some(&[src]), None, "unpaired", src.span().0, zone));
        }
    }
    rows.sort_by(|a, b| a.t0.total_cmp(&b.t0));
    rows
}

fn pair_row(srcs: Option<&[&Row]>, tr: Option<&Row>, confidence: &'static str, t0: f64, zone: Zone) -> Pair {
    Pair {
        t0: round3(t0),
        time: zone(t0).clock(),
        confidence,
        source: srcs.map(|s| s.iter().map(|r| r.text.as_str()).collect::<Vec<_>>().join(" ")).unwrap_or_default(),
        target: tr.map(|t| t.text.clone()).unwrap_or_default(),
        source_seq: srcs.map(|s| s.iter().map(|r| r.seq.clone()).collect()).unwrap_or_default(),
        target_seq: tr.map_or(Value::Null, |t| t.seq.clone()),
    }
}

pub fn transcript_text(meta: &Value, lang: &str, lines: &[Row], zone: Zone) -> String {
    let stamp = |k: &str| meta.get(k).and_then(Value::as_f64).filter(|t| *t != 0.0).map_or("—".to_owned(), |t| zone(t).stamp());
    let run = meta.get("run_id").and_then(Value::as_str).unwrap_or("?");
    let mut out = vec![
        format!("# {} transcript — Relay run {run}", label(lang)),
        format!("# started {}   ended {}   {} lines", stamp("started"), stamp("ended"), lines.len()),
        String::new(),
    ];
    out.extend(lines.iter().map(|r| format!("[{}] {}", zone(r.span().0).clock(), r.text)));
    out.join("\n") + "\n"
}

pub fn pairs_jsonl(source_lang: &str, target_lang: &str, pairs: &[Pair]) -> String {
    let rows: Vec<String> = pairs
        .iter()
        .map(|p| {
            json!({
                "source_language": source_lang, "target_language": target_lang, "t0": p.t0, "time": p.time,
                "confidence": p.confidence, "source": p.source, "target": p.target, "source_seq": p.source_seq,
                "target_seq": p.target_seq,
            })
            .to_string()
        })
        .collect();
    lines_of(rows)
}

fn lines_of(rows: Vec<String>) -> String {
    if rows.is_empty() {
        String::new()
    } else {
        rows.join("\n") + "\n"
    }
}

/// The confident pairs in OpenAI chat fine-tuning format: strictly `{"messages": [...]}` per row, as the uploader rejects other
/// top-level keys. The provenance a reviewer wants is in the pairs file beside it.
pub fn finetune_jsonl(source_lang: &str, target_lang: &str, pairs: &[Pair]) -> String {
    let system = format!(
        "Translate the user's {} into {}. This is live speech from a conference floor: keep the speaker's register, translate only, and add nothing.",
        label(source_lang),
        label(target_lang)
    );
    let rows = pairs
        .iter()
        .filter(|p| p.confidence == "exact" && !p.source.is_empty() && !p.target.is_empty())
        .map(|p| {
            json!({"messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": p.source},
                {"role": "assistant", "content": p.target},
            ]})
            .to_string()
        })
        .collect();
    lines_of(rows)
}

/// Every derived file for one run, by file name.
pub fn build_exports(meta: &Value, rows: &[Row], zone: Zone) -> BTreeMap<String, String> {
    let source_lang = meta.get("source_language").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("ENGLISH");
    let (source, targets) = split_streams(rows);
    let mut files = BTreeMap::new();
    if !source.is_empty() {
        files.insert(format!("transcript-{source_lang}.txt"), transcript_text(meta, source_lang, &source, zone));
    }
    for (lang, lines) in &targets {
        files.insert(format!("transcript-{lang}.txt"), transcript_text(meta, lang, lines, zone));
        let pairs = pair_lines(&source, lines, PAIR_TOLERANCE_S, zone);
        files.insert(format!("pairs-{lang}.jsonl"), pairs_jsonl(source_lang, lang, &pairs));
        let ft = finetune_jsonl(source_lang, lang, &pairs);
        if !ft.is_empty() {
            files.insert(format!("finetune-{lang}.jsonl"), ft);
        }
    }
    let manifest = manifest(meta, &source, &targets, &files, zone);
    files.insert("manifest.txt".to_owned(), manifest);
    files
}

fn manifest(meta: &Value, source: &[Row], targets: &BTreeMap<String, Vec<Row>>, files: &BTreeMap<String, String>, zone: Zone) -> String {
    let started = meta.get("started").and_then(Value::as_f64).filter(|t| *t != 0.0);
    let ended = meta.get("ended").and_then(Value::as_f64).filter(|t| *t != 0.0);
    let stamp = |t: Option<f64>| t.map_or("—".to_owned(), |t| zone(t).stamp());
    let dur = (ended.or(started).unwrap_or(0.0) - started.unwrap_or(0.0)) as i64;
    let source_lang = meta.get("source_language").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("ENGLISH");
    let target_list = if targets.is_empty() {
        "none".to_owned()
    } else {
        targets.iter().map(|(l, v)| format!("{} ({} lines)", label(l), v.len())).collect::<Vec<_>>().join(", ")
    };
    let mut out = vec![
        format!("Relay run {}", meta.get("run_id").and_then(Value::as_str).unwrap_or("?")),
        format!("  started  {}", stamp(started)),
        format!("  ended    {}", stamp(ended)),
        format!("  duration {}h {:02}m {:02}s", dur / 3600, dur % 3600 / 60, dur % 60),
        format!("  source   {} ({} lines)", label(source_lang), source.len()),
        format!("  targets  {target_list}"),
        String::new(),
        "Files".to_owned(),
        "  lines.jsonl       every committed line as it happened (the raw record)".to_owned(),
        "  transcript-*.txt  one timestamped line per caption, per language".to_owned(),
        "  pairs-*.jsonl     source matched to translation, with a confidence field".to_owned(),
        "  finetune-*.jsonl  the 'exact' pairs only, in OpenAI chat format".to_owned(),
        String::new(),
        "Pairing confidence: exact | merged (several source lines in one".to_owned(),
        "translation) | split (one source line, several translations) | loose".to_owned(),
        "(no time overlap, nearest match) | unpaired (nothing on the other side).".to_owned(),
        "Only 'exact' rows are carried into the fine-tuning file.".to_owned(),
        String::new(),
        "Contents".to_owned(),
    ];
    out.extend(files.keys().map(|n| format!("  {n}")));
    out.join("\n") + "\n"
}

/// Everything for one run in a single zip download: the derived files, the raw `lines.jsonl` when there is one, and `meta.json`.
pub fn zip_bytes(meta: &Value, rows: &[Row], raw: &str, zone: Zone, now: f64) -> Vec<u8> {
    let files = build_exports(meta, rows, zone);
    let run = meta.get("run_id").and_then(Value::as_str).unwrap_or("run");
    let mut zip = Zip::new(zone(now));
    for (name, text) in &files {
        zip.add(&format!("{run}/{name}"), text.as_bytes());
    }
    if !raw.is_empty() {
        zip.add(&format!("{run}/lines.jsonl"), raw.as_bytes());
    }
    zip.add(&format!("{run}/meta.json"), serde_json::to_string_pretty(meta).unwrap_or_default().as_bytes());
    zip.finish()
}

/// The little of the zip format a download needs: deflated entries, no extras, names in UTF-8.
struct Zip {
    out: Vec<u8>,
    central: Vec<u8>,
    entries: u16,
    dos_time: u16,
    dos_date: u16,
}

impl Zip {
    fn new(at: crate::clock::Civil) -> Self {
        let year = (at.year.clamp(1980, 2107) - 1980) as u16;
        Self {
            out: Vec::new(),
            central: Vec::new(),
            entries: 0,
            dos_time: (at.hour as u16) << 11 | (at.minute as u16) << 5 | (at.second as u16 / 2),
            dos_date: year << 9 | (at.month as u16) << 5 | at.day as u16,
        }
    }

    fn add(&mut self, name: &str, data: &[u8]) {
        let mut enc = DeflateEncoder::new(Vec::new(), Compression::default());
        let _ = enc.write_all(data);
        let packed = enc.finish().unwrap_or_default();
        let crc = crc32fast::hash(data);
        let offset = self.out.len() as u32;
        let head = |v: &mut Vec<u8>, central: bool| {
            if central {
                v.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
                v.extend_from_slice(&20u16.to_le_bytes()); // version made by
            } else {
                v.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
            }
            v.extend_from_slice(&20u16.to_le_bytes()); // version needed
            v.extend_from_slice(&0x0800u16.to_le_bytes()); // names are UTF-8
            v.extend_from_slice(&8u16.to_le_bytes()); // deflate
            v.extend_from_slice(&self.dos_time.to_le_bytes());
            v.extend_from_slice(&self.dos_date.to_le_bytes());
            v.extend_from_slice(&crc.to_le_bytes());
            v.extend_from_slice(&(packed.len() as u32).to_le_bytes());
            v.extend_from_slice(&(data.len() as u32).to_le_bytes());
            v.extend_from_slice(&(name.len() as u16).to_le_bytes());
            v.extend_from_slice(&0u16.to_le_bytes()); // extra
            if central {
                v.extend_from_slice(&[0u8; 6]); // comment length, disk, internal attributes
                v.extend_from_slice(&0u32.to_le_bytes()); // external attributes
                v.extend_from_slice(&offset.to_le_bytes());
            }
            v.extend_from_slice(name.as_bytes());
        };
        let mut local = Vec::new();
        head(&mut local, false);
        self.out.extend(local);
        self.out.extend_from_slice(&packed);
        let mut central = Vec::new();
        head(&mut central, true);
        self.central.extend(central);
        self.entries += 1;
    }

    fn finish(mut self) -> Vec<u8> {
        let start = self.out.len() as u32;
        let size = self.central.len() as u32;
        self.out.extend(self.central);
        self.out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
        self.out.extend_from_slice(&[0u8; 4]); // disk numbers
        self.out.extend_from_slice(&self.entries.to_le_bytes());
        self.out.extend_from_slice(&self.entries.to_le_bytes());
        self.out.extend_from_slice(&size.to_le_bytes());
        self.out.extend_from_slice(&start.to_le_bytes());
        self.out.extend_from_slice(&0u16.to_le_bytes()); // comment length
        self.out
    }
}
