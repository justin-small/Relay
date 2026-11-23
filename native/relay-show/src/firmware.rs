//! Panel firmware (`app/panelfw.py`): one signed image per kind of panel (board), held for the caption panels.
//!
//! Relay does not decide whether a signature is good: the panel does, against the key it was built with. What is checked here
//! only turns a file picked by mistake (the merged serial image, another board's, an unsigned or truncated build) into a
//! sentence instead of a failed update on a wall. The store is `panel-firmware/<board>/{panel,flash}.{bin,json}` beside
//! config.json, the files a Python Relay wrote.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// An application slot in `partitions.csv`.
pub const SLOT_BYTES: usize = 0x1E0000;
pub const PROJECT_NAME: &str = "relay-display-app";
const CHIP_ESP32_S3: u16 = 0x0009;
const CHIP_ESP32_P4: u16 = 0x0012;

pub struct Board {
    pub id: &'static str,
    pub label: &'static str,
    pub chip: u16,
    pub chip_name: &'static str,
    pub on_hold: bool,
}

pub const BOARDS: [Board; 2] = [
    Board { id: "crowpanel-adv7", label: "CrowPanel Advance 7\"", chip: CHIP_ESP32_S3, chip_name: "ESP32-S3", on_hold: false },
    Board { id: "waveshare-p4-10.1", label: "Waveshare ESP32-P4 10.1\"", chip: CHIP_ESP32_P4, chip_name: "ESP32-P4", on_hold: true },
];
pub const DEFAULT_BOARD: &str = "crowpanel-adv7";

pub fn board(id: &str) -> Option<&'static Board> {
    BOARDS.iter().find(|b| b.id == id)
}

pub fn active_boards() -> impl Iterator<Item = &'static Board> {
    BOARDS.iter().filter(|b| !b.on_hold)
}

pub fn board_label(id: &str) -> String {
    board(id).map_or_else(|| id.to_owned(), |b| b.label.to_owned())
}

/// The board whose firmware a panel takes: the one it reports, or the CrowPanel for a panel that reports none. A board Relay does
/// not know, or one on hold, takes none: `""`.
pub fn panel_board(reported: &str) -> &'static str {
    let id = if reported.is_empty() { DEFAULT_BOARD } else { reported };
    match board(id) {
        Some(b) if !b.on_hold => b.id,
        _ => "",
    }
}

const IMAGE_MAGIC: u8 = 0xE9;
const APP_DESC_MAGIC: u32 = 0xABCD_5432;
const HEADER_BYTES: usize = 24;
const SEGMENT_HEADER_BYTES: usize = 8;
const APP_DESC_OFFSET: usize = 32;
const VERSION_OFFSET: usize = APP_DESC_OFFSET + 16;
const PROJECT_OFFSET: usize = VERSION_OFFSET + 32;
const HEAD_BYTES: usize = PROJECT_OFFSET + 32;
const SECTOR: usize = 4096;
const SIG_MAGIC: [u8; 4] = [0xe7, 0x02, 0x00, 0x00];
const SIG_DIGEST_OFFSET: usize = 4;
const SIG_CRC_OFFSET: usize = 1196;
/// `app0` where a merged image carries the application.
pub const APP_OFFSET: usize = 0x10000;
const PARTITION_TABLE_OFFSET: usize = 0x8000;
const PARTITION_MAGIC: [u8; 2] = [0xaa, 0x50];
pub const FLASH_BYTES: usize = APP_OFFSET + SLOT_BYTES;

/// `X.Y.Z` as numbers (one to five digits each), or `None`.
pub fn parse_version(text: &str) -> Option<(u32, u32, u32)> {
    let parts: Vec<&str> = text.split('.').collect();
    if parts.len() != 3 || !parts.iter().all(|p| (1..=5).contains(&p.len()) && p.bytes().all(|b| b.is_ascii_digit())) {
        return None;
    }
    Some((parts[0].parse().ok()?, parts[1].parse().ok()?, parts[2].parse().ok()?))
}

/// Whether an image at `candidate` is a step forward from a panel on `running` (a version either side cannot read is not one).
pub fn newer(candidate: &str, running: &str) -> bool {
    matches!((parse_version(candidate), parse_version(running)), (Some(a), Some(b)) if a > b)
}

