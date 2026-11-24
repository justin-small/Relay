//! Uploaded media files on disk (`media.py`, #205). Files live in the state folder at `signage/media/<sha256>.<ext>`, named by their
//! content so a URL never changes meaning and the player can cache it for good. Thumbnails, made by the admin browser at upload, are
//! `signage/thumbs/<sha256>.webp`. Each file is also copied once into `backups/signage-media/`, beside the nightly `relay.db`
//! snapshots that hold the playlists. The type is decided from the file's first bytes, never from its name or the browser's Content-Type.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use axum::body::Body;
use http_body_util::BodyExt;
use sha2::{Digest, Sha256};

pub const MB: u64 = 1024 * 1024;
pub const IMAGE_LIMIT: u64 = 25 * MB;
pub const VIDEO_LIMIT: u64 = 500 * MB;
pub const THUMB_LIMIT: usize = 512 * 1024;
pub const LOW_DISK: u64 = 2 * 1024 * MB;
/// Where the player and the preview fetch an uploaded file.
pub const PUBLIC_PREFIX: &str = "/signage/_/media/";

const NOT_PLAYABLE: &str = "That file is not an image or video signage can play. Use JPEG, PNG, WebP or GIF images, or MP4 or WebM video.";

/// An upload Relay will not keep: a fixed sentence for the admin page.
#[derive(Debug, PartialEq, Eq)]
pub struct Refused {
    pub message: &'static str,
    pub status: u16,
}

const fn refuse(message: &'static str, status: u16) -> Refused {
    Refused { message, status }
}

/// `(kind, mime)` of a stored extension.
pub fn type_of(ext: &str) -> Option<(&'static str, &'static str)> {
    Some(match ext {
        "jpg" => ("image", "image/jpeg"),
        "png" => ("image", "image/png"),
        "webp" => ("image", "image/webp"),
        "gif" => ("image", "image/gif"),
        "mp4" => ("video", "video/mp4"),
        "webm" => ("video", "video/webm"),
        _ => return None,
    })
}

/// The extension for a file starting with `head`, or `None` when it is not a type signage plays.
pub fn sniff(head: &[u8]) -> Option<&'static str> {
    if head.starts_with(&[0xff, 0xd8, 0xff]) {
        return Some("jpg");
    }
    if head.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Some("png");
    }
    if head.len() >= 12 && &head[..4] == b"RIFF" && &head[8..12] == b"WEBP" {
        return Some("webp");
    }
    if head.len() >= 6 && (&head[..6] == b"GIF87a" || &head[..6] == b"GIF89a") {
        return Some("gif");
    }
    if head.len() >= 8 && &head[4..8] == b"ftyp" {
        return Some("mp4");
    }
    if head.starts_with(&[0x1a, 0x45, 0xdf, 0xa3]) {
        return Some("webm");
    }
    None
}

