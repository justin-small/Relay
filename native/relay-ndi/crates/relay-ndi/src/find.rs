//! Finding sources: through a discovery server, extra addresses, groups and mDNS, as the SDK is configured.

use crate::ffi::{self, Api, Handle, FindCreate};
use std::ffi::CString;
use std::ptr;
use std::sync::Arc;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceInfo {
    pub name: String,
    /// The address the sender advertises ("192.168.1.5:5961"), empty if it gave none.
    pub address: String,
}

pub struct Finder {
    api: Arc<Api>,
    h: Handle,
}

// SAFETY: a finder is used from one thread at a time (`&mut self` where it matters); the handle has no affinity.
unsafe impl Send for Finder {}

impl Finder {
    pub fn open(show_local: bool) -> Result<Finder, String> {
        let api = ffi::api()?;
        let create = FindCreate { show_local_sources: show_local, groups: ptr::null(), extra_ips: ptr::null() };
        // SAFETY: `create` outlives the call; null groups and extra IPs mean the SDK's configuration.
        let h = unsafe { (api.find_create)(&create) };
        if h.is_null() {
            return Err("NDI would not start looking for sources.".into());
        }
        Ok(Finder { api, h })
    }

    /// Wait until the set of sources changes or `ms` pass.
    pub fn wait(&mut self, ms: u32) -> bool {
        // SAFETY: `h` is live until drop.
        unsafe { (self.api.find_wait)(self.h, ms) }
    }

    pub fn sources(&mut self) -> Vec<SourceInfo> {
        let mut n: u32 = 0;
        // SAFETY: the returned array is valid until the next call on this finder, and is copied out before then.
        unsafe {
            let p = (self.api.find_sources)(self.h, &mut n);
            if p.is_null() {
                return Vec::new();
            }
            (0..n as usize)
                .map(|i| {
                    let s = &*p.add(i);
                    SourceInfo { name: ffi::text(s.name), address: ffi::text(s.url) }
                })
                .collect()
        }
    }

    /// Look for `seconds` (returning early once the list has been still for a moment), then list what was seen.
    pub fn scan(seconds: f64) -> Result<Vec<SourceInfo>, String> {
        let mut f = Finder::open(true)?;
        let end = std::time::Instant::now() + std::time::Duration::from_secs_f64(seconds.max(0.0));
        let mut last = f.sources();
        while std::time::Instant::now() < end {
            let left = end.saturating_duration_since(std::time::Instant::now()).as_millis() as u32;
            if !f.wait(left.min(1000)) {
                break;
            }
            last = f.sources();
        }
        Ok(f.sources().into_iter().chain(last).fold(Vec::new(), |mut acc: Vec<SourceInfo>, s| {
            if !acc.iter().any(|a| a.name == s.name) {
                acc.push(s);
            }
            acc
        }))
    }

    /// The source called `name`, waiting up to `seconds` for it to appear.
    pub fn locate(&mut self, name: &str, seconds: f64) -> Option<SourceInfo> {
        let end = std::time::Instant::now() + std::time::Duration::from_secs_f64(seconds.max(0.0));
        loop {
            if let Some(s) = self.sources().into_iter().find(|s| s.name == name) {
                return Some(s);
            }
            let left = end.saturating_duration_since(std::time::Instant::now()).as_millis() as u32;
            if left == 0 {
                return None;
            }
            self.wait(left.min(500));
        }
    }
}

impl Drop for Finder {
    fn drop(&mut self) {
        // SAFETY: `h` came from find_create and is destroyed once.
        unsafe { (self.api.find_destroy)(self.h) }
    }
}

pub(crate) fn cstr(s: &str) -> CString {
    CString::new(s.replace('\0', "")).unwrap_or_default()
}