fn cstr(raw: &[u8]) -> String {
    let end = raw.iter().position(|b| *b == 0).unwrap_or(raw.len());
    raw[..end].iter().map(|b| if b.is_ascii() { *b as char } else { '\u{fffd}' }).collect()
}

fn u32_at(data: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([data[at], data[at + 1], data[at + 2], data[at + 3]])
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for b in data {
        crc ^= u32::from(*b);
        for _ in 0..8 {
            crc = if crc & 1 != 0 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }
    !crc
}

fn sha(data: &[u8]) -> Vec<u8> {
    Sha256::digest(data).to_vec()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn image_end(data: &[u8]) -> Result<(usize, usize), String> {
    let count = data[1] as usize;
    let mut pos = HEADER_BYTES;
    for _ in 0..count {
        if pos + SEGMENT_HEADER_BYTES > data.len() {
            return Err("that file ends in the middle of its own header".into());
        }
        let length = u32_at(data, pos + 4) as usize;
        pos += SEGMENT_HEADER_BYTES + length;
        if pos > data.len() {
            return Err("that file is shorter than its own header says; it was cut off".into());
        }
    }
    let checked = (pos + 1).div_ceil(16) * 16;
    let end = if data[23] == 1 { checked + 32 } else { checked };
    if end > data.len() {
        return Err("that file is shorter than its own header says; it was cut off".into());
    }
    Ok((checked, end))
}

const UNSIGNED: &str = "That image is not signed. A panel only installs an image signed with the release key, and refuses an unsigned one; use the relay-display-app.bin a release publishes.";

/// Check an upload. Returns `(version, sha256 of the file, board)` or the sentence saying what is wrong with it. With `want`, an
/// image for another board is refused.
pub fn inspect_board(data: &[u8], want: Option<&str>) -> Result<(String, String, &'static str), String> {
    if data.is_empty() {
        return Err("The upload was empty.".into());
    }
    if data.len() > SLOT_BYTES {
        return Err("That file is larger than a panel's application slot, so no panel could install it.".into());
    }
    if data.len() < HEAD_BYTES || data[0] != IMAGE_MAGIC {
        return Err("That file is not panel firmware. Upload relay-display-app.bin from the release.".into());
    }
    if u32_at(data, APP_DESC_OFFSET) != APP_DESC_MAGIC {
        return Err("That is the serial-flash image (relay-display.bin), which holds a bootloader and a partition table that an update cannot write. Upload relay-display-app.bin, the application alone, from the same release.".into());
    }
    let chip = u16::from_le_bytes([data[12], data[13]]);
    let Some(found) = BOARDS.iter().find(|b| b.chip == chip) else {
        let names: Vec<&str> = active_boards().map(|b| b.chip_name).collect();
        return Err(format!("That image is for another chip, not a panel's {}.", names.join(" or ")));
    };
    if found.on_hold {
        return Err(format!("That image is for the {}, whose panels are on hold; Relay does not keep firmware for them.", found.label));
    }
    if let Some(want) = want {
        if found.id != want {
            return Err(format!("That image is for the {}, not the {}.", found.label, board_label(want)));
        }
    }
    let project = cstr(&data[PROJECT_OFFSET..PROJECT_OFFSET + 32]);
    if project != PROJECT_NAME {
        return Err(format!("That image is for another board ({}); a Relay panel's image is named {PROJECT_NAME}.", if project.is_empty() { "unnamed" } else { &project }));
    }
    let version = cstr(&data[VERSION_OFFSET..VERSION_OFFSET + 32]);
    if parse_version(&version).is_none() {
        return Err(format!("That image's version ({}) is not a release number like 0.2.0.", if version.is_empty() { "none" } else { &version }));
    }
    let (checked, end) = image_end(data)?;
    if data[23] == 1 && sha(&data[..checked]) != data[checked..end] {
        return Err("That image is damaged: its own checksum does not match. Download it again.".into());
    }
    if !data.len().is_multiple_of(SECTOR) || data.len() < 2 * SECTOR {
        return Err(UNSIGNED.into());
    }
    let block = &data[data.len() - SECTOR..];
    if block[..4] != SIG_MAGIC {
        return Err(UNSIGNED.into());
    }
    if data[data.len() - 2 * SECTOR..][..4] == SIG_MAGIC {
        return Err("That image carries two signatures; it was signed twice. Build it again.".into());
    }
    if block[SIG_DIGEST_OFFSET..SIG_DIGEST_OFFSET + 32] != sha(&data[..data.len() - SECTOR])[..] {
        return Err("That image's signature is not for these bytes: the file was changed after it was signed.".into());
    }
    if u32_at(block, SIG_CRC_OFFSET) != crc32(&block[..SIG_CRC_OFFSET]) {
        return Err("That image's signature block is damaged.".into());
    }
    Ok((version, hex(&sha(data)), found.id))
}

/// Check a merged image for the USB flasher.
pub fn inspect_flash(data: &[u8], want: Option<&str>) -> Result<(String, String, &'static str), String> {
    if data.is_empty() {
        return Err("The upload was empty.".into());
    }
    if data.len() > FLASH_BYTES {
        return Err("That file is larger than a panel's bootloader and application slot together.".into());
    }
    if data[0] == IMAGE_MAGIC && data.len() >= HEAD_BYTES && u32_at(data, APP_DESC_OFFSET) == APP_DESC_MAGIC {
        return Err("That is the application alone (relay-display-app.bin), which updates a panel that is already running and cannot start a blank one. Upload relay-display.bin from the same release.".into());
    }
    if data[0] != IMAGE_MAGIC || data.len() <= APP_OFFSET || data[PARTITION_TABLE_OFFSET..PARTITION_TABLE_OFFSET + 2] != PARTITION_MAGIC {
        return Err("That file is not a panel's USB image. Upload relay-display.bin from the release.".into());
    }
    match inspect_board(&data[APP_OFFSET..], want) {
        Ok((version, _, found)) => Ok((version, hex(&sha(data)), found)),
        Err(e) => Err(e.replace("relay-display-app.bin", "relay-display.bin")),
    }
}

/// What the store says about one image (`Info` and `FlashInfo`).
#[derive(Clone, Debug, PartialEq)]
pub struct Info {
    pub version: String,
    pub size: u64,
    pub sha256: String,
    pub uploaded_at: f64,
    pub filename: String,
    pub board: String,
}

impl Info {
    pub fn public(&self, now: f64) -> Value {
        json!({"version": self.version, "size": self.size, "sha256": self.sha256, "filename": self.filename, "board": self.board,
            "uploaded_ago_s": ((now - self.uploaded_at).max(0.0) * 10.0).round() / 10.0, "board_label": board_label(&self.board)})
    }
    pub fn manifest(&self) -> Value {
        let b = board(&self.board);
        json!({"version": self.version, "board": self.board, "project": PROJECT_NAME, "chip": b.map_or("", |b| b.chip_name),
            "flash_mb": 16, "sha256": self.sha256, "size": self.size, "url": flash_url(&self.board)})
    }
    /// The offer's path on the viewer port.
    pub fn image_url(&self) -> String {
        format!("/api/panels/firmware/{}/{}.bin", self.board, self.version)
    }
}

pub fn flash_url(board: &str) -> String {
    format!("/api/admin/panels/flash/{board}.bin")
}

/// Which of the two images a board holds.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// What panels are offered over the network.
    App,
    /// The merged image for the USB flasher, never offered to a panel.
    Flash,
}

impl Kind {
    fn stem(self) -> &'static str {
        match self {
            Kind::App => "panel",
            Kind::Flash => "flash",
        }
    }
}