pub fn file_name(sha: &str, ext: &str) -> String {
    format!("{sha}.{ext}")
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `^([0-9a-f]{64})\.(jpg|png|webp|gif|mp4|webm)$`: the sha and extension of a public file name.
pub fn parse_file_name(name: &str) -> Option<(&str, &str)> {
    let (sha, ext) = name.split_once('.')?;
    (is_hex64(sha) && type_of(ext).is_some()).then_some((sha, ext))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Where uploads, thumbnails and their backup copies are.
#[derive(Clone, Debug)]
pub struct Media {
    root: PathBuf,
    backups: PathBuf,
}

impl Media {
    /// `state` is the state folder; `backups` the backups folder (`RELAY_DB_BACKUPS`, else `<state>/backups`).
    pub fn new(state: &Path, backups: &Path) -> Self {
        Self { root: state.join("signage"), backups: backups.to_owned() }
    }

    pub fn media_dir(&self) -> PathBuf {
        self.root.join("media")
    }

    pub fn thumbs_dir(&self) -> PathBuf {
        self.root.join("thumbs")
    }

    pub fn mirror_dir(&self) -> PathBuf {
        self.backups.join("signage-media")
    }

    fn ensure(path: &Path) -> std::io::Result<()> {
        fs::create_dir_all(path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o700));
        }
        Ok(())
    }

    /// `folder/name` when it is a file directly inside `folder` once links and dots are resolved, else `None`.
    fn inside(folder: &Path, name: &str) -> Option<PathBuf> {
        let base = fs::canonicalize(folder).ok()?;
        let full = fs::canonicalize(base.join(name)).ok()?;
        (full.parent() == Some(base.as_path()) && full.is_file()).then_some(full)
    }

    /// The stored file for a public file name, or `None` when the name is not one of ours or the file is gone.
    pub fn file_path(&self, name: &str) -> Option<PathBuf> {
        parse_file_name(name)?;
        Self::inside(&self.media_dir(), name)
    }

    pub fn thumb_path(&self, sha: &str) -> Option<PathBuf> {
        is_hex64(sha).then(|| Self::inside(&self.thumbs_dir(), &format!("{sha}.webp"))).flatten()
    }

    /// Store an upload arriving as a request body. Returns `(sha256, ext, size)`. Refuses a file of a type signage does not play, an
    /// image over 25 MB and a video over 500 MB, as soon as it passes the limit, without keeping the rest. The file is written beside
    /// its final place and moved in once whole.
    pub async fn receive(&self, mut body: Body) -> Result<(String, String, u64), Refused> {
        let folder = self.media_dir();
        Self::ensure(&folder).map_err(|_| refuse("The file could not be saved.", 500))?;
        let tmp = folder.join(format!(".upload-{}", crate::rand_hex(8)));
        let result = self.receive_into(&mut body, &tmp, &folder).await;
        if result.is_err() {
            let _ = fs::remove_file(&tmp);
        }
        result
    }

    async fn receive_into(&self, body: &mut Body, tmp: &Path, folder: &Path) -> Result<(String, String, u64), Refused> {
        let mut out = fs::File::create(tmp).map_err(|_| refuse("The file could not be saved.", 500))?;
        let (mut digest, mut size, mut head, mut ext, mut limit) = (Sha256::new(), 0u64, Vec::<u8>::new(), None::<&'static str>, VIDEO_LIMIT);
        while let Some(frame) = body.frame().await {
            let Ok(frame) = frame else { return Err(refuse("The upload was interrupted.", 400)) };
            let Some(chunk) = frame.data_ref() else { continue };
            if chunk.is_empty() {
                continue;
            }
            if ext.is_none() {
                if head.len() < 32 {
                    head.extend_from_slice(&chunk[..chunk.len().min(32 - head.len())]);
                }
                if head.len() >= 12 {
                    ext = sniff(&head);
                    let Some(e) = ext else { return Err(refuse(NOT_PLAYABLE, 415)) };
                    limit = if type_of(e).is_some_and(|t| t.0 == "image") { IMAGE_LIMIT } else { VIDEO_LIMIT };
                }
            }
            size += chunk.len() as u64;
            if size > limit {
                return Err(refuse("That file is too large. Images can be up to 25 MB and videos up to 500 MB.", 413));
            }
            digest.update(chunk);
            out.write_all(chunk).map_err(|_| refuse("The file could not be saved.", 500))?;
        }
        let ext = match ext.or_else(|| sniff(&head)) {
            Some(e) if size > 0 => e,
            _ => return Err(refuse(NOT_PLAYABLE, 415)),
        };
        drop(out);
        let sha = hex(&digest.finalize());
        let final_path = folder.join(file_name(&sha, ext));
        if final_path.exists() {
            let _ = fs::remove_file(tmp);
        } else {
            fs::rename(tmp, &final_path).map_err(|_| refuse("The file could not be saved.", 500))?;
        }
        self.mirror(&sha, ext);
        Ok((sha, ext.to_owned(), size))
    }

    pub fn save_thumb(&self, sha: &str, data: &[u8]) -> Result<(), Refused> {
        if data.is_empty() || data.len() > THUMB_LIMIT {
            return Err(refuse("The thumbnail is too large.", 413));
        }
        if !matches!(sniff(&data[..data.len().min(32)]), Some("webp" | "jpg" | "png")) {
            return Err(refuse("The thumbnail is not an image.", 415));
        }
        if !is_hex64(sha) {
            return Err(refuse("The thumbnail is not an image.", 415));
        }
        let folder = self.thumbs_dir();
        Self::ensure(&folder).map_err(|_| refuse("The thumbnail could not be saved.", 500))?;
        let tmp = folder.join(format!(".thumb-{}", crate::rand_hex(8)));
        fs::write(&tmp, data).and_then(|()| fs::rename(&tmp, folder.join(format!("{sha}.webp")))).map_err(|_| refuse("The thumbnail could not be saved.", 500))
    }

    /// Copy a stored file into the backups folder once. A failure is logged; the upload still stands.
    pub fn mirror(&self, sha: &str, ext: &str) {
        let name = file_name(sha, ext);
        let src = self.media_dir().join(&name);
        let folder = self.mirror_dir();
        let done = (|| -> std::io::Result<()> {
            Self::ensure(&folder)?;
            let dst = folder.join(&name);
            if dst.exists() {
                return Ok(());
            }
            let tmp = folder.join(format!(".{name}.part"));
            fs::copy(&src, &tmp)?;
            fs::rename(&tmp, &dst)
        })();
        if let Err(e) = done {
            tracing::warn!("signage: could not copy {name} into the backups folder: {e}");
        }
    }

    /// Delete the stored file and thumbnail when no media row names `sha` any more (the backup copy stays until `prune_mirror` finds no
    /// kept snapshot naming it). `used` is the hashes that still do.
    pub fn remove_if_unused(&self, sha: &str, ext: &str, used: &[String]) {
        if used.iter().any(|u| u == sha) {
            return;
        }
        for p in [self.media_dir().join(file_name(sha, ext)), self.thumbs_dir().join(format!("{sha}.webp"))] {
            match fs::remove_file(&p) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => tracing::warn!("signage: could not delete {}: {e}", p.display()),
            }
        }
    }

    /// Delete backup copies that the database (`current`) does not name. A kept `relay-*.db` snapshot might name one this server cannot
    /// read as Python does, so with any snapshot present everything is kept. Returns how many were deleted.
    pub fn prune_mirror(&self, current: &[String]) -> usize {
        let folder = self.mirror_dir();
        let Ok(entries) = fs::read_dir(&folder) else { return 0 };
        let snapshots = fs::read_dir(&self.backups).map(|d| d.flatten().any(|e| e.file_name().to_string_lossy().starts_with("relay-"))).unwrap_or(false);
        if snapshots {
            return 0;
        }
        let mut gone = 0;
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if let Some((sha, _)) = parse_file_name(&name) {
                if !current.iter().any(|c| c == sha) && fs::remove_file(e.path()).is_ok() {
                    gone += 1;
                }
            }
        }
        gone
    }

    /// Copy any stored file the backups folder lacks (an install from before mirroring, a failed copy). `rows` are `(sha, ext)`.
    pub fn mirror_missing(&self, rows: &[(String, String)]) {
        for (sha, ext) in rows {
            let name = file_name(sha, ext);
            if !self.mirror_dir().join(&name).exists() && self.media_dir().join(&name).is_file() {
                self.mirror(sha, ext);
            }
        }
    }

    /// Bytes used by stored media and thumbnails, and free space on that disk (-1 when unknown).
    pub fn disk(&self) -> (u64, i64) {
        let used: u64 = [self.media_dir(), self.thumbs_dir()]
            .iter()
            .filter_map(|d| fs::read_dir(d).ok())
            .flat_map(|d| d.flatten())
            .filter(|e| !e.file_name().to_string_lossy().starts_with('.'))
            .filter_map(|e| e.metadata().ok().filter(|m| m.is_file()).map(|m| m.len()))
            .sum();
        (used, -1)
    }
}