pub struct Store {
    root: PathBuf,
}

impl Store {
    /// `dir` is the state folder (config.json's); images are under `panel-firmware/`.
    pub fn new(dir: &Path) -> Self {
        Self { root: dir.join("panel-firmware") }
    }

    fn folder(&self, board: &str) -> Option<PathBuf> {
        // Built from the board's own id, never from the caller's string.
        board_id(board).map(|b| self.root.join(b))
    }

    fn paths(&self, board: &str, kind: Kind) -> Option<(PathBuf, PathBuf)> {
        let f = self.folder(board)?;
        Some((f.join(format!("{}.bin", kind.stem())), f.join(format!("{}.json", kind.stem()))))
    }

    /// A store from before #122 held one image at `panel-firmware/panel.bin`; it is the CrowPanel's and moves into place.
    fn migrate(&self) {
        let (old_image, old_meta) = (self.root.join("panel.bin"), self.root.join("panel.json"));
        if !old_image.exists() && !old_meta.exists() {
            return;
        }
        let Some((image, meta)) = self.paths(DEFAULT_BOARD, Kind::App) else { return };
        if !meta.exists() && old_meta.exists() && old_image.exists() {
            if let Some(f) = self.folder(DEFAULT_BOARD) {
                let _ = fs::create_dir_all(f);
            }
            let _ = fs::rename(&old_image, &image);
            let _ = fs::rename(&old_meta, &meta);
        }
        let _ = fs::remove_file(old_meta);
        let _ = fs::remove_file(old_image);
    }

    /// The image stored for `board`; a half-written or hand-edited store reads as none.
    pub fn current(&self, board: &str, kind: Kind) -> Option<Info> {
        if kind == Kind::App {
            self.migrate();
        }
        let (image, meta) = self.paths(board, kind)?;
        let m: Value = serde_json::from_str(&fs::read_to_string(meta).ok()?).ok()?;
        let version = m.get("version")?.as_str()?.to_owned();
        let info = Info {
            size: m.get("size")?.as_f64()? as u64,
            sha256: m.get("sha256")?.as_str()?.to_owned(),
            uploaded_at: m.get("uploaded_at")?.as_f64()?,
            filename: m.get("filename").and_then(Value::as_str).unwrap_or("").to_owned(),
            board: board_id(board)?.to_owned(),
            version,
        };
        (parse_version(&info.version).is_some() && fs::metadata(image).ok()?.len() == info.size).then_some(info)
    }

    pub fn path(&self, board: &str, kind: Kind) -> Option<PathBuf> {
        self.current(board, kind)?;
        Some(self.paths(board, kind)?.0)
    }

    /// The path of the image if it is the one at `version`.
    pub fn image_path_for(&self, version: &str, board: &str) -> Option<PathBuf> {
        let info = self.current(board, Kind::App)?;
        (info.version == version).then(|| self.paths(board, Kind::App).map(|p| p.0))?
    }

    /// Check `data` and keep it as its board's image, replacing the one held.
    pub fn store(&self, data: &[u8], filename: &str, want: Option<&str>, kind: Kind, now: f64) -> Result<Info, String> {
        let (version, digest, found) = match kind {
            Kind::App => inspect_board(data, want)?,
            Kind::Flash => inspect_flash(data, want)?,
        };
        let info = Info {
            version,
            size: data.len() as u64,
            sha256: digest,
            uploaded_at: now,
            filename: filename.chars().filter(|c| crate::text::printable(*c)).take(96).collect(),
            board: found.to_owned(),
        };
        if kind == Kind::App {
            self.migrate();
        }
        let folder = self.folder(found).ok_or("unknown board")?;
        fs::create_dir_all(&folder).map_err(|_| "The image could not be saved.".to_owned())?;
        let (image, meta) = self.paths(found, kind).ok_or("unknown board")?;
        // The image first, the description second: a crash between the two leaves a description whose size no longer matches.
        let tmp = folder.join(format!("{}.bin.tmp", kind.stem()));
        fs::write(&tmp, data).and_then(|_| fs::rename(&tmp, &image)).map_err(|_| "The image could not be saved.".to_owned())?;
        let mtmp = folder.join(format!("{}.json.tmp", kind.stem()));
        let body = json!({"version": info.version, "size": info.size, "sha256": info.sha256, "uploaded_at": info.uploaded_at,
            "filename": info.filename, "board": info.board});
        fs::write(&mtmp, body.to_string()).and_then(|_| fs::rename(&mtmp, &meta)).map_err(|_| "The image could not be saved.".to_owned())?;
        Ok(info)
    }

    /// Forget the image stored for `board`. True if there was one.
    pub fn remove(&self, board: &str, kind: Kind) -> bool {
        let had = self.current(board, kind).is_some();
        if let Some((image, meta)) = self.paths(board, kind) {
            let _ = fs::remove_file(meta);
            let _ = fs::remove_file(image);
        }
        had
    }
}

fn board_id(board_: &str) -> Option<&'static str> {
    board(board_).map(|b| b.id)
}
